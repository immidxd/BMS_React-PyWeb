# -*- coding: utf-8 -*-
"""Одне місце, яке знає, ЗВІДКИ посилаються на товар, і вміє це безпечно перевісити.

Навіщо окремий модуль
─────────────────────
Злиття товарів робиться щонайменше з двох місць — ручний скрипт
(`backend/scripts/merge_size_notation_duplicates.py`) і кнопка «прийняти» в
картці кандидатів (`backend/routers/merge_candidates.py`). Поки кожне тримало
власний список таблиць, вони розходились: скрипт проходив усі місця, а `accept`
перевішував рівно `order_items` і `telegram_posts` і видаляв товар — усе інше
або лишалось висіти на неіснуючому id, або тихо обнулялось через
`ON DELETE SET NULL`, забираючи з собою зв'язок публікації з товаром.

⚠️ Три місця тримають `products.id` БЕЗ зовнішнього ключа, тож база їх не
захищає й не каскадить — знайти їх можна лише за конвенцією імені. Тому список
будується як «FK з pg_constraint + колонки за конвенцією»: жодного з двох
джерел окремо не досить.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger("bms.product_refs")

# Колонки з products.id, у яких НЕМА зовнішнього ключа: DELETE товару лишає їх
# висіти мовчки, без жодної помилки.
UNENFORCED_REFS: tuple[tuple[str, str], ...] = (
    ("product_images", "productid"),
    ("story_automation_drafts", "product_id"),
    ("journal_writeback_queue", "product_id"),
)

_FK_SQL = """
    SELECT c.conrelid::regclass::text AS tbl, a.attname AS col
    FROM pg_constraint c
    JOIN unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
    WHERE c.confrelid = 'products'::regclass AND c.contype = 'f'
    ORDER BY 1, 2
"""


def product_ref_columns(session: Session) -> list[tuple[str, str]]:
    """Усі `(таблиця, колонка)`, що тримають products.id — і з FK, і без нього."""
    refs = [(t, c) for t, c in session.execute(text(_FK_SQL)).fetchall()]
    known = set(refs)
    refs.extend((t, c) for t, c in UNENFORCED_REFS if (t, c) not in known)
    return refs


def count_product_refs(session: Session, product_id: int,
                       refs: Optional[list[tuple[str, str]]] = None) -> tuple[int, dict]:
    """Скільки рядків і де саме посилаються на товар."""
    refs = refs if refs is not None else product_ref_columns(session)
    detail, total = {}, 0
    for tbl, col in refs:
        n = session.execute(
            text(f"SELECT count(*) FROM {tbl} WHERE {col} = :i"), {"i": product_id}
        ).scalar() or 0
        if n:
            detail[f"{tbl}.{col}"] = n
            total += n
    return total, detail


def find_dangling_product_refs(session: Session) -> dict:
    """Посилання на НЕІСНУЮЧИЙ товар. Порожній словник = чисто.

    Перевіряються лише три місця без FK — решту база тримає сама (CASCADE або
    SET NULL), і зламати їх можна хіба що вимкнувши констрейнти.

    ⚠️ Зовнішні ключі сюди свідомо НЕ додано. Єдиний варіант, сумісний із
    наявним прибиранням орфанів, — `ON DELETE CASCADE`, а тоді збій на кшталт
    19.08.2026 (знесло 135 живих товарів) забрав би з собою ще й записи фото.
    Тому база лишається терпимою, а цілісність тримають два інші рівні: усі
    злиття ходять через `repoint_product_refs`, і ця перевірка світить, якщо
    з'явився шлях, що видаляє товари повз нього.
    """
    out = {}
    for tbl, col in UNENFORCED_REFS:
        n = session.execute(text(f"""
            SELECT count(*) FROM {tbl} x
            WHERE x.{col} IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM products p WHERE p.id = x.{col})
        """)).scalar() or 0
        if n:
            out[f"{tbl}.{col}"] = n
    return out


def repoint_product_refs(session: Session, from_id: int, to_id: int, *,
                         refs: Optional[list[tuple[str, str]]] = None,
                         skip_tables: Iterable[str] = ()) -> dict:
    """Перевісити всі посилання з `from_id` на `to_id`.

    Рядок, який на новому власнику дав би дубль (унікальний ключ) або
    самопосилання, не ламає операцію — він видаляється, бо потрібного там уже
    досягнуто. Самопосилання реальне: у `merge_candidates` пара
    (new_product_id, suggested_id), і перевішування одного кінця на другий
    зробило б рядок «товар — двійник самого себе».

    `skip_tables` — таблиці, які свідомо лишаємо каскаду; виклик мусить
    пояснити чому.

    Наприкінці НЕ вірить лічильникам, а перевіряє факт: на `from_id` не
    лишилось жодного посилання. Інакше — виняток, бо ходимо по `ctid`
    (фізичний вказівник), і паралельний запис із запущеного застосунку зробив
    би втрату мовчазною.
    """
    if from_id == to_id:
        raise ValueError("перевішування на самого себе")
    refs = refs if refs is not None else product_ref_columns(session)
    skip = set(skip_tables)
    active = [(t, c) for t, c in refs if t not in skip]

    # Таблиці, де на products.id дивиться БІЛЬШЕ ніж одна колонка — саме там
    # можливе самопосилання.
    cols_by_table: dict[str, list[str]] = {}
    for tbl, col in active:
        cols_by_table.setdefault(tbl, []).append(col)

    moved: dict[str, int] = {}
    dropped: dict[str, int] = {}

    for tbl, col in active:
        ctids = [r[0] for r in session.execute(
            text(f"SELECT ctid FROM {tbl} WHERE {col} = :i"), {"i": from_id}
        ).fetchall()]
        if not ctids:
            continue
        siblings = [c for c in cols_by_table[tbl] if c != col]
        key = f"{tbl}.{col}"
        for ctid in ctids:
            if siblings:
                row = session.execute(
                    text(f"SELECT {', '.join(siblings)} FROM {tbl} WHERE ctid = :c"),
                    {"c": ctid},
                ).fetchone()
                if row is not None and to_id in tuple(row):
                    session.execute(text(f"DELETE FROM {tbl} WHERE ctid = :c"), {"c": ctid})
                    dropped[key] = dropped.get(key, 0) + 1
                    continue
            sp = session.begin_nested()
            try:
                session.execute(
                    text(f"UPDATE {tbl} SET {col} = :t WHERE ctid = :c"),
                    {"t": to_id, "c": ctid},
                )
                sp.commit()
                moved[key] = moved.get(key, 0) + 1
            except IntegrityError:
                sp.rollback()
                session.execute(text(f"DELETE FROM {tbl} WHERE ctid = :c"), {"c": ctid})
                dropped[key] = dropped.get(key, 0) + 1

    leftovers = {}
    for tbl, col in active:
        n = session.execute(
            text(f"SELECT count(*) FROM {tbl} WHERE {col} = :i"), {"i": from_id}
        ).scalar() or 0
        if n:
            leftovers[f"{tbl}.{col}"] = n
    if leftovers:
        raise RuntimeError(
            f"на id={from_id} лишились посилання після перевішування: {leftovers}"
        )

    result = {"moved": moved, "dropped_as_duplicate": dropped}
    logger.info(f"[product-refs] {from_id} → {to_id}: {result}"
                + (f" (пропущено: {sorted(skip)})" if skip else ""))
    return result

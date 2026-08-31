# -*- coding: utf-8 -*-
"""Злити товари-двійники, що розійшлись лише НАПИСАННЯМ розміру.

Кейс 27.08.2026 (завіз «24.08.2026(Андрій)»): в аркуші розмір переписали з `38⅔`
на `38.6`. Парсер шукає товар за вмістом і порівнює розмір як текст
(`_fields_match`), тож нового написання він не впізнав як той самий рядок — а що
бренд/тип/стан/колір збіглись, спрацювала гілка «Ростовка» (sheets_parser.py:3537)
і вставила НОВИЙ рядок. Старий лишився назавжди: `_reconcile_delivery_orphans`
судить за НОМЕРОМ, а номер в аркуші є (його тримає новий рядок), тож дробовий
рядок вважається живим.

Наслідок — товар розколотий навпіл: парсер товарів оновлює один рядок, парсер
замовлень чіпляється за інший, а ручна робота (матеріали, чернетки сторіс,
журнальна черга) осідає на тому, який людина відкривала.

Що робить скрипт (dry-run за замовчуванням, `--apply` застосовує):
  1. знаходить кластери: однакові (номер, колір), чиї розміри зводяться
     `decimalize_fractions` в ОДИН канон, але записані по-різному;
  2. перевіряє, що це справді один товар (бренд/тип/стан/ціна) — інакше не чіпає
     й каже про це вголос;
  3. обирає ВИЖИВАЮЧОГО: у кого більше посилань, за нічиєї — старший id.
     Так уцілілий id зберігає все, що на ньому висить, включно з тим, чого цей
     скрипт міг не перелічити (кеші фронтенду, зовнішні лістинги);
  4. перевішує на нього посилання з приречених рядків. Рядок, який на новому
     власнику дав би дубль (унікальний ключ), не «ламає» злиття — він просто
     видаляється, бо такий самий там уже є;
  5. видаляє приречені рядки і ТІЛЬКИ ПОТІМ пише виживаючому канонічний розмір —
     інакше UPDATE впаде на uix_products_num_size_color;
  6. JSON-бекап (рядки + УСІ посилання) — умова видалення, а не побажання.

Аркуш НЕ чіпається: парсер із 2026-08-29 зводить дріб до десяткового ще на
читанні, тож у журналі написання може лишатись яким є.

Запуск (з кореня репозиторію):
    ./venv/bin/python backend/scripts/merge_size_notation_duplicates.py
    ./venv/bin/python backend/scripts/merge_size_notation_duplicates.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from models.database import SessionLocal  # noqa: E402
from services.size_normalization import decimalize_fractions  # noqa: E402

BACKUP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "manual_cleanup_backups")
)

# Колонки, що тримають products.id БЕЗ зовнішнього ключа — база їх не захищає,
# тож знайти їх можна лише за конвенцією імені. Пропустити котрусь = лишити
# висяче посилання на видалений рядок, і ніхто про це не дізнається.
UNENFORCED_REFS = [
    ("product_images", "productid"),
    ("story_automation_drafts", "product_id"),
    ("journal_writeback_queue", "product_id"),
]

# Поля, що мають збігатись, аби вважати два рядки одним товаром. Розбіжність —
# привід НЕ зливати й покликати людину.
IDENTITY_FIELDS = ("brandid", "typeid", "conditionid", "price")


def _canon_size(v) -> str:
    """Канонічне написання розміру. Порожнє → ''."""
    if v is None:
        return ""
    return str(decimalize_fractions(str(v))).strip()


def collect_ref_columns(db) -> list[tuple[str, str]]:
    """Усі місця, звідки хтось посилається на products.id: справжні FK + ті,
    що тримаються лише на конвенції."""
    fks = db.execute(text("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname AS col
        FROM pg_constraint c
        JOIN unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.confrelid = 'products'::regclass AND c.contype = 'f'
        ORDER BY 1, 2
    """)).fetchall()
    refs = [(t, c) for t, c in fks]
    known = {(t, c) for t, c in refs}
    for t, c in UNENFORCED_REFS:
        if (t, c) not in known:
            refs.append((t, c))
    return refs


def count_refs(db, refs, pid: int) -> tuple[int, dict]:
    """Скільки рядків і де саме посилаються на товар."""
    detail = {}
    total = 0
    for tbl, col in refs:
        n = db.execute(
            text(f"SELECT count(*) FROM {tbl} WHERE {col} = :i"), {"i": pid}
        ).scalar() or 0
        if n:
            detail[f"{tbl}.{col}"] = n
            total += n
    return total, detail


def find_clusters(db) -> list[dict]:
    """Групи рядків, що відрізняються лише написанням розміру."""
    rows = db.execute(text("""
        SELECT p.id, p.productnumber, p.sizeeu, p.colorid, p.brandid, p.typeid,
               p.conditionid, p.price, p.quantity, p.statusid, p.measurementscm,
               p.deliveryid, d.deliveryname, p.created_at, p.updated_at
        FROM products p
        LEFT JOIN deliveries d ON d.id = p.deliveryid
        ORDER BY p.id
    """)).fetchall()

    groups = defaultdict(list)
    for r in rows:
        canon = _canon_size(r.sizeeu)
        if not canon:
            continue  # без розміру порівнювати нічого
        groups[(r.productnumber, r.colorid, canon)].append(r)

    clusters = []
    for (pnum, colorid, canon), members in groups.items():
        if len(members) < 2:
            continue
        # цікавлять лише ті, де написання РІЗНІ (однакові — це не наш кейс,
        # їх би не пустив унікальний індекс)
        if len({(m.sizeeu or "") for m in members}) < 2:
            continue
        clusters.append({
            "pnum": pnum, "colorid": colorid, "canon": canon, "members": members,
        })
    return clusters


def check_same_item(members) -> list[str]:
    """Чи справді це один товар. Повертає список розбіжностей (порожній = так)."""
    problems = []
    for field in IDENTITY_FIELDS:
        vals = {getattr(m, field) for m in members}
        vals.discard(None)
        if len(vals) > 1:
            problems.append(f"{field}: {sorted(str(v) for v in vals)}")
    return problems


def pick_survivor(db, refs, members):
    """Виживає найбагатший на посилання; за нічиєї — старший id."""
    scored = []
    for m in members:
        total, detail = count_refs(db, refs, m.id)
        scored.append((total, -m.id, m, detail))
    scored.sort(reverse=True)
    survivor = scored[0][2]
    survivor_detail = scored[0][3]
    doomed = [(s[2], s[3]) for s in scored[1:]]
    return survivor, survivor_detail, doomed


def repoint(db, refs, doomed_id: int, survivor_id: int, dry: bool) -> dict:
    """Перевісити посилання. Рядок, що дав би дубль на новому власнику,
    видаляється: такий самий там уже є."""
    moved, dropped = {}, {}
    for tbl, col in refs:
        ids = [r[0] for r in db.execute(
            text(f"SELECT ctid FROM {tbl} WHERE {col} = :i"), {"i": doomed_id}
        ).fetchall()]
        if not ids:
            continue
        if dry:
            moved[f"{tbl}.{col}"] = len(ids)
            continue
        for ctid in ids:
            sp = db.begin_nested()
            try:
                db.execute(
                    text(f"UPDATE {tbl} SET {col} = :s WHERE ctid = :c"),
                    {"s": survivor_id, "c": ctid},
                )
                sp.commit()
                moved[f"{tbl}.{col}"] = moved.get(f"{tbl}.{col}", 0) + 1
            except IntegrityError:
                sp.rollback()
                db.execute(text(f"DELETE FROM {tbl} WHERE ctid = :c"), {"c": ctid})
                dropped[f"{tbl}.{col}"] = dropped.get(f"{tbl}.{col}", 0) + 1
    if not dry:
        # ctid — ФІЗИЧНИЙ вказівник: якщо застосунок паралельно оновив рядок,
        # він змінився, і UPDATE зачепив би нуль рядків МОВЧКИ, лишивши висяче
        # посилання на видалений товар. Тому не віримо лічильникам, а звіряємо
        # факт: на приреченого не має лишитись жодного посилання.
        leftovers = {}
        for tbl, col in refs:
            n = db.execute(
                text(f"SELECT count(*) FROM {tbl} WHERE {col} = :i"), {"i": doomed_id}
            ).scalar() or 0
            if n:
                leftovers[f"{tbl}.{col}"] = n
        if leftovers:
            raise RuntimeError(
                f"на id={doomed_id} лишились посилання після перевішування: "
                f"{leftovers} — злиття скасовано, база не змінена"
            )
    return {"moved": moved, "dropped_as_duplicate": dropped}


def dump_backup(db, refs, clusters) -> str:
    """Повний знімок ВСІХ задіяних рядків і посилань на них."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"size_notation_merge_{stamp}.json")

    payload = []
    for c in clusters:
        ids = [m.id for m in c["members"]]
        products = [
            {k: (str(v) if v is not None else None) for k, v in row.items()}
            for row in db.execute(
                text("SELECT * FROM products WHERE id = ANY(:ids)"), {"ids": ids}
            ).mappings().all()
        ]
        if len(products) != len(ids):
            raise RuntimeError(f"дамп {len(products)} товарів замість {len(ids)}")
        references = {}
        for tbl, col in refs:
            rows = [
                {k: (str(v) if v is not None else None) for k, v in row.items()}
                for row in db.execute(
                    text(f"SELECT * FROM {tbl} WHERE {col} = ANY(:ids)"), {"ids": ids}
                ).mappings().all()
            ]
            if rows:
                references[f"{tbl}.{col}"] = rows
        payload.append({
            "productnumber": c["pnum"], "colorid": c["colorid"],
            "canon_size": c["canon"], "products": products, "references": references,
        })

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def verify(db) -> int:
    """Чи не лишилось груп, де один розмір записаний двома способами."""
    return len(find_clusters(db))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Злити товари, що розійшлись лише написанням розміру")
    ap.add_argument("--apply", action="store_true", help="без цього — лише показати план")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        refs = collect_ref_columns(db)
        print(f"Місць, що посилаються на products.id: {len(refs)} "
              f"(з них без FK: {len(UNENFORCED_REFS)})\n")

        clusters = find_clusters(db)
        if not clusters:
            print("Двійників за написанням розміру не знайдено.")
            return 0

        plan, skipped = [], []
        for c in sorted(clusters, key=lambda x: x["pnum"]):
            problems = check_same_item(c["members"])
            if problems:
                skipped.append((c, problems))
                continue
            survivor, s_detail, doomed = pick_survivor(db, refs, c["members"])
            plan.append({"cluster": c, "survivor": survivor,
                         "survivor_refs": s_detail, "doomed": doomed})

        print(f"Кластерів до злиття: {len(plan)}"
              + (f", відкладено людині: {len(skipped)}" if skipped else "") + "\n")

        for item in plan:
            c, s = item["cluster"], item["survivor"]
            print(f"── {c['pnum']}  (колір {c['colorid']}, завіз «{s.deliveryname or '—'}»)")
            print(f"   ЛИШАЮ   id={s.id}  розмір {s.sizeeu!r} → {c['canon']!r}"
                  f"   створено {s.created_at:%d.%m %H:%M}")
            print(f"           посилання: {item['survivor_refs'] or '—'}")
            for d, d_detail in item["doomed"]:
                print(f"   ВИДАЛЯЮ id={d.id}  розмір {d.sizeeu!r}"
                      f"   створено {d.created_at:%d.%m %H:%M}")
                print(f"           переїжджає: {d_detail or '—'}")
            print()

        for c, problems in skipped:
            print(f"⚠️  {c['pnum']}: НЕ зливаю — рядки різняться по суті: "
                  f"{'; '.join(problems)}")
            for m in c["members"]:
                print(f"      id={m.id} розмір {m.sizeeu!r} бренд={m.brandid} "
                      f"тип={m.typeid} стан={m.conditionid} ціна={m.price}")
            print()

        if not plan:
            print("Автоматично зливати нічого.")
            return 0

        if not args.apply:
            print("Сухий прогін — база не змінена. Щоб застосувати, додай --apply")
            return 0

        backup = dump_backup(db, refs, [i["cluster"] for i in plan])
        print(f"Бекап: {backup}\n")

        for item in plan:
            c, s = item["cluster"], item["survivor"]
            for d, _ in item["doomed"]:
                res = repoint(db, refs, d.id, s.id, dry=False)
                print(f"{c['pnum']}: id={d.id} → id={s.id}  {res}")
                db.execute(text("DELETE FROM products WHERE id = :i"), {"i": d.id})
            # ТІЛЬКИ після видалення двійника: інакше UPDATE впаде на
            # uix_products_num_size_color.
            db.execute(
                text("UPDATE products SET sizeeu = :v, updated_at = now() WHERE id = :i"),
                {"v": c["canon"], "i": s.id},
            )
            print(f"{c['pnum']}: id={s.id} розмір → {c['canon']!r}")

        db.commit()
        print(f"\nЗлито кластерів: {len(plan)}")

        left = verify(db)
        print(f"Перевірка: груп «один розмір двома написаннями» лишилось {left}")
        return 0 if left == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

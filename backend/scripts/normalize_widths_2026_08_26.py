# -*- coding: utf-8 -*-
"""Разова нормалізація колонки «Ширина»: словесні форми → літерні.

Навіщо
──────
Ширина колодки замовляється літерою (G, W, D, H, F 1/2). У журналі частина
рядків заповнена словами («Стандартна», «Широка»), і вони осіли в базі окремими
значеннями поряд із літерними — у фільтрах це дві різні «ширини» для однієї.

Що робить
─────────
1. Знаходить товари, де `width` не збігається з канонічною формою.
2. Пише в БД канонічне значення ('Стандартна' → 'G', 'Широка' → 'W').
3. Ставить правку в `journal_writeback_queue` — щоб літера доїхала і в АРКУШ,
   інакше наступний парс показував би в журналі слово, а в картці літеру.
4. Значення, які взагалі не схожі на ширину, НЕ чіпає — лише перелічує; що з
   ними робити, вирішує людина.

Парсер із 2026-08-26 нормалізує «Ширина» на читанні, тож слово з аркуша більше
не повертається в базу навіть до того, як аркуш виправлять.

Запуск (з кореня репозиторію):
    ./venv/bin/python backend/scripts/normalize_widths_2026_08_26.py            # сухий прогін
    ./venv/bin/python backend/scripts/normalize_widths_2026_08_26.py --apply    # застосувати
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

from models.database import SessionLocal  # noqa: E402
from services.width_normalization import normalize_width  # noqa: E402
from services import journal_sync  # noqa: E402


BACKUP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "manual_cleanup_backups")
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Нормалізація колонки «Ширина»")
    ap.add_argument("--apply", action="store_true",
                    help="застосувати зміни (без прапорця — лише показати)")
    ap.add_argument("--no-journal", action="store_true",
                    help="не ставити правки в чергу запису в журнал")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT p.id, p.productnumber, p.width, d.deliveryname
            FROM products p
            LEFT JOIN deliveries d ON d.id = p.deliveryid
            WHERE p.width IS NOT NULL AND btrim(p.width) <> ''
            ORDER BY p.productnumber
        """)).fetchall()

        to_fix, junk = [], []
        for r in rows:
            norm = normalize_width(r.width)
            if norm is None:
                junk.append(r)
            elif norm != r.width:
                to_fix.append((r, norm))

        print(f"Товарів із заповненою шириною: {len(rows)}")
        print(f"Потребують нормалізації:       {len(to_fix)}")
        print(f"Не схожі на ширину (не чіпаю): {len(junk)}")

        if to_fix:
            print("\nЗміни:")
            for r, norm in to_fix:
                print(f"  {r.productnumber:>10}  {r.width!r} → {norm!r}"
                      f"   (завіз: {r.deliveryname or '—'})")
        if junk:
            print("\nЗалишаю як є — вирішувати людині:")
            for r in junk:
                print(f"  {r.productnumber:>10}  {r.width!r}")

        if not to_fix:
            print("\nНічого змінювати.")
            return 0

        if not args.apply:
            print("\nСухий прогін. Щоб застосувати — додай --apply")
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"widths_before_{stamp}.json")
        with open(backup_path, "w", encoding="utf-8") as fh:
            json.dump(
                [{"id": r.id, "productnumber": r.productnumber, "width": r.width,
                  "new_width": norm, "deliveryname": r.deliveryname}
                 for r, norm in to_fix],
                fh, ensure_ascii=False, indent=2,
            )
        print(f"\nБекап попередніх значень: {backup_path}")

        for r, norm in to_fix:
            db.execute(text("UPDATE products SET width = :w, updated_at = now() WHERE id = :i"),
                       {"w": norm, "i": r.id})
        db.commit()
        print(f"Оновлено рядків у БД: {len(to_fix)}")

        if args.no_journal:
            print("Чергу запису в журнал пропущено (--no-journal).")
            return 0

        # Той самий шлях, що й при правці з картки: черга journal_writeback_queue.
        for r, norm in to_fix:
            journal_sync.enqueue(db, r.id, r.productnumber, r.deliveryname, "width", norm)
        db.commit()
        print(f"Поставлено в чергу запису в журнал: {len(to_fix)}")

        # Розвантажуємо чергу тут же й синхронно — щоб було видно результат, а не
        # «десь у фоні». `_claim_one` бере задачі через FOR UPDATE SKIP LOCKED,
        # тож паралельний воркер запущеного застосунку не заважає.
        print("Пишу в журнал…")
        stats = journal_sync.drain()
        print(f"Результат запису в аркуш: {stats}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

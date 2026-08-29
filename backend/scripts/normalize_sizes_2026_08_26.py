# -*- coding: utf-8 -*-
"""Разова заміна дробів у «Розмір»/«СМ»/«Габарити» на десяткові.

38⅔ і 38.6 — той самий розмір, але як окремий рядок дробовий запис випадає з
числового сортування, ділить фільтр «Розмір» надвоє і не приймається формами
маркетплейсів. У журналі десятковий запис уже переважає (45.3), тож зводимо все
до нього: ⅓ → .3, ⅔ → .6, ½ → .5.

Діапазони («38-39», «24.5-25») і літерні розміри (S/M/XL) не чіпаються.

Парсер із 2026-08-26 робить те саме на читанні аркуша, тож дріб не повернеться
в базу навіть до того, як аркуш виправлять.

Запуск (з кореня репозиторію):
    ./venv/bin/python backend/scripts/normalize_sizes_2026_08_26.py            # сухий прогін
    ./venv/bin/python backend/scripts/normalize_sizes_2026_08_26.py --apply    # застосувати
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
from services.size_normalization import decimalize_fractions  # noqa: E402
from services import journal_sync  # noqa: E402


FIELDS = ("sizeeu", "measurementscm", "dimensions")
BACKUP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "manual_cleanup_backups")
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Дроби в розмірах → десяткові")
    ap.add_argument("--apply", action="store_true", help="застосувати зміни")
    ap.add_argument("--no-journal", action="store_true", help="не писати в журнал")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        cols = ", ".join(f"p.{f}" for f in FIELDS)
        rows = db.execute(text(f"""
            SELECT p.id, p.productnumber, {cols}, d.deliveryname
            FROM products p
            LEFT JOIN deliveries d ON d.id = p.deliveryid
            ORDER BY p.productnumber
        """)).fetchall()

        changes, blocked = [], []     # (row, field, old, new)
        for r in rows:
            for f in FIELDS:
                old = getattr(r, f)
                if not isinstance(old, str) or not old.strip():
                    continue
                new = decimalize_fractions(old)
                if new == old:
                    continue
                # sizeeu входить в унікальний ключ (номер, розмір, колір): якщо
                # десятковий двійник УЖЕ існує, перейменування впаде на
                # constraint. Це не помилка скрипта, а наявний дублікат — його
                # має розсудити людина, а не UPDATE наосліп.
                twin = None
                if f == "sizeeu":
                    twin = db.execute(text("""
                        SELECT id FROM products
                        WHERE productnumber = :pn AND COALESCE(sizeeu,'') = :sz
                          AND COALESCE(colorid, 0) = (SELECT COALESCE(colorid, 0)
                                                      FROM products WHERE id = :i)
                          AND id <> :i
                    """), {"pn": r.productnumber, "sz": new, "i": r.id}).scalar()
                if twin:
                    blocked.append((r, f, old, new, twin))
                else:
                    changes.append((r, f, old, new))

        print(f"Товарів переглянуто: {len(rows)}")
        print(f"Значень із дробами:  {len(changes) + len(blocked)}")
        for r, f, old, new in changes:
            print(f"   {r.productnumber:>10}  {f}: {old!r} → {new!r}"
                  f"   (завіз: {r.deliveryname or '—'})")
        if blocked:
            print(f"\nЗаблоковано наявним дублікатом (не чіпаю): {len(blocked)}")
            for r, f, old, new, twin in blocked:
                print(f"   {r.productnumber:>10}  id={r.id} {old!r} → {new!r}: "
                      f"рядок із розміром {new!r} уже існує (id={twin})")
            print("   Це дві версії ОДНОГО розміру. Зливати їх — окреме рішення:")
            print("   який рядок лишити (у «дробовому» зазвичай усі ручні правки),")
            print("   куди перенести замовлення й що видалити.")

        if not changes:
            print("\nНічого змінювати.")
            return 0
        if not args.apply:
            print("\nСухий прогін. Щоб застосувати — додай --apply")
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"sizes_before_{stamp}.json")
        with open(backup_path, "w", encoding="utf-8") as fh:
            json.dump([{"id": r.id, "productnumber": r.productnumber, "field": f,
                        "old": old, "new": new, "deliveryname": r.deliveryname}
                       for r, f, old, new in changes], fh, ensure_ascii=False, indent=2)
        print(f"\nБекап попередніх значень: {backup_path}")

        for r, f, _old, new in changes:
            db.execute(text(f"UPDATE products SET {f} = :v, updated_at = now() WHERE id = :i"),
                       {"v": new, "i": r.id})
        db.commit()
        print(f"Оновлено значень у БД: {len(changes)}")

        if args.no_journal:
            print("Чергу запису в журнал пропущено (--no-journal).")
            return 0

        for r, f, _old, new in changes:
            journal_sync.enqueue(db, r.id, r.productnumber, r.deliveryname, f, new)
        db.commit()
        print(f"Поставлено в чергу запису в журнал: {len(changes)}")
        print("Пишу в журнал…")
        print(f"Результат запису в аркуш: {journal_sync.drain()}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

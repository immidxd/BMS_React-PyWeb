#!/usr/bin/env python3
"""Злиття «фантомних» дублікатів товару в справжній запис.

Фантом — порожній двійник уже наявного товару, створений воркспейс-парсером:
той самий productnumber, але без розміру/статі й з price=0. Причина усунена в
sheets_parser (idempotency за ЕФЕКТИВНИМ номером, див. `lookup_pnum`); цей скрипт
прибирає ті, що вже встигли з'явитись.

Що робить для кожної пари (фантом → справжній):
  • переносить order_items на справжній товар (замовлення не втрачають позицію);
  • переносить номер фантома в clonednumbers справжнього (історія номера);
  • прибирає merge_candidates, де фантом фігурує з будь-якого боку;
  • видаляє порожні звʼязки фантома (product_materials/colors/types/subtypes);
  • видаляє сам фантом.

Перед видаленням пише JSON-бекап усіх рядків, яких торкається.

Запуск:
    python3 scripts/merge_phantom_duplicates.py              # лише план (dry-run)
    python3 scripts/merge_phantom_duplicates.py --apply      # застосувати
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from models.database import SessionLocal
except ImportError:
    from backend.models.database import SessionLocal
from sqlalchemy import text

# Фантом: та сама (нормалізована) назва номера, порожні ключові поля, нульова ціна,
# і при цьому існує «живий» двійник із ціною > 0.
FIND_PHANTOMS = text("""
WITH n AS (SELECT *, regexp_replace(productnumber, '^#', '') AS pn FROM products)
SELECT p.id AS phantom_id, p.pn,
       (SELECT s.id FROM n s
         WHERE s.pn = p.pn AND s.id <> p.id AND s.price > 0
         ORDER BY (s.sizeeu IS NOT NULL) DESC, s.updated_at DESC NULLS LAST, s.id
         LIMIT 1) AS real_id
FROM n p
WHERE (p.price IS NULL OR p.price = 0)
  AND p.sizeeu IS NULL
  AND p.genderid IS NULL
  AND EXISTS (SELECT 1 FROM n s WHERE s.pn = p.pn AND s.id <> p.id AND s.price > 0)
ORDER BY p.pn
""")

CHILD_CLEANUP = [
    "product_materials", "product_colors", "product_types", "product_subtypes",
    "unmapped_materials",
]

# Колонки, за якими вирішуємо «це той самий товар чи інший». Якщо в обох записах
# значення заповнене й РІЗНЕ — це не порожній двійник, а окремий товар, що просто
# ділить номер (напр. #А1198: у одного «Сумка», у іншого «Кросівки»). Такий не
# видаляємо НІКОЛИ — рішення за людиною.
IDENTITY_COLS = [
    "typeid", "subtypeid", "brandid", "colorid", "genderid",
    "model", "marking", "gtin", "sizeeu", "size_letter",
]


def conflicts(phantom: dict, real: dict) -> list[str]:
    """Колонки, де обидва записи заповнені й не збігаються."""
    out = []
    for col in IDENTITY_COLS:
        a, b = phantom.get(col), real.get(col)
        if a in (None, "", 0) or b in (None, "", 0):
            continue
        if str(a).strip().lower() != str(b).strip().lower():
            out.append(f"{col}: {a} ≠ {b}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="застосувати зміни (без цього — лише план)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        pairs = db.execute(FIND_PHANTOMS).mappings().all()
        if not pairs:
            print("Фантомних дублікатів не знайдено.")
            return 0

        backup: list[dict] = []
        refused: list[str] = []
        print(f"Кандидатів: {len(pairs)}\n")
        for row in pairs:
            ph, real, pn = row["phantom_id"], row["real_id"], row["pn"]

            ph_row = dict(db.execute(text("SELECT * FROM products WHERE id = :i"), {"i": ph}).mappings().first())
            real_row = dict(db.execute(text("SELECT * FROM products WHERE id = :i"), {"i": real}).mappings().first())
            bad = conflicts(ph_row, real_row)
            if bad:
                refused.append(f"#{pn}: id={ph} vs {real} — {', '.join(bad)}")
                print(f"  #{pn}: ПРОПУСК (окремий товар, не двійник) — {', '.join(bad)}")
                continue

            items = db.execute(
                text("SELECT id, order_id FROM order_items WHERE product_id = :ph"), {"ph": ph}
            ).mappings().all()
            cands = db.execute(
                text("""SELECT id FROM merge_candidates
                        WHERE new_product_id = :ph OR suggested_id = :ph"""), {"ph": ph}
            ).scalars().all()
            print(f"  #{pn}: фантом id={ph} → справжній id={real}"
                  f" | order_items: {len(items)} | merge_candidates: {len(cands)}")

            full = db.execute(text("SELECT * FROM products WHERE id = :ph"), {"ph": ph}).mappings().first()
            backup.append({
                "phantom": {k: str(v) for k, v in dict(full).items()},
                "real_id": real,
                "order_items": [dict(i) for i in items],
                "merge_candidate_ids": list(cands),
            })

            if not args.apply:
                continue

            # 1) замовлення переїжджають на справжній товар
            db.execute(text("UPDATE order_items SET product_id = :real WHERE product_id = :ph"),
                       {"real": real, "ph": ph})
            # 2) номер фантома лишається в історії справжнього
            cur = db.execute(text("SELECT clonednumbers FROM products WHERE id = :real"),
                             {"real": real}).scalar() or ""
            toks = {t.strip().lstrip("#") for t in cur.split(";") if t.strip()}
            if pn not in toks:
                toks.add(pn)
                db.execute(text("UPDATE products SET clonednumbers = :c WHERE id = :real"),
                           {"c": ";".join(sorted(toks)) + ";", "real": real})
            # 2b) заповнюємо ПОРОЖНІ поля справжнього тим, що є у фантома (нічого
            #     не перезаписуємо — лише закриваємо прогалини; конфліктів тут вже
            #     не буває, їх відсіяв conflicts() вище).
            gaps = {c: ph_row[c] for c in IDENTITY_COLS
                    if ph_row.get(c) not in (None, "", 0) and real_row.get(c) in (None, "", 0)}
            for col, val in gaps.items():
                db.execute(text(f"UPDATE products SET {col} = :v WHERE id = :real"),
                           {"v": val, "real": real})
            if gaps:
                print(f"      + перенесено у {real}: {gaps}")
            # 3) пропозиції злиття для фантома більше не потрібні
            db.execute(text("DELETE FROM merge_candidates WHERE new_product_id = :ph OR suggested_id = :ph"),
                       {"ph": ph})
            # 4) дочірні звʼязки
            for tbl in CHILD_CLEANUP:
                db.execute(text(f"DELETE FROM {tbl} WHERE product_id = :ph"), {"ph": ph})
            # 5) сам фантом
            db.execute(text("DELETE FROM products WHERE id = :ph"), {"ph": ph})

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"phantom_backup_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nЗлито: {len(backup)} | пропущено як окремі товари: {len(refused)}")
        for r in refused:
            print(f"  ⚠ {r}")
        print(f"Бекап рядків: {path}")

        if args.apply:
            db.commit()
            print("Застосовано.")
        else:
            print("\nDRY-RUN — нічого не змінено. Для застосування: --apply")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

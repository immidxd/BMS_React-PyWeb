# -*- coding: utf-8 -*-
"""Прибрати зайвий «Всесезон» із сезону ботинок, який додала ШІ-пропозиція.

Запит власника 04.10.2026: «Виправ оці всі зайві "Всесезон" в останніх
записах, які ШІ автозаповнив так». «Всесезон» — заглушка парсера для рядка без
«Виду» й «Сезону», ШІ лише додало до неї справжній сезон (#Ф4489).

Беремо лише товари, де:
  * ПРИЙНЯТО ШІ-пропозицію сезону (source photo*), і в ній «Всесезон» разом з
    іншим сезоном;
  * вид/підвид — із тих, кому «Всесезон» не буває (photo_autofill.allseason_forbidden);
  * сезон у картці ДОСІ дорівнює прийнятому (інакше його вже правили — не чіпаємо).

Запис — update_product + enqueue_writeback_for (як ручна правка: журнал
«Сезон» теж виправиться). Бекап — JSON у backend/scripts/manual_fix_backups/.

    ./venv/bin/python backend/scripts/fix_allseason_on_boots.py          # лише показати
    ./venv/bin/python backend/scripts/fix_allseason_on_boots.py --apply  # записати
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(HERE, "..", "..", ".env"))

from sqlalchemy import text  # noqa: E402

from models.database import SessionLocal  # noqa: E402
from schemas.product import ProductUpdate  # noqa: E402
from services import photo_autofill as pa, product_service  # noqa: E402

BACKUP_DIR = os.path.join(HERE, "manual_fix_backups")


def candidates(db):
    rows = db.execute(text("""
        SELECT DISTINCT ON (p.id) p.id, p.productnumber, t.typename, st.subtypename,
               p.season, fp.value
        FROM product_field_proposals fp
        JOIN products p ON p.id = fp.product_id
        LEFT JOIN types t ON t.id = p.typeid
        LEFT JOIN subtypes st ON st.id = p.subtypeid
        WHERE fp.field = 'season' AND fp.status = 'accepted' AND fp.source LIKE 'photo%'
          AND fp.value LIKE '%Всесезон%' AND fp.value <> 'Всесезон'
        ORDER BY p.id, fp.decided_at DESC
    """)).fetchall()
    out = []
    for pid, pn, typ, sub, season, accepted in rows:
        if not pa.allseason_forbidden(typ, sub):
            continue
        if (season or "") != (accepted or ""):
            continue                      # уже правили після прийняття — не чіпаємо
        parts = [s.strip() for s in season.split(",") if s.strip() and s.strip() != "Всесезон"]
        if not parts:
            continue
        out.append({"id": pid, "number": pn, "type": typ, "subtype": sub,
                    "old": season, "new": ", ".join(parts)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        items = candidates(db)
        for it in items:
            print(f"  {it['number']:8} «{it['old']}» → «{it['new']}»")
        print(f"Усього: {len(items)}")
        if not args.apply or not items:
            return 0
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_allseason_boots.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"Бекап: {path}")
        done = 0
        for it in items:
            # Умова «рядок досі в очікуваному стані» — перечитуємо перед записом.
            cur = db.execute(text("SELECT season FROM products WHERE id = :i"), {"i": it["id"]}).scalar()
            db.commit()
            if (cur or "") != it["old"]:
                print(f"  ПРОПУЩЕНО {it['number']}: сезон уже «{cur}»")
                continue
            updated = product_service.update_product(db, it["id"], ProductUpdate(season=it["new"]))
            if updated is None:
                print(f"  ПОМИЛКА {it['number']}")
                continue
            product_service.enqueue_writeback_for(db, updated)
            done += 1
        print(f"Виправлено: {done} з {len(items)} (журнал оновиться через чергу синхронізації)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

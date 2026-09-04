#!/usr/bin/env python3
"""Злиття перевірених варіантів написання у взуттєвих довідниках.

Джерело правди — services/shoe_attribute_normalization.CANONICAL_GROUPS, де
записані РІШЕННЯ ВЛАСНИКА словника: «підбора» → «каблук», «магніт» → «магнітна
кнопка», одруківки «круголий»/«шунурівка»/«споривна» → канон. Тут немає ані
евристик, ані fuzzy — лише те, що вже переглянуто людиною.

ЧОМУ ЦЕ БЕЗПЕЧНІШЕ ЗА ПЕРЕНОС ПРОТЕКТОРА
────────────────────────────────────────
Там поле ставало NULL, і спрацьовувала гілка «дозаповнити порожнє», яка
manually_edited_fields НЕ перевіряє (див. пам'ять null-enrich-ignores-lock):
парсер повертав старе значення з аркуша попри лок. Тут поле весь час
НЕПОРОЖНЄ — з «підбора» одразу стає «каблук» — тож працює звичайний skip-guard
за локом, і гонки з парсером немає.

Все одно порядок «БД + черга одночасно» лишається правильним саме тому, що лок
тримає до приїзду write-back. Але скрипт ідемпотентний: якщо щось усе-таки
відкотиться, повторний запуск доб'є решту й покаже це в звіті.

РЯДКИ ДОВІДНИКА НЕ ВИДАЛЯЮТЬСЯ. Після злиття варіант лишається з нулем товарів;
прибирати його — окреме рішення (частина значень є легітимною термінологією,
яку просто ніхто не вживав). Див. DEAD_VALUES у нормалізаторі.

Usage:
    ./venv/bin/python backend/scripts/merge_shoe_attribute_variants.py            # dry-run
    ./venv/bin/python backend/scripts/merge_shoe_attribute_variants.py --execute
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(REPO / ".env")

try:
    from services.shoe_attribute_normalization import CANONICAL_GROUPS
except ImportError:  # pragma: no cover
    from backend.services.shoe_attribute_normalization import CANONICAL_GROUPS

BACKUP_DIR = pathlib.Path(__file__).resolve().parent
BACKUP_PREFIX = "shoe_variant_merge_backup_"

# атрибут → (таблиця довідника, колонка назви, FK у products)
TABLES = {
    "sole_type":      ("sole_types",      "soletypename",      "soletypeid"),
    "toe_shape":      ("toe_shapes",      "toeshapename",      "toeshapeid"),
    "fastening_type": ("fastening_types", "fasteningtypename", "fasteningtypeid"),
    "lining":         ("linings",         "liningname",        "liningid"),
    "heel_type":      ("heel_types",      "heeltypename",      "heeltypeid"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="справді злити")
    args = ap.parse_args()

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    plan: list[dict] = []
    problems: list[str] = []

    for attribute, groups in CANONICAL_GROUPS.items():
        if not groups:
            continue
        table, name_col, fk = TABLES[attribute]
        cur.execute(f"SELECT id, {name_col} AS name FROM {table}")
        ids = {r["name"].strip(): r["id"] for r in cur.fetchall()}

        for canonical, variants in groups.items():
            canon_id = ids.get(canonical)
            if canon_id is None:
                problems.append(f"{attribute}: канону «{canonical}» немає в {table}")
                continue
            for variant in variants:
                vid = ids.get(variant)
                if vid is None or vid == canon_id:
                    continue
                cur.execute(
                    f"""SELECT p.id, p.productnumber, d.deliveryname
                        FROM products p LEFT JOIN deliveries d ON d.id = p.deliveryid
                        WHERE p.{fk} = %s""",
                    (vid,),
                )
                rows = [dict(r) for r in cur.fetchall()]
                if not rows:
                    continue
                plan.append({
                    "attribute": attribute, "fk": fk,
                    "variant": variant, "variant_id": vid,
                    "canonical": canonical, "canonical_id": canon_id,
                    "products": rows,
                })

    for p in problems:
        print(f"  ⚠️ {p}")
    if not plan:
        print("Нічого зливати — усі варіанти вже зведені до канону.")
        return 0

    total = sum(len(g["products"]) for g in plan)
    print(f"груп до злиття: {len(plan)};  товарів: {total}")
    for g in plan:
        no_sheet = sum(1 for r in g["products"] if not r["deliveryname"])
        extra = f"  (без завозу: {no_sheet})" if no_sheet else ""
        print(f"   {g['attribute']:15} «{g['variant']}» → «{g['canonical']}»  "
              f"{len(g['products']):4} товарів{extra}")

    if not args.execute:
        print("\nDRY-RUN. Приклади:")
        for g in plan[:3]:
            for r in g["products"][:3]:
                print(f"   {r['productnumber']:10} {g['variant']:12} → {g['canonical']:16} "
                      f"вкладка={r['deliveryname'] or '—'}")
        print("\nДля застосування додайте --execute")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}.json"
    backup.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    print(f"\nбекап → {backup}")

    # ⚠️ services.X і backend.services.X — різні обʼєкти; підтримуємо обидва шляхи.
    try:
        from services import journal_sync
        from models.database import SessionLocal
    except ModuleNotFoundError:
        from backend.services import journal_sync
        from backend.models.database import SessionLocal

    db = SessionLocal()
    moved = 0
    try:
        for g in plan:
            fk = g["fk"]
            for r in g["products"]:
                cur.execute(
                    f"UPDATE products SET {fk} = %s, manually_edited_at = now(), "
                    f"    manually_edited_fields = ("
                    f"        SELECT string_agg(DISTINCT f, ',' ORDER BY f) FROM ("
                    f"            SELECT unnest(string_to_array("
                    f"                coalesce(nullif(btrim(manually_edited_fields), ''), %s),"
                    f"                ',')) AS f"
                    f"            UNION SELECT %s"
                    f"        ) s WHERE btrim(f) <> ''"
                    f"    ) "
                    f"WHERE id = %s",
                    (g["canonical_id"], fk, fk, r["id"]),
                )
                journal_sync.enqueue(db, r["id"], r["productnumber"],
                                     r["deliveryname"], fk, g["canonical"])
                moved += 1
        conn.commit()
        db.commit()
    except Exception:
        conn.rollback()
        db.rollback()
        raise
    finally:
        db.close()

    print(f"зведено {moved} товарів; у чергу поставлено {moved} задач")
    print("Рядки-варіанти лишились у довідниках із нулем товарів — це навмисно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

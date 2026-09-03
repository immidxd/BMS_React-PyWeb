#!/usr/bin/env python3
"""Видалення значень-сміття з довідників — з бекапом і перевіркою посилань.

«Сміття» тут має вузьке визначення: значення, у якому немає ЖОДНОЇ літери й
жодної цифри (сама пунктуація), і на яке не посилається жоден товар. Приклад,
заради якого скрипт написаний: рядок «ʼ» — один апостроф — у heel_types.

Свідомо НЕ чіпає:
  • значення з товарами, навіть якщо виглядають сміттям. Зокрема '???' у types
    (67 товарів) — це службовий номер, у якого своя історія й свої правила;
  • «мертві» значення без товарів, але з осмисленою назвою («goodyear welt»,
    «wingtip»). Це справжня взуттєва термінологія — видаляти шкода, машинному
    джерелу вони просто не пропонуються (див. shoe_attribute_normalization).

Перед видаленням перевіряє по information_schema, що на таблицю не посилається
нічого, крім очікуваного FK у products: у цій базі є місця, де id зберігається
без зовнішнього ключа, тож «нема FK» не означає «нема посилань».

Usage:
    ./venv/bin/python backend/scripts/delete_lookup_garbage.py            # dry-run
    ./venv/bin/python backend/scripts/delete_lookup_garbage.py --execute
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg2
from dotenv import load_dotenv

load_dotenv(REPO / ".env")

BACKUP_DIR = pathlib.Path(__file__).resolve().parent
BACKUP_PREFIX = "lookup_garbage_backup_"

# довідник → (колонка назви, FK-колонка в products)
LOOKUPS = [
    ("sole_types",      "soletypename",      "soletypeid"),
    ("toe_shapes",      "toeshapename",      "toeshapeid"),
    ("fastening_types", "fasteningtypename", "fasteningtypeid"),
    ("linings",         "liningname",        "liningid"),
    ("heel_types",      "heeltypename",      "heeltypeid"),
    ("lace_types",      "lacetypename",      "lacetypeid"),
    ("packaging_types", "packagingname",     "packagingid"),
    ("technologies",    "technologyname",    "technologyid"),
    ("styles",          "stylename",         "styleid"),
]

# Ані літери, ані цифри — сама пунктуація або порожнеча.
_NO_ALNUM = re.compile(r"^[^0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ]*$")


def _referencing_tables(cur, table: str) -> list[str]:
    """Усі таблиці з зовнішнім ключем на `table` (окрім самої себе)."""
    cur.execute(
        """
        SELECT DISTINCT tc.table_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = %s
        """,
        (table,),
    )
    return sorted({r[0] for r in cur.fetchall() if r[0] != table})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="справді видалити (без прапорця — лише показати)")
    args = ap.parse_args()

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )
    cur = conn.cursor()
    victims: list[dict] = []
    blocked: list[str] = []

    for table, name_col, fk in LOOKUPS:
        cur.execute(
            f"SELECT l.id, l.{name_col}, count(p.id) FROM {table} l "
            f"LEFT JOIN products p ON p.{fk} = l.id GROUP BY l.id, l.{name_col}"
        )
        for row_id, name, used in cur.fetchall():
            if not _NO_ALNUM.match(name or ""):
                continue
            if used:
                blocked.append(f"{table}.{name!r} — {used} товарів, не чіпаємо")
                continue
            others = [t for t in _referencing_tables(cur, table) if t != "products"]
            if others:
                blocked.append(f"{table}.{name!r} — на таблицю посилаються ще {others}")
                continue
            victims.append({"table": table, "name_col": name_col,
                            "id": row_id, "name": name})

    for note in blocked:
        print(f"  ПРОПУЩЕНО: {note}")

    if not victims:
        print("Сміття не знайдено — нічого робити.")
        return 0

    print(f"\nдо видалення ({len(victims)}):")
    for v in victims:
        print(f"  {v['table']}.id={v['id']}  {v['name']!r}")

    if not args.execute:
        print("\nDRY-RUN. Для справжнього видалення додайте --execute")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}.json"
    backup.write_text(json.dumps(victims, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nбекап → {backup}")

    for v in victims:
        cur.execute(f"DELETE FROM {v['table']} WHERE id = %s", (v["id"],))
        print(f"  видалено {v['table']}.id={v['id']} {v['name']!r}")
    conn.commit()
    print("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Звірка мапи канонізації взуттєвих атрибутів із живими довідниками.

Відповідає на питання, яке unit-тест поставити не може (він не має БД):
**чи існує реально кожне канонічне значення в своєму довіднику.**

Це не формальність. Канон, якого немає в таблиці, — гірший за відсутність
канону: у режимі закритого словника значення буде відкинуте, і поле мовчки
лишиться порожнім. Саме так виявилось, що «поліуретан» у `linings` не існує,
хоча виглядав очевидним каноном для «pU».

Нічого не змінює — лише читає й доповідає. Запускати після кожної правки
shoe_attribute_normalization.

Usage:
    ./venv/bin/python backend/scripts/verify_shoe_canon.py
"""
from __future__ import annotations

import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg2
from dotenv import load_dotenv

load_dotenv(REPO / ".env")

try:
    from services.shoe_attribute_normalization import CANONICAL_GROUPS, DEAD_VALUES
except ImportError:  # pragma: no cover — залежить від того, звідки запускають
    from backend.services.shoe_attribute_normalization import CANONICAL_GROUPS, DEAD_VALUES

# атрибут → (таблиця довідника, колонка назви, FK у products)
TABLES = {
    "sole_type":      ("sole_types",      "soletypename",      "soletypeid"),
    "toe_shape":      ("toe_shapes",      "toeshapename",      "toeshapeid"),
    "fastening_type": ("fastening_types", "fasteningtypename", "fasteningtypeid"),
    "lining":         ("linings",         "liningname",        "liningid"),
    "heel_type":      ("heel_types",      "heeltypename",      "heeltypeid"),
}


def main() -> int:
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )
    cur = conn.cursor()
    problems: list[str] = []

    for attribute, groups in CANONICAL_GROUPS.items():
        table, name_col, fk = TABLES[attribute]
        # Кількість товарів на кожному значенні — щоб бачити ціну майбутнього злиття.
        cur.execute(
            f"SELECT l.{name_col}, count(p.id) FROM {table} l "
            f"LEFT JOIN products p ON p.{fk} = l.id GROUP BY l.{name_col}"
        )
        live = {(n or "").strip(): k for n, k in cur.fetchall()}

        print(f"\n══ {attribute}  ({len(live)} значень у довіднику)")
        if not groups:
            print("   канонізації не задано")
        for canonical, variants in groups.items():
            if canonical in live:
                print(f"   ✓ канон «{canonical}» — {live[canonical]} товарів")
            else:
                problems.append(f"{attribute}: канону «{canonical}» немає в {table}")
                print(f"   ✗ канон «{canonical}» — У ДОВІДНИКУ НЕМАЄ")
            for variant in variants:
                if variant in live:
                    print(f"       ← «{variant}» ({live[variant]} товарів на злиття)")
                else:
                    print(f"       ← «{variant}» (у довіднику вже немає — нешкідливо)")

        # Мертве значення, що раптом обросло товарами, більше не мертве.
        for dead in DEAD_VALUES.get(attribute, ()):
            if live.get(dead):
                problems.append(
                    f"{attribute}: «{dead}» позначене мертвим, але має {live[dead]} товарів"
                )
                print(f"   ! «{dead}» більше не мертве: {live[dead]} товарів")

    print("\n" + "─" * 60)
    if problems:
        print(f"ПРОБЛЕМ: {len(problems)}")
        for p in problems:
            print(f"  • {p}")
        return 1
    print("Мапа канонізації узгоджена з довідниками.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

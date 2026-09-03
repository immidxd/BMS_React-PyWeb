#!/usr/bin/env python3
"""Розкладання складених технологій по таблиці product_technologies.

«Vibram, MEGAGRIP, Gore-tex» у products.technologyid → три рядки звʼязку.

ЦЕЙ СКРИПТ WRITEBACK-НЕЙТРАЛЬНИЙ І ЦЕ НАВМИСНО
──────────────────────────────────────────────
Атоми зберігаються в ТОМУ САМОМУ написанні, у якому стоять зараз: «vibram»
лишається «vibram», а не стає «Vibram». Тому рядок, зібраний назад через
', '.join(), збігається з тим, що вже лежить у колонці «Технології» Журналу —
аркуш чіпати не треба, черга write-back не задіяна, перезапуск застосунку не
потрібен.

Із тієї ж причини розбір іде ТІЛЬКИ за комою: будь-який інший роздільник змінив
би зібраний рядок і потягнув за собою запис в аркуш.

Канонізація написань (vibram/Vibram, gore-tex/Gore-Tex, OrthoLite/Ortholite —
5 атомів у кількох формах) — ОКРЕМИЙ крок: вона змінює те, що поїде в аркуш,
тож потребує і рішення власника про канон, і робочої черги write-back.

Скалярний products.technologyid НЕ чіпається: поки код читає його, він мусить
лишатись правильним. Скрипт лише ДОДАЄ звʼязки, тож повторний запуск нічого
не ламає й нічого не дублює.

Usage:
    ./venv/bin/python backend/scripts/backfill_product_technologies.py            # dry-run
    ./venv/bin/python backend/scripts/backfill_product_technologies.py --execute
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(REPO / ".env")

BACKUP_DIR = pathlib.Path(__file__).resolve().parent
BACKUP_PREFIX = "product_technologies_backfill_"

# ТІЛЬКИ кома. Крапку з пробілом як роздільник свідомо НЕ вводимо: на всю базу
# є рівно одне таке значення — «gore-tex. Meta-Rocker» (4 товари), тобто
# загальне правило існувало б заради однієї одруківки, а ламало б назви на
# кшталт «U.S. Grip». Це та сама евристика, від якої проєкт відмовляється в
# brand_normalization і product_taxonomy_normalization.
# Одруківка лікується в кроці канонізації написань — він і так змінює аркуш.
_SPLIT = re.compile(r",")


def split_technologies(name: str) -> list[str]:
    """Складене значення → атоми, у порядку запису, без дублів."""
    out: list[str] = []
    for part in _SPLIT.split(name or ""):
        atom = part.strip().strip(".").strip()
        if atom and atom not in out:
            out.append(atom)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="справді записати")
    args = ap.parse_args()

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT id, technologyname FROM technologies")
    by_name = {r["technologyname"]: r["id"] for r in cur.fetchall()}

    cur.execute(
        """
        SELECT p.id, p.productnumber, te.technologyname
        FROM products p
        JOIN technologies te ON te.id = p.technologyid
        ORDER BY p.productnumber
        """
    )
    products = [dict(r) for r in cur.fetchall()]
    if not products:
        print("Товарів із технологіями немає.")
        return 0

    cur.execute("SELECT count(*) FROM product_technologies")
    already = cur.fetchone()[0]

    atoms_needed: list[str] = []
    links = 0
    per_count = collections.Counter()
    for p in products:
        atoms = split_technologies(p["technologyname"])
        per_count[len(atoms)] += 1
        links += len(atoms)
        for a in atoms:
            if a not in by_name and a not in atoms_needed:
                atoms_needed.append(a)

    print(f"товарів із технологіями: {len(products)}")
    print(f"звʼязків до створення  : {links}")
    print(f"нових атомів у довідник: {len(atoms_needed)}")
    print(f"уже є рядків у таблиці : {already}")
    print("\nскільки технологій на товар:")
    for n, k in sorted(per_count.items()):
        print(f"   {n} шт → {k:4} товарів")

    if not args.execute:
        print("\nприклади розкладання:")
        for p in products[:6]:
            print(f"   {p['productnumber']:10} {p['technologyname'][:52]!r}")
            print(f"              → {split_technologies(p['technologyname'])}")
        if atoms_needed:
            print(f"\nнові атоми (перші 15): {atoms_needed[:15]}")
        print("\nDRY-RUN. Для запису додайте --execute")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}.json"
    backup.write_text(json.dumps(products, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    print(f"\nбекап → {backup}")

    created_atoms = 0
    for a in atoms_needed:
        cur.execute(
            "INSERT INTO technologies (technologyname) VALUES (%s) "
            "ON CONFLICT (technologyname) DO UPDATE SET technologyname = EXCLUDED.technologyname "
            "RETURNING id",
            (a,),
        )
        by_name[a] = cur.fetchone()[0]
        created_atoms += 1

    inserted = 0
    for p in products:
        for ord_idx, a in enumerate(split_technologies(p["technologyname"])):
            tid = by_name.get(a)
            if tid is None:
                continue
            cur.execute(
                "INSERT INTO product_technologies (product_id, technology_id, ord) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (product_id, technology_id) DO UPDATE SET ord = EXCLUDED.ord",
                (p["id"], tid, ord_idx),
            )
            inserted += 1
    conn.commit()

    print(f"додано атомів у довідник: {created_atoms}")
    print(f"створено звʼязків       : {inserted}")

    # Контроль: зібраний назад рядок мусить збігатися з тим, що в аркуші.
    cur.execute(
        """
        SELECT p.productnumber, te.technologyname AS original,
               (SELECT string_agg(t2.technologyname, ', ' ORDER BY pt.ord)
                  FROM product_technologies pt
                  JOIN technologies t2 ON t2.id = pt.technology_id
                 WHERE pt.product_id = p.id) AS rebuilt
        FROM products p JOIN technologies te ON te.id = p.technologyid
        """
    )
    # ⚠️ Порівнювати треба з тим самим розбором, а не з підміною роздільника:
    # _SPLIT.sub(", ", "Vibram, MEGAGRIP") дає подвійний пробіл і фальшиву
    # розбіжність на кожному нормальному рядку.
    drift = [(r["productnumber"], r["original"], r["rebuilt"])
             for r in cur.fetchall()
             if (r["rebuilt"] or "") != ", ".join(split_technologies(r["original"]))]
    print(f"\nрозбіжностей «зібрано назад ≠ оригінал»: {len(drift)}")
    for pn, o, rb in drift[:5]:
        print(f"   {pn}: {o!r} → {rb!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

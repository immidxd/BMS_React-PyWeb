#!/usr/bin/env python3
"""Канонізація написань технологій у product_technologies.

Джерело правди — services/shoe_attribute_normalization:
  • TECHNOLOGY        — канон → перевірені варіанти написання
  • TECHNOLOGY_SPLITS — атоми, що насправді містять кілька технологій

ЧОМУ ЦЕ СТАЛО МОЖЛИВИМ ЛИШЕ ЗАРАЗ
─────────────────────────────────
Доки технології були одним FK, зміна написання одного атома всередині складеного
значення («vibram, Cordura» → «Vibram, Cordura») створювала НОВИЙ рядок
довідника — тобто рівно те зростання, яке ми й прибирали. Після переходу на
many-to-many атоми окремі, і перейменування одного нікого не плодить.

ЧОМУ ТУТ КАНОН СТВОРЮЄТЬСЯ, А В merge_shoe_attribute_variants — НІ
──────────────────────────────────────────────────────────────────
Там канон мусив уже існувати: якби його не було, значення вказувало б у
порожнечу (саме так відпав «поліуретан» для «pU»). Тут інакше — канон це те
саме значення без ™/®, тобто нормалізація наявного, а не нове поняття.
verify_shoe_canon чесно повідомляє, що «Relaxed Fit» і «Boost» у довіднику
відсутні; скрипт створює їх сам і це записано в звіті.

ПЕРЕБУДОВА, А НЕ ТОЧКОВЕ ПЕРЕПРИЗНАЧЕННЯ
────────────────────────────────────────
Список технологій кожного зачепленого товару збирається наново: атоми в порядку
ord → канонізація → розкладання складених → дедуплікація зі збереженням порядку
→ перезапис. Так один прохід однаково коректно обробляє і перейменування, і
розбиття, і випадок «у товару вже є і варіант, і канон» (первинний ключ на
(product_id, technology_id) інакше впав би).

Порядок «БД + черга разом» тут безпечний: комірка «Технології» ніколи не стає
порожньою, тож гілка «дозаповнити NULL» не спрацьовує (див. пам'ять
null-enrich-ignores-lock), а звичайний лок тримає до приїзду write-back.

Usage:
    ./venv/bin/python backend/scripts/merge_technology_variants.py            # dry-run
    ./venv/bin/python backend/scripts/merge_technology_variants.py --execute
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
    from services.shoe_attribute_normalization import (
        TECHNOLOGY_SPLITS, canonicalize_shoe_attribute,
    )
except ImportError:  # pragma: no cover
    from backend.services.shoe_attribute_normalization import (
        TECHNOLOGY_SPLITS, canonicalize_shoe_attribute,
    )

BACKUP_DIR = pathlib.Path(__file__).resolve().parent
BACKUP_PREFIX = "technology_merge_backup_"


def canonical_atoms(atoms: list[str]) -> list[str]:
    """Атоми товару → канонічні, з розкладанням складених, без дублів."""
    out: list[str] = []
    for atom in atoms:
        parts = TECHNOLOGY_SPLITS.get(atom, (atom,))
        for part in parts:
            canon = canonicalize_shoe_attribute("technology", part) or part
            if canon not in out:
                out.append(canon)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="справді застосувати")
    args = ap.parse_args()

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute(
        """
        SELECT p.id, p.productnumber, d.deliveryname,
               array_agg(t.technologyname ORDER BY pt.ord) AS atoms
        FROM products p
        JOIN product_technologies pt ON pt.product_id = p.id
        JOIN technologies t ON t.id = pt.technology_id
        LEFT JOIN deliveries d ON d.id = p.deliveryid
        GROUP BY p.id, p.productnumber, d.deliveryname
        ORDER BY p.productnumber
        """
    )
    changed: list[dict] = []
    for r in cur.fetchall():
        before = list(r["atoms"])
        after = canonical_atoms(before)
        if after != before:
            changed.append({
                "id": r["id"], "productnumber": r["productnumber"],
                "deliveryname": r["deliveryname"],
                "before": before, "after": after,
            })

    if not changed:
        print("Нічого канонізувати — усі написання вже канонічні.")
        return 0

    no_sheet = sum(1 for c in changed if not c["deliveryname"])
    print(f"товарів до зміни: {len(changed)}"
          + (f"   (без завозу, лише БД: {no_sheet})" if no_sheet else ""))

    # Які саме назви зникнуть і які зʼявляться — щоб було видно масштаб.
    import collections
    moves = collections.Counter()
    for c in changed:
        for b in c["before"]:
            a = canonical_atoms([b])
            if a != [b]:
                moves[(b, ", ".join(a))] += 1
    print("\nперейменування:")
    for (src, dst), k in moves.most_common():
        print(f"   «{src}» → «{dst}»   {k} товарів")

    if not args.execute:
        print("\nDRY-RUN. Приклади:")
        for c in changed[:6]:
            print(f"   {c['productnumber']:10} {', '.join(c['before'])[:46]!r}")
            print(f"              → {', '.join(c['after'])[:46]!r}")
        print("\nДля застосування додайте --execute")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}.json"
    backup.write_text(json.dumps(changed, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    print(f"\nбекап → {backup}")

    try:
        from services import journal_sync
        from models.database import SessionLocal
    except ModuleNotFoundError:
        from backend.services import journal_sync
        from backend.models.database import SessionLocal

    db = SessionLocal()
    created: list[str] = []
    try:
        for c in changed:
            ids: list[int] = []
            for name in c["after"]:
                cur.execute(
                    "SELECT id FROM technologies WHERE technologyname = %s LIMIT 1", (name,)
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "INSERT INTO technologies (technologyname) VALUES (%s) "
                        "ON CONFLICT (technologyname) DO UPDATE "
                        "SET technologyname = EXCLUDED.technologyname RETURNING id",
                        (name,),
                    )
                    row = cur.fetchone()
                    if name not in created:
                        created.append(name)
                ids.append(int(row[0]))

            cur.execute("DELETE FROM product_technologies WHERE product_id = %s", (c["id"],))
            for ord_idx, tid in enumerate(ids):
                cur.execute(
                    "INSERT INTO product_technologies (product_id, technology_id, ord) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (product_id, technology_id) DO UPDATE SET ord = EXCLUDED.ord",
                    (c["id"], tid, ord_idx),
                )
            cur.execute(
                "UPDATE products SET manually_edited_at = now(), "
                "    manually_edited_fields = ("
                "        SELECT string_agg(DISTINCT f, ',' ORDER BY f) FROM ("
                "            SELECT unnest(string_to_array("
                "                coalesce(nullif(btrim(manually_edited_fields), ''),"
                "                         'technologyid'), ',')) AS f"
                "            UNION SELECT 'technologyid'"
                "        ) s WHERE btrim(f) <> ''"
                "    ) "
                "WHERE id = %s",
                (c["id"],),
            )
            journal_sync.enqueue(db, c["id"], c["productnumber"], c["deliveryname"],
                                 "technologyid", ", ".join(c["after"]))
        conn.commit()
        db.commit()
    except Exception:
        conn.rollback()
        db.rollback()
        raise
    finally:
        db.close()

    print(f"оновлено {len(changed)} товарів; у чергу поставлено {len(changed)} задач")
    if created:
        print(f"створено канонічних назв у довіднику: {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

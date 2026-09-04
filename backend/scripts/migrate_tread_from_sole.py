#!/usr/bin/env python3
"""Перенос значень протектора з «Типу підошви» в нове поле «Протектор».

Переїжджають ЛИШЕ значення, що однозначно описують поверхню контакту:
гладка, рифлена, рельєфна, тракторна. Профільні (плоска, платформа, танкетка,
каблук) лишаються на місці, «спортивна» теж — див. міграцію
2026_09_03_002_add_tread_types.sql, де записано чому.

ЧОМУ НЕДОСИТЬ ЗМІНИТИ ЛИШЕ БАЗУ
───────────────────────────────
Ці поля двосторонньо синхронізовані з Журналом. Якщо в аркуші й далі стоятиме
«рифлена» в колонці «Тип підошви», найближчий прохід парсера прочитає її назад
і поверне soletypeid — перенос мовчки відкотиться. Тому кожен товар отримує ДВІ
задачі в journal_writeback_queue: нове значення в «Протектор» і ПОРОЖНЄ в «Тип
підошви» (writeback перетворює None на '', це його штатна поведінка).

ЧОМУ СТАВИМО ЛОК
────────────────
Між зміною в БД і записом в аркуш є вікно (черга з backoff). Лок у
manually_edited_fields не дає парсеру відкотити значення всередині цього вікна —
рівно так само, як він захищає будь-яку ручну правку з картки. Це і є ручна
правка: рішення про перекласифікацію ухвалив власник довідника.

Ростовка: обидва поля model-level, тож лок і writeback ідуть на ВСІ рядки того
самого номера — так само, як це робить update_product.

Usage:
    ./venv/bin/python backend/scripts/migrate_tread_from_sole.py            # dry-run
    ./venv/bin/python backend/scripts/migrate_tread_from_sole.py --execute
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

# Значення «Типу підошви», що насправді описують протектор.
MOVE = ("гладка", "рифлена", "рельєфна", "тракторна")

BACKUP_DIR = pathlib.Path(__file__).resolve().parent
BACKUP_PREFIX = "tread_migration_backup_"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="справді перенести")
    args = ap.parse_args()

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Цільові id протектора
    cur.execute("SELECT id, treadtypename FROM tread_types")
    tread_id = {r["treadtypename"]: r["id"] for r in cur.fetchall()}
    missing = [v for v in MOVE if v not in tread_id]
    if missing:
        print(f"У tread_types немає: {missing}. Спершу застосуйте міграцію 002.")
        return 1

    cur.execute(
        """
        SELECT p.id, p.productnumber, p.deliveryid, st.soletypename,
               p.treadtypeid, d.deliveryname
        FROM products p
        JOIN sole_types st ON st.id = p.soletypeid
        LEFT JOIN deliveries d ON d.id = p.deliveryid
        WHERE st.soletypename = ANY(%s)
        ORDER BY st.soletypename, p.productnumber
        """,
        (list(MOVE),),
    )
    rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        print("Нічого переносити — таких товарів немає.")
        return 0

    by_value: dict[str, int] = {}
    already = 0
    for r in rows:
        by_value[r["soletypename"]] = by_value.get(r["soletypename"], 0) + 1
        if r["treadtypeid"] is not None:
            already += 1

    print(f"товарів до переносу: {len(rows)}  (унікальних номерів: "
          f"{len({r['productnumber'] for r in rows})})")
    for v, k in sorted(by_value.items(), key=lambda kv: -kv[1]):
        print(f"   {k:4}  «{v}»  →  Протектор «{v}», «Тип підошви» очищається")
    if already:
        print(f"   ⚠️ у {already} товарів протектор уже заповнений — буде перезаписано")
    no_delivery = [r for r in rows if not r["deliveryname"]]
    if no_delivery:
        print(f"   ⚠️ без завозу (в аркуш не поїде, лишиться лише в БД): {len(no_delivery)}")

    if not args.execute:
        print("\nDRY-RUN. Приклади:")
        for r in rows[:5]:
            print(f"   {r['productnumber']:10} {r['soletypename']:11} "
                  f"вкладка={r['deliveryname'] or '—'}")
        print("\nДля застосування додайте --execute")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}.json"
    backup.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    print(f"\nбекап → {backup}")

    # ⚠️ services.X і backend.services.X — РІЗНІ обʼєкти; підтримуємо обидва шляхи,
    # як робить решта коду. Імпорт важкий, тому лише в гілці запису.
    try:  # noqa: E402
        from services import journal_sync
        from models.database import SessionLocal
    except ModuleNotFoundError:  # noqa: E402
        from backend.services import journal_sync
        from backend.models.database import SessionLocal
    from sqlalchemy.orm import Session  # noqa: E402

    db: Session = SessionLocal()
    moved = 0
    try:
        for r in rows:
            tid = tread_id[r["soletypename"]]
            # 1) БД: значення переїжджає, старе поле очищається
            cur.execute(
                "UPDATE products SET treadtypeid = %s, soletypeid = NULL, "
                "    manually_edited_at = now(), "
                "    manually_edited_fields = ("
                "        SELECT string_agg(DISTINCT f, ',' ORDER BY f) FROM ("
                "            SELECT unnest(string_to_array("
                "                coalesce(nullif(btrim(manually_edited_fields), ''), "
                "                         'treadtypeid'), ',')) AS f "
                "            UNION SELECT 'treadtypeid' UNION SELECT 'soletypeid'"
                "        ) s WHERE btrim(f) <> ''"
                "    ) "
                "WHERE id = %s",
                (tid, r["id"]),
            )
            # 2) Аркуш: нове значення + очищення старого поля
            journal_sync.enqueue(db, r["id"], r["productnumber"], r["deliveryname"],
                                 "treadtypeid", r["soletypename"])
            journal_sync.enqueue(db, r["id"], r["productnumber"], r["deliveryname"],
                                 "soletypeid", None)
            moved += 1
        conn.commit()
        db.commit()
    except Exception:
        conn.rollback()
        db.rollback()
        raise
    finally:
        db.close()

    print(f"перенесено {moved} товарів; у чергу поставлено {moved * 2} задач")
    print("Воркер journal_sync донесе їх в аркуш; стан видно в Task Center.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

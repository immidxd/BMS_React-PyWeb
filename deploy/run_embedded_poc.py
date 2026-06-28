# -*- coding: utf-8 -*-
"""
PoC автономного вузла: доводить, що BMS може САМ підняти локальний PostgreSQL,
створити порожню БД і побудувати ПОВНУ схему через models.database.init_db() —
без жодної ручної інсталяції СУБД.

Це симуляція того, що відбуватиметься на чистій Windows 10 при першому запуску
(там лише бінарники будуть Windows-ні, логіка — та сама).

БЕЗПЕЧНО: використовує окремий порт (5433), тимчасову теку даних і базу
`bms_poc` — бойова `bsstorage` на :5432 НЕ чіпається.

Запуск:
    python deploy/run_embedded_poc.py
        # повний прогін: initdb → start → init_db() будує схему → лічба таблиць → stop

    python deploy/run_embedded_poc.py --keep
        # не зупиняти/не видаляти кластер (щоб поколупатись psql-ом)

    python deploy/run_embedded_poc.py --restore /path/to/dump.sql
        # замість init_db() відновити дамп (симуляція cutover з Mac)
"""

from __future__ import annotations

import os
import sys
import argparse
import logging
import tempfile
import shutil
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("bms.poc")

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "deploy"))
from embedded_db import EmbeddedPostgres, resolve_pg_bin_dir  # noqa: E402

POC_PORT = 5433
POC_DB = "bms_poc"
POC_USER = "postgres"
POC_PW = "poc_pw"


def build_schema_via_init_db() -> int:
    """
    Викликати реальний models.database.init_db() проти PoC-кластера.
    Повертає кількість створених таблиць.

    КРИТИЧНО: env DB_* ставимо ДО імпорту models.database — він будує engine
    на момент імпорту, а load_dotenv(override=False) не перезапише наш os.environ.
    """
    os.environ["DB_NAME"] = POC_DB
    os.environ["DB_USER"] = POC_USER
    os.environ["DB_PASSWORD"] = POC_PW
    os.environ["DB_HOST"] = "127.0.0.1"
    os.environ["DB_PORT"] = str(POC_PORT)

    # та сама розкладка sys.path, що й у застосунку (root + backend)
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "backend"))

    log.info("Імпортую models.database з DB_PORT=%s DB_NAME=%s", POC_PORT, POC_DB)
    from models.database import init_db, engine  # noqa: E402
    from sqlalchemy import text

    # підтвердити, що engine реально дивиться на PoC-інстанс
    with engine.connect() as conn:
        who = conn.execute(text(
            "select current_database(), inet_server_port()"
        )).fetchone()
    log.info("engine підключений до db=%s port=%s", who[0], who[1])
    assert who[0] == POC_DB, f"engine на чужій БД ({who[0]})! Зупиняюсь заради безпеки."

    log.info("Викликаю init_db() — будую повну схему з нуля…")
    init_db()

    with engine.connect() as conn:
        n = conn.execute(text(
            "select count(*) from information_schema.tables where table_schema='public'"
        )).scalar()
    return int(n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="не зупиняти і не видаляти кластер після прогону")
    ap.add_argument("--restore", metavar="DUMP.sql",
                    help="відновити дамп замість init_db()")
    args = ap.parse_args()

    bin_dir = resolve_pg_bin_dir()
    log.info("PostgreSQL бінарники: %s", bin_dir)

    data_root = Path(tempfile.mkdtemp(prefix="bms_poc_pg_"))
    log.info("Тимчасова тека кластера: %s", data_root)

    pg = EmbeddedPostgres(
        data_dir=data_root / "pgdata",
        bin_dir=bin_dir,
        port=POC_PORT,
        superuser=POC_USER,
        password=POC_PW,
        db_name=POC_DB,
    )

    ok = False
    try:
        # ── Те, що зробить інсталятор+перший запуск на Windows ──────────────
        pg.initdb()
        pg.start()
        created = pg.ensure_database()
        log.info("База %s %s", POC_DB, "створена щойно" if created else "вже була")

        if args.restore:
            pg.restore_dump(Path(args.restore))
            n = pg.table_count()
            log.info("✅ Дамп відновлено. Таблиць у public: %s", n)
        else:
            n = build_schema_via_init_db()
            log.info("✅ init_db() побудував схему. Таблиць у public: %s", n)

        if n < 10:
            log.error("Замало таблиць (%s) — щось пішло не так", n)
            return 2

        log.info("─" * 60)
        log.info("PoC УСПІШНИЙ: автономний вузол сам підняв СУБД і побудував схему.")
        log.info("На Windows ідентично, лише бінарники postgres/bin будуть .exe.")
        log.info("─" * 60)
        ok = True
        return 0

    except Exception as e:  # noqa: BLE001
        log.exception("PoC ВПАВ: %s", e)
        return 1
    finally:
        if args.keep:
            log.info("--keep: лишаю кластер. psql -h127.0.0.1 -p%s -U%s -d%s",
                     POC_PORT, POC_USER, POC_DB)
            log.info("Зупинити вручну: pg_ctl -D %s -m fast stop", pg.data_dir)
        else:
            try:
                pg.stop()
            finally:
                if ok:
                    shutil.rmtree(data_root, ignore_errors=True)
                    log.info("Прибрав тимчасову теку %s", data_root)
                else:
                    log.info("Лишаю теку для розбору: %s", data_root)


if __name__ == "__main__":
    raise SystemExit(main())

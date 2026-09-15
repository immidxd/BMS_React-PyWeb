"""Інтеграційний тест бренд-апсерту — ЛИШЕ на тестовій базі.

⚠️ Раніше він брав DB_HOST/DB_NAME із оточення: сам по собі мовчки
проходив (змінних нема), а в повному прогоні інший тест підвантажував
`.env` — і цей тест ішов у БОЙОВУ bsstorage: створював товар «#TEST1» і
вносив Adidas у brand_blocklist. Рятувало лише те, що падав раніше
(RealDictCursor проти `fetchone()[0]`). Тепер запускається виключно з
BMS_TEST_DATABASE_URL, і лише якщо в назві бази є «test».
"""
import os

import psycopg2
import pytest

from backend.scripts.brand_utils import normalize_brand, upsert_brand_and_get_id

TEST_DB = os.getenv("BMS_TEST_DATABASE_URL", "")


def ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("create table if not exists brands (id serial primary key, brandname varchar unique, normalized_name text)")
    cur.execute("create table if not exists brand_blocklist (normalized_name text primary key, reason text, created_at timestamptz default now())")
    cur.execute("create table if not exists products (id serial primary key, productnumber varchar unique, brandid int references brands(id))")
    cur.execute("create unique index if not exists uq_brands_normalized_name on brands (normalized_name) where normalized_name is not null")
    conn.commit()
    cur.close()


@pytest.mark.skipif(not TEST_DB or "test" not in TEST_DB.lower(),
                    reason="потрібна ТЕСТОВА база: BMS_TEST_DATABASE_URL із «test» у назві")
def test_brand_upsert_and_linking():
    conn = psycopg2.connect(TEST_DB)
    ensure_tables(conn)
    cur = conn.cursor()   # upsert читає fetchone()[0] — звичайний кортеж

    # Upsert brand in various forms
    b1 = upsert_brand_and_get_id(cur, conn, "  Ni ké ")
    b2 = upsert_brand_and_get_id(cur, conn, "NIKE")
    assert b1 is not None and b2 is not None and b1 == b2

    # Link a product
    cur.execute("insert into products (productnumber, brandid) values (%s, %s) returning id", ("#TEST1", b1))
    pid = cur.fetchone()[0]
    conn.commit()

    cur.execute("select p.id, p.brandid, b.normalized_name from products p join brands b on b.id=p.brandid where p.id=%s", (pid,))
    row = cur.fetchone()
    assert row[2] == normalize_brand("Ni ké")

    # Block brand and ensure upsert returns None
    cur.execute("insert into brand_blocklist(normalized_name, reason) values (%s, %s) on conflict do nothing", (normalize_brand("Adidas"), "test"))
    conn.commit()
    blocked = upsert_brand_and_get_id(cur, conn, "adidas")
    assert blocked is None

    cur.close()
    conn.close()



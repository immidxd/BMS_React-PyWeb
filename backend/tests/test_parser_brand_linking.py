import os
import psycopg2
from psycopg2.extras import RealDictCursor

from backend.scripts.brand_utils import normalize_brand, upsert_brand_and_get_id


def ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("create table if not exists brands (id serial primary key, brandname varchar unique, normalized_name text)")
    cur.execute("create table if not exists brand_blocklist (normalized_name text primary key, reason text, created_at timestamptz default now())")
    cur.execute("create table if not exists products (id serial primary key, productnumber varchar unique, brandid int references brands(id))")
    cur.execute("create unique index if not exists uq_brands_normalized_name on brands (normalized_name) where normalized_name is not null")
    conn.commit()
    cur.close()


def test_brand_upsert_and_linking():
    required = ["DB_HOST", "DB_NAME", "DB_USER"]
    if not all(os.getenv(k) for k in required):
        return

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    ensure_tables(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Upsert brand in various forms
    b1 = upsert_brand_and_get_id(cur, conn, "  Ni ké ")
    b2 = upsert_brand_and_get_id(cur, conn, "NIKE")
    assert b1 is not None and b2 is not None and b1 == b2

    # Link a product
    cur.execute("insert into products (productnumber, brandid) values (%s, %s) returning id", ("#TEST1", b1))
    pid = cur.fetchone()["id"]
    conn.commit()

    cur.execute("select p.id, p.brandid, b.normalized_name from products p join brands b on b.id=p.brandid where p.id=%s", (pid,))
    row = cur.fetchone()
    assert row["normalized_name"] == normalize_brand("Ni ké")

    # Block brand and ensure upsert returns None
    cur.execute("insert into brand_blocklist(normalized_name, reason) values (%s, %s) on conflict do nothing", (normalize_brand("Adidas"), "test"))
    conn.commit()
    blocked = upsert_brand_and_get_id(cur, conn, "adidas")
    assert blocked is None

    cur.close()
    conn.close()



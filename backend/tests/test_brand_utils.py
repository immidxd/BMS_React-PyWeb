import os
import psycopg2
from psycopg2.extras import RealDictCursor

from backend.scripts.brand_utils import normalize_brand, upsert_brand_and_get_id


def test_normalize_brand_basic():
    assert normalize_brand("  Nike  ") == "nike"
    assert normalize_brand("NIKE") == "nike"
    assert normalize_brand("Ni ké") == "ni ke"
    assert normalize_brand("") is None
    assert normalize_brand(None) is None


def test_upsert_brand_and_get_id_blocklist(monkeypatch):
    # Інтеграційний тест вимагає тестової БД; якщо змінні оточення відсутні – пропустити
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
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # ensure migrations applied: create blocklist and normalized_name
    cur.execute("create table if not exists brand_blocklist(normalized_name text primary key, reason text, created_at timestamptz default now())")
    cur.execute("alter table if exists brands add column if not exists normalized_name text")
    conn.commit()

    # block 'nike'
    cur.execute("insert into brand_blocklist (normalized_name, reason) values ('nike', 'blocked for test') on conflict do nothing")
    conn.commit()

    bid = upsert_brand_and_get_id(cur, conn, "NIKE")
    assert bid is None

    # cleanup
    cur.execute("delete from brand_blocklist where normalized_name='nike'")
    conn.commit()
    cur.close()
    conn.close()



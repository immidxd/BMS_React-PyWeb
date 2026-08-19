"""Конфлікт унікальності на ОДНОМУ рядку не сміє зносити всю вкладку.

19.08.2026: `except IntegrityError: session.rollback()` у гілках створення товару
відкочував усю транзакцію аркуша — раніше додані товари зникали, а лічильник
`added` лишався. Вкладка 23.01.2025(Андрій) роками рапортувала «додано 59» і не
додавала жодного (#Ф955 і сусіди). Правильна поведінка — SAVEPOINT на рядок.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    if not all(os.getenv(k) for k in ("DB_HOST", "DB_NAME", "DB_USER")):
        pytest.skip("немає доступу до БД")
    url = (f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD','')}"
           f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME')}")
    s = sessionmaker(bind=create_engine(url))()
    s.execute(text("CREATE TEMP TABLE t_sp (id serial PRIMARY KEY, num text UNIQUE) ON COMMIT DROP"))
    yield s
    s.rollback()
    s.close()


def _insert(s, num):
    s.execute(text("INSERT INTO t_sp (num) VALUES (:n)"), {"n": num})


def test_savepoint_keeps_the_rows_added_before_the_conflict(session):
    _insert(session, "Ф955")
    sp = session.begin_nested()
    try:
        _insert(session, "Ф955")          # дубль → IntegrityError
        session.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
    _insert(session, "Ф956")

    rows = {r[0] for r in session.execute(text("SELECT num FROM t_sp"))}
    assert rows == {"Ф955", "Ф956"}


def test_plain_rollback_wipes_them_all(session):
    """Документує стару поведінку — саме через неї гинули цілі вкладки."""
    _insert(session, "Ф955")
    try:
        _insert(session, "Ф955")
        session.flush()
    except IntegrityError:
        session.rollback()

    assert session.execute(text("SELECT to_regclass('pg_temp.t_sp')")).scalar() is None

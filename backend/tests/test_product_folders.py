"""Папки товарів: назви, унікальність без урахування регістру, фільтр списку."""
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routers import product_folders as pf  # noqa: E402
from schemas.product import ProductFilter  # noqa: E402
from services.product_service import _build_product_where  # noqa: E402


def test_clean_name_collapses_spaces():
    assert pf.clean_name("  На   фотосесію \n") == "На фотосесію"


@pytest.mark.parametrize("raw", ["", "   ", "x" * (pf.NAME_MAX + 1)])
def test_clean_name_rejects_empty_and_long(raw):
    with pytest.raises(HTTPException) as e:
        pf.clean_name(raw)
    assert e.value.status_code == 422


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_a, **_k):
        return SimpleNamespace(fetchall=lambda: self.rows)


def test_unique_ignores_case_for_cyrillic():
    # lower() у базі з локаллю C кирилицю не опускає — тому перевірка в Python.
    db = _FakeDb([(1, "На фотосесію")])
    with pytest.raises(HTTPException) as e:
        pf._ensure_unique(db, "на ФОТОСЕСІЮ")
    assert e.value.status_code == 409
    pf._ensure_unique(db, "на ФОТОСЕСІЮ", exclude_id=1)  # перейменування самої себе
    pf._ensure_unique(db, "Для Олі")


def test_folder_filter_uses_exists():
    conditions, params = _build_product_where(ProductFilter(folder_id=7))
    sql = " ".join(conditions)
    assert "FROM product_folder_items fi" in sql
    assert "fi.folder_id = :folder_id" in sql
    assert params["folder_id"] == 7


def test_folder_filter_combines_with_others():
    conditions, params = _build_product_where(ProductFilter(folder_id=7, only_unsold=True))
    assert params["folder_id"] == 7
    assert len(conditions) >= 2


def test_migration_is_idempotent_and_has_fk_for_merges():
    path = os.path.join(os.path.dirname(__file__), "..", "migrations", "2026_10_07_001_product_folders.sql")
    sql = open(path, encoding="utf-8").read()
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 2
    assert "CREATE INDEX IF NOT EXISTS" in sql
    # FK на products — щоб repoint_product_refs переносив членство при злитті.
    assert "REFERENCES products(id) ON DELETE CASCADE" in sql
    assert "PRIMARY KEY (folder_id, product_id)" in sql

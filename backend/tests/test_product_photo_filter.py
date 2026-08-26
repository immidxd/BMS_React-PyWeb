import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from schemas.product import ProductFilter  # noqa: E402
from services import product_images  # noqa: E402
from services import product_service  # noqa: E402


def _photo_clause(clauses):
    """Єдина умова фільтра «Тільки з фото» серед побудованих clause-ів."""
    found = [c for c in clauses if "photo_pnums" in c or c == "FALSE"]
    assert len(found) == 1, clauses
    return found[0]


def test_without_flag_no_photo_clause():
    clauses, params = product_service._build_product_where(ProductFilter())

    assert not any("photo_pnums" in c for c in clauses)
    assert "photo_pnums" not in params


def test_photo_filter_matches_own_number_and_donor(monkeypatch):
    monkeypatch.setattr(
        product_images, "get_photo_pnum_set", lambda force=False: frozenset({"ф4350", "р101"})
    )

    clauses, params = product_service._build_product_where(
        ProductFilter(only_with_photo=True),
    )
    clause = _photo_clause(clauses)

    # Власний номер АБО номер товару-донора (official_photos_from) — дзеркало has_photo.
    assert "p.productnumber" in clause
    assert "official_photos_from" in clause
    assert params["photo_pnums"] == ["р101", "ф4350"]


def test_photo_filter_lowercases_via_icu_collation(monkeypatch):
    """База створена з локаллю C: голий lower() не опускає кирилицю, тож без
    COLLATE "und-x-icu" збігались би лише суто цифрові номери."""
    monkeypatch.setattr(
        product_images, "get_photo_pnum_set", lambda force=False: frozenset({"ф4350"})
    )

    clauses, _ = product_service._build_product_where(
        ProductFilter(only_with_photo=True),
    )
    clause = _photo_clause(clauses)

    assert clause.count('COLLATE "und-x-icu"') == 2


def test_empty_photo_set_yields_no_rows(monkeypatch):
    """Тека з фото недоступна → чесний нуль, а не мовчазне «показати всі»."""
    monkeypatch.setattr(product_images, "get_photo_pnum_set", lambda force=False: frozenset())

    clauses, params = product_service._build_product_where(
        ProductFilter(only_with_photo=True),
    )

    assert _photo_clause(clauses) == "FALSE"
    assert "photo_pnums" not in params

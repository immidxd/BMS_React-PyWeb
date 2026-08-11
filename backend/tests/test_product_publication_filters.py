import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from schemas.product import ProductFilter  # noqa: E402
from services import product_service  # noqa: E402


def test_viber_positive_filter_uses_only_published_join():
    clauses, params = product_service._build_product_where(
        ProductFilter(published_on=["viber"]),
    )

    assert params == {}
    assert clauses == ["(viberpub.pnum IS NOT NULL)"]
    assert "FROM viber_publications vp" in product_service._PRODUCT_FROM_SQL
    assert "WHERE vp.status = 'published'" in product_service._PRODUCT_FROM_SQL


def test_viber_negative_filter_matches_other_platform_exclusions():
    clauses, params = product_service._build_product_where(
        ProductFilter(published_on_not=["viber"]),
    )

    assert params == {}
    assert clauses == ["NOT COALESCE((viberpub.pnum IS NOT NULL), FALSE)"]


def test_viber_combines_with_other_positive_platforms_using_or():
    clauses, _ = product_service._build_product_where(
        ProductFilter(published_on=["telegram", "viber"]),
    )

    assert clauses == ["(tgpub.pnum IS NOT NULL OR viberpub.pnum IS NOT NULL)"]

from backend.schemas.product import ProductFilter
from backend.services.product_service import _build_product_where


def test_single_brand_filter_expands_its_alliance():
    conditions, params = _build_product_where(ProductFilter(brandid=119))
    sql = " ".join(conditions)
    assert "selected.concern_id IS NOT NULL" in sql
    assert "member.concern_id = selected.concern_id" in sql
    assert params["brandid"] == 119


def test_multi_brand_filter_expands_each_alliance():
    conditions, params = _build_product_where(ProductFilter(brandids=[119, 71]))
    sql = " ".join(conditions)
    assert "SELECT DISTINCT member.id" in sql
    assert "selected.id = ANY(:brandids)" in sql
    assert params["brandids"] == [119, 71]

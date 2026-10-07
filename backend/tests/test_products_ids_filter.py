"""«Дії → Показати вибране»: фільтр списку товарів за id з буфера виділення."""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routers.products import MAX_IDS_FILTER, _parse_id_list  # noqa: E402
from schemas.product import ProductFilter  # noqa: E402
from services.product_service import _build_product_where  # noqa: E402


def test_ids_filter_limits_to_exact_ids():
    conditions, params = _build_product_where(ProductFilter(ids=[5, 7, 11]))
    assert "p.id = ANY(:ids)" in conditions
    assert params["ids"] == [5, 7, 11]


def test_empty_ids_means_nothing_not_everything():
    # Порожнє виділення не повинно перетворитись на «показати всі товари».
    conditions, params = _build_product_where(ProductFilter(ids=[]))
    assert "p.id = ANY(:ids)" in conditions
    assert params["ids"] == []


def test_no_ids_adds_no_condition():
    conditions, params = _build_product_where(ProductFilter())
    assert not any(":ids" in c for c in conditions)
    assert "ids" not in params


def test_parse_id_list():
    assert _parse_id_list(None) is None
    assert _parse_id_list("") == []
    assert _parse_id_list("3, 1,2,") == [3, 1, 2]


def test_parse_id_list_rejects_garbage_and_oversize():
    with pytest.raises(HTTPException) as e:
        _parse_id_list("1,abc")
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _parse_id_list(",".join(str(i) for i in range(MAX_IDS_FILTER + 1)))
    assert e.value.status_code == 422

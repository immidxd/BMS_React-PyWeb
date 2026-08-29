import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from schemas.product import ProductCreate, ProductUpdate  # noqa: E402
from services.width_normalization import (  # noqa: E402
    CANONICAL_WIDTHS,
    is_width_like,
    normalize_width,
)


@pytest.mark.parametrize("raw,expected", [
    # Словесні форми з журналу — те, заради чого все це.
    ("Стандартна", "G"),
    ("стандартна", "G"),
    ("  СТАНДАРТНА  ", "G"),
    ("Широка", "W"),
    ("wide", "W"),
    # Літерні форми лишаються собою (у верхньому регістрі).
    ("G", "G"),
    ("g", "G"),
    ("D", "D"),
    ("EE", "EE"),
    ("2E", "2E"),
    # Половинні розміри зводяться до одного написання.
    ("F 1/2", "F 1/2"),
    ("g1/2", "G 1/2"),
    ("G 1 / 2", "G 1/2"),
])
def test_normalize_known_widths(raw, expected):
    assert normalize_width(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_is_none(raw):
    assert normalize_width(raw) is None


@pytest.mark.parametrize("raw", [
    "дуже широка колодка",
    "Стандартна ширина",
    "Вузька",            # свідомо не мапимо: у базі не було, літеру не вгадуємо
    "35",                # розмір, а не ширина
])
def test_non_width_text_is_rejected(raw):
    assert normalize_width(raw) is None
    assert is_width_like(raw) is False


def test_empty_counts_as_valid_input():
    """Порожнє — це очищення поля, а не помилка вводу."""
    assert is_width_like("") is True
    assert is_width_like(None) is True


def test_canonical_widths_are_self_consistent():
    for w in CANONICAL_WIDTHS:
        assert normalize_width(w) == w


def test_product_update_normalizes_word_form():
    assert ProductUpdate(width="Стандартна").width == "G"
    assert ProductUpdate(width="широка").width == "W"


def test_product_update_rejects_long_text():
    with pytest.raises(ValueError):
        ProductUpdate(width="дуже широка колодка")


def test_product_update_blank_clears_field():
    assert ProductUpdate(width="  ").width is None


def test_product_create_uses_same_rule():
    assert ProductCreate(productnumber="Ф1", width="Стандартна").width == "G"
    with pytest.raises(ValueError):
        ProductCreate(productnumber="Ф1", width="Стандартна ширина")


def test_parser_normalizes_on_read():
    """Парсер має зводити слово з аркуша до літери — інакше наступний парс
    повертав би «Стандартна» поверх виправленого значення."""
    from scripts.sheets_parser import _normalize_width

    assert _normalize_width("Стандартна") == "G"
    assert _normalize_width("Широка") == "W"
    assert _normalize_width("не ширина взагалі") is None

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from schemas.product import ProductUpdate  # noqa: E402
from services.size_normalization import (  # noqa: E402
    decimalize_fractions,
    has_vulgar_fraction,
)


@pytest.mark.parametrize("raw,expected", [
    # Знаки дробу — те, що реально приходить з аркуша.
    ("38⅔", "38.6"),
    ("43⅓", "43.3"),
    ("44⅔", "44.6"),
    ("45⅓", "45.3"),
    ("38½", "38.5"),
    # Ті самі дроби текстом.
    ("43 1/3", "43.3"),
    ("38 2/3", "38.6"),
    ("42 1/2", "42.5"),
    # Дріб без числа попереду.
    ("½", "0.5"),
])
def test_fractions_become_decimals(raw, expected):
    assert decimalize_fractions(raw) == expected


@pytest.mark.parametrize("raw", [
    "38-39",        # діапазон ростовки
    "24.5-25",      # діапазон замірів
    "36-38.5",
    "S", "M", "XL", "XXXL",
    "40", "45.3",
    "40x32x14",     # габарити
    "",
])
def test_non_fraction_values_untouched(raw):
    assert decimalize_fractions(raw) == raw
    assert has_vulgar_fraction(raw) is False


def test_width_notation_is_not_a_size_fraction():
    """«G 1/2» — ширина колодки; правило для розмірів її не чіпає
    (перед дробом має стояти ЧИСЛО)."""
    assert decimalize_fractions("G 1/2") == "G 1/2"


def test_numeric_slash_range_is_not_a_fraction():
    """«41/42» — діапазон розмірів, його розбирає парсер, а не це правило."""
    assert decimalize_fractions("41/42") == "41/42"


def test_non_string_passes_through():
    assert decimalize_fractions(None) is None
    assert decimalize_fractions(41) == 41


def test_product_update_decimalizes_size_and_measurements():
    assert ProductUpdate(sizeeu="38⅔").sizeeu == "38.6"
    assert ProductUpdate(measurementscm="24 1/2").measurementscm == "24.5"
    assert ProductUpdate(sizeeu="38-39").sizeeu == "38-39"


def test_parser_decimalizes_on_read():
    """Аркуш віддає «38⅔» — у базу має лягти 38.6, інакше дріб повертався б
    поверх виправленого значення при кожному парсі."""
    from scripts.sheets_parser import _normalize_size

    assert _normalize_size("38⅔") == "38.6"
    assert _normalize_size("38 2/3") == "38.6"
    # Наявна поведінка парсера не зламана.
    assert _normalize_size("41/42") == "41-42"
    assert _normalize_size("46,6") == "46.6"
    assert _normalize_size("3XL") == "XXXL"

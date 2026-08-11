"""Канали продажу з текстових маркерів замовлення."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.sheets_parser import _detect_sales_channel  # noqa: E402


@pytest.mark.parametrize(("text", "expected"), [
    ("PROM", "Prom"),
    ("замовлення з Prom.ua", "Prom"),
    ("MONo", "MONO"),
    ("МОНО", "MONO"),
    ("CT", "Каталог"),
    ("CG, відправити після ефіру", "Каталог"),
    ("Catalog", "Каталог"),
])
def test_detects_new_sales_channels(text, expected):
    assert _detect_sales_channel(text) == expected


@pytest.mark.parametrize("text", [
    "Чоловічі промасляні теж поміряємо",
    "оплата через monobank",
    "catalogue",
    "CGI",
    "object",
])
def test_does_not_match_channel_inside_another_word(text):
    assert _detect_sales_channel(text) is None

"""Імпорт витрат на рекламу з підсумкового блоку вкладки «Замовлення»."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.sheets_parser import (  # noqa: E402
    _extract_advertising_expense,
    _parse_nonnegative_money,
)


@pytest.mark.parametrize(("raw", "expected"), [
    ("1000", 1000.0),
    ("1 449,50 грн", 1449.5),
    ("1.234,56 ₴", 1234.56),
    ("1,234.56 UAH", 1234.56),
    (1000, 1000.0),
])
def test_parses_supported_advertising_amounts(raw, expected):
    assert _parse_nonnegative_money(raw) == expected


@pytest.mark.parametrize("raw", ["", "немає", "-50"])
def test_rejects_missing_or_negative_advertising_amount(raw):
    assert _parse_nonnegative_money(raw) is None


def test_reads_value_directly_below_exact_summary_label():
    rows = [
        ["Клієнт", "Коментарі", ""],
        ["", "Женя OLX (500 грн на рекламу)", ""],
        ["", "", "Витрати на рекламу"],
        ["", "", "1 000"],
    ]

    assert _extract_advertising_expense(rows) == {
        "found": True,
        "amount": 1000.0,
        # Сире значення потрібне тому, хто вирішує «чи можна сюди писати»:
        # `amount` дорівнює None і для порожньої комірки, і для нечитабельної.
        "raw": "1 000",
        "label_cell": "C3",
        "value_cell": "C4",
    }


def test_ignores_free_text_advertising_mentions():
    rows = [["Коментарі"], ["Женя OLX (500 грн на рекламу)"]]

    assert _extract_advertising_expense(rows)["found"] is False


def test_marks_empty_summary_value_as_invalid_instead_of_zero():
    rows = [["Витрати на рекламу"], [""]]

    result = _extract_advertising_expense(rows)
    assert result["found"] is True
    assert result["amount"] is None


def test_an_unreadable_cell_is_told_apart_from_an_empty_one():
    """`amount` дорівнює None в обох випадках, тож без сирого значення той, хто
    вирішує «комірка вільна», затер би чужий текст числом."""
    empty = _extract_advertising_expense(
        [["", "Витрати на рекламу"], ["", ""]])
    unreadable = _extract_advertising_expense(
        [["", "Витрати на рекламу"], ["", "уточнити у Жені"]])

    assert empty["amount"] is None and unreadable["amount"] is None
    assert empty["raw"].strip() == ""
    assert unreadable["raw"].strip() == "уточнити у Жені"

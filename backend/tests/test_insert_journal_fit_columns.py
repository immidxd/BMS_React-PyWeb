"""Арифметика позицій для вставки замірних колонок у Журнал.

Ця арифметика виконається на 430 живих вкладках робочого документа власника.
Помилка на одиницю розкидала б заголовки по чужих колонках, тож симулюємо
вставку так само, як це зробить Sheets API, і звіряємо підсумкову розкладку.
"""
from __future__ import annotations

import pytest

from backend.scripts.insert_journal_fit_columns import (
    NEW_COLS,
    build_requests,
    plan_for,
)

# Реальна розкладка Журналу навколо замірного блоку (1-based 42–46).
STANDARD = ["СМ", "Висота", "Товщина підошви", "Підбор", "Ціна"]
# Реальна альтернативна розкладка — вкладка '21.04.2024(Андрій)'.
ALTERNATE = ["Геометрична форма", "СМ", "Висота", "Товщина підошви", "Підбор"]


def _apply(header: list[str], sheet_id: int = 1) -> list[str]:
    """Прогнати запити так, як їх виконає Sheets API, і повернути заголовок."""
    reqs = build_requests(sheet_id, header)
    assert reqs is not None
    out = [h.strip() for h in header]
    for r in reqs:
        if "insertDimension" in r:
            out.insert(r["insertDimension"]["range"]["startIndex"], "")
        else:
            cell = r["updateCells"]
            idx = cell["start"]["columnIndex"]
            out[idx] = cell["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
    return out


def test_standard_layout_lands_correctly():
    assert _apply(STANDARD) == [
        "СМ", "Ширина устілки", "Висота", "Обхват халяви", "Товщина підошви",
        "Підбор", "Ціна",
    ]


def test_alternate_layout_lands_correctly():
    """Позиції беруться з РЕАЛЬНОГО заголовка, тож інший порядок теж працює."""
    assert _apply(ALTERNATE) == [
        "Геометрична форма", "СМ", "Ширина устілки", "Висота", "Обхват халяви",
        "Товщина підошви", "Підбор",
    ]


def test_anchors_far_apart():
    """Якорі не зобов'язані бути сусідніми — арифметика не має цього припускати."""
    header = ["СМ", "X", "Y", "Z", "Висота", "Підбор"]
    assert _apply(header) == [
        "СМ", "Ширина устілки", "X", "Y", "Z", "Висота", "Обхват халяви", "Підбор",
    ]


def test_anchors_in_reverse_order():
    """Якщо «Висота» стоїть ЛІВОРУЧ від «СМ», кожна колонка все одно йде за своїм."""
    header = ["Висота", "СМ", "Підбор"]
    assert _apply(header) == [
        "Висота", "Обхват халяви", "СМ", "Ширина устілки", "Підбор",
    ]


def test_inserted_columns_never_overwrite_existing_data():
    """Жоден updateCells не має потрапити в колонку, що вже щось містить."""
    for header in (STANDARD, ALTERNATE):
        result = _apply(header)
        # усе, що було, лишилось і в тому ж відносному порядку
        assert [c for c in result if c not in NEW_COLS] == [h.strip() for h in header]
        # обидві нові колонки з'явились рівно по разу
        for col in NEW_COLS:
            assert result.count(col) == 1


@pytest.mark.parametrize("header", [
    [],
    ["Номер", "Бренд", "Ціна"],          # вкладка без замірного блоку
    ["СМ", "Товщина підошви"],           # є «СМ», немає «Висота»
    ["Висота", "Підбор"],                # є «Висота», немає «СМ»
])
def test_tab_without_both_anchors_is_skipped(header):
    assert plan_for(header) is None
    assert build_requests(1, header) is None


def test_whitespace_in_headers_is_tolerated():
    assert _apply(["  СМ ", "Висота  ", "Підбор"])[:4] == [
        "СМ", "Ширина устілки", "Висота", "Обхват халяви",
    ]

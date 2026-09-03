"""Арифметика вставки однієї колонки в Журнал.

Виконується на 431 живій вкладці робочого документа власника, тож симулюємо
поведінку Sheets API і звіряємо підсумкову розкладку.
"""
from __future__ import annotations

import pytest

from backend.scripts.insert_journal_column import build_requests, plan_for

# Реальний фрагмент шапки Журналу навколо взуттєвого блоку.
HEADER = ["Проміжна підошва", "Тип підошви", "Форма носка", "Тип шнурівки", "Застібка"]


def _apply(header: list[str], column: str, after: str) -> list[str]:
    at = plan_for(header, column, after)
    assert at is not None
    out = [h.strip() for h in header]
    for r in build_requests(1, at, column):
        if "insertDimension" in r:
            out.insert(r["insertDimension"]["range"]["startIndex"], "")
        else:
            cell = r["updateCells"]
            out[cell["start"]["columnIndex"]] = \
                cell["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
    return out


def test_column_lands_right_after_anchor():
    assert _apply(HEADER, "Протектор", "Тип підошви") == [
        "Проміжна підошва", "Тип підошви", "Протектор", "Форма носка",
        "Тип шнурівки", "Застібка",
    ]


def test_nothing_existing_is_lost_or_reordered():
    result = _apply(HEADER, "Протектор", "Тип підошви")
    assert [c for c in result if c != "Протектор"] == HEADER
    assert result.count("Протектор") == 1


def test_anchor_at_the_end():
    assert _apply(["A", "B"], "Новa", "B") == ["A", "B", "Новa"]


def test_anchor_first():
    assert _apply(["A", "B"], "Нова", "A") == ["A", "Нова", "B"]


def test_whitespace_in_headers_is_tolerated():
    assert _apply(["  Тип підошви ", "Форма носка"], "Протектор", "Тип підошви") == [
        "Тип підошви", "Протектор", "Форма носка",
    ]


@pytest.mark.parametrize("header, column, after", [
    (["Номер", "Бренд"], "Протектор", "Тип підошви"),   # якоря немає
    ([], "Протектор", "Тип підошви"),                    # порожня вкладка
])
def test_missing_anchor_is_skipped(header, column, after):
    assert plan_for(header, column, after) is None


def test_already_done_tab_is_skipped():
    """Ідемпотентність: вкладка з уже наявною колонкою не обробляється вдруге."""
    header = ["Тип підошви", "Протектор", "Форма носка"]
    assert plan_for(header, "Протектор", "Тип підошви") is None

"""Кілька розмірів одного номера в одному рядку «Замовлень».

17.10.2026: каталог дозволив замовити ростовку кількома розмірами одразу й пише
рядок «Ф1; Ф1;» + «Ф1 (39); Ф1 (40);». Парсер тримав підказки як dict номер →
розмір: друга затирала першу, обидві позиції сідали на рядок 40-го, а 39-й
лишався «Непродано» (так уже записано, наприклад, #45182 і #43968). Тепер у
номера СПИСОК підказок, і кожна поява номера в рядку бере свою.

Без Google і без БД: сесія підставна, фільтри SQLAlchemy перевіряються в пам'яті.
"""
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BooleanClauseList

from backend.scripts.sheets_parser import (
    _parse_size_hints, _resolve_order_product, _take_size_hint,
)


# ── Розбір 'Уточнення' ──────────────────────────────────────────────────────
@pytest.mark.parametrize("text, expected", [
    ("П321 (37);", {"#П321": ["37"]}),
    ("Ф3320 (37-38);", {"#Ф3320": ["37-38"]}),
    ("Розмір: 38 (П321);", {"#П321": ["38"]}),
    ("#ф1 (39)", {"#Ф1": ["39"]}),
    ("", {}),
])
def test_single_hint_formats_are_unchanged(text, expected):
    assert _parse_size_hints(text) == expected


def test_repeated_number_keeps_every_hint_in_order():
    assert _parse_size_hints("Ф1 (39); Ф1 (40);") == {"#Ф1": ["39", "40"]}
    # #45182: «Ф986 - уточнити розмір» — не підказка; продано 36-й і 37-й
    assert _parse_size_hints("Ф986 - уточнити розмір; Ф986 (36); Ф986 (37);") == {
        "#Ф986": ["36", "37"]}


def test_empty_brackets_give_no_hint():
    # #55137: «Р103 ()» — розмір другої пари не вказано
    assert _parse_size_hints("готівка; Р103 (43.5); Р103 ();") == {"#Р103": ["43.5"]}


def test_suffix_hint_never_lands_on_the_base_number():
    assert _parse_size_hints("Ф1 (39); Ф1-2 (41);").get("#Ф1") == ["39"]


# ── Яка поява номера бере яку підказку ──────────────────────────────────────
def _takes(clarification, numbers):
    hints = _parse_size_hints(clarification)
    return [_take_size_hint(hints, n) for n in numbers]


def test_each_occurrence_takes_its_own_hint():
    assert _takes("Ф1 (39); Ф1 (40);", ["Ф1", "Ф1"]) == ["39", "40"]


def test_single_hint_still_applies_to_every_occurrence():
    assert _takes("Ф1 (39);", ["Ф1", "Ф1", "Ф1"]) == ["39", "39", "39"]


def test_extra_occurrences_reuse_the_last_hint():
    """Як і раніше, коли перемагала остання підказка."""
    assert _takes("Ф1 (39); Ф1 (40);", ["Ф1", "Ф1", "Ф1"]) == ["39", "40", "40"]


def test_other_numbers_and_spellings_share_nothing_but_their_own_queue():
    assert _takes("С1 (42); Ф1 (39); Ф1 (40);", ["ф1", "С1", "#Ф1"]) == ["39", "42", "40"]


def test_suffix_number_does_not_consume_base_hints():
    assert _takes("Ф1810 (39); Ф1810 (40);", ["Ф1810-2", "Ф1810", "Ф1810"]) == [None, "39", "40"]


def test_number_without_hint_gets_none():
    assert _takes("П321 (37);", ["Ф1"]) == [None]


# ── Повний шлях позиції: підказка → рядок товару ────────────────────────────
def _p(pid, pnum, size):
    return SimpleNamespace(id=pid, productnumber=pnum, sizeeu=size, clonednumbers=None,
                           dateadded=datetime(2026, 10, 1), price=1600)


def _holds(cond, p):
    """Фільтр із резолвера (==, or_, like) → перевірка рядка в пам'яті."""
    if isinstance(cond, BooleanClauseList):
        return any(_holds(c, p) for c in cond.clauses)
    value, wanted = getattr(p, cond.left.key), cond.right.value
    if cond.operator is operators.eq:
        return value == wanted
    if cond.operator is operators.like_op:
        return wanted.strip("%") in (value or "")
    raise AssertionError(f"непередбачений фільтр: {cond}")


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _model):
        return self

    def filter(self, *conds):
        return _Session([p for p in self.rows if all(_holds(c, p) for c in conds)])

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


ROSTOVKA = [_p(1, "#Ф1", "39"), _p(2, "#Ф1", "40"), _p(3, "#Ф1-2", "41")]


def _items(numbers, clarification):
    """Як цикл позицій у `_parse_orders_sheet`: кожна поява бере свою підказку."""
    session, hints = _Session(ROSTOVKA), _parse_size_hints(clarification)
    return [_resolve_order_product(session, n, _take_size_hint(hints, n),
                                   order_date=date(2026, 10, 17), item_price=1600.0).id
            for n in numbers]


def test_catalog_row_sells_both_sizes():
    assert _items(["Ф1", "Ф1"], "Ф1 (39); Ф1 (40);") == [1, 2]


def test_catalog_row_with_a_suffix_number():
    assert _items(["Ф1", "Ф1", "Ф1-2"], "Ф1 (39); Ф1 (40); Ф1-2 (41);") == [1, 2, 3]


def test_single_hint_rows_resolve_as_before():
    assert _items(["Ф1", "Ф1"], "Ф1 (40);") == [2, 2]
    assert _items(["Ф1"], "Розмір: 39 (Ф1);") == [1]

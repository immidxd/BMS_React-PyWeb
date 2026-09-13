"""Перейменування номера у вкладці не сміє плодити двійника.

13.09.2026: у вкладці 13.08.2026(Лісоводи) Ф4336 стало #Ф4350, старий номер лишили
в «Номера-клони». Парсер за новим номером нічого не знайшов → створив нові рядки,
а 5 старих (10 пар) лишились у базі, у вітрині, на Prom і в Instagram — подвійний
залишок. Правильна поведінка — успадкувати запис зі старим номером (id, замовлення,
публікації) і перейменувати його.
"""
from types import SimpleNamespace

from backend.scripts.sheets_parser import _declared_clone_numbers, _pick_clone_reclaim


def _p(pid, num):
    return SimpleNamespace(id=pid, productnumber=num)


def test_declared_clones_are_canonical_and_exclude_the_row_number():
    assert _declared_clone_numbers("Ф4336;", "#Ф4350") == ["Ф4336"]
    assert _declared_clone_numbers(" #ф4336 , 4336 ; Ф4350", "#Ф4350") == ["Ф4336", "4336"]
    assert _declared_clone_numbers("", "#Ф4350") == []
    assert _declared_clone_numbers(None, "#Ф4350") == []


def test_renamed_number_is_reclaimed_when_it_left_the_journal():
    old = _p(347740, "#Ф4336")
    journal = {"Ф4350", "Ф955"}          # Ф4336 в журналі більше нема
    assert _pick_clone_reclaim([old], seen_in_run={}, journal_nums=journal) is old


def test_clone_that_still_lives_in_the_journal_is_a_different_item():
    # «Номера-клони» бувають і в живих товарів (варіанти одного номера) —
    # поки старий номер є в аркуші, це не перейменування.
    old = _p(1, "#Ф4336")
    assert _pick_clone_reclaim([old], {}, {"Ф4336", "Ф4350"}) is None


def test_record_already_seen_this_run_is_not_reclaimed():
    # Запис, який цього прогону вже отримав свій рядок, — живий, не сирота.
    old = _p(1, "#Ф4336")
    assert _pick_clone_reclaim([old], {1: 1}, {"Ф4350"}) is None


def test_ambiguity_is_left_visible_instead_of_merged_blindly():
    a, b = _p(1, "#Ф4336"), _p(2, "#4336")
    assert _pick_clone_reclaim([a, b], {}, {"Ф4350"}) is None


def test_nothing_to_reclaim_without_candidates():
    assert _pick_clone_reclaim([], {}, {"Ф4350"}) is None

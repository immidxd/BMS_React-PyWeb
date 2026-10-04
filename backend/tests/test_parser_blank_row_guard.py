"""Рядок «лише номер» не забирає заповнений товар з іншого завозу.

04.10.2026: лот 4440–4509 спершу записали без «Ф» — порожній рядок «4501»
(лише номер) зіставився зі старим проданим #4501 з 18.02.2023, і той переїхав
у новий завоз. Тут закріплено і заборону, і все, що мусить працювати як раніше.
"""
from types import SimpleNamespace

from backend.scripts.sheets_parser import _blank_row_foreign_targets as guard

NEW, OLD = 812, 537


def _p(pid=1, delivery=OLD, brand=7, size="42", letter=None):
    return SimpleNamespace(id=pid, deliveryid=delivery, brandid=brand, sizeeu=size, size_letter=letter)


def test_blank_row_cannot_take_filled_product_of_another_delivery():
    """Сам інцидент: «4501» без даних у новому лоті vs старий Reebok 42."""
    old = _p()
    assert guard([old], NEW, "", "", "") == [old]


def test_letter_only_product_is_also_protected():
    assert guard([_p(brand=None, size=None, letter="L")], NEW, "", "", "")


def test_new_number_creates_product_as_before():
    """Нові номери (у базі ще нема) — товари створюються одразу, як і було."""
    assert guard([], NEW, "", "", "") == []


def test_reparse_of_same_delivery_still_matches():
    """Повторний синк лоту з порожніми рядками — свої записи, логіка без змін."""
    assert guard([_p(delivery=NEW)], NEW, "", "", "") == []


def test_mixed_candidates_keep_old_logic():
    """Якщо серед кандидатів є запис цього завозу — звичайна логіка вибору."""
    assert guard([_p(1), _p(2, delivery=NEW, brand=None, size=None)], NEW, "", "", "") == []


def test_row_with_brand_or_size_moves_as_before():
    """Рядок із даними перенесли в іншу вкладку (розділили завоз) — товар їде за ним."""
    assert guard([_p()], NEW, "Reebok", "", "") == []
    assert guard([_p()], NEW, "", "42", "") == []
    assert guard([_p()], NEW, "", "", "L") == []


def test_still_blank_product_may_follow_blank_row():
    """Порожній рядок перенесли ДО заповнення — у БД теж порожньо, як раніше."""
    assert guard([_p(brand=None, size=None)], NEW, "", "", "") == []
    assert guard([_p(brand=None, size="  ")], NEW, "", "", "") == []


def test_no_delivery_context_changes_nothing():
    """Без завозу (shipment_id нема) чи товар без завозу — поведінка як була."""
    assert guard([_p()], None, "", "", "") == []
    assert guard([_p(delivery=None)], NEW, "", "", "") == []

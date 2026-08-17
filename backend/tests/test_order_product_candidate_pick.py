"""Вибір товару, коли `productnumber` неунікальний.

Регресія 17.08.2026: продаж ботинок Gino Rossi #4336 від 2023-03-29 (980 ₴)
приклеївся до кросівок Ecco #4336, завезених 13.08.2026 — резолвер брав
«першого кандидата з #» без ORDER BY, без дати й без ціни.
"""
from datetime import date, datetime
from types import SimpleNamespace

from backend.scripts.sheets_parser import _pick_order_candidate


def _p(pid, pnum, dateadded, price):
    return SimpleNamespace(id=pid, productnumber=pnum, dateadded=dateadded, price=price)


GINO = _p(132314, "#4336", datetime(2023, 2, 10), 980)
ECCO_38 = _p(347716, "#4336", datetime(2026, 8, 13), 2900)
ECCO_39 = _p(347717, "#4336", datetime(2026, 8, 13), 2900)


def test_old_order_goes_to_the_product_that_existed_back_then():
    picked = _pick_order_candidate([ECCO_38, GINO, ECCO_39],
                                   order_date=date(2023, 3, 29), item_price=980)
    assert picked is GINO


def test_price_decides_when_both_already_existed():
    picked = _pick_order_candidate([GINO, ECCO_38],
                                   order_date=date(2026, 8, 14), item_price=2900)
    assert picked is ECCO_38


def test_pick_is_stable_without_date_or_price():
    order = [ECCO_39, GINO, ECCO_38]
    assert _pick_order_candidate(order) is _pick_order_candidate(list(reversed(order)))


def test_falls_back_instead_of_returning_nothing():
    # Замовлення датоване раніше за ЄДИНИЙ наявний товар (передзамовлення):
    # фільтр по даті лишає порожньо → беремо що є, а не None.
    picked = _pick_order_candidate([ECCO_38, ECCO_39],
                                   order_date=date(2026, 1, 1), item_price=0)
    assert picked is ECCO_38


def test_hash_prefix_still_wins_over_legacy_number():
    legacy = _p(90001, "4336", None, None)
    picked = _pick_order_candidate([legacy, ECCO_38], order_date=None, item_price=None)
    assert picked is ECCO_38


def test_no_candidates_is_none():
    assert _pick_order_candidate([]) is None

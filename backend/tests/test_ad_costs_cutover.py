"""Уся реклама = комірка + Meta з банку з 01.09.2026 (рішення власника 08.10.2026).

До дати комірка ефіру містила всю рекламу (Meta всередині), з дати — лише ІНШУ.
Сторож: кожен розрахунок реклами в статистиці мусить іти через `_ad_costs_sql`,
інакше прибуток і сторінка «Реклама» розійдуться.
"""
import inspect
import re
from datetime import date

from backend.routers import statistics as st
from backend.services import meta_ads


def test_cutover_date_is_the_owners_choice():
    assert meta_ads.AD_CELL_OTHER_ONLY_FROM == date(2026, 9, 1)


def test_unified_source_adds_bank_meta_only_from_the_cutover():
    sql = st._ad_costs_sql()
    assert "FROM advertising_expenses" in sql and "FROM meta_ad_charges" in sql
    assert "UNION ALL" in sql
    assert ">= DATE '2026-09-01'" in sql


def test_every_statistics_ad_sum_goes_through_the_unified_source():
    src = inspect.getsource(st)
    helper = inspect.getsource(st._ad_costs_sql)
    rest = src.replace(helper, "")
    # Прямо читати комірки дозволено лише для списку років (там суми не рахуються).
    direct = [m.group(0) for m in re.finditer(r"FROM advertising_expenses[^\n]*", rest)]
    assert all("ORDER BY yr" in d for d in direct), direct

"""Фото в «Дефекти» → «Поточний стан» = «Пошкоджений» (правило власника 04.10.2026).

Лише з «Новий» або порожнього стану, лише якщо людина стан не правила (лок),
і лише для того товару, в картці якого фото лягло в «Дефекти».
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from services import defect_condition as dc  # noqa: E402
import services.product_service as ps  # noqa: E402

NAMES = {1: "Новий", 2: "Хороший", 3: "Вживаний", 4: "Пошкоджений", 90: "Легковживаний"}


class _DB:
    def __init__(self, product):
        self.product = product
        self.rolled_back = False
    def query(self, *_a):
        prod = self.product
        return types.SimpleNamespace(filter=lambda *a: types.SimpleNamespace(first=lambda: prod))
    def execute(self, _stmt, params=None):
        return types.SimpleNamespace(scalar=lambda: NAMES.get((params or {}).get("i")))
    def rollback(self):
        self.rolled_back = True


def _product(cur=None, base=None, locks=None):
    return types.SimpleNamespace(id=7, productnumber="#Ф4470", current_conditionid=cur,
                                 conditionid=base, manually_edited_fields=locks)


@pytest.fixture
def calls(monkeypatch):
    seen = {"update": [], "enqueue": []}
    monkeypatch.setattr(ps, "update_product",
                        lambda db, pid, upd: seen["update"].append((pid, upd.current_condition_name))
                        or types.SimpleNamespace(id=pid))
    monkeypatch.setattr(ps, "enqueue_writeback_for", lambda db, p: seen["enqueue"].append(p.id) or {})
    return seen


def test_new_item_becomes_damaged_and_goes_to_journal(calls):
    res = dc.apply_for_defect_photo(_DB(_product(cur=None, base=1)), 7)
    assert res == {"applied": True, "from": "Новий", "to": "Пошкоджений"}
    assert calls["update"] == [(7, "Пошкоджений")]
    assert calls["enqueue"] == [7]           # той самий шлях у журнал, що й ручна правка


def test_empty_condition_becomes_damaged(calls):
    assert dc.apply_for_defect_photo(_DB(_product()), 7)["applied"]


@pytest.mark.parametrize("cur", [2, 3, 90])
def test_other_condition_is_never_changed(calls, cur):
    """Власник: «якщо стан зараз вже інший — не змінювати!»"""
    assert dc.apply_for_defect_photo(_DB(_product(cur=cur, base=1)), 7) is None
    assert calls["update"] == []


def test_already_damaged_is_left_alone(calls):
    assert dc.apply_for_defect_photo(_DB(_product(cur=4)), 7) is None
    assert calls["update"] == []


def test_users_own_choice_always_wins(calls):
    """Лок «Поточного стану» = людина вже вирішила — правило мовчить назавжди."""
    p = _product(cur=1, locks="price, current_conditionid")
    assert dc.apply_for_defect_photo(_DB(p), 7) is None
    assert calls["update"] == []


def test_failure_never_breaks_the_photo_operation(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(ps, "update_product", boom)
    db = _DB(_product(cur=1))
    assert dc.apply_for_defect_photo(db, 7) is None
    assert db.rolled_back


def test_router_applies_only_for_defect_kind(monkeypatch):
    from routers import products as rp
    seen = []
    monkeypatch.setattr(dc, "apply_for_defect_photo", lambda db, pid: seen.append(pid) or {"applied": True})
    import services as _services
    monkeypatch.setattr(_services, "defect_condition", dc, raising=False)
    assert rp._defect_condition(None, 7, "real") is None
    assert rp._defect_condition(None, 7, "official") is None
    assert rp._defect_condition(None, 7, "defect") == {"applied": True}
    assert seen == [7]

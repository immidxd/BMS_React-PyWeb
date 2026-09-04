"""Сховище пропозицій: поріг певності, заміна невирішеної, і межа модуля.

Головне, що тут перевіряється, — не SQL, а ГРАНИЦЯ: модуль не має жодного
шляху записати значення в products. Прийняття лише повертає словник, який
викликач зобовʼязаний провести через update_product.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import field_proposals as fp  # noqa: E402


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows


class _FakeDB:
    """Лічить виконані запити — щоб довести, що в products нічого не летить.

    Форма рядка залежить від запиту, тож двійник роздає її за SQL: SELECT для
    списку чекає сім колонок, RETURNING після accept — три.
    """
    def __init__(self, ret=None):
        self.sql: list[str] = []
        self.params: list[dict] = []
        self._ret = ret
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.sql.append(sql)
        self.params.append(params or {})
        if self._ret is not None:
            return self._ret
        if sql.startswith("SELECT"):
            return _FakeResult([])          # порожній список пропозицій
        return _FakeResult([], rowcount=1)


# ── Поріг певності ──────────────────────────────────────────────────────────

def test_low_confidence_is_not_proposed_at_all():
    """Третій рубіж: невпевнене не доходить навіть до сховища."""
    db = _FakeDB()
    assert fp.propose(db, 1, "sole_type_name", "плоска", 0.4) is False
    assert db.sql == [], "нічого не мало виконуватись"


def test_confidence_at_threshold_passes():
    db = _FakeDB()
    assert fp.propose(db, 1, "sole_type_name", "плоска",
                      fp.threshold_for("sole_type_name")) is True
    assert len(db.sql) == 1


def test_article_has_the_strictest_threshold():
    """Помилка в артикулі найдорожча — поріг має бути вищим за типові поля."""
    assert fp.threshold_for("marking") > fp.threshold_for("sole_type_name")
    db = _FakeDB()
    assert fp.propose(db, 1, "marking", "CW2288-111", 0.80) is False
    assert fp.propose(db, 1, "marking", "CW2288-111", 0.90) is True


def test_unknown_field_falls_back_to_default_threshold():
    assert fp.threshold_for("щось_нове") == fp.DEFAULT_THRESHOLD


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_value_is_never_proposed(value):
    db = _FakeDB()
    assert fp.propose(db, 1, "sole_type_name", value, 0.99) is False
    assert db.sql == []


def test_missing_confidence_is_allowed():
    """Певності може не бути (ручне джерело) — тоді поріг не застосовується."""
    db = _FakeDB()
    assert fp.propose(db, 1, "sole_type_name", "плоска", None) is True


# ── Заміна невирішеної ──────────────────────────────────────────────────────

def test_repeat_replaces_open_proposal_not_appends():
    """Повторне розпізнавання не має плодити чергу варіантів у картці."""
    db = _FakeDB()
    fp.propose(db, 1, "sole_type_name", "плоска", 0.9)
    sql = db.sql[0]
    assert "ON CONFLICT (product_id, field) WHERE status = 'pending'" in sql
    assert "DO UPDATE SET" in sql


# ── Межа модуля: у products не пишемо ───────────────────────────────────────

def test_no_function_ever_touches_products():
    """Жоден запит модуля не має згадувати products.

    Це і є архітектурна гарантія: у картку значення потрапляє лише через
    update_product, тобто разом із локом і чергою write-back.
    """
    db = _FakeDB()          # форма рядка роздається за SQL
    fp.propose(db, 7, "sole_type_name", "плоска", 0.9)
    fp.open_for_product(db, 7)
    fp.reject(db, 1)
    fp.mark_stale(db, 7, {"sole_type_name"})
    # accept має свою форму відповіді — окремим двійником
    db2 = _FakeDB(_FakeResult([(7, "sole_type_name", "плоска")]))
    fp.accept(db2, 1)
    db.sql.extend(db2.sql)
    for sql in db.sql:
        assert " products " not in f" {sql} ", f"модуль торкнувся products: {sql}"


def test_accept_returns_update_payload_and_does_not_apply_it():
    db = _FakeDB(_FakeResult([(7, "sole_type_name", "плоска")]))
    out = fp.accept(db, 42)
    assert out == {"product_id": 7, "update": {"sole_type_name": "плоска"}}
    # рівно ОДИН запит — позначити прийнятою; застосування не тут
    assert len(db.sql) == 1


def test_accept_of_already_decided_returns_none():
    """Гонка двох кліків не має застосувати пропозицію двічі."""
    db = _FakeDB(_FakeResult([]))
    assert fp.accept(db, 42) is None


def test_field_name_matches_product_update_contract():
    """Імена полів мусять бути такі, які приймає ProductUpdate.

    Інакше прийняття довелося б перекладати іменами, і зʼявився б четвертий
    список, який неминуче розійдеться з рештою.
    """
    from backend.schemas.product import ProductUpdate
    allowed = set(ProductUpdate.model_fields)
    unknown = [f for f in fp.CONFIDENCE_THRESHOLD if f not in allowed]
    assert not unknown, f"ProductUpdate не приймає: {unknown}"

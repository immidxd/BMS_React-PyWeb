"""Бюджетне гальмо: за межею воно ВІДМОВЛЯЄ, а не попереджає."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import ai_budget as ab  # noqa: E402


class _FakeDB:
    """Віддає задану суму витраченого; лічить, що саме запитували."""
    def __init__(self, total=0.0, by_purpose=None):
        self.total = total
        self.by_purpose = by_purpose or {}
        self.inserts: list[dict] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        if sql.startswith("INSERT"):
            self.inserts.append(params)
            return _Scalar(None)
        if "purpose = :p" in sql:
            return _Scalar(self.by_purpose.get(params.get("p"), 0.0))
        return _Scalar(self.total)


class _Scalar:
    def __init__(self, v): self._v = v
    def scalar(self): return self._v


# ── Гальмо ──────────────────────────────────────────────────────────────────

def test_allows_while_under_cap():
    v = ab.guard(_FakeDB(total=3.0))
    assert v.allowed is True
    assert v.remaining_usd == pytest.approx(ab.MONTHLY_CAP_USD - 3.0)


def test_refuses_at_cap_not_merely_warns():
    """Ключове: allowed=False, а не «попередження й усе одно так»."""
    v = ab.guard(_FakeDB(total=ab.MONTHLY_CAP_USD))
    assert v.allowed is False
    assert "вичерпано" in v.reason
    assert v.remaining_usd == 0


def test_refuses_over_cap():
    assert ab.guard(_FakeDB(total=ab.MONTHLY_CAP_USD + 0.01)).allowed is False


def test_backfill_has_its_own_smaller_share():
    """Дозаповнення старої бази не має зʼїдати стелю, призначену новим товарам."""
    cap_backfill = ab.MONTHLY_CAP_USD * ab.BACKFILL_SHARE
    db = _FakeDB(total=cap_backfill, by_purpose={"backfill": cap_backfill})
    assert ab.guard(db, purpose="backfill").allowed is False
    # а новим товарам за тих самих витрат ще можна
    assert ab.guard(db, purpose="autofill").allowed is True


def test_backfill_allowed_while_under_its_share():
    db = _FakeDB(total=1.0, by_purpose={"backfill": 1.0})
    assert ab.guard(db, purpose="backfill").allowed is True


# ── Тарифи ──────────────────────────────────────────────────────────────────

def test_unknown_model_is_priced_expensively():
    """Незнану модель рахуємо дорого — помилка має бути на користь стелі."""
    assert ab.rate_for("щось-нове") == ab.FALLBACK_RATE
    known = ab.estimate_cost("gemini-3.1-flash-lite", 1_000_000, 0)
    unknown = ab.estimate_cost("щось-нове", 1_000_000, 0)
    assert unknown > known


def test_cost_math():
    # 1M вхідних по $0.30 + 1M вихідних по $2.50
    assert ab.estimate_cost("gemini-3.5-flash", 1_000_000, 1_000_000) == pytest.approx(2.80)
    assert ab.estimate_cost("gemini-3.5-flash", 0, 0) == 0


# ── Облік ───────────────────────────────────────────────────────────────────

def test_failed_call_is_still_recorded():
    """Провайдер тарифікує вхідні токени навіть коли відповідь не склалась.

    Рахувати лише успішні означало б систематично занижувати витрачене — саме
    так стеля «раптом» виявляється перевищеною.
    """
    db = _FakeDB()
    cost = ab.record(db, model="gemini-3.5-flash", prompt_tokens=5000,
                     output_tokens=0, ok=False, error="HTTP 500")
    assert len(db.inserts) == 1
    assert db.inserts[0]["ok"] is False
    assert cost > 0


def test_record_freezes_the_rate_it_used():
    """Ціни змінюються; історія має лишатись відтворюваною."""
    db = _FakeDB()
    ab.record(db, model="gemini-3.5-flash", prompt_tokens=1000, output_tokens=100)
    row = db.inserts[0]
    assert float(row["ri"]) == 0.30 and float(row["ro"]) == 2.50


def test_purpose_is_recorded_for_separate_limits():
    db = _FakeDB()
    ab.record(db, model="gemini-3.5-flash", prompt_tokens=10, output_tokens=1,
              purpose="backfill")
    assert db.inserts[0]["pu"] == "backfill"

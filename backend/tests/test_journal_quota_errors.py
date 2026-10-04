"""429 від Google — тимчасова помилка, а не «вкладки нема» (04.10.2026).

Голий `except Exception` при пошуку вкладки перетворював «Quota exceeded»
на «worksheet … not found» — перманентну причину черги: правка йшла в
'skipped' без повтору. А зміна номера здавалась за ~5 с при хвилинній квоті.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gspread  # noqa: E402
import pytest  # noqa: E402

from backend.scripts import sheets_parser as sp  # noqa: E402
from backend.scripts import journal_writer as jw  # noqa: E402


class _Quota(Exception):
    def __str__(self):
        return "APIError: [429]: Quota exceeded for quota metric 'Read requests'"


def _gc_raising(exc):
    sh = types.SimpleNamespace(worksheet=lambda title: (_ for _ in ()).throw(exc))
    return types.SimpleNamespace(open_by_key=lambda key: sh)


def test_quota_during_lookup_is_not_reported_as_missing_tab(monkeypatch):
    monkeypatch.setattr(sp, "WRITEBACK_ENABLED", True)
    monkeypatch.setattr(sp, "get_gc", lambda: _gc_raising(_Quota()))
    with pytest.raises(_Quota):
        sp._writeback_fields_to_journal_locked("01.10.2026(Андрій)", "#Ф4503", {"season": "Демі"})


def test_really_missing_tab_is_still_permanent(monkeypatch):
    monkeypatch.setattr(sp, "WRITEBACK_ENABLED", True)
    monkeypatch.setattr(sp, "get_gc", lambda: _gc_raising(gspread.exceptions.WorksheetNotFound("x")))
    res = sp._writeback_fields_to_journal_locked("Нема такої", "#Ф1", {"season": "Демі"})
    assert res["ok"] is False and "not found" in res["reason"]


def test_quota_retries_wait_for_the_minute_window(monkeypatch):
    sleeps = []
    monkeypatch.setattr(jw.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 4:
            raise _Quota()
        return "ok"
    assert jw._with_retry(fn) == "ok"
    assert sleeps == [4.0, 8.0, 16.0]


def test_network_retries_keep_short_delays(monkeypatch):
    sleeps = []
    monkeypatch.setattr(jw.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset by peer")
        return "ok"
    assert jw._with_retry(fn) == "ok"
    assert sleeps == [0.8, 1.6]


def test_exhausted_quota_says_so_plainly(monkeypatch):
    monkeypatch.setattr(jw.time, "sleep", lambda s: None)
    with pytest.raises(jw.JournalTransientError) as ei:
        jw._with_retry(lambda: (_ for _ in ()).throw(_Quota()))
    assert "хвилинна квота" in str(ei.value)
    from backend.routers.deliveries import _journal_err_detail
    assert "Зачекайте хвилину" in _journal_err_detail(ei.value, "01.10.2026(Андрій)")

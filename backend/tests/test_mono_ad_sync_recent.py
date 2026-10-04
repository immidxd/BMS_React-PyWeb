"""Свіжі списання Meta: прохід ВПЕРЕД від останньої перевірки.

До 04.10.2026 збирач ішов лише назад і після `exhausted=True` пропускав
рахунок назавжди — Статистика → «Реклама» застигла на 30.08.2026.
"""
from __future__ import annotations

import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import mono_ad_sync as mas  # noqa: E402


class _FakeMono:
    CHUNK_DAYS = 31
    SLEEP_BETWEEN_SEC = 62

    def __init__(self, accounts, items_by_window=None, fail_for=()):
        self._accounts = accounts
        self.calls = []
        self.items = items_by_window or (lambda acc, a, b: [])
        self.fail_for = set(fail_for)

    def accounts(self):
        return self._accounts

    def statement_chunk(self, account_id, since, until):
        assert (until - since).days <= 31
        self.calls.append((account_id, since, until))
        if account_id in self.fail_for:
            raise RuntimeError("ліміт monobank: 1 запит на 60 с")
        return self.items(account_id, since, until)

    def meta_charges_from(self, items):
        return [{"bank_transaction_id": i["id"], "charge_date": date(2026, 9, 20),
                 "charged_at": None, "amount_uah": 100} for i in items if i.get("meta")]


def _setup(monkeypatch, mono, states):
    saved, stored = [], []
    monkeypatch.setattr(mas, "_mono", lambda: mono)
    monkeypatch.setattr(mas, "_state", lambda db, a: dict(states.get(a, {})))
    monkeypatch.setattr(mas, "_save_state", lambda db, a, **f: saved.append((a, f)))
    monkeypatch.setattr(mas, "_store_charge", lambda db, a, c, raw: stored.append(c["bank_transaction_id"]) or True)
    return saved, stored


NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
DB = types.SimpleNamespace(rollback=lambda: None)


def test_exhausted_account_is_still_caught_up_to_today(monkeypatch):
    mono = _FakeMono([{"id": "A", "masked_pan": ["444111******2438"]}],
                     items_by_window=lambda acc, a, b: [{"id": "tx1", "meta": True}])
    saved, stored = _setup(monkeypatch, mono, {"A": {"exhausted": True, "newest_fetched": date(2026, 9, 2)}})
    sleeps = []
    res = mas.sync_recent(DB, sleeper=sleeps.append, now=NOW)
    # Від 30.08 (перекриття 3 дні) до 04.10 — два вікна по ≤31 дню.
    assert [c[1].date() for c in mono.calls] == [date(2026, 8, 30), date(2026, 9, 30)]
    assert mono.calls[-1][2] == NOW
    assert res["found"] == 2 and res["ok"]
    # Межа пишеться після КОЖНОГО вікна.
    assert [f["newest_fetched"] for _a, f in saved] == [date(2026, 9, 30), date(2026, 10, 4)]


def test_rate_limit_pause_between_every_request(monkeypatch):
    mono = _FakeMono([{"id": "A"}, {"id": "B"}])
    _setup(monkeypatch, mono, {"A": {"newest_fetched": date(2026, 10, 1)},
                               "B": {"newest_fetched": date(2026, 10, 1)}})
    sleeps = []
    res = mas.sync_recent(DB, sleeper=sleeps.append, now=NOW)
    # client-info + 2 вікна = 3 запити → 2 паузи по 62 с.
    assert res["requests"] == 3 and sleeps == [62, 62]


def test_one_failing_card_does_not_stop_others(monkeypatch):
    mono = _FakeMono([{"id": "A"}, {"id": "B"}], fail_for={"A"})
    saved, _ = _setup(monkeypatch, mono, {"A": {"newest_fetched": date(2026, 10, 1)},
                                          "B": {"newest_fetched": date(2026, 10, 1)}})
    res = mas.sync_recent(DB, sleeper=lambda s: None, now=NOW)
    assert not res["ok"]
    assert any(c[0] == "B" for c in mono.calls)
    assert ("A", {"masked_pan": "—", "last_error": "ліміт monobank: 1 запит на 60 с"}) in saved


def test_second_run_while_first_is_going_returns_busy(monkeypatch):
    assert mas._RECENT_LOCK.acquire(blocking=False)
    try:
        assert mas.sync_recent(DB, sleeper=lambda s: None)["busy"]
    finally:
        mas._RECENT_LOCK.release()


def test_new_card_walks_its_history_a_little_per_run(monkeypatch):
    mono = _FakeMono([{"id": "N"}])
    _setup(monkeypatch, mono, {})
    seen = {}
    monkeypatch.setattr(mas, "sync_account",
                        lambda db, acc, max_windows=None, sleeper=None, progress=None:
                        seen.update(max_windows=max_windows) or {"windows": 2, "found": 0})
    res = mas.sync_recent(DB, sleeper=lambda s: None, now=NOW)
    assert seen["max_windows"] == mas.NEW_ACCOUNT_WINDOWS_PER_RUN
    assert res["accounts"][0]["mode"] == "history"

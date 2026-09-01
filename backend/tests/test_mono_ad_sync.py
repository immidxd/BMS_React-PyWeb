"""Вивантаження виписки: пошук початку історії й відновлюваність.

Обидві властивості продиктовані реальністю, а не смаком: Personal API дає
31 день за запит і 1 запит на 60 секунд, а власник не пам'ятає, коли купив
першу рекламу.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.services import mono_ad_sync as sync


class _FakeDB:
    """Стан у пам'яті: те, що сервіс пише в mono_sync_state і meta_ad_charges."""

    def __init__(self):
        self.state = {}
        self.charges = {}
        self.commits = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        if sql.startswith("SELECT account_id, masked_pan"):
            row = self.state.get(params["a"])
            return _Res([row] if row else [])
        if sql.startswith("INSERT INTO mono_sync_state"):
            cur = self.state.setdefault(params["account_id"], {"account_id": params["account_id"]})
            cur.update({k: v for k, v in params.items() if k != "account_id"})
            return _Res([])
        if sql.startswith("INSERT INTO meta_ad_charges"):
            key = params["tx"]
            if key in self.charges:
                return _Res([])            # ON CONFLICT DO NOTHING
            self.charges[key] = dict(params)
            return _Res([(1,)])
        raise AssertionError(f"несподіваний запит: {sql[:80]}")

    def commit(self):
        self.commits += 1


class _Res:
    def __init__(self, rows): self._rows = rows
    def first(self): return self._rows[0] if self._rows else None
    def mappings(self): return self


def _charge(day, tx, uah):
    return {
        "bank_transaction_id": tx, "receipt_id": f"R-{tx}",
        "charge_date": day, "charged_at": datetime.combine(day, datetime.min.time(),
                                                           tzinfo=timezone.utc),
        "description": "Facebook", "mcc": 7311,
        "amount_uah": Decimal(uah), "operation_amount": Decimal("87.00"),
        "operation_currency": "USD",
    }


class _FakeMono:
    CHUNK_DAYS = 31
    SLEEP_BETWEEN_SEC = 62

    def __init__(self, windows):
        """`windows` — список списків операцій, від новіших до старіших."""
        self._windows = list(windows)
        self.calls = 0

    def statement_chunk(self, _acc, _since, _until):
        items = self._windows[self.calls] if self.calls < len(self._windows) else []
        self.calls += 1
        return items

    def meta_charges_from(self, items):
        return [_charge(date(2026, 8, 30), i["id"], "3897.67")
                for i in items if i.get("meta")]

    def accounts(self):
        return [{"id": "acc", "masked_pan": ["444111******6650"]}]


@pytest.fixture
def patched(monkeypatch):
    def _apply(fake):
        monkeypatch.setattr(sync, "_mono", lambda: fake)
        return fake
    return _apply


def test_history_stops_where_the_card_did_not_exist_yet(patched):
    """Два порожні вікна поспіль = картки тоді не було. Інакше прогін копав би
    у порожнечу роками по хвилині на вікно."""
    fake = patched(_FakeMono([
        [{"id": "a", "meta": True}],
        [{"id": "b"}],
        [],                              # порожньо — 1
        [],                              # порожньо — 2 → стоп
        [{"id": "c", "meta": True}],     # сюди вже не дійде
    ]))
    db = _FakeDB()
    result = sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)

    assert fake.calls == 4
    assert "без жодної операції" in result["stopped"]
    assert db.state["acc"]["exhausted"] is True


def test_a_month_without_ads_does_not_end_the_history(patched):
    """Порожнім вважається вікно БЕЗ ЖОДНОЇ операції, а не без рекламних.
    Місяць без реклами — звичайна річ, і обривати на ньому історію не можна."""
    fake = patched(_FakeMono([
        [{"id": "a", "meta": True}],
        [{"id": "b"}, {"id": "c"}],      # покупки є, реклами немає
        [{"id": "d"}],
        [{"id": "e", "meta": True}],     # і ось знову реклама
        [], [],
    ]))
    db = _FakeDB()
    sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)

    assert fake.calls == 6
    assert set(db.charges) == {"a", "e"}


def test_state_is_saved_after_every_window_so_a_break_costs_nothing(patched):
    patched(_FakeMono([[{"id": "a"}], [{"id": "b"}], [], []]))
    db = _FakeDB()
    sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)
    # Коміт після кожного вікна плюс фінальний «exhausted».
    assert db.commits >= 4
    assert db.state["acc"]["windows_done"] == 4


def test_a_second_run_continues_instead_of_starting_over(patched):
    fake = patched(_FakeMono([[{"id": "a"}]] * 10))
    db = _FakeDB()
    first = sync.sync_account(db, {"id": "acc", "masked_pan": []},
                              max_windows=3, sleeper=lambda _s: None)
    assert fake.calls == 3
    assert first["stopped"] == "ліміт вікон цього запуску"

    second = sync.sync_account(db, {"id": "acc", "masked_pan": []},
                               max_windows=3, sleeper=lambda _s: None)
    assert fake.calls == 6
    # Друга частина йде ДАЛІ в минуле, а не по тих самих вікнах.
    assert second["oldest"] < first["oldest"]


def test_an_exhausted_account_is_not_touched_again(patched):
    fake = patched(_FakeMono([[], []]))
    db = _FakeDB()
    sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)
    calls_after_first = fake.calls
    again = sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)
    assert fake.calls == calls_after_first
    assert again["skipped"] == "історію пройдено"


def test_the_same_charge_is_never_stored_twice(patched):
    """Відновлення неминуче проходить деякі вікна повторно. Дублікат тут
    означав би подвоєні витрати в аркуші."""
    patched(_FakeMono([[{"id": "same", "meta": True}], [{"id": "same", "meta": True}], [], []]))
    db = _FakeDB()
    result = sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)
    assert len(db.charges) == 1
    assert result["found"] == 1


def test_a_broken_window_keeps_what_was_already_read(patched):
    class _Broken(_FakeMono):
        def statement_chunk(self, acc, since, until):
            if self.calls == 2:
                self.calls += 1
                raise RuntimeError("ліміт monobank: 1 запит на 60 с")
            return super().statement_chunk(acc, since, until)

    patched(_Broken([[{"id": "a", "meta": True}], [{"id": "b"}], [], []]))
    db = _FakeDB()
    result = sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=lambda _s: None)

    assert "ліміт monobank" in result["error"]
    assert set(db.charges) == {"a"}                 # знайдене не втрачено
    assert db.state["acc"]["oldest_fetched"] is not None   # і поступ збережено


def test_the_pause_is_between_requests_not_before_the_first(patched):
    """Перший запит у запуску не має чекати хвилину дарма."""
    patched(_FakeMono([[{"id": "a"}], [], []]))
    slept = []
    db = _FakeDB()
    sync.sync_account(db, {"id": "acc", "masked_pan": []}, sleeper=slept.append)
    assert slept == [62, 62]

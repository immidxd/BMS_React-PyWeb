"""Пакетне розпізнавання завозу: черга, квота як очікування, скасування.

Перевіряємо саме поведінку воркера, а не модель: `run_one` підмінений, бо
жоден тест не має права викликати Gemini або торкатись бойової БД.
"""
from __future__ import annotations

import sys
import time
import types
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from services import autofill_batch as ab


def _wait_done(job_id: str, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = ab.get(job_id)
        if job and job["state"] not in ("running", "waiting"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"задача {job_id} не завершилась за {timeout} с: {ab.get(job_id)}")


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch):
    """Без пауз між товарами й без спільного стану між тестами."""
    monkeypatch.setattr(ab, "PAUSE_BETWEEN_S", 0.0)
    ab._JOBS.clear()
    ab._ORDER.clear()
    yield
    ab._JOBS.clear()
    ab._ORDER.clear()


def _stub(monkeypatch, *, run_one, quota=None):
    """Підмінити те, що воркер імпортує ЛІНИВО (всередині `_run`).

    ⚠️ Саме АТРИБУТИ пакета, а не `sys.modules`. `from services import ai_quota`
    бере атрибут пакета, якщо модуль уже імпортовано, — а в повному прогоні його
    імпортували сусідні тести. Підміна лише в `sys.modules` тоді мовчки не діє:
    поодинці тест проходив, разом з усіма — падав.
    """
    quota = quota or {"free": {"exhausted": False, "retry_after_s": 0}, "month": {"allowed": True}}
    import models.database as _db
    import services as _services
    monkeypatch.setattr(_db, "SessionLocal", lambda: _FakeSession(), raising=False)
    monkeypatch.setattr(_services, "ai_quota",
                        types.SimpleNamespace(status=lambda db, **kw: quota), raising=False)
    monkeypatch.setattr(_services, "autofill_run",
                        types.SimpleNamespace(run_one=run_one), raising=False)
    monkeypatch.setattr(_services, "photo_autofill",
                        types.SimpleNamespace(paid_key_available=lambda: False), raising=False)
    # Той самий пакет під другим іменем (services.X і backend.services.X — два
    # різні об'єкти); воркер може прийти будь-яким із двох шляхів.
    try:
        import backend.services as _bservices
        import backend.models.database as _bdb
        monkeypatch.setattr(_bdb, "SessionLocal", lambda: _FakeSession(), raising=False)
        for name, mod in (("ai_quota", types.SimpleNamespace(status=lambda db, **kw: quota)),
                          ("autofill_run", types.SimpleNamespace(run_one=run_one)),
                          ("photo_autofill", types.SimpleNamespace(paid_key_available=lambda: False))):
            monkeypatch.setattr(_bservices, name, mod, raising=False)
    except ImportError:  # pragma: no cover
        pass


class _FakeSession:
    def execute(self, *a, **kw):
        return types.SimpleNamespace(fetchone=lambda: None, scalar=lambda: None)
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def test_batch_walks_every_product_and_counts_fields(monkeypatch):
    seen = []

    def run_one(db, pid, **kw):
        seen.append(pid)
        return {"ok": True, "proposed": [("style_name", "Класичний", 0.9)] * (pid % 3)}

    _stub(monkeypatch, run_one=run_one)
    job = ab.start([1, 2, 3], "Завіз тест")
    done = _wait_done(job["id"])
    assert seen == [1, 2, 3]
    assert done["state"] == "done" and done["done"] == 3
    # 1→1 поле, 2→2 поля, 3→0 полів
    assert done["proposed_fields"] == 3
    assert done["proposed_products"] == 2
    assert done["nothing"] == 1


def test_exhausted_daily_quota_stops_the_batch_and_says_so(monkeypatch):
    """Домовленість проєкту: вичерпана квота — це ОЧІКУВАННЯ, а не помилка.

    Двадцять разів отримати одну й ту саму відмову — це не наполегливість,
    а спалена квота. Пакет зупиняється й чесно каже, що лишилось на завтра.
    """
    calls = []
    _stub(monkeypatch, run_one=lambda db, pid, **kw: calls.append(pid) or {"ok": True},
          quota={"free": {"exhausted": True, "retry_after_s": 0}, "month": {"allowed": True}})
    job = ab.start([1, 2, 3], "Завіз тест")
    done = _wait_done(job["id"])
    assert calls == []                       # жодного виклику моделі
    assert done["state"] == "done"           # не «error»
    assert done["skipped"] == 3
    assert "квота" in (done["stop_reason"] or "").lower()


def test_monthly_cap_stops_the_batch(monkeypatch):
    _stub(monkeypatch, run_one=lambda db, pid, **kw: {"ok": True},
          quota={"free": {"exhausted": False, "retry_after_s": 0},
                 "month": {"allowed": False, "reason": "стеля $20"}})
    done = _wait_done(ab.start([1, 2], "Тест")["id"])
    assert done["skipped"] == 2 and "стеля" in (done["stop_reason"] or "")


def test_minute_limit_is_waited_out_not_failed(monkeypatch):
    """Хвилинна межа спільна на проєкт і минає сама — її перечікують."""
    monkeypatch.setattr(ab, "MAX_WAIT_S", 0.05)
    _stub(monkeypatch, run_one=lambda db, pid, **kw: {"ok": True, "proposed": [("a", "b", 1)]},
          quota={"free": {"exhausted": False, "retry_after_s": 30}, "month": {"allowed": True}})
    done = _wait_done(ab.start([1], "Тест")["id"])
    assert done["state"] == "done" and done["done"] == 1
    assert done["errors"] == 0


def test_second_batch_is_refused_while_one_runs(monkeypatch):
    """Квота спільна: два пакети паралельно лише швидше її спалять."""
    gate = {"open": False}

    def slow(db, pid, **kw):
        while not gate["open"]:
            time.sleep(0.01)
        return {"ok": True}

    _stub(monkeypatch, run_one=slow)
    first = ab.start([1, 2], "Перший")
    try:
        with pytest.raises(RuntimeError) as e:
            ab.start([3], "Другий")
        assert "Перший" in str(e.value)
    finally:
        gate["open"] = True
    _wait_done(first["id"])


def test_cancel_marks_the_rest_skipped(monkeypatch):
    started = {"n": 0}

    def run_one(db, pid, **kw):
        started["n"] += 1
        return {"ok": True}

    _stub(monkeypatch, run_one=run_one)
    monkeypatch.setattr(ab, "PAUSE_BETWEEN_S", 0.2)
    job = ab.start([1, 2, 3, 4], "Тест")
    time.sleep(0.05)
    assert ab.cancel(job["id"]) is True
    done = _wait_done(job["id"])
    assert done["state"] == "cancelled"
    assert done["done"] + done["skipped"] == 4
    assert started["n"] < 4


def test_failed_product_does_not_stop_the_rest(monkeypatch):
    def run_one(db, pid, **kw):
        if pid == 2:
            return {"ok": False, "failed": True, "reason": "Розпізнавання перервалось"}
        return {"ok": True, "proposed": [("a", "b", 1)]}

    _stub(monkeypatch, run_one=run_one)
    done = _wait_done(ab.start([1, 2, 3], "Тест")["id"])
    assert done["done"] == 3 and done["errors"] == 1 and done["proposed_products"] == 2
    assert done["state"] == "done"


def test_public_snapshot_hides_internals():
    """У відповідь API не мають потрапляти Event і сирий список id."""
    ab._JOBS["x"] = {"id": "x", "state": "done", "label": "l", "done": 0, "total": 0,
                     "_cancel": object(), "_ids": [1, 2]}
    ab._ORDER.append("x")
    snap = ab.get("x")
    assert "_cancel" not in snap and "_ids" not in snap
    assert snap["id"] == "x"


def test_duplicate_ids_are_collapsed(monkeypatch):
    seen = []
    _stub(monkeypatch, run_one=lambda db, pid, **kw: seen.append(pid) or {"ok": True})
    job = ab.start([5, 5, 7, 5], "Тест")
    _wait_done(job["id"])
    assert seen == [5, 7] and job["total"] == 2

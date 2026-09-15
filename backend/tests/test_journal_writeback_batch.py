"""Write-back кількох полів одного номера — ОДНИМ проходом по аркушу.

Прийняття пропозицій дає 5–10 полів на товар; поле за полем це були 2 читання
+ 1 запис на КОЖНЕ поле й «синхронізація з журналом» на пів хвилини.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.scripts import sheets_parser as sp
from backend.services import journal_sync


# ── Двійник Google Sheets ───────────────────────────────────────────────────

class _WS:
    def __init__(self, rows, fail_times=0):
        self.rows = rows
        self.reads = 0
        self.writes = []          # (value_input_option, updates)
        self.fail_times = fail_times
    def get_all_values(self):
        self.reads += 1
        return [list(r) for r in self.rows]
    def batch_update(self, updates, value_input_option=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("429 rate limit")
        self.writes.append((value_input_option, updates))


class _SH:
    def __init__(self, ws): self._ws = ws; self.opens = 0
    def worksheet(self, title):
        self.opens += 1
        if title == "missing":
            raise KeyError(title)
        return self._ws


def _sheet(monkeypatch, rows, **kw):
    ws = _WS(rows, **kw)
    sh = _SH(ws)
    monkeypatch.setattr(sp, "get_gc", lambda: SimpleNamespace(open_by_key=lambda k: sh))
    monkeypatch.setattr(sp, "_save_writeback_backup", lambda *a, **k: "/tmp/backup.json")
    monkeypatch.setattr(sp, "WRITEBACK_ENABLED", True)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    return ws, sh


HEADER = ["Номер", "Модель", "Маркування", "Тип підошви", "Розмір", "Ціна"]


def test_many_fields_cost_one_read_and_one_write(monkeypatch):
    ws, sh = _sheet(monkeypatch, [HEADER, ["#Ф1", "", "", "", "38", "1000"]])
    out = sp.writeback_fields_to_journal("Т", "#Ф1", {"model": "Gazelle", "price": 1500})
    assert out["ok"] and ws.reads == 1 and sh.opens == 1
    assert out["results"]["model"] == {"ok": True, "rows_updated": 1, "header": "Модель", "backup": "/tmp/backup.json"}
    assert out["results"]["price"]["ok"] and out["results"]["price"]["rows_updated"] == 1
    # текстове поле — RAW, ціна — USER_ENTERED: два пакети, але один прохід
    modes = sorted(m for m, _ in ws.writes)
    assert modes == ["RAW", "USER_ENTERED"]
    assert sum(len(u) for _, u in ws.writes) == 2


def test_each_field_gets_its_own_verdict(monkeypatch):
    """Поле без колонки і per-item поле на ростовці не тягнуть за собою решту."""
    ws, _ = _sheet(monkeypatch, [HEADER, ["#Ф1", "", "", "", "38", ""], ["#Ф1", "", "", "", "39", ""]])
    out = sp.writeback_fields_to_journal("Т", "#Ф1", {
        "model": "Gazelle", "no_such_field": "x", "sizeeu": "40", "price": "1200"})
    r = out["results"]
    assert r["model"]["ok"] and r["model"]["rows_updated"] == 2
    assert not r["no_such_field"]["ok"] and "no journal column" in r["no_such_field"]["reason"]
    assert not r["sizeeu"]["ok"] and "per-item field" in r["sizeeu"]["reason"]
    assert r["price"]["ok"] and r["price"]["rows_updated"] == 2
    assert ws.reads == 1


def test_already_current_needs_no_write(monkeypatch):
    ws, _ = _sheet(monkeypatch, [HEADER, ["#Ф1", "Gazelle", "", "", "38", "1000"]])
    out = sp.writeback_fields_to_journal("Т", "#Ф1", {"model": "Gazelle"})
    assert out["ok"] and out["results"]["model"]["rows_updated"] == 0 and ws.writes == []


def test_unknown_number_fails_every_field_permanently(monkeypatch):
    _sheet(monkeypatch, [HEADER, ["#Ф2", "", "", "", "", ""]])
    out = sp.writeback_fields_to_journal("Т", "#Ф1", {"model": "a", "marking": "b"})
    assert not out["ok"] and "not found" in out["reason"]
    assert all("not found" in v["reason"] for v in out["results"].values())


def test_write_failure_after_retries_marks_every_written_field(monkeypatch):
    ws, _ = _sheet(monkeypatch, [HEADER, ["#Ф1", "", "", "", "", ""]], fail_times=10)
    out = sp.writeback_fields_to_journal("Т", "#Ф1", {"model": "a", "marking": "b"})
    assert not out["ok"]
    assert all("sheet write failed" in v["reason"] for v in out["results"].values())


def test_single_field_wrapper_keeps_its_contract(monkeypatch):
    _sheet(monkeypatch, [HEADER, ["#Ф1", "", "", "", "", ""]])
    assert sp.writeback_field_to_journal("Т", "#Ф1", "model", "a")["rows_updated"] == 1
    assert "no journal column" in sp.writeback_field_to_journal("Т", "#Ф1", "zzz", "a")["reason"]


def test_own_write_is_noted_for_the_poller(monkeypatch):
    _sheet(monkeypatch, [HEADER, ["#Ф1", "", "", "", "", ""]])
    monkeypatch.setattr(sp, "_last_own_journal_write_at", 0.0)
    assert sp.seconds_since_own_journal_write() == float("inf")
    sp.writeback_fields_to_journal("Т", "#Ф1", {"model": "a"})
    assert sp.seconds_since_own_journal_write() < 5


# ── Воркер черги: пакет одного номера ───────────────────────────────────────

class _DB:
    def __init__(self): self.calls = []
    def execute(self, stmt, params=None):
        self.calls.append((" ".join(str(stmt).split()), params or {}))
        return SimpleNamespace(rowcount=1, first=lambda: None)
    def commit(self): pass


def _rows():
    mk = lambda i, f, v: SimpleNamespace(id=i, product_id=10, productnumber="#Ф1",
                                         sheet_title="Т", field=f, value=v, attempts=0, status="processing")
    return [mk(1, "model", "Gazelle"), mk(2, "zzz", "x"), mk(3, "marking", "AB")]


def test_batch_calls_the_sheet_once_and_judges_each_task(monkeypatch):
    seen = []
    def _batch(sheet, pnum, fv):
        seen.append((sheet, pnum, dict(fv)))
        return {"ok": True, "results": {
            "model": {"ok": True, "rows_updated": 1},
            "zzz": {"ok": False, "reason": "no journal column for 'zzz'"},
            "marking": {"ok": True, "rows_updated": 1}}}
    monkeypatch.setattr(journal_sync, "_sheets_parser",
                        lambda: SimpleNamespace(writeback_fields_to_journal=_batch))
    db = _DB()
    counts = journal_sync._process_batch(db, _rows())
    assert seen == [("Т", "#Ф1", {"model": "Gazelle", "zzz": "x", "marking": "AB"})]
    assert counts == {"done": 2, "skipped": 1}
    statuses = [p.get("err") for _s, p in db.calls if "skipped" in _s]
    assert statuses == ["no journal column for 'zzz'"]


def test_network_failure_delays_the_whole_batch(monkeypatch):
    def _batch(*a): raise ConnectionError("reset")
    monkeypatch.setattr(journal_sync, "_sheets_parser",
                        lambda: SimpleNamespace(writeback_fields_to_journal=_batch))
    counts = journal_sync._process_batch(_DB(), _rows())
    assert counts == {"retry": 3}


def test_old_parser_double_without_batch_fn_still_works(monkeypatch):
    monkeypatch.setattr(journal_sync, "_sheets_parser",
                        lambda: SimpleNamespace(writeback_field_to_journal=lambda *a: {"ok": True}))
    assert journal_sync._process_batch(_DB(), _rows()) == {"done": 3}


def test_enqueue_skips_a_value_already_in_flight():
    class _Inflight(_DB):
        def execute(self, stmt, params=None):
            self.calls.append((" ".join(str(stmt).split()), params or {}))
            return SimpleNamespace(rowcount=1, first=lambda: (1,))
    db = _Inflight()
    journal_sync.enqueue(db, 10, "#Ф1", "Т", "model", "Gazelle")
    assert not any("INSERT" in s for s, _ in db.calls)
    db2 = _DB()
    journal_sync.enqueue(db2, 10, "#Ф1", "Т", "model", "Gazelle")
    assert any("INSERT" in s for s, _ in db2.calls)


# ── Поллер: свій запис ≠ правка людини ──────────────────────────────────────

def test_own_write_does_not_trigger_a_full_parse():
    t = journal_sync.JournalChangeTracker(safety_sec=1800)
    assert t.observe("J", "t1", own=False) == "first"
    assert t.observe("J", "t1", own=False) == "unchanged"
    assert t.observe("J", "t2", own=True) == "own"          # наш write-back
    assert t.observe("J", "t3", own=False) == "changed"     # людина в аркуші
    assert t.skipped_own == 1


def test_safety_parse_waits_for_quiet_and_interval():
    t = journal_sync.JournalChangeTracker(safety_sec=1800)
    assert not t.safety_parse_due(9999, 9999)               # нічого не пропускали
    t.observe("J", "a", False); t.observe("J", "b", True)
    assert not t.safety_parse_due(600, 9999)                # парсили нещодавно
    assert not t.safety_parse_due(9999, 30)                 # воркер ще пише
    assert t.safety_parse_due(9999, 300)
    assert t.skipped_own == 0 and not t.safety_parse_due(9999, 300)


def test_kick_during_a_pass_is_not_lost(monkeypatch):
    passes = []
    monkeypatch.setattr(journal_sync, "COALESCE_SECONDS", 0.01)
    monkeypatch.setattr(journal_sync, "drain", lambda: passes.append(1) or {})
    journal_sync._worker_running = False; journal_sync._kick_pending = False
    journal_sync.kick(); journal_sync.kick(); journal_sync.kick()
    import time
    for _ in range(200):
        time.sleep(0.01)
        if not journal_sync._worker_running:
            break
    assert 1 <= len(passes) <= 2 and not journal_sync._worker_running


def test_revisions_expose_a_human_edit_hidden_between_our_writes():
    """modifiedTime бачить лише ОСТАННЬОГО автора; ревізії — кожного. Правка
    людини між двома нашими записами більше не маскується."""
    t = journal_sync.JournalChangeTracker()
    r = lambda ts, own: {"id": ts, "modifiedTime": f"2026-09-15T{ts}:00.000Z", "own": own}
    assert t.observe_revisions("J", [r("10:00", True)]) == "first"
    assert t.observe_revisions("J", [r("10:00", True)]) == "unchanged"
    assert t.observe_revisions("J", [r("10:00", True), r("10:05", True)]) == "own"
    # людина о 10:07, наш запис о 10:09 — за modifiedTime це виглядало б як «наш»
    assert t.observe_revisions("J", [r("10:00", True), r("10:05", True), r("10:07", False), r("10:09", True)]) == "changed"
    assert t.observe_revisions("J", [r("10:09", True), r("10:11", None)]) == "changed"   # без автора — людина
    assert t.skipped_own == 1

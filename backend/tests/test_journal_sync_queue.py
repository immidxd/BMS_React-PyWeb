"""Черга записів у журнал: що ретраїться, а що ні.

Головне, що тут захищено: виняток із запису в аркуш (падіння токена, SSL,
обрив мережі) НЕ має губитись. Раніше він губився — фоновий потік ловив його,
писав рядок у лог, і правка лишалась тільки в БД назавжди.
"""

from types import SimpleNamespace

from backend.services import journal_sync


class _FakeDB:
    """Ловить SQL-виклики замість справжньої сесії."""

    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return SimpleNamespace(rowcount=1)

    def commit(self):
        pass


def _row(attempts=0):
    return SimpleNamespace(id=1, product_id=10, productnumber="#Ф1",
                           sheet_title="01.05.2025(Андрій)", field="model",
                           value="Nartilla", attempts=attempts)


def _with_result(monkeypatch, result=None, exc=None):
    def _wb(sheet, pnum, field, value):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(journal_sync, "_sheets_parser",
                        lambda: SimpleNamespace(writeback_field_to_journal=_wb))


def test_successful_write_marks_done(monkeypatch):
    _with_result(monkeypatch, result={"ok": True, "rows_updated": 1})
    db = _FakeDB()
    assert journal_sync._process_one(db, _row()) == "done"
    assert "status='done'" in db.calls[0][0]


def test_network_failure_is_retried_not_lost(monkeypatch):
    # Саме цей клас помилок губився: виняток на етапі оновлення OAuth-токена.
    _with_result(monkeypatch, exc=OSError("SSL: no certificate or crl found"))
    db = _FakeDB()
    assert journal_sync._process_one(db, _row()) == "retry"
    sql, params = db.calls[0]
    assert params["a"] == 1
    assert params["delay"] == str(journal_sync.BACKOFF_SECONDS[0])


def test_retries_run_out_into_failed(monkeypatch):
    _with_result(monkeypatch, exc=OSError("connection reset"))
    db = _FakeDB()
    last = journal_sync.MAX_ATTEMPTS - 1
    assert journal_sync._process_one(db, _row(attempts=last)) == "failed"
    assert "status='failed'" in db.calls[0][0]


def test_missing_column_is_not_retried_forever(monkeypatch):
    _with_result(monkeypatch, result={"ok": False,
                                      "reason": "no journal column for 'foo'"})
    db = _FakeDB()
    assert journal_sync._process_one(db, _row()) == "skipped"


def test_rostovka_per_item_guard_is_permanent(monkeypatch):
    _with_result(monkeypatch, result={
        "ok": False,
        "reason": "per-item field 'sizeeu' skipped: 3 rostovka rows share number Ф1"})
    db = _FakeDB()
    assert journal_sync._process_one(db, _row()) == "skipped"


def test_classify_treats_unknown_errors_as_transient():
    assert journal_sync._classify("exception: Max retries exceeded") == "retry"
    assert journal_sync._classify("no sheet_title (product has no delivery)") == "skipped"

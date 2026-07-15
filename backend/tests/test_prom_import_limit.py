from datetime import datetime, timedelta, timezone

from backend.services import prom_service


def _state(hit, success=None, since=None, imports=0):
    return {
        "imports_today": imports,
        "first_at": success,
        "last_at": success,
        "limit_hits_today": 1 if hit else 0,
        "last_hit_at": hit,
        "limit_since_at": since or hit,
    }


def test_limit_window_uses_first_hit_without_extending_on_retries():
    hit = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    result = prom_service._limit_window(
        _state(hit, since=hit),
        now=hit + timedelta(hours=1),
    )

    assert result["active"] is True
    assert result["retry_at"] == hit + timedelta(hours=2)
    assert result["retry_in_seconds"] == 3600


def test_success_after_rejection_clears_warning_immediately():
    hit = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    success = hit + timedelta(minutes=5)

    result = prom_service._limit_window(
        _state(hit, success=success),
        now=success + timedelta(minutes=1),
    )

    assert result["active"] is False
    assert result["cleared_by_success"] is True
    assert result["retry_at"] is None


def test_limit_window_expires_after_two_hours():
    hit = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    result = prom_service._limit_window(
        _state(hit),
        now=hit + timedelta(hours=2, seconds=1),
    )

    assert result["active"] is False
    assert result["retry_in_seconds"] == 0


def test_warning_does_not_claim_zero_imports(monkeypatch):
    hit = datetime.now(timezone.utc) - timedelta(minutes=30)
    monkeypatch.setattr(prom_service, "_ensure_import_log", lambda _db: None)
    monkeypatch.setattr(prom_service, "_limit_status", lambda _db: _state(hit, imports=0))

    result = prom_service.import_limit_status(object())

    assert result["limit_active"] is True
    assert result["limit_retry_at"]
    assert "0 імпорт" not in result["limit_warning"]
    assert "денний ліміт" not in result["limit_warning"].lower()


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar(self):
        return self.value


class _FakeDb:
    def __init__(self, data_type="bigint"):
        self.data_type = data_type
        self.calls = []
        self.committed = False
        self.rolled_back = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "information_schema.columns" in sql:
            return _ScalarResult(self.data_type)
        return _ScalarResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_import_log_schema_migrates_string_ids():
    db = _FakeDb(data_type="bigint")

    prom_service._ensure_import_log(db)

    assert db.committed is True
    assert db.rolled_back is False
    assert any("ALTER COLUMN import_id TYPE varchar(120)" in sql for sql, _ in db.calls)
    assert any("ADD COLUMN IF NOT EXISTS completed_at" in sql for sql, _ in db.calls)


def test_log_import_preserves_opaque_prom_id():
    db = _FakeDb(data_type="character varying")

    prom_service._log_import(db, "opaque-import-id-123", "batch×2", ["A", "B"])

    insert = next(params for sql, params in db.calls if "INSERT INTO prom_import_log" in sql)
    assert insert["i"] == "opaque-import-id-123"
    assert insert["n"] == "batch×2"
    assert insert["s"] == "A,B"


def test_mark_completed_updates_existing_row_without_counting_again():
    db = _FakeDb(data_type="character varying")

    prom_service._mark_import_completed(db, "opaque-import-id-123")

    assert db.committed is True
    assert not any("INSERT INTO prom_import_log" in sql for sql, _ in db.calls)
    update = next((sql, params) for sql, params in db.calls if "UPDATE prom_import_log" in sql)
    assert "completed_at = COALESCE(completed_at, now())" in update[0]
    assert update[1]["import_id"] == "opaque-import-id-123"

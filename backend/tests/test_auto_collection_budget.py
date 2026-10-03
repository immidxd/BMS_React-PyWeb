"""Бюджет Neon: фоновий цикл Топ-9 (кожні 5 хв) не будить хмару щоразу."""
from backend.services import auto_collection_cloud_sync as acs


def _level(monkeypatch, level):
    try:
        from backend.services import cloud_budget as a
    except ImportError:  # pragma: no cover
        a = None
    try:
        from services import cloud_budget as b
    except ImportError:
        b = None
    for mod in {id(m): m for m in (a, b) if m}.values():
        monkeypatch.setattr(mod, "level", lambda: level)


def test_background_pull_at_most_once_per_period(monkeypatch):
    _level(monkeypatch, "ok")
    monkeypatch.setenv("AUTO_COLLECTION_CLOUD_PULL_SEC", "21600")
    monkeypatch.setattr(acs, "_LAST_BACKGROUND_PULL", 0.0)
    assert acs.background_pull_due()
    acs.mark_background_pull()
    assert not acs.background_pull_due()          # наступні 5-хв цикли — без хмари


def test_stop_level_blocks_cloud(monkeypatch):
    _level(monkeypatch, "stop")
    monkeypatch.setattr(acs, "_LAST_BACKGROUND_PULL", 0.0)
    assert not acs.background_pull_due()
    assert not acs.cloud_wake_allowed()
    monkeypatch.setenv("AUTO_COLLECTION_CLOUD_DATABASE_URL", "postgresql://x/y")
    assert acs.trigger("ui") is False             # навіть дія з UI не будить хмару на stop


def test_engine_does_not_keep_connections(monkeypatch):
    from sqlalchemy.pool import NullPool
    monkeypatch.setattr(acs, "_ENGINE", None)
    monkeypatch.setenv("AUTO_COLLECTION_CLOUD_DATABASE_URL", "postgresql+psycopg2://u:p@localhost:1/db")
    assert isinstance(acs._engine().pool, NullPool)
    monkeypatch.setattr(acs, "_ENGINE", None)

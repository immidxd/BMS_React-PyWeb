"""Безкоштовний ліміт Neon: рівні бюджету й те, як вони гальмують синк каталогу."""
import datetime as dt
import threading
import time

from backend.services import catalog_sync_service as css
from backend.services import cloud_budget

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 16, tzinfo=UTC)
PERIOD = {"consumption_period_start": "2026-10-01T00:00:00Z", "consumption_period_end": "2026-11-01T00:00:00Z"}


def _cu(hours):
    return {**PERIOD, "compute_time_seconds": hours * 3600}


def test_levels_by_used_share():
    assert cloud_budget.compute_status(_cu(10), NOW)["level"] == "ok"
    assert cloud_budget.compute_status(_cu(61), NOW)["level"] == "warn"
    assert cloud_budget.compute_status(_cu(76), NOW)["level"] == "economy"
    st = cloud_budget.compute_status(_cu(91), NOW)
    assert st["level"] == "stop" and st["percent"] == 91.0 and st["used_cu_hours"] == 91.0


def test_pace_towards_limit_warns_early():
    # 50 CU-год за пів місяця → прогноз ≈100 ≥ 90% ліміту → попередження вже зараз
    st = cloud_budget.compute_status(_cu(50), NOW)
    assert st["level"] == "warn" and st["projected_cu_hours"] > 90


def test_september_incident_would_stop():
    # агент друку не давав заснути: 0.25 CU цілодобово → на 16-й день уже ~90 CU-год
    st = cloud_budget.compute_status(_cu(0.25 * 24 * 15), NOW)
    assert st["level"] == "stop"


def test_unconfigured_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUD_BUDGET_STATE", str(tmp_path / "b.json"))
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    monkeypatch.delenv("NEON_PROJECT_ID", raising=False)
    st = cloud_budget.refresh()
    assert st["level"] == "unknown" and not st["configured"]
    assert (tmp_path / "b.json").is_file()      # файл стану читає sync_to_cloud.py


def _fake_runtime(monkeypatch, tmp_path, level):
    py, script = tmp_path / "python", tmp_path / "sync.py"
    py.write_text(""); script.write_text("")
    monkeypatch.setattr(css, "_sync_paths", lambda: (tmp_path, py, script))
    monkeypatch.setattr(cloud_budget, "level", lambda: level)
    monkeypatch.setattr(css, "_LAST_START", 0.0)
    monkeypatch.setattr(css, "_RUNNING", False)
    monkeypatch.setattr(css, "_PENDING", None)
    monkeypatch.setattr(css, "_TIMER", None)
    runs = []
    done = threading.Event()
    monkeypatch.setattr(css, "_run_once", lambda *a: (runs.append(a[3]), done.set()))
    return runs, done


def test_stop_level_never_wakes_cloud(monkeypatch, tmp_path):
    runs, _ = _fake_runtime(monkeypatch, tmp_path, "stop")
    assert css.trigger_catalog_cloud_sync("toggle", urgent=True) is False
    assert css.trigger_catalog_cloud_sync("parse", urgent=False) is False
    assert runs == []


def test_auto_sync_is_rate_limited_but_not_lost(monkeypatch, tmp_path):
    runs, done = _fake_runtime(monkeypatch, tmp_path, "ok")
    monkeypatch.setenv("CATALOG_SYNC_MIN_INTERVAL_SEC", "0.3")
    assert css.trigger_catalog_cloud_sync("parse 1", urgent=False)
    assert done.wait(2); time.sleep(0.05)
    for i in range(5):                       # серія парсингів за інтервал → один відкладений синк
        assert css.trigger_catalog_cloud_sync(f"parse {i + 2}", urgent=False)
    assert len(runs) == 1
    time.sleep(0.8)
    assert len(runs) == 2 and "parse 6" in runs[1]


def test_user_toggle_is_immediate_when_budget_ok(monkeypatch, tmp_path):
    runs, done = _fake_runtime(monkeypatch, tmp_path, "warn")
    monkeypatch.setattr(css, "_LAST_START", time.time())   # щойно був синк
    assert css.trigger_catalog_cloud_sync("toggle", urgent=True)
    assert done.wait(2) and runs == ["toggle"]

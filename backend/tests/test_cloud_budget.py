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


import pytest


@pytest.fixture
def free_plan(monkeypatch):
    monkeypatch.setenv("NEON_PLAN", "free")


def test_levels_by_used_share(free_plan):
    assert cloud_budget.compute_status(_cu(10), NOW)["level"] == "ok"
    assert cloud_budget.compute_status(_cu(61), NOW)["level"] == "warn"
    assert cloud_budget.compute_status(_cu(76), NOW)["level"] == "economy"
    st = cloud_budget.compute_status(_cu(91), NOW)
    assert st["level"] == "stop" and st["percent"] == 91.0 and st["used_cu_hours"] == 91.0


def test_pace_towards_limit_warns_early(free_plan):
    # 50 CU-год за пів місяця → прогноз ≈100 ≥ 90% ліміту → попередження вже зараз
    st = cloud_budget.compute_status(_cu(50), NOW)
    assert st["level"] == "warn" and st["projected_cu_hours"] > 90


def test_september_incident_would_stop(free_plan):
    # агент друку не давав заснути: 0.25 CU цілодобово → на 16-й день уже ~90 CU-год
    st = cloud_budget.compute_status(_cu(0.25 * 24 * 15), NOW)
    assert st["level"] == "stop"


# ── План Launch: бюджет у доларах ($3 за замовчуванням) ────────────────────
def _usd(cu_hours, storage_gb=0.0):
    return {**_cu(cu_hours), "synthetic_storage_size": storage_gb * 1e9}


def test_launch_budget_in_dollars(monkeypatch):
    monkeypatch.delenv("NEON_PLAN", raising=False)
    st = cloud_budget.compute_status(_usd(5, 0.3), NOW)        # 5×0.106 + 0.3×0.35 = 0.635 $
    assert st["plan"] == "launch" and st["budget_usd"] == 3.0 and st["cost_usd"] == 0.64
    assert st["level"] == "ok"
    assert cloud_budget.compute_status(_usd(17), NOW)["level"] == "warn"      # 1.80 $ = 60%
    assert cloud_budget.compute_status(_usd(26), NOW)["level"] == "stop"      # 2.76 $ ≥ 90%
    # дозвіл власника +2 $ → межа 5 $, та сама витрата вже не «stop»
    assert cloud_budget.compute_status(_usd(26), NOW, extra_usd=2)["level"] == "warn"


def test_storage_counts_for_whole_month(monkeypatch):
    monkeypatch.delenv("NEON_PLAN", raising=False)
    st = cloud_budget.compute_status(_usd(0, 2.0), NOW)        # 2 ГБ × 0.35 = 0.70 $ навіть без compute
    assert st["storage_usd"] == 0.7 and st["cost_usd"] == 0.7


def test_september_incident_on_launch_caps_early(monkeypatch):
    monkeypatch.delenv("NEON_PLAN", raising=False)
    # 0.25 CU цілодобово: межу 2.70 $ досягнуто вже за ~4.2 доби (а не 21 $ за місяць)
    early = dt.datetime(2026, 10, 5, 6, tzinfo=UTC)
    assert cloud_budget.compute_status(_usd(0.25 * 24 * 4.25), early)["level"] == "stop"


def _consumption(daily_values):
    """Формат реальної відповіді Neon (знімок 03.10.2026)."""
    return {"projects": [{"project_id": "plain-breeze-73014199", "periods": [{
        "period_id": "p", "period_plan": "launch", "period_start": "2026-10-01T00:00:00Z",
        "consumption": [{"timeframe_start": f"2026-10-0{i + 1}T00:00:00Z",
                         "timeframe_end": f"2026-10-0{i + 2}T00:00:00Z",
                         "metrics": [{"metric_name": "compute_unit_seconds", "value": v},
                                     {"metric_name": "root_branch_bytes_month", "value": 1081344}]}
                        for i, v in enumerate(daily_values)]}]}]}


def test_metric_sum_real_neon_response():
    # 165 + 452 CU-с за 1–2 жовтня; root_branch_bytes_month не плутаємо з compute
    assert cloud_budget._metric_sum(_consumption([165, 452]), "compute_unit_seconds") == 617


def test_consumption_daily_plus_today_hourly(monkeypatch):
    monkeypatch.setenv("NEON_PROJECT_ID", "plain-breeze-73014199")
    calls = []

    def api(method, path, body=None, params=None):
        calls.append(params)
        return _consumption([1000] if params["granularity"] == "daily" else [200])
    monkeypatch.setattr(cloud_budget, "_api", api)
    start = dt.datetime(2026, 10, 1, tzinfo=UTC)
    now = dt.datetime(2026, 10, 3, 18, 40, tzinfo=UTC)
    assert cloud_budget.consumption_cu_seconds({"org_id": "org-x"}, start, now) == 1200
    assert [c["granularity"] for c in calls] == ["daily", "hourly"]
    assert calls[0]["from"] == "2026-10-01T00:00:00Z" and calls[0]["to"] == "2026-10-03T00:00:00Z"
    assert calls[1]["from"] == "2026-10-03T00:00:00Z" and calls[1]["to"] == "2026-10-03T18:00:00Z"
    assert all(c["org_id"] == "org-x" for c in calls)


class _FakeNeon:
    """Підміна Neon API: проєкт із заданою витратою + ендпоінти з прапором disabled."""
    def __init__(self, cu_hours):
        self.cu_hours, self.eps, self.calls = cu_hours, [{"id": "ep-1", "disabled": False}], []

    def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, body, params))
        if path == "":   # як на живому Launch-плані: поля проєкту нульові, org_id є
            return {"project": {**PERIOD, "compute_time_seconds": 0, "cpu_used_sec": 0,
                                "synthetic_storage_size": 0, "org_id": "org-test"}}
        if path.startswith("/consumption_history"):
            assert params["org_id"] == "org-test"
            value = self.cu_hours * 3600 if params["granularity"] == "daily" else 0
            return _consumption([value])
        if path == "/endpoints":
            return {"endpoints": [dict(e) for e in self.eps]}
        if method == "PATCH":
            self.eps[0]["disabled"] = body["endpoint"]["disabled"]
            return {}
        raise AssertionError(path)


def test_hard_cap_disables_and_owner_permit_reenables(monkeypatch, tmp_path):
    monkeypatch.delenv("NEON_PLAN", raising=False)
    monkeypatch.setenv("CLOUD_BUDGET_STATE", str(tmp_path / "b.json"))
    monkeypatch.setenv("NEON_API_KEY", "k"); monkeypatch.setenv("NEON_PROJECT_ID", "p")
    monkeypatch.setattr(cloud_budget, "_state", {})
    neon = _FakeNeon(cu_hours=0.5)
    monkeypatch.setattr(cloud_budget, "_api", neon)
    assert cloud_budget.refresh()["level"] == "ok" and not neon.eps[0]["disabled"]
    neon.cu_hours = 26                                           # 2.76 $ ≥ 90% з 3 $
    st = cloud_budget.refresh()
    assert st["level"] == "stop" and st["capped"] and neon.eps[0]["disabled"]
    assert not cloud_budget.cloud_wake_allowed()
    st = cloud_budget.refresh()                                  # без дозволу — лишається вимкненою
    assert st["capped"] and neon.eps[0]["disabled"]
    st = cloud_budget.permit(2)                                  # власник: +2 $
    assert st["level"] != "stop" and not st["capped"] and not neon.eps[0]["disabled"]
    assert st["budget_usd"] == 5.0


def test_cap_can_be_switched_to_notify_only(monkeypatch, tmp_path):
    monkeypatch.delenv("NEON_PLAN", raising=False)
    monkeypatch.setenv("NEON_HARD_CAP", "0")
    monkeypatch.setenv("CLOUD_BUDGET_STATE", str(tmp_path / "b.json"))
    monkeypatch.setenv("NEON_API_KEY", "k"); monkeypatch.setenv("NEON_PROJECT_ID", "p")
    monkeypatch.setattr(cloud_budget, "_state", {})
    neon = _FakeNeon(cu_hours=30)
    monkeypatch.setattr(cloud_budget, "_api", neon)
    assert cloud_budget.refresh()["level"] == "stop" and not neon.eps[0]["disabled"]


def test_unconfigured_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUD_BUDGET_STATE", str(tmp_path / "b.json"))
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    monkeypatch.delenv("NEON_PROJECT_ID", raising=False)
    monkeypatch.setattr(cloud_budget, "_state", {})
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

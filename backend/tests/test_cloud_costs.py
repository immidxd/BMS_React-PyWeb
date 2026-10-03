"""Витрати на серверну частину: ціни, розбір відповідей провайдерів, розподіл за напрямами."""
import datetime as dt

import pytest

from backend.services import cloud_costs as cc

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 16, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUD_COSTS_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "_state", {})
    monkeypatch.setattr(cc, "usd_uah_rate", lambda prev=None: 41.5)
    for k in ("RAILWAY_API_TOKEN", "RAILWAY_PROJECT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
              "CLOUDFLARE_WORKERS_PLAN", "RAILWAY_PLAN_FEE_USD", "RAILWAY_INCLUDED_USAGE_USD"):
        monkeypatch.delenv(k, raising=False)


def test_railway_prices_and_hobby_credit():
    # 0.1 vCPU і 0.5 ГБ цілодобово 30 днів: 0.1*43200*20/43200=2.0 $ + 0.5*43200*10/43200=5.0 $
    usage = cc.railway_cost_from_usage({"CPU_USAGE": 0.1 * 43200, "MEMORY_USAGE_GB": 0.5 * 43200})
    assert round(usage, 2) == 7.0
    assert cc.railway_bill(3.0) == 5.0           # в межах кредиту — лише абонплата
    assert round(cc.railway_bill(usage), 2) == 7.0   # 5 + (7 − 5)


def test_r2_and_workers_free_tiers():
    assert cc.r2_cost(3.0, 50_000, 2_000_000) == 0.0
    assert round(cc.r2_cost(12.0, 1_000_000, 12_000_000), 3) == round(2 * 0.015 + 2 * 0.36, 3)
    assert cc.workers_cost(5_000_000) == 0.0     # Free: не тарифікується


def test_railway_service_parses_usage(monkeypatch):
    monkeypatch.setenv("RAILWAY_API_TOKEN", "t"); monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    seen = {}

    def post(url, token, payload, timeout=20):
        seen.update(payload["variables"])
        return {"usage": [{"measurement": "CPU_USAGE", "value": 600}, {"measurement": "MEMORY_USAGE_GB", "value": 3000},
                          {"measurement": "NETWORK_TX_GB", "value": 1.5}]}
    monkeypatch.setattr(cc, "_post_json", post)
    svc = cc.railway_service(NOW)
    assert svc["status"] == "ok" and svc["cost_usd"] == 5.0          # usage ≈ $1.04 < кредит $5
    assert seen["p"] == "p" and seen["s"] == "2026-10-01T00:00:00Z"
    assert {u["label"] for u in svc["usage"]} >= {"CPU", "Памʼять", "Вихідний трафік"}


def test_cloudflare_parses_workers_and_r2(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t"); monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setattr(cc, "_post_json", lambda *a, **k: {"viewer": {"accounts": [{
        "workersInvocationsAdaptive": [
            {"sum": {"requests": 4000, "errors": 0}, "dimensions": {"scriptName": "bms-auto-collection-drafts"}},
            {"sum": {"requests": 20000, "errors": 1}, "dimensions": {"scriptName": "bms-instagram-dispatcher"}}],
        "r2OperationsAdaptiveGroups": [
            {"sum": {"requests": 1200}, "dimensions": {"actionType": "PutObject"}},
            {"sum": {"requests": 90000}, "dimensions": {"actionType": "GetObject"}},
            {"sum": {"requests": 50}, "dimensions": {"actionType": "DeleteObject"}}],
        "r2StorageAdaptiveGroups": [
            {"max": {"payloadSize": 2.5e9, "metadataSize": 1e6, "objectCount": 9000}, "dimensions": {"bucketName": "photos"}},
            {"max": {"payloadSize": 2.0e9, "metadataSize": 1e6, "objectCount": 8000}, "dimensions": {"bucketName": "photos"}}],
    }]}})
    workers, r2 = cc.cloudflare_services(NOW)
    assert workers["usage"][0]["value"] == 24000 and workers["cost_usd"] == 0.0
    r2u = {u["label"]: u["value"] for u in r2["usage"]}
    assert r2u["Запис/список (Class A)"] == 1200 and r2u["Читання (Class B)"] == 90000   # Delete — безкоштовний
    assert r2u["Зберігання"] == round(2.501, 3)                       # найсвіжіший рядок бакета
    assert r2["cost_usd"] == 0.0


def test_summarize_splits_by_area_and_manual_items():
    services = [cc._service("neon", "Neon", cost_usd=1.0, projected_usd=2.0),
                cc._service("railway", "Railway", cost_usd=5.0, projected_usd=5.0)]
    cfg = cc.load_config()
    cfg["manual"] = [{"name": "Домен", "usd_month": 1.5, "area": "catalog"}]
    out = cc.summarize(services, cfg, None, None)
    areas = {a["key"]: a for a in out["areas"]}
    assert areas["catalog"]["cost_usd"] == round(1.0 * 0.6 + 5.0 * 0.7 + 1.5, 2)
    assert areas["warehouse"]["cost_usd"] == round(1.0 * 0.3 + 5.0 * 0.3, 2)
    assert areas["bms"]["cost_usd"] == round(1.0 * 0.1, 2)
    assert out["total"]["cost_usd"] == 7.5
    assert round(sum(a["cost_usd"] for a in out["areas"]), 2) == out["total"]["cost_usd"]


def test_config_saved_and_validated():
    cc.save_config(allocation={"railway": {"catalog": 1, "warehouse": 1, "bms": 0}},
                   manual=[{"name": "Домен", "usd_month": "2", "area": "nowhere"}, {"name": ""}])
    cfg = cc.load_config()
    assert cfg["allocation"]["railway"] == {"catalog": 1.0, "warehouse": 1.0, "bms": 0.0}
    assert cfg["manual"] == [{"name": "Домен", "usd_month": 2.0, "area": "bms"}]
    with pytest.raises(ValueError):
        cc.save_config(allocation={"neon": {"catalog": 0, "warehouse": 0, "bms": 0}})


def test_refresh_survives_failing_provider(monkeypatch):
    def neon_service(now):
        return cc._service("neon", "Neon", cost_usd=0.5, projected_usd=1.0)

    def railway_service(now):   # імʼя важливе: з нього береться ключ сервісу при збої
        raise RuntimeError("Railway недоступний")
    monkeypatch.setattr(cc, "PROVIDERS", [neon_service, railway_service])
    st = cc.refresh(NOW)
    keys = {s["key"]: s for s in st["services"]}
    assert keys["neon"]["cost_usd"] == 0.5
    assert keys["railway"]["status"] == "error" and "недоступний" in keys["railway"]["error"]
    assert cc.status()["total"]["cost_usd"] == st["total"]["cost_usd"]

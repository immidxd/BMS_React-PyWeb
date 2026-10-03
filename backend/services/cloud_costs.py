"""Витрати на серверну частину бізнесу: Neon, Railway, Cloudflare, AI + ручні пункти.

Одна сторінка «Статистика → Сервери й хмара»: скільки грошей пішло цього місяця на
кожен сервіс і на кожен напрям — Каталог (вітрина), Склад (Mini App), BMS — і всього.

⚠️ ЖОРСТКЕ ПРАВИЛО (CLAUDE.md): моніторинг НЕ будить хмарну БД. Усі джерела —
білінгові/аналітичні API провайдерів (control plane) і локальна БД BMS. Оновлення —
раз на 30 хв у фоні або кнопкою; сторінка читає лише кеш.

Розподіл за напрямами: Каталог і Склад живуть на ОДНОМУ сервері (Railway) і ОДНІЙ базі
(Neon), тож частку не виміряти — її задає власник відсотками (дефолти нижче, правка на
сторінці; зберігається в ~/.bms/cloud_costs_config.json).

Налаштування (.env):
  Neon       — див. services/cloud_budget.py (NEON_API_KEY Org-wide, NEON_PROJECT_ID)
  Railway    — RAILWAY_API_TOKEN (Account → Tokens), RAILWAY_PROJECT_ID
               RAILWAY_PLAN_FEE_USD=5, RAILWAY_INCLUDED_USAGE_USD=5 (Hobby: $5 з $5 кредиту)
  Cloudflare — CLOUDFLARE_API_TOKEN (права Account Analytics: Read), CLOUDFLARE_ACCOUNT_ID
               CLOUDFLARE_WORKERS_PLAN=free|paid
Ціни — в PRICES (змінюються рідко; правка проходить ревʼю).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

AREAS = {"catalog": "Каталог", "warehouse": "Склад", "bms": "BMS"}

# $ — публічні тарифи (жовтень 2026). Одиниці — як віддають API провайдерів.
PRICES = {
    # Railway: CPU $20/vCPU-міс, RAM $10/ГБ-міс (за хвилину), egress $0.05/ГБ, диск $0.15/ГБ-міс
    "railway_cpu_min": 20 / 43200, "railway_mem_gb_min": 10 / 43200,
    "railway_egress_gb": 0.05, "railway_disk_gb_min": 0.15 / 43200,
    # Cloudflare R2 (понад безкоштовне): $0.015/ГБ-міс, $4.50/млн Class A, $0.36/млн Class B
    "r2_gb_month": 0.015, "r2_class_a_m": 4.50, "r2_class_b_m": 0.36,
    # Workers Paid: $5/міс + $0.30/млн понад 10 млн
    "workers_paid_fee": 5.0, "workers_req_m": 0.30,
}
FREE = {"r2_gb": 10, "r2_class_a": 1_000_000, "r2_class_b": 10_000_000,
        "workers_req_day": 100_000, "workers_paid_req_month": 10_000_000}

# Дефолтний розподіл (%) — власник змінює на сторінці.
DEFAULT_ALLOCATION: Dict[str, Dict[str, float]] = {
    "neon": {"catalog": 60, "warehouse": 30, "bms": 10},        # вітрина, склад, Топ-9 з BMS
    "railway": {"catalog": 70, "warehouse": 30, "bms": 0},      # один сервер: вітрина + /wh
    "cloudflare_workers": {"catalog": 0, "warehouse": 0, "bms": 100},  # диспетчери, Топ-9
    "cloudflare_r2": {"catalog": 70, "warehouse": 0, "bms": 30},       # фото: показ / завантаження
    "ai": {"catalog": 0, "warehouse": 0, "bms": 100},           # автозаповнення карток
}

_lock = threading.Lock()
_state: Dict[str, Any] = {}


# ─────────────────────────────── службове ────────────────────────────────────

def _bms_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("CLOUD_COSTS_DIR", "~/.bms")))


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("cloud_costs: не записано %s: %s", path, exc)


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def month_bounds(now: Optional[_dt.datetime] = None) -> tuple[_dt.datetime, _dt.datetime]:
    now = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    return start, end


def _projection(cost: float, now: _dt.datetime, start: _dt.datetime, end: _dt.datetime) -> float:
    elapsed = max((now - start).total_seconds(), 86400.0)
    return cost * (end - start).total_seconds() / elapsed


def _iso(t: _dt.datetime) -> str:
    return t.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_json(url: str, token: str, payload: dict, timeout: int = 20) -> Dict[str, Any]:
    import requests
    r = requests.post(url, json=payload, timeout=timeout,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("errors"):
        raise RuntimeError("; ".join(str(e.get("message", e)) for e in data["errors"])[:300])
    return data.get("data") or {}


def _service(key: str, name: str, **kw: Any) -> Dict[str, Any]:
    base = {"key": key, "name": name, "status": "ok", "cost_usd": 0.0, "projected_usd": 0.0,
            "usage": [], "note": None, "error": None, "setup": None}
    base.update(kw)
    return base


# ─────────────────────────────── провайдери ──────────────────────────────────

def neon_service(now: _dt.datetime) -> Dict[str, Any]:
    try:
        from backend.services import cloud_budget
    except ImportError:
        from services import cloud_budget
    st = cloud_budget.status(auto_refresh=False)
    if not st.get("configured"):
        return _service("neon", "Neon (база каталогу й складу)", status="not_configured",
                        setup="NEON_API_KEY (Org-wide) і NEON_PROJECT_ID у .env")
    svc = _service("neon", "Neon (база каталогу й складу)",
                   cost_usd=float(st.get("cost_usd") or 0), projected_usd=float(st.get("projected_usd") or 0),
                   usage=[{"label": "Робота бази", "value": st.get("used_cu_hours"), "unit": "CU-год"},
                          {"label": "Зберігання", "value": st.get("storage_gb"), "unit": "ГБ"}],
                   budget_usd=st.get("budget_usd"), level=st.get("level"), capped=st.get("capped"),
                   error=st.get("error"))
    if st.get("error"):
        svc["status"] = "error"
    return svc


RAILWAY_API = "https://backboard.railway.com/graphql/v2"
_RAILWAY_MEASUREMENTS = ["CPU_USAGE", "MEMORY_USAGE_GB", "NETWORK_TX_GB", "DISK_USAGE_GB"]


def railway_cost_from_usage(values: Dict[str, float]) -> float:
    """Railway usage (CPU — vCPU-хв, RAM/диск — ГБ-хв, egress — ГБ) → $."""
    return (values.get("CPU_USAGE", 0) * PRICES["railway_cpu_min"]
            + values.get("MEMORY_USAGE_GB", 0) * PRICES["railway_mem_gb_min"]
            + values.get("NETWORK_TX_GB", 0) * PRICES["railway_egress_gb"]
            + values.get("DISK_USAGE_GB", 0) * PRICES["railway_disk_gb_min"])


def railway_bill(usage_usd: float) -> float:
    """Рахунок за план: абонплата, у яку входить кредит на usage (Hobby: $5 і $5)."""
    fee, included = _f("RAILWAY_PLAN_FEE_USD", 5), _f("RAILWAY_INCLUDED_USAGE_USD", 5)
    return fee + max(0.0, usage_usd - included)


def railway_service(now: _dt.datetime) -> Dict[str, Any]:
    token, project = os.getenv("RAILWAY_API_TOKEN", "").strip(), os.getenv("RAILWAY_PROJECT_ID", "").strip()
    name = "Railway (сервер каталогу й складу)"
    if not (token and project):
        return _service("railway", name, status="not_configured",
                        cost_usd=_f("RAILWAY_PLAN_FEE_USD", 5), projected_usd=_f("RAILWAY_PLAN_FEE_USD", 5),
                        note="Без токена показано лише абонплату плану.",
                        setup="RAILWAY_API_TOKEN (Account Settings → Tokens) і RAILWAY_PROJECT_ID у .env")
    start, end = month_bounds(now)
    query = """query($p: String!, $s: DateTime!, $e: DateTime!, $m: [MetricMeasurement!]!) {
      usage(projectId: $p, measurements: $m, startDate: $s, endDate: $e) { measurement value }
    }"""
    data = _post_json(RAILWAY_API, token, {"query": query, "variables": {
        "p": project, "s": _iso(start), "e": _iso(now), "m": _RAILWAY_MEASUREMENTS}})
    values: Dict[str, float] = {}
    for row in data.get("usage") or []:
        values[row.get("measurement")] = values.get(row.get("measurement"), 0.0) + float(row.get("value") or 0)
    usage_usd = railway_cost_from_usage(values)
    projected_usage = _projection(usage_usd, now, start, end)
    return _service("railway", name,
                    cost_usd=railway_bill(usage_usd), projected_usd=railway_bill(projected_usage),
                    usage=[{"label": "CPU", "value": round(values.get("CPU_USAGE", 0) / 60, 2), "unit": "vCPU-год"},
                           {"label": "Памʼять", "value": round(values.get("MEMORY_USAGE_GB", 0) / 60, 2), "unit": "ГБ-год"},
                           {"label": "Вихідний трафік", "value": round(values.get("NETWORK_TX_GB", 0), 3), "unit": "ГБ"},
                           {"label": "Використання (до кредиту)", "value": round(usage_usd, 2), "unit": "$"}],
                    note=f"План: ${_f('RAILWAY_PLAN_FEE_USD', 5):g}/міс, з них ${_f('RAILWAY_INCLUDED_USAGE_USD', 5):g} — кредит на використання.")


CF_API = "https://api.cloudflare.com/client/v4/graphql"
R2_CLASS_A = {"ListBuckets", "PutBucket", "ListObjects", "ListObjectsV2", "PutObject", "CopyObject",
              "CompleteMultipartUpload", "CreateMultipartUpload", "UploadPart", "UploadPartCopy",
              "ListMultipartUploads", "ListParts", "PutBucketEncryption", "PutBucketCors",
              "PutBucketLifecycleConfiguration", "LifecycleStorageTierTransition"}
R2_CLASS_B = {"HeadBucket", "HeadObject", "GetObject", "UsageSummary", "GetBucketEncryption",
              "GetBucketLocation", "GetBucketCors", "GetBucketLifecycleConfiguration"}


def r2_cost(storage_gb: float, class_a: float, class_b: float) -> float:
    return (max(0.0, storage_gb - FREE["r2_gb"]) * PRICES["r2_gb_month"]
            + max(0.0, class_a - FREE["r2_class_a"]) / 1e6 * PRICES["r2_class_a_m"]
            + max(0.0, class_b - FREE["r2_class_b"]) / 1e6 * PRICES["r2_class_b_m"])


def workers_cost(requests_month: float) -> float:
    if (os.getenv("CLOUDFLARE_WORKERS_PLAN", "free") or "free").lower() != "paid":
        return 0.0   # Free: понад ліміт запити відхиляються, а не тарифікуються
    return PRICES["workers_paid_fee"] + max(0.0, requests_month - FREE["workers_paid_req_month"]) / 1e6 * PRICES["workers_req_m"]


def cloudflare_services(now: _dt.datetime) -> List[Dict[str, Any]]:
    token, account = os.getenv("CLOUDFLARE_API_TOKEN", "").strip(), os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not (token and account):
        setup = "CLOUDFLARE_API_TOKEN (права Account Analytics: Read) і CLOUDFLARE_ACCOUNT_ID у .env"
        return [_service("cloudflare_workers", "Cloudflare Workers (диспетчери, Топ-9)", status="not_configured", setup=setup),
                _service("cloudflare_r2", "Cloudflare R2 (фото товарів)", status="not_configured", setup=setup)]
    start, end = month_bounds(now)
    query = """query($a: string!, $d0: Date!, $d1: Date!, $t0: Time!, $t1: Time!) { viewer { accounts(filter: {accountTag: $a}) {
      workersInvocationsAdaptive(limit: 10000, filter: {date_geq: $d0, date_leq: $d1}) { sum { requests errors } dimensions { scriptName } }
      r2OperationsAdaptiveGroups(limit: 10000, filter: {datetime_geq: $t0, datetime_leq: $t1}) { sum { requests } dimensions { actionType } }
      r2StorageAdaptiveGroups(limit: 100, filter: {datetime_geq: $t0, datetime_leq: $t1}, orderBy: [datetime_DESC]) { max { payloadSize metadataSize objectCount } dimensions { bucketName } }
    } } }"""
    data = _post_json(CF_API, token, {"query": query, "variables": {
        "a": account, "d0": start.date().isoformat(), "d1": now.date().isoformat(),
        "t0": _iso(start), "t1": _iso(now)}})
    acc = ((data.get("viewer") or {}).get("accounts") or [{}])[0]
    per_script: Dict[str, float] = {}
    for row in acc.get("workersInvocationsAdaptive") or []:
        script = (row.get("dimensions") or {}).get("scriptName") or "?"
        per_script[script] = per_script.get(script, 0) + float((row.get("sum") or {}).get("requests") or 0)
    req = sum(per_script.values())
    days = max((now - start).total_seconds() / 86400, 1)
    class_a = class_b = 0.0
    for row in acc.get("r2OperationsAdaptiveGroups") or []:
        n = float((row.get("sum") or {}).get("requests") or 0)
        action = (row.get("dimensions") or {}).get("actionType")
        if action in R2_CLASS_A:
            class_a += n
        elif action in R2_CLASS_B:
            class_b += n
    seen, storage_b = set(), 0.0
    for row in acc.get("r2StorageAdaptiveGroups") or []:   # найсвіжіший рядок на бакет
        bucket = (row.get("dimensions") or {}).get("bucketName")
        if bucket in seen:
            continue
        seen.add(bucket)
        mx = row.get("max") or {}
        storage_b += float(mx.get("payloadSize") or 0) + float(mx.get("metadataSize") or 0)
    gb = storage_b / 1e9
    w_cost, r_cost = workers_cost(req), r2_cost(gb, class_a, class_b)
    top = sorted(per_script.items(), key=lambda kv: -kv[1])[:5]
    return [
        _service("cloudflare_workers", "Cloudflare Workers (диспетчери, Топ-9)",
                 cost_usd=w_cost, projected_usd=workers_cost(_projection(req, now, start, end)),
                 usage=[{"label": "Запити за місяць", "value": int(req), "unit": ""},
                        {"label": "У середньому на добу", "value": int(req / days), "unit": "",
                         "limit": FREE["workers_req_day"]}]
                       + [{"label": f"· {s}", "value": int(n), "unit": ""} for s, n in top],
                 note="План Free: понад 100 тис. запитів/добу запити відхиляються, не тарифікуються."
                 if workers_cost(0) == 0 else None),
        _service("cloudflare_r2", "Cloudflare R2 (фото товарів)",
                 cost_usd=r_cost,
                 projected_usd=r2_cost(gb, _projection(class_a, now, start, end), _projection(class_b, now, start, end)),
                 usage=[{"label": "Зберігання", "value": round(gb, 3), "unit": "ГБ", "limit": FREE["r2_gb"]},
                        {"label": "Запис/список (Class A)", "value": int(class_a), "unit": "", "limit": FREE["r2_class_a"]},
                        {"label": "Читання (Class B)", "value": int(class_b), "unit": "", "limit": FREE["r2_class_b"]}]),
    ]


def ai_service(now: _dt.datetime) -> Dict[str, Any]:
    try:
        try:
            from backend.services import ai_budget
            from backend.models.database import SessionLocal
        except ImportError:
            from services import ai_budget
            from models.database import SessionLocal
    except Exception:  # noqa: BLE001 — гілка без AI-обліку
        return _service("ai", "AI (Gemini, автозаповнення)", status="not_configured", setup="Модуль AI-обліку відсутній")
    db = SessionLocal()
    try:
        spent = float(ai_budget.spent_this_month(db))
    finally:
        db.close()
    start, end = month_bounds(now)
    return _service("ai", "AI (Gemini, автозаповнення)", cost_usd=spent,
                    projected_usd=_projection(spent, now, start, end),
                    usage=[{"label": "Стеля AI-витрат", "value": ai_budget.MONTHLY_CAP_USD, "unit": "$"}],
                    note="Облік за токенами кожного виклику (services/ai_budget.py).")


def usd_uah_rate(prev: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Курс НБУ USD→UAH (публічний API, раз на добу; збій — останній відомий)."""
    prev = prev or {}
    if prev.get("usd_uah") and time.time() - float(prev.get("usd_uah_at") or 0) < 86400:
        return float(prev["usd_uah"])
    try:
        import requests
        r = requests.get("https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange",
                         params={"valcode": "USD", "json": ""}, timeout=10)
        r.raise_for_status()
        return float(r.json()[0]["rate"])
    except Exception as exc:  # noqa: BLE001
        logger.info("cloud_costs: курс НБУ недоступний: %s", exc)
        return float(prev["usd_uah"]) if prev.get("usd_uah") else None


PROVIDERS: List[Callable[[_dt.datetime], Any]] = [neon_service, railway_service, cloudflare_services, ai_service]


# ─────────────────────────────── конфіг і зведення ───────────────────────────

def config_path() -> Path:
    return _bms_dir() / "cloud_costs_config.json"


def load_config() -> Dict[str, Any]:
    cfg = _read_json(config_path())
    alloc = {k: dict(v) for k, v in DEFAULT_ALLOCATION.items()}
    for key, shares in (cfg.get("allocation") or {}).items():
        alloc[key] = {a: float(shares.get(a, 0)) for a in AREAS}
    manual = [m for m in (cfg.get("manual") or []) if m.get("name")]
    return {"allocation": alloc, "manual": manual}


def save_config(allocation: Optional[Dict[str, Dict[str, float]]] = None,
                manual: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    cfg = load_config()
    if allocation is not None:
        for key, shares in allocation.items():
            vals = {a: max(0.0, float(shares.get(a, 0))) for a in AREAS}
            if sum(vals.values()) <= 0:
                raise ValueError(f"{key}: сума часток має бути більшою за 0")
            cfg["allocation"][key] = vals
    if manual is not None:
        clean = []
        for m in manual:
            name = str(m.get("name") or "").strip()[:80]
            if not name:
                continue
            area = m.get("area") if m.get("area") in AREAS else "bms"
            clean.append({"name": name, "usd_month": max(0.0, float(m.get("usd_month") or 0)), "area": area})
        cfg["manual"] = clean
    _write_json(config_path(), cfg)
    with _lock:
        if _state.get("services") is not None:
            _state.update(summarize(_state["services"], cfg, _state.get("period_start"), _state.get("period_end")))
    return cfg


def summarize(services: List[Dict[str, Any]], cfg: Dict[str, Any],
              period_start: Optional[str], period_end: Optional[str]) -> Dict[str, Any]:
    """Сервіси + ручні пункти → підсумки за напрямами та загалом (чиста функція)."""
    areas = {a: {"key": a, "name": n, "cost_usd": 0.0, "projected_usd": 0.0, "items": []} for a, n in AREAS.items()}
    rows = []
    for svc in services:
        shares = cfg["allocation"].get(svc["key"]) or {"bms": 100}
        total_share = sum(shares.values()) or 1.0
        split = {a: round(100 * shares.get(a, 0) / total_share, 1) for a in AREAS}
        rows.append({**svc, "split": split})
        for a in AREAS:
            part = split[a] / 100
            if part:
                areas[a]["cost_usd"] += svc["cost_usd"] * part
                areas[a]["projected_usd"] += svc["projected_usd"] * part
                areas[a]["items"].append({"name": svc["name"], "cost_usd": round(svc["cost_usd"] * part, 2)})
    for m in cfg["manual"]:
        usd = float(m["usd_month"])
        rows.append(_service(f"manual:{m['name']}", m["name"], cost_usd=usd, projected_usd=usd,
                             note="Ручний пункт (фіксована сума на місяць)", manual=True,
                             split={a: (100.0 if a == m["area"] else 0.0) for a in AREAS}))
        areas[m["area"]]["cost_usd"] += usd
        areas[m["area"]]["projected_usd"] += usd
        areas[m["area"]]["items"].append({"name": m["name"], "cost_usd": round(usd, 2)})
    for a in areas.values():
        a["cost_usd"], a["projected_usd"] = round(a["cost_usd"], 2), round(a["projected_usd"], 2)
    for r in rows:
        r["cost_usd"], r["projected_usd"] = round(r["cost_usd"], 2), round(r["projected_usd"], 2)
    return {
        "services": rows,
        "areas": list(areas.values()),
        "total": {"cost_usd": round(sum(r["cost_usd"] for r in rows), 2),
                  "projected_usd": round(sum(r["projected_usd"] for r in rows), 2)},
        "allocation": cfg["allocation"], "manual": cfg["manual"], "area_names": AREAS,
        "period_start": period_start, "period_end": period_end,
    }


def refresh(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Опитати провайдерів (кожен незалежно: збій одного не ламає решту)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    start, end = month_bounds(now)
    prev_state = status()
    prev = {s["key"]: s for s in (prev_state.get("services") or [])}
    services: List[Dict[str, Any]] = []
    for provider in PROVIDERS:
        try:
            out = provider(now)
            services.extend(out if isinstance(out, list) else [out])
        except Exception as exc:  # noqa: BLE001
            name = provider.__name__.replace("_services", "").replace("_service", "")
            logger.warning("cloud_costs: %s недоступний: %s", name, exc)
            keys = ["cloudflare_workers", "cloudflare_r2"] if provider is cloudflare_services else [name]
            for key in keys:   # лишаємо останні відомі цифри, позначаємо помилку
                old = prev.get(key) or _service(key, key)
                services.append({**old, "status": "error", "error": str(exc)[:300]})
    st = summarize([s for s in services if not s.get("manual")], load_config(), _iso(start), _iso(end))
    st["updated_at"] = time.time()
    rate = usd_uah_rate(prev_state)
    if rate:
        st["usd_uah"] = rate
        st["usd_uah_at"] = prev_state.get("usd_uah_at") if rate == prev_state.get("usd_uah") else time.time()
    with _lock:
        _state.clear()
        _state.update(st)
    _write_json(_bms_dir() / "cloud_costs_state.json", st)
    return dict(st)


def status() -> Dict[str, Any]:
    with _lock:
        if not _state:
            _state.update(_read_json(_bms_dir() / "cloud_costs_state.json"))
        return dict(_state)

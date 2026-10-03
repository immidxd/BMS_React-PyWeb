"""Бюджет хмарної БД каталогу (Neon) — лічильник, сповіщення і ЖОРСТКИЙ ЗАПОБІЖНИК.

⚠️ ЖОРСТКЕ ПРАВИЛО ПРОЄКТУ (CLAUDE.md): витрати на Neon — не більше бюджету власника
(NEON_BUDGET_USD, за замовчуванням $3 на місяць). Більше — ЛИШЕ з його явного дозволу.
Інцидент: вересень 2026, −881.85 ₴ (≈$21) за compute, що не спав.

Що робить:
  • читає витрату через Neon API (control plane — САМ ЗАПИТ НЕ БУДИТЬ БАЗУ): раз на
    30 хв, а коли витрата вже помітна (warn/economy) — раз на 10 хв;
  • рахує вартість у доларах (план Launch): compute = CU-год × ціна; зберігання —
    одразу ЗА ВЕСЬ МІСЯЦЬ (воно нараховується, навіть коли compute вимкнено);
  • рівні: ok → warn (≥60%) → economy (≥75%) → stop (≥90% бюджету);
  • на «stop» — ВИМИКАЄ compute-ендпоінти проєкту через API (disabled=true): база
    фізично не може прокинутись, хоч би хто до неї стукав (каталог, бот, синк).
    Увімкнути знову: дозвіл власника (кнопка в BMS → permit(), +$N до бюджету місяця)
    або новий розрахунковий період (витрата обнуляється);
  • пише стан у ~/.bms/cloud_budget.json (читає BMS_catalog/cloud/sync_to_cloud.py).

Налаштування (.env / %LOCALAPPDATA%\\BMS\\secrets.env):
  NEON_API_KEY      — Neon Console → Settings → API keys → ORG-WIDE (проєктний ключ не бачить
                      споживання: 403 «outside the project»); потрібен і для вимикання ендпоінта
  NEON_ORG_ID       — необов'язково (береться з проєкту: org_id)
  NEON_PROJECT_ID   — id проєкту (plain-breeze-73014199)
  NEON_PLAN=launch  — launch (бюджет у $) | free (100 CU-год)
  NEON_BUDGET_USD=3, NEON_PRICE_CU_HOUR=0.106, NEON_PRICE_STORAGE_GB_MONTH=0.35
  NEON_HARD_CAP=1   — вимикати базу на «stop» (0 — лише сповіщати)
  NEON_FREE_CU_HOURS=100, NEON_BUDGET_WARN=0.6, NEON_BUDGET_ECONOMY=0.75, NEON_BUDGET_STOP=0.9
Без ключа лічильник у стані «unknown» і BMS нагадує його налаштувати.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NEON_API = "https://console.neon.tech/api/v2"
LEVELS = ("ok", "warn", "economy", "stop")

# Мінімальний інтервал між синками каталогу (с) для кожного рівня.
# unknown = як ok: без лічильника не блокуємо, але й не частимо.
SYNC_MIN_INTERVAL = {"ok": 1200, "unknown": 1200, "warn": 1800, "economy": 3600}

_lock = threading.Lock()
_state: Dict[str, Any] = {}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def state_path() -> Path:
    return Path(os.path.expanduser(os.getenv("CLOUD_BUDGET_STATE", "~/.bms/cloud_budget.json")))


def permit_path() -> Path:
    return state_path().with_name("cloud_budget_permit.json")


def _configured() -> bool:
    return bool(os.getenv("NEON_API_KEY", "").strip() and os.getenv("NEON_PROJECT_ID", "").strip())


def _hard_cap() -> bool:
    return (os.getenv("NEON_HARD_CAP", "1") or "1").lower() not in ("0", "false", "no", "off")


def _parse_ts(v: Any) -> Optional[_dt.datetime]:
    if not v:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — нема/битий файл
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("cloud_budget: не записано %s: %s", path, exc)


def _period(project: Dict[str, Any], now: _dt.datetime) -> tuple[_dt.datetime, _dt.datetime]:
    start = _parse_ts(project.get("consumption_period_start"))
    end = _parse_ts(project.get("consumption_period_end"))
    if not start:   # Neon не дав період — рахуємо від початку календарного місяця
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end or end.year < 2000 or end <= start:
        end = (start.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    return start, end


def permit_usd(period_start: str) -> float:
    """Додатковий бюджет, дозволений власником на ЦЕЙ розрахунковий період."""
    p = _read_json(permit_path())
    return float(p.get("usd") or 0) if p.get("period_start") == period_start else 0.0


def compute_status(project: Dict[str, Any], now: Optional[_dt.datetime] = None,
                   extra_usd: float = 0.0, compute_unit_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Відповідь Neon API → витрата й рівень. Чиста функція (тестується).

    compute_unit_seconds — з API споживання (`/consumption_history/v2/projects`).
    ⚠️ На платних планах поля проєкту compute_time_seconds/cpu_used_sec = 0 (перевірено
    03.10.2026 на живому Neon) — вони лише запасний варіант для Free."""
    warn, economy, stop = _f("NEON_BUDGET_WARN", 0.6), _f("NEON_BUDGET_ECONOMY", 0.75), _f("NEON_BUDGET_STOP", 0.9)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    start, end = _period(project, now)
    elapsed = max((now - start).total_seconds(), 86400.0)   # прогноз — не раніше ніж за добу даних
    total = max((end - start).total_seconds(), elapsed)

    # CU-секунди (1 CU протягом 1 с = 1); /3600 = CU-години.
    cu_sec = compute_unit_seconds
    if cu_sec is None:
        cu_sec = project.get("compute_time_seconds") or project.get("cpu_used_sec") or 0
    used_cu = float(cu_sec) / 3600.0
    projected_cu = used_cu * total / elapsed

    st: Dict[str, Any] = {
        "configured": True,
        "used_cu_hours": round(used_cu, 2),
        "projected_cu_hours": round(projected_cu, 1),
        "active_hours": round(float(project.get("active_time_seconds") or 0) / 3600.0, 1),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "last_active": project.get("compute_last_active_at"),
        "thresholds": {"warn": warn, "economy": economy, "stop": stop},
    }
    if (os.getenv("NEON_PLAN", "launch") or "launch").lower() == "free":
        limit = _f("NEON_FREE_CU_HOURS", 100.0)
        share, projected_share = used_cu / limit, projected_cu / limit
        st.update(plan="free", free_cu_hours=limit)
    else:
        price_cu, price_gb = _f("NEON_PRICE_CU_HOUR", 0.106), _f("NEON_PRICE_STORAGE_GB_MONTH", 0.35)
        gb = float(project.get("synthetic_storage_size") or 0) / 1e9
        storage_month = gb * price_gb            # нараховується весь місяць незалежно від compute
        budget = _f("NEON_BUDGET_USD", 3.0) + max(extra_usd, 0.0)
        cost = used_cu * price_cu + storage_month
        projected = projected_cu * price_cu + storage_month
        share = cost / budget if budget > 0 else 1.0
        projected_share = projected / budget if budget > 0 else 1.0
        st.update(plan="launch", budget_usd=round(budget, 2), extra_usd=round(extra_usd, 2),
                  cost_usd=round(cost, 2), projected_usd=round(projected, 2),
                  storage_gb=round(gb, 3), storage_usd=round(storage_month, 2))

    level = "stop" if share >= stop else "economy" if share >= economy else "warn" if share >= warn else "ok"
    if level == "ok" and projected_share >= stop:
        level = "warn"   # поки в межах, але темп веде до межі до кінця періоду
    st.update(level=level, percent=round(share * 100, 1))
    return st


# ───────────────────────── Neon API (control plane) ──────────────────────────

def _api(method: str, path: str, body: Optional[dict] = None,
         params: Optional[dict] = None) -> Dict[str, Any]:
    """path з «/projects/…» або «/consumption_history/…» — від кореня API;
    інакше — відносно поточного проєкту ("" — сам проєкт, "/endpoints" тощо)."""
    import requests
    if not path.startswith(("/projects", "/consumption_history")):
        path = f"/projects/{os.environ['NEON_PROJECT_ID'].strip()}{path}"
    r = requests.request(method, f"{NEON_API}{path}", params=params,
                         headers={"Authorization": f"Bearer {os.environ['NEON_API_KEY'].strip()}",
                                  "Accept": "application/json"}, json=body, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"Neon API {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def _metric_sum(payload: Any, name: str) -> float:
    """Сума значень метрики в будь-якій вкладеності відповіді споживання
    ({"metric_name": name, "value": N} або {name: N})."""
    total = 0.0
    if isinstance(payload, dict):
        if payload.get("metric_name") == name and isinstance(payload.get("value"), (int, float)):
            total += float(payload["value"])
        elif isinstance(payload.get(name), (int, float)):
            total += float(payload[name])
        for v in payload.values():
            if isinstance(v, (dict, list)):
                total += _metric_sum(v, name)
    elif isinstance(payload, list):
        for v in payload:
            total += _metric_sum(v, name)
    return total


def _iso(t: _dt.datetime) -> str:
    return t.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def consumption_cu_seconds(project: Dict[str, Any], start: _dt.datetime, now: _dt.datetime) -> float:
    """CU-секунди з початку періоду: повні доби — daily, сьогодні — hourly.
    Потрібен ключ рівня ОРГАНІЗАЦІЇ (Org-wide): проєктний ключ отримує 403."""
    org = os.getenv("NEON_ORG_ID", "").strip() or project.get("org_id")
    if not org:
        raise RuntimeError("Невідомий org_id проєкту Neon (задайте NEON_ORG_ID)")
    base = {"org_id": org, "project_ids": os.environ["NEON_PROJECT_ID"].strip(),
            "metrics": "compute_unit_seconds"}
    today = now.astimezone(_dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    hour = now.astimezone(_dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    total = 0.0
    if today > start:
        total += _metric_sum(_api("GET", "/consumption_history/v2/projects",
                                  params={**base, "from": _iso(start), "to": _iso(today),
                                          "granularity": "daily"}), "compute_unit_seconds")
    since = max(today, start)
    if hour > since:
        try:
            total += _metric_sum(_api("GET", "/consumption_history/v2/projects",
                                      params={**base, "from": _iso(since), "to": _iso(hour),
                                              "granularity": "hourly"}), "compute_unit_seconds")
        except Exception as exc:  # noqa: BLE001 — сьогоднішні години дочитаємо наступного разу
            logger.warning("cloud_budget: погодинне споживання недоступне: %s", exc)
    return total


def _set_endpoints_disabled(disabled: bool) -> List[str]:
    """Вимкнути/увімкнути ВСІ compute-ендпоінти проєкту. Повертає id змінених."""
    changed = []
    for ep in _api("GET", "/endpoints").get("endpoints") or []:
        if bool(ep.get("disabled")) != disabled:
            _api("PATCH", f"/endpoints/{ep['id']}", {"endpoint": {"disabled": disabled}})
            changed.append(ep["id"])
    if changed:
        logger.warning("cloud_budget: compute %s: %s", "ВИМКНЕНО (бюджет)" if disabled else "увімкнено", changed)
    return changed


def _enforce(st: Dict[str, Any], prev: Dict[str, Any]) -> None:
    """Жорсткий запобіжник: stop → базу вимкнено; нижче stop і була вимкнена нами → увімкнути."""
    if not _hard_cap():
        st["capped"] = False
        return
    was_capped = bool(prev.get("capped"))
    try:
        if st["level"] == "stop":
            _set_endpoints_disabled(True)
            st["capped"] = True
        elif was_capped:
            _set_endpoints_disabled(False)
            st["capped"] = False
    except Exception as exc:  # noqa: BLE001 — спробуємо на наступному циклі
        logger.warning("cloud_budget: не вдалося змінити стан compute: %s", exc)
        st["capped"] = was_capped
        st["cap_error"] = str(exc)[:200]


def refresh() -> Dict[str, Any]:
    """Оновити витрату з Neon API і застосувати запобіжник (мережа; БАЗУ НЕ БУДИТЬ)."""
    prev = status(auto_refresh=False)
    if not _configured():
        st = {"configured": False, "level": "unknown",
              "message": "Лічильник Neon не налаштовано: задайте NEON_API_KEY і NEON_PROJECT_ID."}
    else:
        try:
            project = _api("GET", "").get("project") or {}
            now = _dt.datetime.now(_dt.timezone.utc)
            start, _ = _period(project, now)
            cu = consumption_cu_seconds(project, start, now)
            st = compute_status(project, now, extra_usd=permit_usd(start.isoformat()),
                                compute_unit_seconds=cu)
            _enforce(st, prev)
        except Exception as exc:  # noqa: BLE001 — мережа/ключ: лишаємо останній відомий рівень
            logger.warning("cloud_budget: Neon API недоступний: %s", exc)
            st = {**prev, "configured": True, "error": str(exc)[:200]}
            st.setdefault("level", "unknown")
    st["updated_at"] = time.time()
    with _lock:
        _state.clear()
        _state.update(st)
    _write_json(state_path(), st)
    if prev.get("level") and st.get("level") != prev.get("level"):
        logger.warning("cloud_budget: рівень %s → %s (%s%%)", prev.get("level"), st.get("level"), st.get("percent"))
    return dict(st)


def permit(extra_usd: float) -> Dict[str, Any]:
    """Дозвіл власника: +extra_usd до бюджету ПОТОЧНОГО періоду (сумується), далі refresh —
    якщо нова межа вища за витрату, запобіжник сам увімкне базу."""
    st = status(auto_refresh=False)
    period_start = st.get("period_start")
    if not period_start:
        st = refresh()
        period_start = st.get("period_start")
    if not period_start:
        raise RuntimeError("Немає даних про період Neon — перевірте NEON_API_KEY/NEON_PROJECT_ID")
    total = permit_usd(period_start) + float(extra_usd)
    _write_json(permit_path(), {"period_start": period_start, "usd": round(total, 2), "granted_at": time.time()})
    logger.warning("cloud_budget: власник дозволив +$%.2f (разом +$%.2f) на період з %s", extra_usd, total, period_start)
    return refresh()


def status(auto_refresh: bool = True) -> Dict[str, Any]:
    """Останній відомий стан (з пам'яті / файлу). auto_refresh — оновити, якщо застарів."""
    with _lock:
        if not _state:
            _state.update(_read_json(state_path()))
        st = dict(_state)
    stale = time.time() - float(st.get("updated_at") or 0) > next_refresh_sec(st.get("level"))
    if auto_refresh and stale:
        return refresh()
    return st


def next_refresh_sec(lv: Optional[str] = None) -> float:
    """Як часто питати Neon: поблизу межі — частіше, щоб запобіжник спрацював вчасно."""
    base = max(_f("NEON_BUDGET_REFRESH_SEC", 1800), 300)
    lv = lv if lv is not None else _state.get("level")
    return min(base, 600) if lv in ("warn", "economy") else base


def level() -> str:
    lv = status(auto_refresh=False).get("level") or "unknown"
    return lv if lv in LEVELS else "unknown"


def cloud_wake_allowed() -> bool:
    """Чи можна будити хмарну БД автоматично (ручний запуск — завжди можна)."""
    return level() != "stop"


def sync_min_interval_sec() -> float:
    return float(os.getenv("CATALOG_SYNC_MIN_INTERVAL_SEC", "") or SYNC_MIN_INTERVAL.get(level(), 1200))

"""Лічильник безкоштовного ліміту Neon (хмарна БД публічного каталогу).

⚠️ ЖОРСТКЕ ПРАВИЛО ПРОЄКТУ (CLAUDE.md): не перевищувати безкоштовний ліміт —
100 CU-годин на місяць. Інцидент: вересень 2026, −881.85 ₴ за compute, що не спав.

Що робить:
  • раз на NEON_BUDGET_REFRESH_SEC (30 хв) читає витрату через Neon API
    (control plane — САМ ЗАПИТ НЕ БУДИТЬ БАЗУ і не коштує CU);
  • рахує рівень: ok → warn (≥60%) → economy (≥75%) → stop (≥90%);
    прогноз на кінець періоду теж піднімає ok до warn;
  • пише стан у ~/.bms/cloud_budget.json — його читає BMS_catalog/cloud/sync_to_cloud.py
    (на «stop» launchd-синк не будить хмару);
  • рівень керує частотою синку каталогу (catalog_sync_service) і банером у BMS.

Налаштування (.env / %LOCALAPPDATA%\\BMS\\secrets.env):
  NEON_API_KEY      — Neon Console → Account settings → API keys (лише читання достатньо)
  NEON_PROJECT_ID   — id проєкту (напр. plain-breeze-73014199)
  NEON_FREE_CU_HOURS=100, NEON_BUDGET_WARN=0.6, NEON_BUDGET_ECONOMY=0.75, NEON_BUDGET_STOP=0.9
Без ключа лічильник у стані «unknown»: синк працює за звичайними (ощадними) правилами,
а BMS показує нагадування, що лічильник не налаштовано.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

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


def _configured() -> bool:
    return bool(os.getenv("NEON_API_KEY", "").strip() and os.getenv("NEON_PROJECT_ID", "").strip())


def _parse_ts(v: Any) -> Optional[_dt.datetime]:
    if not v:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_status(project: Dict[str, Any], now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Відповідь Neon API (об'єкт project) → рівень бюджету. Чиста функція (тестується)."""
    free = _f("NEON_FREE_CU_HOURS", 100.0)
    warn, economy, stop = _f("NEON_BUDGET_WARN", 0.6), _f("NEON_BUDGET_ECONOMY", 0.75), _f("NEON_BUDGET_STOP", 0.9)
    # compute_time_seconds — CU-секунди (1 CU протягом 1 с = 1); /3600 = CU-години.
    cu_sec = project.get("compute_time_seconds")
    if cu_sec is None:
        cu_sec = project.get("cpu_used_sec") or 0
    used = float(cu_sec) / 3600.0
    pct = used / free if free > 0 else 1.0

    now = now or _dt.datetime.now(_dt.timezone.utc)
    start = _parse_ts(project.get("consumption_period_start"))
    end = _parse_ts(project.get("consumption_period_end"))
    if not start:   # Neon не дав період — рахуємо від початку календарного місяця
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end or end.year < 2000 or end <= start:
        end = (start.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    elapsed = max((now - start).total_seconds(), 86400.0)   # прогноз — не раніше ніж за добу даних
    total = max((end - start).total_seconds(), elapsed)
    projected = used * total / elapsed

    level = "stop" if pct >= stop else "economy" if pct >= economy else "warn" if pct >= warn else "ok"
    if level == "ok" and projected >= free * stop:
        level = "warn"   # поки в межах, але темп веде до межі до кінця періоду
    return {
        "configured": True,
        "level": level,
        "used_cu_hours": round(used, 2),
        "free_cu_hours": free,
        "percent": round(pct * 100, 1),
        "projected_cu_hours": round(projected, 1),
        "active_hours": round(float(project.get("active_time_seconds") or 0) / 3600.0, 1),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "last_active": project.get("compute_last_active_at"),
        "thresholds": {"warn": warn, "economy": economy, "stop": stop},
    }


def _write_state(st: Dict[str, Any]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("cloud_budget: стан не записано (%s): %s", path, exc)


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — нема/битий файл
        return {}


def refresh() -> Dict[str, Any]:
    """Оновити витрату з Neon API (мережевий виклик; БАЗУ НЕ БУДИТЬ)."""
    prev = status(auto_refresh=False)
    if not _configured():
        st = {"configured": False, "level": "unknown",
              "message": "Лічильник Neon не налаштовано: задайте NEON_API_KEY і NEON_PROJECT_ID."}
    else:
        import requests
        try:
            r = requests.get(f"{NEON_API}/projects/{os.environ['NEON_PROJECT_ID'].strip()}",
                             headers={"Authorization": f"Bearer {os.environ['NEON_API_KEY'].strip()}",
                                      "Accept": "application/json"}, timeout=15)
            r.raise_for_status()
            st = compute_status(r.json().get("project") or {})
        except Exception as exc:  # noqa: BLE001 — мережа/ключ: лишаємо останній відомий рівень
            logger.warning("cloud_budget: Neon API недоступний: %s", exc)
            st = {**prev, "configured": True, "error": str(exc)[:200]}
            st.setdefault("level", "unknown")
    st["updated_at"] = time.time()
    with _lock:
        _state.clear()
        _state.update(st)
    _write_state(st)
    if prev.get("level") and st.get("level") != prev.get("level"):
        logger.warning("cloud_budget: рівень %s → %s (%s%% з %s CU-год)", prev.get("level"), st.get("level"),
                       st.get("percent"), st.get("free_cu_hours"))
    return dict(st)


def status(auto_refresh: bool = True) -> Dict[str, Any]:
    """Останній відомий стан (з пам'яті / файлу). auto_refresh — оновити, якщо застарів."""
    with _lock:
        if not _state:
            _state.update(_load_state())
        st = dict(_state)
    stale = time.time() - float(st.get("updated_at") or 0) > _f("NEON_BUDGET_REFRESH_SEC", 1800)
    if auto_refresh and stale:
        return refresh()
    return st


def level() -> str:
    lv = status(auto_refresh=False).get("level") or "unknown"
    return lv if lv in LEVELS or lv == "unknown" else "unknown"


def cloud_wake_allowed() -> bool:
    """Чи можна будити хмарну БД автоматично (ручний запуск — завжди можна)."""
    return level() != "stop"


def sync_min_interval_sec() -> float:
    return float(os.getenv("CATALOG_SYNC_MIN_INTERVAL_SEC", "") or SYNC_MIN_INTERVAL.get(level(), 1200))

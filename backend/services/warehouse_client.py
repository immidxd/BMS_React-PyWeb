"""Клієнт хмарного API складу (/api/wh/* у сервісі BMS_catalog на Railway).

Дані складу (коробки, вміст, події) живуть ЛИШЕ в хмарній базі (Neon) —
див. памʼять warehouse-cloud-and-miniapp. BMS не тримає їх копії і не
синхронізує: кожен запит іде в хмару, авторизуючись тим самим адмін-токеном,
що й публікації в каталозі (CATALOG_ADMIN_TOKEN). Токен читаємо з оточення
або з `.env` вітрини (~/Desktop/BMS_catalog) — так само, як адресу Neon для
автопідбірок; у браузер він ніколи не потрапляє (фронт ходить через
routers/warehouse.py на цьому ж бекенді).

Адреса сервісу: BMS_CLOUD_API_URL (за замовчуванням прод Railway).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests

try:
    from services.auto_collection_cloud_sync import _catalog_dir, _dotenv_value
except ImportError:  # pragma: no cover
    from backend.services.auto_collection_cloud_sync import _catalog_dir, _dotenv_value

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://bmscatalog-production.up.railway.app"


class CloudError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status = status
        self.detail = detail


def base_url() -> str:
    return (os.getenv("BMS_CLOUD_API_URL") or DEFAULT_URL).rstrip("/")


def admin_token() -> Optional[str]:
    return (os.getenv("CATALOG_ADMIN_TOKEN") or "").strip() \
        or _dotenv_value(_catalog_dir() / ".env", "CATALOG_ADMIN_TOKEN")


def is_configured() -> bool:
    return bool(admin_token())


def request(method: str, path: str, *, json: Any = None, params: Optional[Dict[str, Any]] = None,
            timeout: float = 20.0) -> Any:
    """HTTP до хмари; помилка сервісу → CloudError із тим самим статусом і detail."""
    tok = admin_token()
    if not tok:
        raise CloudError(503, "Склад не налаштовано: немає CATALOG_ADMIN_TOKEN (у .env вітрини)")
    url = f"{base_url()}/api/wh{path}"
    try:
        r = requests.request(method, url, json=json, params=params, timeout=timeout,
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
    except requests.RequestException as exc:
        logger.warning("Склад: хмара недоступна (%s %s): %s", method, path, exc)
        raise CloudError(502, "Хмарний сервіс складу недоступний — перевірте інтернет") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        raise CloudError(r.status_code, detail or f"HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        return None


def ping() -> Tuple[bool, str]:
    """Чи відповідає хмара і чи прийнято токен (GET /boxes)."""
    try:
        request("GET", "/boxes", timeout=8)
        return True, "ok"
    except CloudError as exc:
        return False, str(exc)

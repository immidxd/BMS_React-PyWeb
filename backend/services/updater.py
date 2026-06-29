# -*- coding: utf-8 -*-
"""
Перевірка оновлень (Крок E1) — лише ВИЯВЛЕННЯ, без застосування.

Як працює
─────────
Реліз публікує `manifest.json` за стабільним URL (GitHub Releases asset / raw /
R2). Застосунок на запиті `/api/update-status` тягне маніфест, бере секцію свого
КАНАЛУ (stable/beta/dev) і порівнює версію з локальною (файл VERSION). Якщо в
каналі є новіша — повертає {update_available: true, ...} + URL інсталятора й SHA-256.

Цей модуль НІЧОГО не завантажує й не замінює — це окремий, ризиковий крок (E2),
який робитиметься на Windows ітеративно. Тут лише безпечне читання по мережі з
таймаутом і повним проковтуванням помилок (офлайн/збій → error, не виняток).

Формат manifest.json (див. deploy/make_manifest.py):
{
  "channels": {
    "stable": {
      "version": "0.1.1-alpha",
      "setup_url": "https://github.com/immidxd/BMS_React-PyWeb/releases/download/v0.1.1-alpha/BMS_Setup_0.1.1-alpha.exe",
      "sha256": "…",
      "notes": "короткий опис релізу",
      "full_install": true            // true = новий Setup.exe; false = гаряче оновлення (E2)
    },
    "beta": { … }
  }
}
"""

from __future__ import annotations

import os
import json
import logging
import urllib.request
from typing import Any, Dict, Optional

try:
    from packaging.version import parse as parse_version
except Exception:  # noqa: BLE001 — крайній випадок, коли packaging відсутній
    parse_version = None

logger = logging.getLogger("bms.updater")

# URL маніфесту. Порожній → перевірка вимкнена (dev-режим за замовчуванням).
MANIFEST_URL_ENV = "BMS_UPDATE_MANIFEST_URL"


def _runtime():
    """Поточні version+channel із єдиного шару конфігу."""
    try:
        from services.runtime_config import app_version, resolve_channel
    except ImportError:
        from backend.services.runtime_config import app_version, resolve_channel
    return app_version(), resolve_channel()


def fetch_manifest(url: str, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """Завантажити й розпарсити manifest.json. Будь-який збій → None (не виняток)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BMS-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (наш URL)
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.info("Маніфест недоступний (%s): %s", url, e)
        return None


def _is_newer(latest: str, current: str) -> bool:
    """latest > current. Через packaging (надійно для X.Y.Z[-prerelease])."""
    if parse_version is None:
        # запасний грубий лексичний варіант — лише якщо packaging немає
        return latest != current and latest > current
    try:
        return parse_version(latest) > parse_version(current)
    except Exception:  # noqa: BLE001
        return False


def check_for_update() -> Dict[str, Any]:
    """
    Зведений статус оновлення для поточного каналу. Завжди повертає dict,
    ніколи не кидає — придатний для прямої віддачі з ендпоінта.
    """
    current_version, channel = _runtime()
    url = os.getenv(MANIFEST_URL_ENV, "").strip()

    base = {
        "enabled": bool(url),
        "current_version": current_version,
        "channel": channel,
        "update_available": False,
        "latest_version": None,
        "setup_url": None,
        "sha256": None,
        "notes": None,
        "full_install": None,
        "error": None,
    }
    if not url:
        return base  # перевірка вимкнена (немає BMS_UPDATE_MANIFEST_URL)

    manifest = fetch_manifest(url)
    if manifest is None:
        base["error"] = "manifest_unreachable"
        return base

    chan = (manifest.get("channels") or {}).get(channel)
    if not isinstance(chan, dict) or not chan.get("version"):
        base["error"] = f"no_channel:{channel}"
        return base

    latest = str(chan["version"])
    base["latest_version"] = latest
    base["update_available"] = _is_newer(latest, current_version)
    if base["update_available"]:
        base["setup_url"] = chan.get("setup_url")
        base["sha256"] = chan.get("sha256")
        base["notes"] = chan.get("notes")
        base["full_install"] = bool(chan.get("full_install", True))
    return base

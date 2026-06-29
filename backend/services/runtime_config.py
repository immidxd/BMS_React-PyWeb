# -*- coding: utf-8 -*-
"""
Єдиний шар runtime-конфігурації: ПЛАТФОРМА + КАНАЛ + FEATURE-ПРАПОРИ.

Навіщо
──────
Дає змогу одному й тому самому білду поводитись по-різному залежно від машини,
БЕЗ розгалуження коду й без двох кодових баз. Відповідає на три задачі:

  1. Експериментальні білди, що не чіпають інших → КАНАЛ (dev/beta/stable).
     Авто-апдейтер тягне лише свій канал; прапори можуть відрізнятись по каналах.

  2. Інший UI під Windows / певну конфігурацію (тільки фронтенд) → ПЛАТФОРМА.
     Фронтенд читає /api/runtime-config і рендерить відповідно. Бекенд не чіпаємо.

  3. Обмежити функцію на Windows, але лишити в себе → FEATURE-ПРАПОР по платформі.
     На Mac (dev) увімкнено, на Windows (stable) вимкнено — один рядок у правилах.

Порядок застосування (наступне перекриває попереднє):
    DEFAULT_FLAGS  →  правила по платформі  →  правила по каналу  →  файл-оверайди

Файл-оверайди (per-машина, БЕЗ перезбірки):
    Windows : %LOCALAPPDATA%\\BMS\\config.json
    macOS   : ~/Library/Application Support/BMS/config.json
Приклад вмісту:
    { "channel": "beta", "flags": { "olx_publishing": false } }

ВАЖЛИВО: модуль чистий і дешевий — лише читає опційний JSON, без побічних ефектів.
Поки фронтенд не споживає ці прапори, увімкнення/вимкнення нічого не змінює в UI,
тож додавання модуля безпечне для поточної поведінки.
"""

from __future__ import annotations

import os
import sys
import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("bms.runtime_config")

VALID_CHANNELS = ("dev", "beta", "stable")


# ── платформа ──────────────────────────────────────────────────────────────────
def detect_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


# ── шляхи конфігу (співпадають з логікою deploy/embedded_db.py) ─────────────────
def config_dir() -> Path:
    override = os.getenv("BMS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if detect_platform() == "windows":
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return Path(base) / "BMS"
    return Path(os.path.expanduser("~/Library/Application Support/BMS"))


def config_file() -> Path:
    return config_dir() / "config.json"


# ── резолв секретів/кредів/сесій (портативність Mac ↔ Windows) ──────────────────
# Принцип: ПРОД (Windows) тримає секрети у %LOCALAPPDATA%\BMS; DEV (Mac) — у проєкті
# (mcp-google-sheets/, backend/.telegram_session/). Спільний код, рантайм-резолв —
# жодних хардкод-шляхів і жодного форку версій.
def _repo_root() -> Path:
    # backend/services/runtime_config.py → parents[2] = корінь репо
    return Path(__file__).resolve().parents[2]


def credentials_file(env_var: str = "", filename: str = "working_credentials.json") -> Optional[str]:
    """Шлях до файлу кредів сервіс-акаунта Google.
    Пріоритет: env (якщо заданий) → %BMS%/filename → legacy mcp-google-sheets/filename.
    У frozen-білді legacy-шляху нема → береться BMS-тека. Повертає None, якщо ніде нема.
    """
    if env_var:
        v = os.getenv(env_var)
        if v:
            return v
    for cand in (config_dir() / filename,
                 _repo_root() / "mcp-google-sheets" / filename):
        if cand.is_file():
            return str(cand)
    return None


def telegram_session_prefix() -> str:
    """Префікс шляху telethon-сесії (telethon додасть '.session').
    Пріоритет: env BMS_TELEGRAM_SESSION → %BMS%/bms (якщо там є bms.session) →
    legacy backend/.telegram_session/bms (dev-Mac). Для нових прод-інсталяцій —
    дефолт у BMS-теці (створюємо за потреби).
    """
    env = os.getenv("BMS_TELEGRAM_SESSION")
    if env:
        return env
    cfg = config_dir()
    if (cfg / "bms.session").is_file():
        return str(cfg / "bms")
    legacy_dir = _repo_root() / "backend" / ".telegram_session"
    if (legacy_dir / "bms.session").is_file():
        return str(legacy_dir / "bms")
    try:
        cfg.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(cfg / "bms")


# ── версія ─────────────────────────────────────────────────────────────────────
def app_version() -> str:
    """Версія застосунку: env BMS_VERSION → файл VERSION у корені → 'dev'."""
    env_v = os.getenv("BMS_VERSION")
    if env_v:
        return env_v
    try:
        root = Path(__file__).resolve().parents[2]  # backend/services/ → repo root
        vfile = root / "VERSION"
        if vfile.exists():
            return vfile.read_text(encoding="utf-8").strip() or "dev"
    except Exception:  # noqa: BLE001
        pass
    return "dev"


# ── канал ──────────────────────────────────────────────────────────────────────
def _read_file_overrides() -> Dict[str, Any]:
    path = config_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("Не зміг прочитати %s: %s — ігнорую оверайди", path, e)
        return {}


def resolve_channel(file_overrides: Dict[str, Any] | None = None) -> str:
    """Канал: env BMS_CHANNEL → файл config.json → дефолт по платформі."""
    if file_overrides is None:
        file_overrides = _read_file_overrides()
    ch = os.getenv("BMS_CHANNEL") or file_overrides.get("channel")
    if ch in VALID_CHANNELS:
        return ch
    # дефолт: на Windows-проді — stable, на dev-машині (Mac) — dev
    return "stable" if detect_platform() == "windows" else "dev"


# ── FEATURE-ПРАПОРИ ────────────────────────────────────────────────────────────
# Базові значення. Тут НІЧОГО не вимикаємо за замовчуванням, щоб поточна
# поведінка лишалась незмінною, поки фронтенд не почне читати ці прапори.
DEFAULT_FLAGS: Dict[str, bool] = {
    # приклади-плейсхолдери — реальні прапори додаватимемо за потреби:
    "olx_publishing": True,        # публікація на OLX
    "telegram_publishing": True,    # публікація в Telegram
    "experimental_ui": False,       # експериментальні елементи інтерфейсу
}

# Перекриття по платформі. Приклад сценарію №3 (закоментовано — увімкнути за потреби):
#   "windows": {"olx_publishing": False}  → на Windows-проді OLX вимкнено, у тебе на Mac лишається
PLATFORM_FLAG_OVERRIDES: Dict[str, Dict[str, bool]] = {
    # "windows": {},
    # "darwin": {},
}

# Перекриття по каналу. Приклад: експерименти видно лише на dev/beta.
CHANNEL_FLAG_OVERRIDES: Dict[str, Dict[str, bool]] = {
    "dev": {"experimental_ui": True},
    "beta": {"experimental_ui": True},
    # "stable": {}  — нічого експериментального
}


def compute_flags(platform: str, channel: str,
                  file_overrides: Dict[str, Any]) -> Dict[str, bool]:
    flags = dict(DEFAULT_FLAGS)
    flags.update(PLATFORM_FLAG_OVERRIDES.get(platform, {}))
    flags.update(CHANNEL_FLAG_OVERRIDES.get(channel, {}))
    # файл-оверайди (per-машина) мають найвищий пріоритет
    file_flags = file_overrides.get("flags")
    if isinstance(file_flags, dict):
        for k, v in file_flags.items():
            if isinstance(v, bool):
                flags[k] = v
    return flags


# ── публічне API ───────────────────────────────────────────────────────────────
def get_runtime_config() -> Dict[str, Any]:
    """Зведена конфігурація для бекенду і для віддачі фронтенду."""
    file_overrides = _read_file_overrides()
    platform = detect_platform()
    channel = resolve_channel(file_overrides)
    flags = compute_flags(platform, channel, file_overrides)
    return {
        "platform": platform,
        "channel": channel,
        "version": app_version(),
        "flags": flags,
    }


def is_enabled(flag: str) -> bool:
    """Зручний хелпер для бекенд-коду: gate функції за прапором."""
    return bool(get_runtime_config()["flags"].get(flag, False))

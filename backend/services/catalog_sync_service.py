"""Неблокуючий тригер синхронізації публічного каталогу.

BMS пише стан публікації товарів у локальну таблицю ``catalog_listings``.
Публічний Telegram Mini App читає хмарну копію, яку оновлює
``~/Desktop/BMS_catalog/cloud/sync_to_cloud.py``.

Цей модуль не змінює дані напряму: він лише акуратно запускає існуючий sync у
фоні після локального commit. Якщо sync уже триває, наступний запуск
коалеситься в один follow-up, щоб не плодити паралельні повні синхрони.

⚠️ Безкоштовний ліміт Neon (CLAUDE.md): кожен синк будить хмарну БД на ~6 хв.
Тому автоматичні (після парсингу) синки — не частіше за інтервал рівня бюджету
(``cloud_budget.sync_min_interval_sec``, ≥20 хв), з відкладеним запуском, щоб
остання зміна все одно доїхала. Ручні дії користувача (тумблер публікації) —
одразу, поки бюджет не в «economy». На рівні «stop» хмару не будимо взагалі.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from services import cloud_budget
except ImportError:
    from backend.services import cloud_budget

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNNING = False
_PENDING: Optional[tuple[str, bool]] = None     # (reason, urgent) — follow-up після поточного
_LAST_START = 0.0
_TIMER: Optional[threading.Timer] = None
_DEFERRED_REASON: Optional[str] = None


def _catalog_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("BMS_CATALOG_DIR", "~/Desktop/BMS_catalog")))


def _sync_paths() -> tuple[Path, Path, Path]:
    catalog_dir = _catalog_dir()
    return catalog_dir, catalog_dir / "venv" / "bin" / "python", catalog_dir / "cloud" / "sync_to_cloud.py"


def trigger_catalog_cloud_sync(reason: str, urgent: bool = True) -> bool:
    """Запустити cloud-sync у фоні.

    urgent=True — дія користувача (одразу, якщо бюджет дозволяє);
    urgent=False — автоматика (після парсингу): не частіше за інтервал рівня бюджету.

    Returns:
        True — sync запущено або поставлено в чергу/відкладено.
        False — sync вимкнений, бюджет Neon на «stop» або BMS_catalog не знайдено.
    """

    if os.getenv("CATALOG_CLOUD_SYNC", "1") == "0":
        logger.info("Catalog cloud-sync disabled by CATALOG_CLOUD_SYNC=0 (%s)", reason)
        return False

    catalog_dir, py, script = _sync_paths()
    if not (py.is_file() and script.is_file()):
        logger.info("Catalog cloud-sync skipped: BMS_catalog runtime not found at %s", catalog_dir)
        return False

    if not cloud_budget.cloud_wake_allowed():
        logger.warning("Catalog cloud-sync skipped: бюджет Neon на рівні stop (%s)", reason)
        return False

    global _RUNNING, _PENDING, _LAST_START, _TIMER, _DEFERRED_REASON
    lv = cloud_budget.level()
    interval = 0.0 if (urgent and lv in ("ok", "warn", "unknown")) else cloud_budget.sync_min_interval_sec()
    with _LOCK:
        if _RUNNING:
            _PENDING = (reason, urgent or bool(_PENDING and _PENDING[1]))
            logger.info("Catalog cloud-sync already running; queued follow-up (%s)", reason)
            return True
        wait = _LAST_START + interval - time.time()
        if wait > 0:
            _DEFERRED_REASON = reason
            if _TIMER is None:
                _TIMER = threading.Timer(wait, _fire_deferred)
                _TIMER.daemon = True
                _TIMER.start()
                logger.info("Catalog cloud-sync deferred %.0fs (budget %s): %s", wait, lv, reason)
            return True
        _RUNNING = True
        _LAST_START = time.time()
        if _TIMER is not None:          # цей запуск покриває й відкладений
            _TIMER.cancel()
            _TIMER, _DEFERRED_REASON = None, None

    thread = threading.Thread(
        target=_sync_worker,
        args=(catalog_dir, py, script, reason),
        name="catalog-cloud-sync",
        daemon=True,
    )
    thread.start()
    logger.info("Catalog cloud-sync queued (%s)", reason)
    return True


def _fire_deferred() -> None:
    global _TIMER, _DEFERRED_REASON, _LAST_START
    with _LOCK:
        reason, _TIMER, _DEFERRED_REASON = _DEFERRED_REASON, None, None
        _LAST_START = 0.0 if reason else _LAST_START   # інтервал уже вичекано
    if reason:
        trigger_catalog_cloud_sync(f"{reason} / deferred", urgent=False)


def _sync_worker(catalog_dir: Path, py: Path, script: Path, reason: str) -> None:
    global _RUNNING, _PENDING

    try:
        _run_once(catalog_dir, py, script, reason)
    except Exception as exc:
        logger.warning("Catalog cloud-sync failed (%s): %s", reason, exc)

    with _LOCK:
        pending, _PENDING = _PENDING, None
        _RUNNING = False
    if pending:
        trigger_catalog_cloud_sync(f"{pending[0]} / follow-up", urgent=pending[1])


def _run_once(catalog_dir: Path, py: Path, script: Path, reason: str) -> None:
    timeout = int(os.getenv("CATALOG_CLOUD_SYNC_TIMEOUT", "180"))
    log_path = Path(os.getenv("CATALOG_CLOUD_SYNC_LOG", "/tmp/bms_catalog_sync.out"))
    stamp = _dt.datetime.now().isoformat(timespec="seconds")

    with log_path.open("ab") as out:
        out.write(f"\n--- BMS catalog sync trigger {stamp}: {reason} ---\n".encode("utf-8"))
        completed = subprocess.run(
            [str(py), str(script)],
            cwd=str(catalog_dir),
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    if completed.returncode == 0:
        logger.info("Catalog cloud-sync finished (%s)", reason)
    else:
        logger.warning("Catalog cloud-sync exited with code %s (%s)", completed.returncode, reason)

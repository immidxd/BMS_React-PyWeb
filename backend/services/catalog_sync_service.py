"""Неблокуючий тригер синхронізації публічного каталогу.

BMS пише стан публікації товарів у локальну таблицю ``catalog_listings``.
Публічний Telegram Mini App читає хмарну копію, яку оновлює
``~/Desktop/BMS_catalog/cloud/sync_to_cloud.py``.

Цей модуль не змінює дані напряму: він лише акуратно запускає існуючий sync у
фоні після локального commit. Якщо sync уже триває, наступний запуск
коалеситься в один follow-up, щоб не плодити паралельні повні синхрони.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNNING = False
_PENDING_REASON: Optional[str] = None


def _catalog_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("BMS_CATALOG_DIR", "~/Desktop/BMS_catalog")))


def _sync_paths() -> tuple[Path, Path, Path]:
    catalog_dir = _catalog_dir()
    return catalog_dir, catalog_dir / "venv" / "bin" / "python", catalog_dir / "cloud" / "sync_to_cloud.py"


def trigger_catalog_cloud_sync(reason: str) -> bool:
    """Запустити cloud-sync у фоні.

    Returns:
        True — sync запущено або поставлено в чергу.
        False — sync вимкнений або BMS_catalog не знайдено на цій машині.
    """

    if os.getenv("CATALOG_CLOUD_SYNC", "1") == "0":
        logger.info("Catalog cloud-sync disabled by CATALOG_CLOUD_SYNC=0 (%s)", reason)
        return False

    catalog_dir, py, script = _sync_paths()
    if not (py.is_file() and script.is_file()):
        logger.info("Catalog cloud-sync skipped: BMS_catalog runtime not found at %s", catalog_dir)
        return False

    global _RUNNING, _PENDING_REASON
    with _LOCK:
        if _RUNNING:
            _PENDING_REASON = reason
            logger.info("Catalog cloud-sync already running; queued follow-up (%s)", reason)
            return True
        _RUNNING = True

    thread = threading.Thread(
        target=_sync_worker,
        args=(catalog_dir, py, script, reason),
        name="catalog-cloud-sync",
        daemon=True,
    )
    thread.start()
    logger.info("Catalog cloud-sync queued (%s)", reason)
    return True


def _sync_worker(catalog_dir: Path, py: Path, script: Path, reason: str) -> None:
    global _RUNNING, _PENDING_REASON

    current_reason = reason
    while True:
        try:
            _run_once(catalog_dir, py, script, current_reason)
        except Exception as exc:
            logger.warning("Catalog cloud-sync failed (%s): %s", current_reason, exc)

        with _LOCK:
            if _PENDING_REASON:
                current_reason = f"{_PENDING_REASON} / follow-up"
                _PENDING_REASON = None
                continue
            _RUNNING = False
            return


def _reap_orphan_syncs(script: Path) -> None:
    """Прибрати «осиротілі» sync_to_cloud.py з попереднього запуску BMS.

    Якщо бекенд перезапустили під час синхрону, дочірній процес лишається жити
    (PPID=1) і може вічно тримати ВІДКРИТУ транзакцію в локальній БД, поки висить
    на мережевому запиті до хмари. Наступний ALTER/VACUUM стає в чергу за нею, а
    за ним — усі читачі: програма зависає повністю. Один нормальний синхрон
    триває ~5с, тож будь-який живий процес на момент старту нового — зомбі."""
    try:
        out = subprocess.run(["pgrep", "-f", str(script)],
                             capture_output=True, text=True, timeout=5)
        for pid in (p for p in out.stdout.split() if p.isdigit()):
            if int(pid) == os.getpid():
                continue
            try:
                os.kill(int(pid), 15)
                logger.warning("Reaped orphaned catalog cloud-sync pid=%s", pid)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception as exc:  # pgrep відсутній (Windows) тощо — не критично
        logger.debug("Orphan cloud-sync reap skipped: %s", exc)


def _run_once(catalog_dir: Path, py: Path, script: Path, reason: str) -> None:
    _reap_orphan_syncs(script)
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

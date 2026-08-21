"""Non-blocking cloud-to-BMS refresh for public catalog analytics."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_RUNNING = False


def is_running() -> bool:
    with _LOCK:
        return _RUNNING


def trigger(reason: str = "statistics") -> bool:
    """Queue one refresh; repeated UI polls never create parallel sync processes."""
    global _RUNNING
    if os.getenv("CATALOG_ANALYTICS_SYNC", "1") == "0":
        return False
    root = Path(os.path.expanduser(os.getenv("BMS_CATALOG_DIR", "~/Desktop/BMS_catalog")))
    py = root / "venv" / "bin" / "python"
    script = root / "cloud" / "sync_analytics_from_cloud.py"
    if not py.is_file() or not script.is_file():
        logger.info("Catalog analytics runtime not found at %s", root)
        return False
    with _LOCK:
        if _RUNNING:
            return True
        _RUNNING = True
    threading.Thread(
        target=_worker,
        args=(root, py, script, reason),
        name="catalog-analytics-sync",
        daemon=True,
    ).start()
    return True


def _worker(root: Path, py: Path, script: Path, reason: str) -> None:
    global _RUNNING
    try:
        timeout = int(os.getenv("CATALOG_ANALYTICS_SYNC_TIMEOUT", "120"))
        log_path = Path(os.getenv("CATALOG_ANALYTICS_SYNC_LOG", "/tmp/bms_catalog_analytics_sync.out"))
        with log_path.open("ab") as out:
            completed = subprocess.run(
                [str(py), str(script)],
                cwd=str(root),
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        if completed.returncode:
            logger.warning("Catalog analytics sync exited %s (%s)", completed.returncode, reason)
    except Exception as exc:
        logger.warning("Catalog analytics sync failed (%s): %s", reason, exc)
    finally:
        with _LOCK:
            _RUNNING = False

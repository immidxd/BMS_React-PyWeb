#!/usr/bin/env python3
"""Wait for Prom's next import window, then submit one complete photo-only batch."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models.database import SessionLocal  # noqa: E402
from backend.scripts.update_prom_main_images import run  # noqa: E402
from backend.services.prom_service import import_limit_status  # noqa: E402

RESULT_PATH = Path("/private/tmp/bs-prom-photo-refresh-result.json")
DEADLINE = datetime.now().astimezone() + timedelta(hours=8)


def _record(payload: dict) -> None:
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    print(f"[waiter] started={datetime.now().astimezone().isoformat()}", flush=True)
    while datetime.now().astimezone() < DEADLINE:
        db = SessionLocal()
        try:
            status = import_limit_status(db)
        finally:
            db.close()
        if status.get("limit_active"):
            remaining = max(5, int(status.get("limit_retry_in_seconds") or 0) + 3)
            print(
                f"[waiter] Prom window estimated at {status.get('limit_retry_at')}; "
                f"sleep={min(30, remaining)}s",
                flush=True,
            )
            time.sleep(min(30, remaining))
            continue
        try:
            result = run(execute=True, phase="all")
            payload = {
                "ok": True,
                "finished_at": datetime.now().astimezone().isoformat(),
                "result": result,
            }
            _record(payload)
            print("[waiter] SUCCESS " + json.dumps(payload, ensure_ascii=False, default=str), flush=True)
            return 0
        except Exception as exc:
            message = str(exc)
            print(f"[waiter] attempt failed: {message}", flush=True)
            if "Prom не приймає публікацію" not in message:
                _record({
                    "ok": False,
                    "finished_at": datetime.now().astimezone().isoformat(),
                    "error": message,
                })
                return 1
            time.sleep(60)
    _record({
        "ok": False,
        "finished_at": datetime.now().astimezone().isoformat(),
        "error": "Prom не відкрив імпортне вікно протягом 8 годин",
    })
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

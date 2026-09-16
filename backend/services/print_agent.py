"""Агент друку: завдання з хмари (телефон працівника) → Xprinter у крамниці.

Принтер стоїть у локальній мережі за мостом на Windows-ПК; з хмари (Railway)
до нього шляху нема. Тому Mini App «BMS Склад» кладе завдання у `wh_print_jobs`
(«етикетка коробки Z1», «стікер Ф4350 ×2»), а цей потік у BMS кожні кілька
секунд опитує чергу, бере завдання (claim — щоб два агенти не надрукували
двічі), рендерить тим самим label_service і шле на принтер по TSPL.

Умови роботи: BMS запущена, є мережевий принтер (label_printer.json або
BMS_LABEL_PRINTER_HOST), є доступ до хмари (CATALOG_ADMIN_TOKEN). Без цього
потік просто спить і пульс не шле — телефон покаже «у черзі, надрукується,
щойно BMS буде запущена». Вимкнути: BMS_PRINT_AGENT=0.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from models.database import SessionLocal
    from services import label_service as ls
    from services import warehouse_client as cloud
except ImportError:  # pragma: no cover
    from backend.models.database import SessionLocal
    from backend.services import label_service as ls
    from backend.services import warehouse_client as cloud

logger = logging.getLogger(__name__)

POLL_SEC = float(os.getenv("BMS_PRINT_AGENT_POLL_SEC", "5") or 5)
HEARTBEAT_SEC = 30.0
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_state: Dict[str, Any] = {"running": False, "last_poll": None, "last_job": None, "printed": 0, "failed": 0, "error": None}


def agent_name() -> str:
    return f"bms@{platform.node() or 'mac'}"[:64]


def status() -> Dict[str, Any]:
    return {**_state, "poll_sec": POLL_SEC, "printer": ls.network_printer_host()}


def _print_job(job: Dict[str, Any], host: str) -> None:
    kind, payload = job["kind"], job.get("payload") or {}
    copies = max(1, int(payload.get("copies") or 1))
    if kind == "box_label":
        box = cloud.request("GET", f"/boxes/{payload['code']}")
        page = ls.render_box_label(box["code"], box.get("title") or "", box.get("location") or "")
        ls.print_tspl([page], ls.get_layout(ls.DEFAULT_LAYOUT), host, copies=copies)
        return
    if kind == "stickers":
        ids = [int(i) for i in payload.get("product_ids") or []]
        layout = payload.get("layout") or ls.DEFAULT_LAYOUT
        db = SessionLocal()
        try:
            rows = ls.load_rows(db, ids)
            items = [ls.item_from_row(r, copies=copies) for r in rows]
            if not items:
                raise RuntimeError("товарів не знайдено в базі BMS")
            pages = ls.render_pages(items, layout, show_price=True)
            ls.print_tspl(pages, ls.get_layout(layout), host)
            ls.mark_printed(db, items, layout=layout, pages=len(pages), mode="agent", printer=host, file_path=None)
            db.commit()
        finally:
            db.close()
        return
    raise RuntimeError(f"невідомий тип завдання: {kind}")


def _tick(last_heartbeat: List[float]) -> None:
    host = ls.network_printer_host()
    if not host or not cloud.is_configured():
        return
    if not ls.network_printer_reachable(host, timeout=1.0):
        _state["error"] = f"принтер {host} недоступний"
        return
    _state["error"] = None
    now = time.time()
    if now - last_heartbeat[0] > HEARTBEAT_SEC:
        cloud.request("POST", "/print-agent/heartbeat", params={"agent": agent_name(), "printer": host}, timeout=8)
        last_heartbeat[0] = now
    jobs = cloud.request("GET", "/print-jobs", params={"status": "queued", "limit": 5}, timeout=8).get("jobs") or []
    _state["last_poll"] = time.strftime("%H:%M:%S")
    for job in jobs:
        try:
            claimed = cloud.request("POST", f"/print-jobs/{job['id']}/claim", params={"agent": agent_name()}, timeout=8)
        except cloud.CloudError as exc:
            if exc.status == 409:
                continue  # взяв хтось інший
            raise
        try:
            _print_job(claimed, host)
            cloud.request("POST", f"/print-jobs/{job['id']}/done", json={"ok": True}, timeout=8)
            _state["printed"] += 1
            _state["last_job"] = f"#{job['id']} {job['kind']} ok"
            logger.info("Агент друку: завдання #%s (%s) надруковано", job["id"], job["kind"])
        except Exception as exc:  # noqa: BLE001
            _state["failed"] += 1
            _state["last_job"] = f"#{job['id']} {job['kind']} FAIL: {exc}"
            logger.warning("Агент друку: завдання #%s не вдалось: %s", job["id"], exc)
            cloud.request("POST", f"/print-jobs/{job['id']}/done", json={"ok": False, "error": str(exc)[:300]}, timeout=8)


def _loop() -> None:
    last_heartbeat = [0.0]
    _state["running"] = True
    while not _stop.is_set():
        try:
            _tick(last_heartbeat)
        except Exception as exc:  # noqa: BLE001 — мережа моргнула, не вмираємо
            _state["error"] = str(exc)[:200]
        _stop.wait(POLL_SEC)
    _state["running"] = False


def start() -> bool:
    """Запустити фоновий агент (ідемпотентно). False — вимкнено змінною."""
    global _thread
    if (os.getenv("BMS_PRINT_AGENT", "1") or "1").lower() in ("0", "false", "no", "off"):
        return False
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="print-agent", daemon=True)
    _thread.start()
    logger.info("Агент друку запущено (%s, кожні %.0f с)", agent_name(), POLL_SEC)
    return True


def stop() -> None:
    _stop.set()

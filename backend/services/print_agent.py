"""Агент друку: завдання з хмари (телефон працівника) → Xprinter у крамниці.

Принтер стоїть у локальній мережі за мостом на Windows-ПК; з хмари (Railway)
до нього шляху нема. Тому Mini App «BMS Склад» кладе завдання у `wh_print_jobs`
(«етикетка коробки Z1», «стікер Ф4350 ×2», «правка товару: ціна/стан»), а цей потік у BMS кожні кілька
секунд опитує чергу, бере завдання (claim — щоб два агенти не надрукували
двічі), рендерить тим самим label_service і шле на принтер по TSPL.

Умови роботи: BMS запущена, є доступ до хмари (CATALOG_ADMIN_TOKEN); для
друку — ще й мережевий принтер (label_printer.json або BMS_LABEL_PRINTER_HOST).
Без принтера друк лишається в черзі, а правки товару застосовуються. Без BMS
потік не працює — телефон покаже «у черзі, виконається, щойно BMS буде
запущена». Вимкнути: BMS_PRINT_AGENT=0.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from models import models
    from models.database import SessionLocal
    from schemas import product as schemas
    from services import label_service as ls
    from services import product_service
    from services import warehouse_client as cloud
except ImportError:  # pragma: no cover
    from backend.models import models
    from backend.models.database import SessionLocal
    from backend.schemas import product as schemas
    from backend.services import label_service as ls
    from backend.services import product_service
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
    if kind == "product_edit":
        _apply_product_edit(int(payload["product_id"]), dict(payload.get("fields") or {}))
        return
    raise RuntimeError(f"невідомий тип завдання: {kind}")


def _apply_product_edit(product_id: int, fields: Dict[str, Any]) -> None:
    """Правка з телефона — ТИМ САМИМ шляхом, що й картка товару в BMS
    (PUT /api/products/{id}): product_service.update_product → база + правило
    «стара ціна» (лише при зниженні, якщо порожня) + лок поля від парсера +
    пропагація ціни на ростовку; далі enqueue_writeback_for → Журнал.
    Після цього — патч дзеркала в хмарі, щоб телефон бачив свіже одразу.
    Стан приходить назвою (як у картці) — резолв у current_conditionid робить сервіс."""
    data: Dict[str, Any] = {}
    if fields.get("price") is not None:
        data["price"] = float(fields["price"])
    if fields.get("current_condition_name"):
        data["current_condition_name"] = str(fields["current_condition_name"]).strip()
    if not data:
        raise RuntimeError("порожня правка")
    db = SessionLocal()
    try:
        if not product_service.get_product(db, product_id):
            raise RuntimeError(f"товар id={product_id} не знайдено в BMS")
        updated = product_service.update_product(db, product_id, schemas.ProductUpdate(**data))
        if not updated:
            raise RuntimeError("update_product повернув порожньо")
        product_service.enqueue_writeback_for(db, updated)
        # Дзеркало — всієї ростовки: ціна в BMS розходиться на рядки того ж
        # номера (того ж стану, без власного лока), стан — лише цей рядок.
        rows = db.query(models.Product).filter(models.Product.productnumber == updated.productnumber).all()
        mirror = [{"id": r.id, "price": r.price, "oldprice": r.oldprice,
                   "current_conditionid": r.current_conditionid} for r in rows]
    finally:
        db.close()
    cloud.request("POST", "/products/mirror", json={"rows": mirror}, timeout=10)


PRINT_KINDS = {"box_label", "stickers"}


def _parse_in_progress() -> bool:
    """Чи зараз іде парсинг журналу. Парсер перезаписує поля товарів зі
    знімком локів «до/після», тож правку, що влізе всередину його 10–20 с,
    він може відкотити. Правки з телефона в цей час лишаємо в черзі."""
    db = SessionLocal()
    try:
        return bool(db.query(models.ParsingJob.id)
                    .filter(models.ParsingJob.status.in_(("queued", "running"))).first())
    except Exception:  # noqa: BLE001 — нема таблиці/базa моргнула: не блокуємо
        return False
    finally:
        db.close()


def _tick(last_heartbeat: List[float]) -> None:
    if not cloud.is_configured():
        return
    # Принтер потрібен лише для друку; правки товару застосовуємо і без нього.
    host = ls.network_printer_host()
    printer_ok = bool(host) and ls.network_printer_reachable(host, timeout=1.0)
    _state["error"] = None if printer_ok else (f"принтер {host} недоступний" if host else "мережевий принтер не налаштовано")
    now = time.time()
    if now - last_heartbeat[0] > HEARTBEAT_SEC:
        cloud.request("POST", "/print-agent/heartbeat",
                      params={"agent": agent_name(), "printer": host if printer_ok else None}, timeout=8)
        last_heartbeat[0] = now
    jobs = cloud.request("GET", "/print-jobs", params={"status": "queued", "limit": 5}, timeout=8).get("jobs") or []
    _state["last_poll"] = time.strftime("%H:%M:%S")
    parsing = any(j["kind"] == "product_edit" for j in jobs) and _parse_in_progress()
    for job in jobs:
        if job["kind"] in PRINT_KINDS and not printer_ok:
            continue  # лишаємо в черзі до появи принтера
        if job["kind"] == "product_edit" and parsing:
            _state["error"] = "іде парсинг журналу — правки чекають"
            continue  # застосуємо за кілька секунд, коли парсер закінчить
        try:
            claimed = cloud.request("POST", f"/print-jobs/{job['id']}/claim", params={"agent": agent_name()}, timeout=8)
        except cloud.CloudError as exc:
            if exc.status == 409:
                continue  # взяв хтось інший
            raise
        try:
            _print_job(claimed, host or "")
            cloud.request("POST", f"/print-jobs/{job['id']}/done", json={"ok": True}, timeout=8)
            _state["printed"] += 1
            _state["last_job"] = f"#{job['id']} {job['kind']} ok"
            logger.info("Агент: завдання #%s (%s) виконано", job["id"], job["kind"])
        except Exception as exc:  # noqa: BLE001
            _state["failed"] += 1
            _state["last_job"] = f"#{job['id']} {job['kind']} FAIL: {exc}"
            logger.warning("Агент: завдання #%s не вдалось: %s", job["id"], exc)
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

"""API стікерів із QR: черга друку, перегляд аркуша, друк/збереження PDF.

Потік у програмі:
  • «Додати товар» → товар сам стає в чергу (див. deliveries.add_product_to_delivery);
  • таблиця товарів → «Дії → Друк стікерів» над виділеними, або «у чергу»;
  • картка завозу → усі товари завозу;
  • дровер «Стікери»: розкладка 2×2/2×3/3×3, копії, прев'ю, «Друк».

Сам рендер і робота з БД — у services/label_service.py; тут лише HTTP-контракт.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from models.database import get_db
    from services import label_service as ls
    from services.file_saver import save_bytes
    from services.runtime_config import is_desktop_shell
except ImportError:  # pragma: no cover
    from backend.models.database import get_db
    from backend.services import label_service as ls
    from backend.services.file_saver import save_bytes
    from backend.services.runtime_config import is_desktop_shell

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/labels", tags=["labels"])

MAX_STICKERS = 2000  # запобіжник від випадкового «друк усього» на 13 000 пар


# ───────────────────────────── схеми ─────────────────────────────────────────

class ItemIn(BaseModel):
    product_id: int
    copies: int = Field(1, ge=0, le=99)


class ResolveIn(BaseModel):
    product_ids: Optional[List[int]] = None
    delivery_id: Optional[int] = None
    from_queue: bool = False


class EnqueueIn(BaseModel):
    product_ids: Optional[List[int]] = None
    delivery_id: Optional[int] = None
    copies: Optional[int] = Field(None, ge=1, le=99)  # None → наявна кількість
    source: str = "selection"


class RenderIn(BaseModel):
    items: List[ItemIn]
    layout: str = ls.DEFAULT_LAYOUT
    show_price: bool = False
    cut_marks: bool = True


class PrintIn(RenderIn):
    mode: str = "print"            # print | save | download
    printer: Optional[str] = None


class QueueCopiesIn(BaseModel):
    copies: int = Field(..., ge=1, le=99)


# ───────────────────────────── допоміжне ─────────────────────────────────────

def _row_summary(r: Dict[str, Any], copies: int, queue_id: Optional[int] = None,
                 source: Optional[str] = None, added_at: Optional[datetime] = None) -> Dict[str, Any]:
    avail = int(r.get("available_qty") or 0)
    return {
        "product_id": int(r["id"]),
        "productnumber": r.get("productnumber"),
        "number": ls.display_number(r.get("productnumber")),
        "size": ls._size_text(r),
        "insole": ls._insole_text(r),
        "brand": r.get("brandname"),
        "model": r.get("model"),
        "type": r.get("typename"),
        "color": r.get("colorname"),
        "price": r.get("price"),
        "quantity": int(r.get("quantity") or 0),
        "sold_count": int(r.get("sold_count") or 0),
        "available_qty": avail,
        "sold": avail <= 0,
        "mainimage": r.get("mainimage"),
        "label_printed_at": r.get("label_printed_at"),
        "copies": int(copies),
        "queue_id": queue_id,
        "source": source,
        "added_at": added_at,
    }


def _default_copies(r: Dict[str, Any]) -> int:
    """Скільки стікерів треба за замовчуванням: наявні пари (quantity − продано)."""
    return max(0, int(r.get("available_qty") or 0))


def _build_items(db: Session, items: List[ItemIn]) -> List[ls.LabelItem]:
    wanted = [(it.product_id, it.copies) for it in items if it.copies > 0]
    if not wanted:
        raise HTTPException(status_code=400, detail="Немає стікерів для друку (усі копії = 0)")
    total = sum(c for _, c in wanted)
    if total > MAX_STICKERS:
        raise HTTPException(status_code=400,
                            detail=f"Забагато стікерів за раз: {total} (межа {MAX_STICKERS})")
    rows = {r["id"]: r for r in ls.load_rows(db, [pid for pid, _ in wanted])}
    out: List[ls.LabelItem] = []
    missing: List[int] = []
    for pid, copies in wanted:
        r = rows.get(pid)
        if not r:
            missing.append(pid)
            continue
        out.append(ls.item_from_row(r, copies=copies))
    if missing:
        logger.warning("Стікери: товарів не знайдено, пропущено: %s", missing[:20])
    if not out:
        raise HTTPException(status_code=404, detail="Жодного з обраних товарів не знайдено")
    return out


# ───────────────────────────── ендпоїнти ─────────────────────────────────────

NET_PREFIX = "net:"  # ім'я «принтера» для мережевого друку: net:192.168.1.150


def _agent_status() -> Dict[str, Any]:
    try:
        try:
            from services import print_agent
        except ImportError:  # pragma: no cover
            from backend.services import print_agent
        return print_agent.status()
    except Exception:  # noqa: BLE001
        return {"running": False}


def _printer_options() -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    """Принтери для діалогу: мережевий (TSPL, без драйвера) + системні CUPS.
    Повертає (список, обраний за замовчуванням, чи можна друкувати звідси)."""
    printers: List[Dict[str, Any]] = []
    host = ls.network_printer_host()
    net_ok = bool(host) and ls.network_printer_reachable(host)
    if host:
        printers.append({"name": f"{NET_PREFIX}{host}", "label": f"Xprinter по мережі ({host})",
                         "kind": "network", "reachable": net_ok, "default": net_ok})
    for p in ls.list_printers():
        printers.append({"name": p["name"], "label": p["name"], "kind": "cups",
                         "reachable": True, "default": p.get("default", False) and not net_ok})
    preferred = f"{NET_PREFIX}{host}" if net_ok else ls.preferred_printer([p for p in printers if p["kind"] == "cups"])
    can_print = net_ok or (ls.can_print_here() and any(p["kind"] == "cups" for p in printers))
    return printers, preferred, can_print


@router.get("/config")
def labels_config(db: Session = Depends(get_db)):
    printers, preferred, can_print = _printer_options()
    return {
        "layouts": [spec.to_dict() for spec in ls.LAYOUTS.values()],
        "default_layout": ls.DEFAULT_LAYOUT,
        "printers": printers,
        "preferred_printer": preferred,
        "can_print": can_print,
        "network_printer": ls.network_printer_host(),
        # Лише коли принтер мовчить: у робочому стані підказка не потрібна.
        "network_hint": None if any(p["kind"] == "network" and p["reachable"] for p in printers)
                        else ls.network_hint(),
        "print_agent": _agent_status(),
        "desktop": is_desktop_shell(),
        "platform": ls.platform_name(),
        "queue_count": ls.queue_count(db),
    }


class NetPrinterIn(BaseModel):
    host: Optional[str] = None   # None/порожньо — забути


@router.post("/discover")
def labels_discover():
    """Знайти принтери етикеток у локальній мережі (порт 9100)."""
    return {"hosts": ls.discover_network_printers(), "my_ip": ls.local_ipv4()}


@router.put("/network-printer")
def labels_set_network_printer(payload: NetPrinterIn = Body(...)):
    host = (payload.host or "").strip() or None
    if host and not ls.network_printer_reachable(host):
        raise HTTPException(status_code=400, detail=f"Принтер {host} не відповідає на порту 9100")
    ls.save_network_printer_host(host)
    return {"host": host, "reachable": bool(host)}


@router.post("/test-print")
def labels_test_print(printer: Optional[str] = Query(None)):
    """Тестовий аркуш: один стікер-зразок і рамка — перевірити носій/щільність."""
    spec = ls.get_layout(ls.DEFAULT_LAYOUT)
    sample = ls.item_from_row(dict(id=0, productnumber="#ТЕСТ", sizeeu="40", measurementscm="26",
                                   brandname="BMS", model="перевірка друку", typename="стікер",
                                   colorname="чорний", gendername="унісекс", price=1234,
                                   current_condition_name="Новий"), copies=4)
    pages = ls.render_pages([sample], spec.key, show_price=True)
    name = printer or _printer_options()[1]
    if name and name.startswith(NET_PREFIX):
        n = ls.print_tspl(pages, spec, name[len(NET_PREFIX):])
        return {"printed": True, "printer": name, "pages": n}
    raise HTTPException(status_code=400, detail="Тестовий друк доступний лише для мережевого принтера")


@router.get("/queue/count")
def labels_queue_count(db: Session = Depends(get_db)):
    """Лише лічильник — для бейджа «До друку: N» (без опитування принтерів)."""
    return {"count": ls.queue_count(db)}


@router.get("/queue")
def labels_queue(db: Session = Depends(get_db)):
    pending = ls.queue_pending(db)
    rows = {r["id"]: r for r in ls.load_rows(db, [q["product_id"] for q in pending])}
    items, stale = [], []
    for q in pending:
        r = rows.get(q["product_id"])
        if not r:
            stale.append(q["id"])  # товар зник (злиття/видалення) — прибираємо мовчки
            continue
        items.append(_row_summary(r, q["copies"], queue_id=q["id"], source=q["source"],
                                  added_at=q["added_at"]))
    if stale:
        for qid in stale:
            ls.queue_remove(db, qid)
        db.commit()
    return {"items": items, "count": len(items),
            "stickers": sum(i["copies"] for i in items)}


@router.post("/queue")
def labels_enqueue(payload: EnqueueIn = Body(...), db: Session = Depends(get_db)):
    ids = list(payload.product_ids or [])
    if payload.delivery_id:
        ids += ls.product_ids_for_delivery(db, payload.delivery_id)
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        raise HTTPException(status_code=400, detail="Не передано товарів")
    rows = ls.load_rows(db, ids)
    entries, skipped_sold = [], 0
    for r in rows:
        copies = payload.copies if payload.copies is not None else _default_copies(r)
        if copies <= 0:
            skipped_sold += 1  # продане — стікер не потрібен
            continue
        entries.append((int(r["id"]), copies))
    res = ls.enqueue(db, entries, payload.source) if entries else {"added": 0, "updated": 0}
    db.commit()
    return {**res, "skipped_sold": skipped_sold, "count": ls.queue_count(db)}


@router.patch("/queue/{item_id}")
def labels_queue_copies(item_id: int, payload: QueueCopiesIn = Body(...),
                        db: Session = Depends(get_db)):
    if not ls.queue_update_copies(db, item_id, payload.copies):
        raise HTTPException(status_code=404, detail="Запису в черзі немає")
    db.commit()
    return {"id": item_id, "copies": payload.copies}


@router.delete("/queue/{item_id}")
def labels_queue_remove(item_id: int, db: Session = Depends(get_db)):
    n = ls.queue_remove(db, item_id)
    db.commit()
    return {"removed": n, "count": ls.queue_count(db)}


@router.delete("/queue")
def labels_queue_clear(db: Session = Depends(get_db)):
    n = ls.queue_remove(db, None)
    db.commit()
    return {"removed": n, "count": 0}


@router.post("/resolve")
def labels_resolve(payload: ResolveIn = Body(...), db: Session = Depends(get_db)):
    """Список стікерів для дровера: за виділенням, завозом або чергою — з копіями
    за замовчуванням, позначкою «продано» і датою попереднього друку."""
    if payload.from_queue:
        pending = ls.queue_pending(db)
        rows = {r["id"]: r for r in ls.load_rows(db, [q["product_id"] for q in pending])}
        items = [_row_summary(rows[q["product_id"]], q["copies"], queue_id=q["id"],
                              source=q["source"], added_at=q["added_at"])
                 for q in pending if q["product_id"] in rows]
        return {"items": items}
    ids = list(payload.product_ids or [])
    if payload.delivery_id:
        ids += ls.product_ids_for_delivery(db, payload.delivery_id)
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        raise HTTPException(status_code=400, detail="Не передано товарів")
    rows = ls.load_rows(db, ids)
    return {"items": [_row_summary(r, _default_copies(r)) for r in rows]}


@router.post("/preview")
def labels_preview(payload: RenderIn = Body(...), db: Session = Depends(get_db)):
    """Перша сторінка аркуша як PNG (base64) + скільки всього стікерів і аркушів."""
    try:
        spec = ls.get_layout(payload.layout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    items = _build_items(db, payload.items)
    stickers, pages = ls.page_count(items, spec.key)
    first = ls.render_pages(items, spec.key, show_price=payload.show_price,
                            cut_marks=payload.cut_marks, max_pages=1)
    png = ls.page_to_png(first[0]) if first else b""
    return {"png": base64.b64encode(png).decode("ascii"), "stickers": stickers, "pages": pages,
            "layout": spec.to_dict()}


@router.post("/print")
def labels_print(payload: PrintIn = Body(...), db: Session = Depends(get_db)):
    """Зібрати PDF і: `print` — на принтер (CUPS), `save` — у «Завантаження»,
    `download` — віддати файл браузеру. Після print/save товари позначаються як
    зі стікером і зникають із черги; download — теж (намір надрукувати є)."""
    try:
        spec = ls.get_layout(payload.layout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    mode = (payload.mode or "print").lower()
    if mode not in ("print", "save", "download"):
        raise HTTPException(status_code=400, detail="mode має бути print | save | download")
    items = _build_items(db, payload.items)
    stickers, pages = ls.page_count(items, spec.key)
    pdf = ls.pages_to_pdf(ls.render_pages(items, spec.key, show_price=payload.show_price,
                                          cut_marks=payload.cut_marks))
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"BMS стікери {stamp} ({stickers} шт, {spec.key}).pdf"

    if mode == "download":
        ls.mark_printed(db, items, layout=spec.key, pages=pages, mode=mode, printer=None,
                        file_path=None)
        db.commit()
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="labels.pdf"',
                                 "X-Stickers": str(stickers), "X-Pages": str(pages)})

    path, final_name = save_bytes(pdf, filename, "labels.pdf")
    printed, printer, message = False, None, ""
    if mode == "print":
        printer = payload.printer or _printer_options()[1]
        try:
            if not printer:
                raise RuntimeError("Принтер не знайдено — файл збережено, надрукуйте його вручну")
            if printer.startswith(NET_PREFIX):
                # Напряму на Xprinter по мережі (TSPL, порт 9100) — без драйвера.
                n = ls.print_tspl(ls.render_pages(items, spec.key, show_price=payload.show_price,
                                                  cut_marks=payload.cut_marks),
                                  spec, printer[len(NET_PREFIX):])
                out = f"Надіслано {n} арк. на {printer[len(NET_PREFIX):]}"
            else:
                out = ls.print_pdf(path, printer, spec)
            printed = True
            message = out or "Надіслано на принтер"
        except RuntimeError as exc:
            message = str(exc)
            logger.warning("Друк стікерів: %s", exc)
            ls.open_file(path)  # хоч відкрити PDF, щоб людина надрукувала сама
    else:
        ls.open_file(path)
    job_id = ls.mark_printed(db, items, layout=spec.key, pages=pages, mode=mode,
                             printer=printer if printed else None, file_path=path)
    db.commit()
    return {"job_id": job_id, "path": path, "filename": final_name, "stickers": stickers,
            "pages": pages, "printed": printed, "printer": printer, "message": message,
            "queue_count": ls.queue_count(db)}


@router.get("/jobs")
def labels_jobs(db: Session = Depends(get_db)):
    return {"jobs": ls.recent_jobs(db, 10)}


@router.get("/parse")
def labels_parse(code: str):
    """Розбір відсканованого тексту (для сканера й тестів): що це — товар чи коробка."""
    parsed = ls.parse_payload(code)
    if not parsed:
        raise HTTPException(status_code=422, detail="Це не код BMS")
    return parsed

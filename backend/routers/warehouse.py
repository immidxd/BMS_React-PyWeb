"""Склад у BMS: тонкий проксі до хмарного API (/api/wh/* на Railway) + етикетки
коробок. Уся логіка коробок живе в хмарі (єдине джерело правди, там же пише
Mini App працівників); тут — авторизація адмін-токеном на боці бекенда (у
браузер токен не йде), прокидання помилок як є та друк етикетки коробки тим
самим конвеєром, що й стікери товарів (services/label_service).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

try:
    from services import label_service as ls
    from services import warehouse_client as cloud
    from services.file_saver import save_bytes
    from services.runtime_config import is_desktop_shell
except ImportError:  # pragma: no cover
    from backend.services import label_service as ls
    from backend.services import warehouse_client as cloud
    from backend.services.file_saver import save_bytes
    from backend.services.runtime_config import is_desktop_shell

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])


def _fwd(method: str, path: str, *, json: Any = None, params: Optional[Dict[str, Any]] = None) -> Any:
    try:
        return cloud.request(method, path, json=json, params=params)
    except cloud.CloudError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


# ───────────────────────────── стан ──────────────────────────────────────────

@router.get("/status")
def status():
    ok, msg = (cloud.ping() if cloud.is_configured() else (False, "Немає CATALOG_ADMIN_TOKEN"))
    return {"configured": cloud.is_configured(), "reachable": ok, "message": msg,
            "cloud": cloud.base_url()}


# ───────────────────────────── читання ───────────────────────────────────────

@router.get("/boxes")
def boxes(status: Optional[str] = None):
    return _fwd("GET", "/boxes", params={"status": status} if status else None)


@router.get("/boxes/next-code")
def next_code(category: str = Query(..., min_length=1)):
    return _fwd("GET", "/boxes/next-code", params={"category": category})


@router.get("/boxes/{code}")
def box(code: str):
    return _fwd("GET", f"/boxes/{code}")


@router.get("/events")
def events(box: Optional[str] = None, product_id: Optional[int] = None, limit: int = 50):
    return _fwd("GET", "/events", params={"box": box, "product_id": product_id, "limit": limit})


@router.get("/locations")
def locations(product_ids: str = Query(..., description="id через кому")):
    """Де лежать товари (для колонки «Коробка» і картки товару)."""
    ids = [x for x in product_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return {"locations": {}}
    return _fwd("GET", "/locations", params={"product_ids": ",".join(ids)})


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    return _fwd("GET", "/search", params={"q": q})


@router.get("/scan")
def scan(code: str = Query(..., min_length=1)):
    return _fwd("GET", "/scan", params={"code": code})


# ───────────────────────────── запис ─────────────────────────────────────────

class BoxCreate(BaseModel):
    code: Optional[str] = None
    category: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    needs_check: bool = False


class BoxPatch(BaseModel):
    title: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    category: Optional[str] = None
    needs_check: Optional[bool] = None


class PackIn(BaseModel):
    product_id: int
    qty: int = Field(1, ge=1, le=99)
    move: bool = False


class UnpackIn(BaseModel):
    product_id: int
    qty: Optional[int] = Field(None, ge=1, le=99)


@router.post("/boxes", status_code=201)
def box_create(payload: BoxCreate = Body(...)):
    return _fwd("POST", "/boxes", json=payload.dict())


@router.patch("/boxes/{code}")
def box_patch(code: str, payload: BoxPatch = Body(...)):
    return _fwd("PATCH", f"/boxes/{code}", json={k: v for k, v in payload.dict().items() if v is not None})


@router.post("/boxes/{code}/seal")
def box_seal(code: str):
    return _fwd("POST", f"/boxes/{code}/seal")


@router.post("/boxes/{code}/open")
def box_open(code: str):
    return _fwd("POST", f"/boxes/{code}/open")


@router.post("/boxes/{code}/check")
def box_check(code: str):
    return _fwd("POST", f"/boxes/{code}/check")


@router.delete("/boxes/{code}")
def box_delete(code: str, force: bool = False):
    return _fwd("DELETE", f"/boxes/{code}", params={"force": "true"} if force else None)


@router.post("/boxes/{code}/pack")
def pack(code: str, payload: PackIn = Body(...)):
    return _fwd("POST", f"/boxes/{code}/pack", json=payload.dict())


@router.post("/boxes/{code}/unpack")
def unpack(code: str, payload: UnpackIn = Body(...)):
    return _fwd("POST", f"/boxes/{code}/unpack", json=payload.dict())


@router.post("/boxes/{code}/unpack-all")
def unpack_all(code: str):
    return _fwd("POST", f"/boxes/{code}/unpack-all")


@router.post("/unpack")
def unpack_anywhere(payload: UnpackIn = Body(...)):
    return _fwd("POST", "/unpack", json=payload.dict())


# ───────────────────────────── етикетка коробки ──────────────────────────────

class LabelIn(BaseModel):
    mode: str = "print"              # print | save | download
    printer: Optional[str] = None
    copies: int = Field(1, ge=1, le=10)


def _box_for_label(code: str) -> Dict[str, Any]:
    b = _fwd("GET", f"/boxes/{code}")
    return {"code": b["code"], "title": b.get("title") or "", "location": b.get("location") or ""}


@router.get("/boxes/{code}/label.png")
def box_label_png(code: str):
    b = _box_for_label(code)
    page = ls.render_box_label(b["code"], b["title"], b["location"])
    return Response(content=ls.page_to_png(page), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/boxes/{code}/label")
def box_label(code: str, payload: LabelIn = Body(...)):
    """Етикетка коробки на аркуш 100×100 — друк / збереження / завантаження,
    тим самим шляхом, що й стікери товарів."""
    b = _box_for_label(code)
    mode = (payload.mode or "print").lower()
    if mode not in ("print", "save", "download"):
        raise HTTPException(status_code=400, detail="mode має бути print | save | download")
    page = ls.render_box_label(b["code"], b["title"], b["location"])
    pdf = ls.pages_to_pdf([page] * int(payload.copies))
    filename = f"BMS коробка {b['code']} {datetime.now().strftime('%Y-%m-%d')}.pdf"
    if mode == "download":
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="box-label.pdf"'})
    path, final_name = save_bytes(pdf, filename, "box-label.pdf")
    printed, printer, message = False, None, ""
    spec = ls.get_layout(ls.DEFAULT_LAYOUT)  # носій той самий: 100×100
    if mode == "print":
        printer = payload.printer or ls.preferred_printer()
        try:
            if not printer:
                raise RuntimeError("Принтер не знайдено — файл збережено, надрукуйте його вручну")
            message = ls.print_pdf(path, printer, spec) or "Надіслано на принтер"
            printed = True
        except RuntimeError as exc:
            message = str(exc)
            ls.open_file(path)
    else:
        ls.open_file(path)
    return {"path": path, "filename": final_name, "printed": printed, "printer": printer,
            "message": message, "desktop": is_desktop_shell()}

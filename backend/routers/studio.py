"""Майстерня публікацій — галерея, шрифти, власні пости.

Роути навмисно синхронні (`def`, не `async def`): усередині — звичайний
блокуючий SQLAlchemy й мережа до R2, і в корутині вони морозили б event loop
на весь час запиту. FastAPI віддає такі роути в threadpool сам. Винятки —
завантаження файлів, де потрібен `await file.read()`; там важка частина
(конверт у WebP + заливка) явно йде в `run_in_threadpool`.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException, Path,
                     Query, UploadFile, status)
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

try:
    from models.database import get_db
    from services import studio
    from services import studio_fonts
    from services import studio_publish
except ImportError:  # запуск з кореня репо
    from backend.models.database import get_db  # type: ignore
    from backend.services import studio  # type: ignore
    from backend.services import studio_fonts  # type: ignore
    from backend.services import studio_publish  # type: ignore

router = APIRouter()

# Растри й шрифти незмінні під своїм ключем (ім'я містить sha256), тож кеш
# браузера може тримати їх скільки завгодно — нова версія = нова адреса.
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _fail(exc: studio.StudioError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ── Довідник ────────────────────────────────────────────────────────────────

@router.get("/api/studio/config")
def studio_config():
    """Формати полотна й мережі — щоб фронт не тримав власну копію правил."""
    return {"formats": studio.canvas_formats(), "platforms": studio.platforms()}


# ── Підбірки ────────────────────────────────────────────────────────────────

@router.get("/api/studio/collections")
def list_collections(kind: Optional[str] = Query(None, regex="^(media|post)$"),
                     db: Session = Depends(get_db)):
    return {"items": studio.list_collections(db, kind)}


@router.post("/api/studio/collections")
def create_collection(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return studio.create_collection(
            db, kind=str(payload.get("kind") or "media"),
            name=str(payload.get("name") or ""))
    except studio.StudioError as exc:
        raise _fail(exc)


@router.patch("/api/studio/collections/{collection_id}")
def rename_collection(collection_id: int = Path(..., ge=1),
                      payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return studio.rename_collection(db, collection_id,
                                        str(payload.get("name") or ""))
    except studio.StudioError as exc:
        raise _fail(exc)


@router.delete("/api/studio/collections/{collection_id}")
def delete_collection(collection_id: int = Path(..., ge=1),
                      db: Session = Depends(get_db)):
    try:
        return studio.delete_collection(db, collection_id)
    except studio.StudioError as exc:
        raise _fail(exc)


# ── Галерея ─────────────────────────────────────────────────────────────────

@router.get("/api/studio/assets")
def list_assets(collection_id: Optional[int] = Query(None, ge=1),
                search: Optional[str] = Query(None),
                limit: int = Query(200, ge=1, le=500),
                offset: int = Query(0, ge=0),
                db: Session = Depends(get_db)):
    return studio.list_assets(db, collection_id=collection_id, search=search,
                              limit=limit, offset=offset)


@router.post("/api/studio/assets")
async def add_assets(files: List[UploadFile] = File(...),
                     collection_id: Optional[int] = Form(None),
                     db: Session = Depends(get_db)):
    """Залити фото в галерею. Дублікат (той самий байт-у-байт файл) не
    створює другий об'єкт — повертається наявний запис із `duplicate: true`."""
    added, errors = [], []
    for upload in files:
        raw = await upload.read()
        try:
            item = await run_in_threadpool(
                studio.add_asset, db, filename=upload.filename or "photo",
                raw=raw, collection_id=collection_id)
            added.append(item)
        except studio.StudioError as exc:
            errors.append({"file": upload.filename, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("studio: фото %s не залилось", upload.filename)
            errors.append({"file": upload.filename, "reason": str(exc)})
    if not added and errors:
        reasons = "; ".join(f"{e['file']}: {e['reason']}" for e in errors)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Жодне фото не додано: {reasons}")
    return {"added": len(added), "items": added, "errors": errors}


@router.patch("/api/studio/assets/{asset_id}")
def update_asset(asset_id: int = Path(..., ge=1), payload: dict = Body(...),
                 db: Session = Depends(get_db)):
    try:
        return studio.update_asset(
            db, asset_id,
            title=payload.get("title"),
            collection_id=payload.get("collection_id"),
            clear_collection=bool(payload.get("clear_collection")),
            tags=payload.get("tags"),
            sort_order=payload.get("sort_order"))
    except studio.StudioError as exc:
        raise _fail(exc)


@router.post("/api/studio/assets/reorder")
def reorder_assets(payload: dict = Body(...), db: Session = Depends(get_db)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Очікується список ids")
    return studio.reorder_assets(db, [int(value) for value in ids])


@router.delete("/api/studio/assets/{asset_id}")
def delete_asset(asset_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    try:
        return studio.delete_asset(db, asset_id)
    except studio.StudioError as exc:
        raise _fail(exc)


@router.get("/api/studio/assets/{asset_id}/file")
def asset_file(asset_id: int = Path(..., ge=1), thumb: int = Query(0, ge=0, le=1),
               db: Session = Depends(get_db)):
    """Байти фото з того самого походження, що й сторінка.

    Саме тому не редирект на CDN: редактор вшиває фото в SVG перед рендером, а
    крос-доменна картинка «отруює» canvas — растр перестав би зберігатися."""
    try:
        data, mime = studio.asset_bytes(db, asset_id, thumb=bool(thumb))
    except studio.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=data, media_type=mime, headers=_IMMUTABLE)


# ── Шрифти ──────────────────────────────────────────────────────────────────

@router.get("/api/studio/fonts")
def list_fonts(db: Session = Depends(get_db)):
    return {"items": studio.list_fonts(db)}


@router.post("/api/studio/fonts")
async def add_fonts(files: List[UploadFile] = File(...),
                    family: Optional[str] = Form(None),
                    weight: Optional[int] = Form(None),
                    style: Optional[str] = Form(None),
                    db: Session = Depends(get_db)):
    added, errors = [], []
    for upload in files:
        raw = await upload.read()
        try:
            item = await run_in_threadpool(
                studio.add_font, db, filename=upload.filename or "font.ttf",
                raw=raw, family=family, weight=weight, style=style)
            added.append(item)
        except studio.StudioError as exc:
            errors.append({"file": upload.filename, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("studio: шрифт %s не залився", upload.filename)
            errors.append({"file": upload.filename, "reason": str(exc)})
    if not added and errors:
        reasons = "; ".join(f"{e['file']}: {e['reason']}" for e in errors)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Жоден шрифт не додано: {reasons}")
    return {"added": len(added), "items": added, "errors": errors}


@router.get("/api/studio/fonts/system")
def system_fonts(refresh: int = Query(0, ge=0, le=1)):
    """Шрифти, встановлені на цьому пристрої.

    Читання майже тисячі гарнітур займає секунди, тому відповідь кешується й
    перечитується лише коли теки шрифтів змінились (або на явний `refresh=1`)."""
    return studio_fonts.catalogue(refresh=bool(refresh))


@router.post("/api/studio/fonts/system/import")
def import_system_fonts(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Перенести обрані гарнітури з пристрою в майстерню.

    Саме копія, а не посилання на файл: макет має збиратись однаково й на
    іншій машині, де цього шрифта немає."""
    tokens = payload.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        raise HTTPException(status_code=400, detail="Не обрано жодного накреслення")
    added, errors = [], []
    for token in tokens[:40]:
        try:
            added.append(studio_fonts.import_face(db, str(token)))
        except studio.StudioError as exc:
            errors.append({"token": str(token)[:24], "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("studio: системний шрифт не імпортовано")
            errors.append({"token": str(token)[:24], "reason": str(exc)})
    if not added and errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(item["reason"] for item in errors))
    return {"added": len(added), "items": added, "errors": errors}


@router.delete("/api/studio/fonts/{font_id}")
def delete_font(font_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    try:
        return studio.delete_font(db, font_id)
    except studio.StudioError as exc:
        raise _fail(exc)


@router.get("/api/studio/fonts/{font_id}/file")
def font_file(font_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    try:
        data, mime = studio.font_bytes(db, font_id)
    except studio.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=data, media_type=mime, headers=_IMMUTABLE)


# ── Пости ───────────────────────────────────────────────────────────────────

@router.get("/api/studio/posts")
def list_posts(status_filter: Optional[str] = Query(None, alias="status"),
               collection_id: Optional[int] = Query(None, ge=1),
               search: Optional[str] = Query(None),
               limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    return studio.list_posts(db, status=status_filter, collection_id=collection_id,
                             search=search, limit=limit, offset=offset)


@router.post("/api/studio/posts")
def create_post(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return studio.create_post(db, payload)
    except studio.StudioError as exc:
        raise _fail(exc)


@router.get("/api/studio/posts/{post_id}")
def get_post(post_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    try:
        return studio.get_post(db, post_id)
    except studio.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/api/studio/posts/{post_id}")
def update_post(post_id: int = Path(..., ge=1), payload: dict = Body(...),
                db: Session = Depends(get_db)):
    try:
        return studio.update_post(db, post_id, payload)
    except studio.StudioError as exc:
        raise _fail(exc)


@router.delete("/api/studio/posts/{post_id}")
def delete_post(post_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    try:
        return studio.delete_post(db, post_id)
    except studio.StudioError as exc:
        raise _fail(exc)


@router.post("/api/studio/posts/{post_id}/render")
async def save_render(post_id: int = Path(..., ge=1),
                      file: UploadFile = File(...),
                      canvas_format: str = Form(..., alias="format"),
                      as_preview: bool = Form(True),
                      db: Session = Depends(get_db)):
    """Растр, зібраний редактором у браузері. Бекенд його не перемальовує —
    саме цей файл потім забирає мережа."""
    raw = await file.read()
    mime = "image/png" if (file.content_type or "").endswith("png") else "image/jpeg"
    try:
        return await run_in_threadpool(
            studio.save_render, db, post_id, fmt=canvas_format, raw=raw,
            mime=mime, as_preview=as_preview)
    except studio.StudioError as exc:
        raise _fail(exc)


@router.get("/api/studio/posts/{post_id}/preview")
def post_preview(post_id: int = Path(..., ge=1),
                 canvas_format: Optional[str] = Query(None, alias="format"),
                 db: Session = Depends(get_db)):
    try:
        data, mime = studio.preview_bytes(db, post_id, canvas_format)
    except studio.StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # Прев'ю живе під content-addressed ключем, але сама адреса поста стала —
    # тому короткий кеш, інакше після перезбереження показувався б старий кадр.
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "no-cache"})


# ── Публікація ──────────────────────────────────────────────────────────────

@router.get("/api/studio/publish/status")
async def publish_status():
    """Готовність мереж приймати пост. Нічого не публікує й не змінює."""
    return await studio_publish.readiness()


@router.post("/api/studio/posts/{post_id}/publish")
async def publish_post(post_id: int = Path(..., ge=1), payload: dict = Body(default={}),
                       db: Session = Depends(get_db)):
    """Відправити пост у ввімкнені мережі.

    `dry_run: true` проходить увесь шлях, окрім самої відправки: збирає
    публікаційний JPEG, перевіряє підписи й розклад і повертає, що саме пішло
    б у кожну мережу. Це той самий код, тому репетиція не бреше."""
    try:
        return await studio_publish.publish_post(db, post_id, payload)
    except (studio_publish.PublishError, studio.StudioError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/studio/posts/{post_id}/publications")
def post_publications(post_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    return {"items": studio_publish.list_publications(db, post_id)}


@router.post("/api/studio/publish/sync")
async def publish_sync(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Перепитати хмарні диспетчери про незавершені відправки."""
    post_id = payload.get("post_id")
    return await studio_publish.sync_statuses(
        db, post_id=int(post_id) if post_id else None)

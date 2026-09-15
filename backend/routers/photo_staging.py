"""Розкладання фото по товарах: тека «до розбору» → картки.

ЗАДАЧА. Після зйомки в людини купа знімків — перемішаних, різних форматів, без
жодної нумерації. Раніше єдиний шлях був вручну перейменувати кожен у Finder
під `<номер>_001.jpg`, дивлячись на цінник, і лише потім запускати ingest.
Тут це один екран: дивишся на цінник у великому превʼю, клікаєш знімки, вводиш
номер — і вони лягають у картку тим самим шляхом, що й кнопка «Додати» в ній
(`photo_manager.add_photos`: іменування, WebP-майстер, R2). Паралельного
шляху не існує.

ВХІД — тека `~/Downloads/Бізнес/Товар_до_розбору/<категорія>/` (уже так і
розкладена). Прикріплені оригінали переносяться в `_done/<номер>/`, а не
видаляються: це заразом закриває стару діру, що оригіналів живих знімків у нас
не лишалось.

⚠️ ROUTERS ARE SYNC (`def`) — правило проєкту.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

try:
    from models.database import get_db
    from models import models
    from services.photo_manager import add_photos, resolve_category, VALID_CATEGORIES, PHOTO_KINDS
    from services.product_images import invalidate_image_list_cache, list_images
except ImportError:  # pragma: no cover
    from backend.models.database import get_db
    from backend.models import models
    from backend.services.photo_manager import add_photos, resolve_category, VALID_CATEGORIES, PHOTO_KINDS
    from backend.services.product_images import invalidate_image_list_cache, list_images

logger = logging.getLogger(__name__)
router = APIRouter()

STAGING_ROOT = Path(os.environ.get(
    "PRODUCT_PHOTOS_STAGING_DIR",
    os.path.expanduser("~/Downloads/Бізнес/Товар_до_розбору"))).expanduser()
DONE_DIR = "_done"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
_SAFE_NAME = re.compile(r"^[^/\\\x00]+$")


def _category_dir(category: str) -> Path:
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Невідома категорія: {category!r}")
    return STAGING_ROOT / category


def _safe_path(category: str, name: str) -> Path:
    """Файл лише з теки категорії — жодних шляхів у назві."""
    if not name or not _SAFE_NAME.match(name) or name.startswith("."):
        raise HTTPException(status_code=400, detail="Некоректна назва файлу")
    p = (_category_dir(category) / name)
    if not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
        raise HTTPException(status_code=404, detail=f"Файл не знайдено: {name}")
    return p


@router.get("/api/photo-staging/categories")
def staging_categories() -> Dict[str, Any]:
    """Категорії, у яких щось лежить до розбору."""
    out = []
    for cat in sorted(VALID_CATEGORIES):
        d = STAGING_ROOT / cat
        n = sum(1 for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXT) if d.is_dir() else 0
        out.append({"category": cat, "count": n})
    return {"root": str(STAGING_ROOT), "categories": out}


@router.get("/api/photo-staging")
def staging_list(category: str = Query(...)) -> Dict[str, Any]:
    """Знімки до розбору в категорії — у порядку зйомки (за часом файлу)."""
    d = _category_dir(category)
    if not d.is_dir():
        return {"category": category, "files": []}
    files = []
    for f in d.iterdir():
        if f.is_file() and f.suffix.lower() in IMAGE_EXT and not f.name.startswith("."):
            st = f.stat()
            files.append({"name": f.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    # За часом зйомки: навіть перемішані знімки одного товару здебільшого стоять
    # поруч, і сітка в такому порядку читається швидше, ніж за назвою файлу.
    files.sort(key=lambda x: (x["mtime"], x["name"]))
    return {"category": category, "files": files}


@router.get("/api/photo-staging/counts")
def staging_counts(numbers: str = Query(..., description="номери через кому")) -> Dict[str, Any]:
    """Скільки знімків уже є в кожної картки — щоб у «Розкласти фото» бачити,
    кому ще роздавати, а кому вже ні. Без цього власник плутався й підвʼязував
    повторно. Читає індекс R2 (у памʼяті) — дешево навіть на 40 номерів."""
    out: Dict[str, Dict[str, int]] = {}
    for raw in numbers.split(","):
        n = raw.strip()
        if not n:
            continue
        counts = {"real": 0, "official": 0, "defect": 0}
        try:
            for img in list_images(n):
                counts[img.kind] = counts.get(img.kind, 0) + 1
        except Exception as e:  # noqa: BLE001 — один поганий номер не валить решту
            logger.warning("[staging] counts for %s: %s", n, e)
        out[n] = counts
    return {"counts": out}


# ── Превʼю ──────────────────────────────────────────────────────────────────
# Оригінали важкі (3–8 МБ), а в сітці їх сотні. Віддаємо зменшену JPEG-копію,
# кешовану в памʼяті за (шлях, mtime, ширина). Кеш обмежений, бо тека може
# містити тисячі файлів.
_THUMB_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_THUMB_LOCK = threading.Lock()
_THUMB_MAX = 600


def _thumb(path: Path, width: int) -> bytes:
    key = (str(path), int(path.stat().st_mtime), width)
    with _THUMB_LOCK:
        if key in _THUMB_CACHE:
            _THUMB_CACHE.move_to_end(key)
            return _THUMB_CACHE[key]
    from PIL import Image, ImageOps
    try:
        from pillow_heif import register_heif_opener  # HEIC з айфона
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((width, width * 2), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
    data = buf.getvalue()
    with _THUMB_LOCK:
        _THUMB_CACHE[key] = data
        while len(_THUMB_CACHE) > _THUMB_MAX:
            _THUMB_CACHE.popitem(last=False)
    return data


@router.get("/api/photo-staging/image")
def staging_image(category: str = Query(...), name: str = Query(...),
                  w: int = Query(320, ge=64, le=2000)):
    p = _safe_path(category, name)
    try:
        data = _thumb(p, w)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=415, detail=f"Не вдалось відкрити: {exc}")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


# ── Прикріплення ────────────────────────────────────────────────────────────

def _find_product(db: Session, productnumber: str):
    """Товар за номером — з «#» чи без; у базі канон із «#»."""
    raw = (productnumber or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Порожній номер")
    cands = {raw, raw.lstrip("#"), "#" + raw.lstrip("#")}
    rows = (db.query(models.Product)
              .filter(models.Product.productnumber.in_(list(cands)))
              .order_by(models.Product.id).all())
    if not rows:
        raise HTTPException(status_code=404, detail=f"Товару {raw} немає в базі — спершу додай його в завоз")
    return rows[0]


@router.post("/api/photo-staging/attach")
def staging_attach(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Прикріпити вибрані знімки до товару.

    Іде через `photo_manager.add_photos` — той самий код, що й кнопка «Додати»
    в картці: наступний вільний індекс, WebP-майстер, R2. Оригінали не
    видаляються, а переносяться в `_done/<номер>/`.
    """
    category = str(payload.get("category") or "")
    names: List[str] = [str(n) for n in (payload.get("files") or []) if str(n).strip()]
    kind = str(payload.get("kind") or "real")
    if kind not in PHOTO_KINDS:
        raise HTTPException(status_code=400, detail=f"Невідомий вид фото: {kind!r}")
    if not names:
        raise HTTPException(status_code=400, detail="Не вибрано жодного знімка")

    product = _find_product(db, str(payload.get("productnumber") or ""))
    pnum = product.productnumber
    type_name = getattr(product.type, "typename", None) if getattr(product, "type", None) else None
    # Куди класти: туди, де вже лежать фото цього товару, інакше за типом. Тека
    # «до розбору» лише підказує; правда — у міорі.
    mirror_category = resolve_category(pnum, type_name) if type_name else category

    paths = [_safe_path(category, n) for n in names]
    result = add_photos(pnum, mirror_category, [(str(p), p.name) for p in paths], kind=kind)

    # Оригінали — у _done/<номер>/ (не видаляємо: це наші єдині оригінали).
    failed = {e.get("file") for e in result.get("errors", [])}
    done_dir = _category_dir(category) / DONE_DIR / pnum.lstrip("#")
    moved = 0
    for p in paths:
        if p.name in failed:
            continue
        done_dir.mkdir(parents=True, exist_ok=True)
        target = done_dir / p.name
        i = 1
        while target.exists():
            target = done_dir / f"{p.stem}_{i}{p.suffix}"; i += 1
        shutil.move(str(p), str(target))
        moved += 1

    invalidate_image_list_cache(pnum)
    try:
        from routers.products import _invalidate_photo_cache
    except ImportError:  # pragma: no cover
        from backend.routers.products import _invalidate_photo_cache
    _invalidate_photo_cache(pnum, membership_changed=True)

    return {"ok": True, "productnumber": pnum, "product_id": product.id,
            "category": mirror_category, "kind": kind,
            "added": result.get("added", 0), "moved": moved,
            "errors": result.get("errors", [])}

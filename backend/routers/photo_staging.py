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

import hashlib
import io
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

try:
    from models.database import get_db
    from models import models
    from services.photo_manager import add_photos, resolve_category, VALID_CATEGORIES, PHOTO_KINDS
    from services.photo_edit import parse_edit
    from services.product_images import invalidate_image_list_cache, list_images
except ImportError:  # pragma: no cover
    from backend.models.database import get_db
    from backend.models import models
    from backend.services.photo_manager import add_photos, resolve_category, VALID_CATEGORIES, PHOTO_KINDS
    from backend.services.photo_edit import parse_edit
    from backend.services.product_images import invalidate_image_list_cache, list_images

logger = logging.getLogger(__name__)
router = APIRouter()

STAGING_ROOT = Path(os.environ.get(
    "PRODUCT_PHOTOS_STAGING_DIR",
    os.path.expanduser("~/Downloads/Бізнес/Товар_до_розбору"))).expanduser()
DONE_DIR = "_done"
TRASH_DIR = "_trash"   # «видалені» з розбору — не unlink: це єдині оригінали знімка
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
_SAFE_NAME = re.compile(r"^[^/\\\x00]+$")


# ── «Дата додавання» (як у Finder) ──────────────────────────────────────────
# Для сортування «нові спершу» потрібен час, коли файл ЛІГ у теку. mtime —
# це час зйомки (копіювання його зберігає), а ctime збивається від будь-якої
# зміни метаданих: тег Finder, права, навіть жорстке посилання на файл з
# іншої теки. На macOS у кожного запису теки є окрема «Дата додавання»
# (ATTR_CMN_ADDEDTIME) — її й читаємо одним системним викликом на файл.
# Інші ОС / збій — ctime (на Windows це якраз час створення копії).
_added_time = None
if sys.platform == "darwin":
    try:
        import ctypes
        import ctypes.util
        import struct

        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

        class _AttrList(ctypes.Structure):
            _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                        ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                        ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                        ("forkattr", ctypes.c_uint32)]

        _ATTR_CMN_RETURNED_ATTRS = 0x80000000
        _ATTR_CMN_ADDEDTIME = 0x10000000
        _FSOPT_NOFOLLOW = 0x1

        def _added_time(path: str) -> Optional[int]:  # noqa: F811
            al = _AttrList(5, 0, _ATTR_CMN_RETURNED_ATTRS | _ATTR_CMN_ADDEDTIME, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(64)
            if _libc.getattrlist(os.fsencode(path), ctypes.byref(al), buf, 64, _FSOPT_NOFOLLOW) != 0:
                return None
            # [u32 довжина][attribute_set_t: 5×u32][timespec: i64 сек, i64 нс]
            returned = struct.unpack_from("I", buf, 4)[0]
            if not returned & _ATTR_CMN_ADDEDTIME:
                return None
            return int(struct.unpack_from("q", buf, 24)[0])
    except Exception:  # noqa: BLE001 — без цього просто ctime
        _added_time = None


def _file_added(path: str, st: os.stat_result) -> int:
    if _added_time is not None:
        try:
            t = _added_time(path)
            if t:
                return t
        except Exception:  # noqa: BLE001
            pass
    return int(st.st_ctime)


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
    """Знімки до розбору в категорії.

    Порядок і сортування — на фронті (миттєво, без повторного запиту); тут
    віддаємо обидва часи:
      • `mtime` — час зйомки/правки файлу (копіювання його зберігає);
      • `added` — коли файл ЛІГ у теку: «Дата додавання» Finder
        (`_file_added`; копіювання, перенесення, повернення з _trash її
        оновлюють), поза macOS — ctime.
    """
    d = _category_dir(category)
    if not d.is_dir():
        return {"category": category, "files": []}
    files = []
    for f in os.scandir(d):
        if f.name.startswith(".") or not f.is_file():
            continue
        if os.path.splitext(f.name)[1].lower() not in IMAGE_EXT:
            continue
        st = f.stat()
        files.append({"name": f.name, "size": st.st_size, "mtime": int(st.st_mtime),
                      "added": _file_added(f.path, st)})
    # Типово — за часом зйомки: знімки одного товару здебільшого стоять поруч.
    files.sort(key=lambda x: (x["mtime"], x["name"]))
    _prewarm(category, files)
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
# Оригінали бувають по 3–8 МБ, а в теці їх тисячі. Віддаємо зменшену JPEG-копію:
#   • JPEG декодується одразу зменшеним (`draft`, DCT-масштаб 1/2…1/8) —
#     на важких кадрах це 135 → 50 мс, а не повний декод заради плитки;
#   • кеш — на ДИСКУ (`~/.cache/bms_thumbs/staging`), ключ включає mtime і
#     розмір файлу: переживає перезапуск BMS, а заміна файлу сама скидає кеш.
#     Колишній кеш у памʼяті тримав 600 кадрів на теку з 2233 — сітка
#     перегенеровувалась при кожному прокручуванні;
#   • URL несе `v=<mtime>`, тож браузер кешує назавжди (`immutable`).
# Ширини — закритий набір, щоб довільний `w` не плодив варіанти в кеші:
#   400 — плитка сітки (квадрат ≈200 px @2x): МЕНША сторона = 400 (cover);
#   1200 — велике превʼю й редактор кадру: БІЛЬША сторона = 1200 (contain).
GRID_W = 400
PREVIEW_W = 1200
_ALLOWED_W = (GRID_W, PREVIEW_W)
_THUMB_DIR = Path(os.path.expanduser(os.environ.get(
    "PHOTO_STAGING_THUMBS_DIR", "~/.cache/bms_thumbs/staging")))
_THUMB_VERSION = "2"   # змінити, якщо зміниться спосіб рендеру — старий кеш стане промахом


def _norm_width(w: int) -> int:
    for allowed in _ALLOWED_W:
        if w <= allowed:
            return allowed
    return _ALLOWED_W[-1]


def _thumb_cache_path(path: Path, width: int) -> Path:
    st = path.stat()
    key = f"{_THUMB_VERSION}|{path}|{st.st_mtime_ns}|{st.st_size}|{width}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _THUMB_DIR / str(width) / digest[:2] / f"{digest}.jpg"


def _render_thumb(path: Path, width: int) -> bytes:
    from PIL import Image, ImageOps
    try:
        from pillow_heif import register_heif_opener  # HEIC з айфона
        register_heif_opener()
    except ImportError:
        pass
    cover = width <= GRID_W
    with Image.open(path) as im:
        if im.format == "JPEG":
            # Декодер одразу дає кадр ≥ запитаного по обох сторонах — у рази швидше.
            im.draft("RGB", (width, width))
        im = ImageOps.exif_transpose(im).convert("RGB")
        w0, h0 = im.size
        side = min(w0, h0) if cover else max(w0, h0)
        if side > width:
            scale = width / side
            im = im.resize((max(1, round(w0 * scale)), max(1, round(h0 * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82 if cover else 85, optimize=True)
    return buf.getvalue()


def _thumb(path: Path, width: int) -> bytes:
    width = _norm_width(width)
    cache = _thumb_cache_path(path, width)
    try:
        return cache.read_bytes()
    except OSError:
        pass
    data = _render_thumb(path, width)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        # Через тимчасовий файл: паралельні запити не побачать половинчастий кадр.
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cache)
    except OSError as e:  # кеш — прискорення, не умова роботи
        logger.debug("[staging] thumb cache write %s: %s", cache, e)
    return data


@router.get("/api/photo-staging/image")
def staging_image(category: str = Query(...), name: str = Query(...),
                  w: int = Query(GRID_W, ge=64, le=2000),
                  v: Optional[str] = Query(None, description="mtime файлу — для вічного кешу браузера")):
    p = _safe_path(category, name)
    try:
        data = _thumb(p, w)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=415, detail=f"Не вдалось відкрити: {exc}")
    # З `v` адреса незмінна для цих байтів → кешуємо назавжди; без — година.
    cache = "private, max-age=31536000, immutable" if v else "private, max-age=3600"
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": cache})


# ── Прогрів мініатюр ────────────────────────────────────────────────────────
# Щойно відкрили теку — в одному фоновому потоці готуємо плитки, найновіші
# додані першими (їх розбирають найчастіше). Перший прогін теки на 2233 знімки
# ≈ 20 с, далі — лише нові файли. Один потік і пауза між кадрами, щоб не
# відбирати процесор у живих запитів сітки.
_PREWARM_ENABLED = os.environ.get("PHOTO_STAGING_PREWARM", "1") != "0"
_prewarm_lock = threading.Lock()
_prewarm_state: Dict[str, Any] = {"queue": None, "thread": None}


def _prewarm(category: str, files: List[Dict[str, Any]]) -> None:
    if not _PREWARM_ENABLED or not files:
        return
    d = _category_dir(category)
    order = sorted(files, key=lambda f: (-f["added"], f["mtime"], f["name"]))
    paths = [d / f["name"] for f in order]
    with _prewarm_lock:
        _prewarm_state["queue"] = paths          # нова тека витісняє стару чергу
        t = _prewarm_state.get("thread")
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_prewarm_worker, name="staging-thumb-prewarm", daemon=True)
        _prewarm_state["thread"] = t
        t.start()


def _prewarm_worker() -> None:
    while True:
        with _prewarm_lock:
            paths = _prewarm_state.get("queue")
            _prewarm_state["queue"] = None
            if not paths:
                _prewarm_state["thread"] = None
                return
        for p in paths:
            with _prewarm_lock:
                if _prewarm_state.get("queue") is not None:
                    break                       # відкрили іншу теку — беремо її
            try:
                if not _thumb_cache_path(p, GRID_W).exists():
                    _thumb(p, GRID_W)
                    time.sleep(0.005)
            except Exception:  # noqa: BLE001 — битий/зниклий файл не зупиняє прогрів
                continue


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

    # Кадри з редактора (1:1, поворот) — за назвою файлу. Ріжуться з оригіналу.
    raw_edits = payload.get("edits") or {}
    if not isinstance(raw_edits, dict):
        raise HTTPException(status_code=400, detail="edits має бути обʼєктом {файл: кадр}")
    try:
        edits = {str(k): parse_edit(v) for k, v in raw_edits.items()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Некоректний кадр: {e}")

    paths = [_safe_path(category, n) for n in names]
    result = add_photos(pnum, mirror_category,
                        [(str(p), p.name, edits.get(p.name)) for p in paths], kind=kind)

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


@router.post("/api/photo-staging/delete")
def staging_delete(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Прибрати знімки з розбору назовсім — у `_trash/` тієї ж категорії.

    Не unlink: у теці «до розбору» лежать ЄДИНІ оригінали, і випадкове
    видалення (клік не туди в сітці з 60 схожих кадрів) коштувало б знімка.
    Із сітки файл зникає одразу; фізично прибрати — спорожнити `_trash/`.
    """
    category = str(payload.get("category") or "")
    names: List[str] = [str(n) for n in (payload.get("files") or []) if str(n).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="Не вибрано жодного знімка")
    paths = [_safe_path(category, n) for n in names]
    trash = _category_dir(category) / TRASH_DIR
    trash.mkdir(parents=True, exist_ok=True)
    deleted, errors = [], []
    for p in paths:
        target = trash / p.name
        i = 1
        while target.exists():
            target = trash / f"{p.stem}_{i}{p.suffix}"; i += 1
        try:
            shutil.move(str(p), str(target))
            deleted.append(p.name)
        except OSError as e:  # noqa: BLE001
            errors.append({"file": p.name, "error": str(e)})
    return {"ok": True, "deleted": len(deleted), "files": deleted, "trash": str(trash), "errors": errors}


@router.post("/api/photo-staging/restore")
def staging_restore(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Повернути з `_trash/` назад у розбір — «Повернути» в тості після ×."""
    category = str(payload.get("category") or "")
    names: List[str] = [str(n) for n in (payload.get("files") or []) if str(n).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="Нема що повертати")
    cat = _category_dir(category)
    trash = cat / TRASH_DIR
    restored, errors = [], []
    for name in names:
        if not _SAFE_NAME.match(name) or name.startswith("."):
            errors.append({"file": name, "error": "некоректна назва"}); continue
        src = trash / name
        if not src.is_file():
            errors.append({"file": name, "error": "у _trash немає"}); continue
        target = cat / name
        i = 1
        while target.exists():
            target = cat / f"{src.stem}_{i}{src.suffix}"; i += 1
        try:
            shutil.move(str(src), str(target)); restored.append(target.name)
        except OSError as e:  # noqa: BLE001
            errors.append({"file": name, "error": str(e)})
    return {"ok": True, "restored": restored, "errors": errors}

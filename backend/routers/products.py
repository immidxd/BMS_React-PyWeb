from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Path, status, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import os
import re
import threading
from datetime import datetime

try:
    from utils.productnumber_normalizer import normalize as _norm_pn
except ImportError:
    from backend.utils.productnumber_normalizer import normalize as _norm_pn

try:
    from models.database import get_db
    from models import models
    from schemas import product as schemas
    from services import product_service, catalog_sync_service, journal_sync
except ImportError:
    from backend.models.database import get_db
    from backend.models import models
    from backend.schemas import product as schemas
    from backend.services import product_service, catalog_sync_service, journal_sync

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/products", response_model=schemas.ProductListResponse)
def get_products(
    page: int = Query(1, ge=1, description="Current page (starts from 1)"),
    per_page: int = Query(20, ge=1, le=200, description="Number of items per page"),
    skip: Optional[int] = Query(None, ge=0, description="Alternative to page/per_page"),
    limit: Optional[int] = Query(None, ge=1, le=200, description="Alternative to page/per_page"),
    search: Optional[str] = Query(None, description="Search term for products"),
    # Single ID filters (legacy)
    typeid: Optional[int] = Query(None),
    subtypeid: Optional[int] = Query(None),
    brandid: Optional[int] = Query(None),
    genderid: Optional[int] = Query(None),
    colorid: Optional[int] = Query(None),
    statusid: Optional[int] = Query(None),
    conditionid: Optional[int] = Query(None),
    # Multi-ID filters
    typeids: Optional[List[int]] = Query(None),
    subtypeids: Optional[List[int]] = Query(None),
    brandids: Optional[List[int]] = Query(None),
    genderids: Optional[List[int]] = Query(None),
    colorids: Optional[List[int]] = Query(None),
    color_group_ids: Optional[List[int]] = Query(None),
    statusids: Optional[List[int]] = Query(None),
    conditionids: Optional[List[int]] = Query(None),
    published_on: Optional[List[str]] = Query(None, description="Де опубліковано: telegram|viber|instagram|facebook|olx|prom|shafa|catalog"),
    published_on_not: Optional[List[str]] = Query(None, description="Виключити опубліковані на: telegram|viber|instagram|facebook|olx|prom|shafa|catalog (AND, незалежно від published_on)"),
    # Нові фільтри
    styleid: Optional[int] = Query(None),
    styleids: Optional[List[int]] = Query(None),
    current_conditionid: Optional[int] = Query(None),
    current_conditionids: Optional[List[int]] = Query(None),
    seasons: Optional[List[str]] = Query(None),
    widths: Optional[List[str]] = Query(None),
    # Price / size
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    sizeeu: Optional[List[str]] = Query(None),
    min_sizeeu: Optional[float] = Query(None),
    max_sizeeu: Optional[float] = Query(None),
    size_letter: Optional[List[str]] = Query(None),
    # Measurements CM range
    min_measurementscm: Optional[float] = Query(None),
    max_measurementscm: Optional[float] = Query(None),
    # Visibility / stock
    is_visible: Optional[bool] = Query(None),
    with_stock_only: Optional[bool] = Query(None),
    only_unsold: Optional[bool] = Query(None),
    only_problematic: Optional[bool] = Query(None),
    only_rostovka: Optional[bool] = Query(None),
    shipment_id: Optional[int] = Query(None),
    sort_by: str = Query("delivery_date", description="Sort mode: delivery_date(=за датою завозу, дефолт), delivery_date_asc, created_at(=найновіші в базі), created_at_asc, last_sold, price_desc, price_asc, id"),
    sort_dir: str = Query("desc", description="Sort direction: asc|desc"),
    db: Session = Depends(get_db)
):
    """Повертає список товарів з пагінацією та базовими фільтрами."""
    try:
        # Обчислюємо skip/limit
        if skip is None or limit is None:
            computed_skip = (page - 1) * per_page
            computed_limit = per_page
        else:
            computed_skip = skip
            computed_limit = limit

        logger.info(
            f"Requesting products: page={page}, per_page={per_page}, skip={skip}, limit={limit}, search={search}, sort={sort_by} {sort_dir}, only_unsold={only_unsold}, sizeeu={sizeeu}"
        )

        # Формуємо фільтри
        filters = schemas.ProductFilter(
            search=search,
            typeid=typeid,
            subtypeid=subtypeid,
            brandid=brandid,
            genderid=genderid,
            colorid=colorid,
            statusid=statusid,
            conditionid=conditionid,
            typeids=typeids,
            subtypeids=subtypeids,
            brandids=brandids,
            genderids=genderids,
            colorids=colorids,
            color_group_ids=color_group_ids,
            statusids=statusids,
            conditionids=conditionids,
            published_on=published_on,
            published_on_not=published_on_not,
            styleid=styleid,
            styleids=styleids,
            current_conditionid=current_conditionid,
            current_conditionids=current_conditionids,
            seasons=seasons,
            widths=widths,
            min_price=min_price,
            max_price=max_price,
            sizeeu=sizeeu,
            min_sizeeu=min_sizeeu,
            max_sizeeu=max_sizeeu,
            size_letter=size_letter,
            min_measurementscm=min_measurementscm,
            max_measurementscm=max_measurementscm,
            is_visible=is_visible,
            with_stock_only=with_stock_only,
            only_unsold=only_unsold,
            only_problematic=only_problematic,
            only_rostovka=only_rostovka,
            shipment_id=shipment_id,
        )

        result = product_service.get_products(
            db=db,
            skip=computed_skip,
            limit=computed_limit,
            filters=filters,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        # Service now returns dictionaries with JOIN data, so we can use them directly
        items = result["items"]

        response = {
            "items": items,
            "total": result["total"],
            "page": result["page"],
            "per_page": computed_limit,
            "pages": result["pages"],
        }

        logger.info(f"Returning {len(items)} products (total={response['total']})")
        return response
    except Exception as e:
        logger.error(f"Error getting products: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні товарів: {str(e)}")

@router.get("/api/products/filters", response_model=schemas.FilterOptions)
def get_product_filters(db: Session = Depends(get_db)):
    """
    Повертає опції фільтрів товарів з БД.
    """
    try:
        logger.info("Fetching product filters from DB")
        return product_service.get_product_filters(db)
    except Exception as e:
        logger.error(f"Error getting product filters: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні фільтрів: {str(e)}")


@router.get("/api/products/available-facets")
def get_available_facets(
    search: Optional[str] = Query(None),
    typeid: Optional[int] = Query(None),
    subtypeid: Optional[int] = Query(None),
    brandid: Optional[int] = Query(None),
    genderid: Optional[int] = Query(None),
    colorid: Optional[int] = Query(None),
    statusid: Optional[int] = Query(None),
    conditionid: Optional[int] = Query(None),
    typeids: Optional[List[int]] = Query(None),
    subtypeids: Optional[List[int]] = Query(None),
    brandids: Optional[List[int]] = Query(None),
    genderids: Optional[List[int]] = Query(None),
    colorids: Optional[List[int]] = Query(None),
    color_group_ids: Optional[List[int]] = Query(None),
    statusids: Optional[List[int]] = Query(None),
    conditionids: Optional[List[int]] = Query(None),
    published_on: Optional[List[str]] = Query(None),
    published_on_not: Optional[List[str]] = Query(None),
    styleid: Optional[int] = Query(None),
    styleids: Optional[List[int]] = Query(None),
    current_conditionid: Optional[int] = Query(None),
    current_conditionids: Optional[List[int]] = Query(None),
    seasons: Optional[List[str]] = Query(None),
    widths: Optional[List[str]] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    min_measurementscm: Optional[float] = Query(None),
    max_measurementscm: Optional[float] = Query(None),
    size_letter: Optional[List[str]] = Query(None),
    is_visible: Optional[bool] = Query(None),
    with_stock_only: Optional[bool] = Query(None),
    only_unsold: Optional[bool] = Query(None),
    only_problematic: Optional[bool] = Query(None),
    only_rostovka: Optional[bool] = Query(None),
    shipment_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Динамічні фасети: EU-розміри ТА кольорові групи, наявні в поточному
    відфільтрованому наборі.

    Кожен фасет виключає СВІЙ фільтр (faceted search) — тож сітка розмірів і чіпи
    кольорів адаптуються під інші активні фільтри/пошук, миттєво звужуючись, але
    лишаються вибірними.
    """
    try:
        filters = schemas.ProductFilter(
            search=search,
            typeid=typeid, subtypeid=subtypeid, brandid=brandid, genderid=genderid,
            colorid=colorid, statusid=statusid, conditionid=conditionid,
            typeids=typeids, subtypeids=subtypeids, brandids=brandids, genderids=genderids,
            colorids=colorids, color_group_ids=color_group_ids, statusids=statusids,
            conditionids=conditionids, published_on=published_on, published_on_not=published_on_not,
            styleid=styleid, styleids=styleids,
            current_conditionid=current_conditionid, current_conditionids=current_conditionids,
            seasons=seasons, widths=widths, min_price=min_price, max_price=max_price,
            min_measurementscm=min_measurementscm, max_measurementscm=max_measurementscm,
            size_letter=size_letter, is_visible=is_visible, with_stock_only=with_stock_only,
            only_unsold=only_unsold, only_problematic=only_problematic,
            only_rostovka=only_rostovka, shipment_id=shipment_id,
        )
        return {
            "eu": product_service.get_available_sizes(db, filters),
            "color_groups": product_service.get_available_color_groups(db, filters),
        }
    except Exception as e:
        logger.error(f"Error getting available facets: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні фасетів: {str(e)}")

@router.get("/api/products/next-number")
def get_next_number(
    prefix: str = Query("Ф", description="Літерний префікс серії (Ф/Р/Т/А/…); '' = цифрова серія"),
    db: Session = Depends(get_db),
):
    """Наступний вільний номер товару (політика max+1, реал-тайм, безколізійно).

    Снапшот УСІХ productnumber + clonednumbers → ніколи не дублює (вкл. ростовка-клони
    й «дірки»). Для кнопки «Згенерувати» у вікні додавання товару (Фаза 0 write-layer).
    Статичний роут оголошений ДО `/{product_id}` (той int-типований) — без перехоплення.
    """
    try:
        return {"prefix": prefix, "number": product_service.get_next_product_number(db, prefix)}
    except Exception as e:
        logger.error(f"Error generating next product number: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка генерації номера: {str(e)}")


def _product_gallery(product_id: int, db: Session):
    """(productnumber, borrowed_from, images) — єдине джерело галереї товару.

    Спільне для лістингу фото і для пакетного експорту, щоб zip містив рівно те,
    що видно в картці (включно з позиченими студійними фото донора).
    """
    try:
        from services.product_images import list_images
    except ImportError:
        from backend.services.product_images import list_images

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")

    productnumber = product.productnumber or ""
    own_images = list_images(productnumber)
    borrowed_from = (getattr(product, "official_photos_from", None) or "").strip()

    # Якщо заповнено official_photos_from — викидаємо власні official
    # (зазвичай їх нема, але про всяк) і додаємо official від донора.
    # Захист від циклу: НЕ дивимось у donor.official_photos_from — один хоп.
    if borrowed_from and borrowed_from.lstrip("#").lower() != productnumber.lstrip("#").lower():
        own_images = [e for e in own_images if e.kind != "official"]
        donor_all = list_images(borrowed_from)
        donor_official = [e for e in donor_all if e.kind == "official"]
        merged = own_images + donor_official
        # Перетасовуємо у єдину стрічку: спершу official, потім real, потім defect
        kind_order = {"official": 0, "real": 1, "defect": 2}
        merged.sort(key=lambda e: (kind_order.get(e.kind, 9), e.filename.lower()))
        # Переіндексувати 0..N
        from dataclasses import replace
        images = [replace(e, index=i) for i, e in enumerate(merged)]
    else:
        images = own_images

    return productnumber, borrowed_from, images


@router.get("/api/products/{product_id}/images")
def get_product_images(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """Повертає список фото товару (за productnumber).
    Сортовано: фото з меншим суфіксним номером — головне (першим).
    Зараз: локальна папка. Майбутнє: cloud-провайдер з тією ж сигнатурою.
    """
    productnumber, borrowed_from, images = _product_gallery(product_id, db)

    return {
        "productnumber": productnumber,
        "official_photos_from": borrowed_from or None,
        "count": len(images),
        "defect_count": sum(1 for img in images if img.is_defect),
        "official_count": sum(1 for img in images if img.kind == "official"),
        "real_count": sum(1 for img in images if img.kind == "real"),
        "images": [
            {
                "filename": img.filename,
                "url": img.url,
                "index": img.index,
                "is_defect": img.is_defect,
                "kind": img.kind,
            }
            for img in images
        ],
    }


def _build_photos_zip(product_id: int, kind: str, db: Session):
    """(байти zip, ім'я архіву, скільки фото запаковано) — спільне для двох шляхів:
    віддачі архіву потоком у браузер і запису на диск у десктоп-режимі."""
    import io
    import zipfile

    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes

    productnumber, _borrowed, images = _product_gallery(product_id, db)
    if kind != "all":
        images = [img for img in images if img.kind == kind]
    if not images:
        raise HTTPException(status_code=404, detail="У товару немає фото для завантаження")

    buf = io.BytesIO()
    used: set = set()
    packed = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for img in images:
            data = read_image_bytes(img)
            if data is None:
                logger.warning(f"Skipping unreadable photo in zip: {img.filename}")
                continue
            name = img.filename
            # Колізія імен (напр. власне фото і фото донора) — додаємо суфікс.
            if name.lower() in used:
                stem, ext = os.path.splitext(name)
                name = f"{stem}_{img.index}{ext}"
            used.add(name.lower())
            zf.writestr(name, data)
            packed += 1

    if packed == 0:
        raise HTTPException(status_code=404, detail="Фото недоступні (файли не читаються)")

    stem = (productnumber or f"product-{product_id}").lstrip("#").strip() or f"product-{product_id}"
    return buf.getvalue(), f"{stem}_фото.zip", packed


@router.get("/api/products/{product_id}/photos/download")
def download_product_photos(
    product_id: int = Path(..., ge=1, description="ID товару"),
    kind: str = Query("all", regex="^(all|official|real|defect)$",
                      description="що класти в архів: all (за замовчуванням) або один набір"),
    db: Session = Depends(get_db),
):
    """Пакетне викачування: усі фото товару одним .zip (у теку завантажень браузера).

    Архів містить рівно те, що видно в картці — включно з позиченими студійними
    фото донора. Файли зберігають оригінальні імена (`<pnum>_01.webp` …).

    ⚠️ Це шлях ДЛЯ БРАУЗЕРА. У десктоп-застосунку (PyWebView) він марний: вебв'ю
    не вміє зберігати відповідь як файл — див. `/photos/save-zip` нижче.
    """
    import io
    from urllib.parse import quote as _urlquote

    data, zip_name, packed = _build_photos_zip(product_id, kind, db)
    # ASCII-фолбек + RFC 5987 для кирилиці в імені файлу.
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", zip_name) or "photos.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; "
                f"filename*=UTF-8''{_urlquote(zip_name)}"
            ),
            "X-Photo-Count": str(packed),
        },
    )


# ── Збереження на диск (десктоп-режим) ────────────────────────────────────────
# У вбудованому вебв'ю `<a download>` не працює: клік не зберігає файл, а
# переходить на нього — фото розгортається на весь екран поверх застосунку й
# блокує роботу, а zip просто зникає в нікуди. Оскільки бекенд у десктоп-режимі
# на тій самій машині, що й вікно, він і записує файл у «Завантаження», а UI
# показує, куди саме. Див. services/file_saver.py.

def _saver():
    try:
        from services.file_saver import save_bytes
    except ImportError:
        from backend.services.file_saver import save_bytes
    return save_bytes


@router.post("/api/products/{product_id}/photos/save-one")
def save_product_photo_to_disk(
    product_id: int = Path(..., ge=1, description="ID товару"),
    filename: str = Query(..., description="ім'я фото з галереї картки"),
    db: Session = Depends(get_db),
):
    """Зберегти ОДНЕ фото товару в теку «Завантаження». Повертає шлях."""
    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes

    _pnum, _borrowed, images = _product_gallery(product_id, db)
    # Шукаємо саме серед фото ЦІЄЇ картки — довільний шлях ззовні сюди не потрапить.
    img = next((i for i in images if i.filename == filename), None)
    if img is None:
        raise HTTPException(status_code=404, detail=f"Фото {filename} не знайдено в картці товару")

    data = read_image_bytes(img)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Файл {filename} не читається")

    try:
        path, saved_name = _saver()(data, img.filename, fallback_name="photo.webp")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не вдалося зберегти файл: {e}")
    return {"saved": True, "path": path, "filename": saved_name, "bytes": len(data)}


@router.post("/api/products/{product_id}/photos/save-zip")
def save_product_photos_zip_to_disk(
    product_id: int = Path(..., ge=1, description="ID товару"),
    kind: str = Query("all", regex="^(all|official|real|defect)$"),
    db: Session = Depends(get_db),
):
    """Зберегти ВСІ фото товару одним .zip у теку «Завантаження». Повертає шлях."""
    data, zip_name, packed = _build_photos_zip(product_id, kind, db)
    try:
        path, saved_name = _saver()(data, zip_name, fallback_name="photos.zip")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не вдалося зберегти архів: {e}")
    return {"saved": True, "path": path, "filename": saved_name, "count": packed, "bytes": len(data)}


def _pnum_and_category(product_id: int, db: Session):
    """(productnumber, категорія-папка) для товару. Категорія: де вже лежать
    фото → інакше за типом → «Інше»."""
    try:
        from services.photo_manager import resolve_category
    except ImportError:
        from backend.services.photo_manager import resolve_category
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
    pnum = (product.productnumber or "").strip()
    if not pnum:
        raise HTTPException(status_code=400, detail="У товару немає номера")
    type_name = None
    if getattr(product, "typeid", None):
        t = db.query(models.Type).filter(models.Type.id == product.typeid).first()
        type_name = getattr(t, "typename", None) if t else None
    return pnum, resolve_category(pnum, type_name)


def _photo_owner_and_category(product_id: int, filename: str, db: Session):
    """Власник канонічного файла, видимого в картці.

    Для звичайного фото це відкритий товар. Для позичених студійних фото —
    номер донора: редагуємо один оригінал донора, тож оновлення одразу бачать
    усі картки ростовки, що ним користуються.
    """
    try:
        from services.photo_manager import photo_belongs_to, resolve_category
    except ImportError:
        from backend.services.photo_manager import photo_belongs_to, resolve_category

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
    own = (product.productnumber or "").strip()
    if not own:
        raise HTTPException(status_code=400, detail="У товару немає номера")
    if photo_belongs_to(own, filename):
        return _pnum_and_category(product_id, db)

    donor = (getattr(product, "official_photos_from", None) or "").strip()
    if donor and photo_belongs_to(donor, filename):
        return donor, resolve_category(donor)
    return _pnum_and_category(product_id, db)


def _invalidate_photo_cache(*productnumbers: str, membership_changed: bool = False):
    """Скинути списки фото; за зміни кількості також оновити маркери таблиці."""
    try:
        from services.product_images import get_photo_pnum_set, invalidate_image_list_cache
    except ImportError:
        from backend.services.product_images import get_photo_pnum_set, invalidate_image_list_cache
    invalidate_image_list_cache(*productnumbers)
    if membership_changed:
        try:
            get_photo_pnum_set(force=True)
        except Exception:
            pass


@router.post("/api/products/{product_id}/photos")
async def add_product_photos(
    product_id: int = Path(..., ge=1),
    files: List[UploadFile] = File(...),
    kind: str = Query("official", regex="^(official|real|defect)$",
                      description="куди вантажити: official (_NN) або real (_00N)"),
    db: Session = Depends(get_db),
):
    """Додати фото товару (multipart). kind='official'→`_NN`; 'real'→`_00N`.
    Конверт у WebP → мірор + R2."""
    import tempfile, os as _os
    try:
        from services.photo_manager import add_photos
    except ImportError:
        from backend.services.photo_manager import add_photos
    from starlette.concurrency import run_in_threadpool
    pnum, category = _pnum_and_category(product_id, db)
    sources = []
    tmps = []
    try:
        for uf in files:
            suffix = _os.path.splitext(uf.filename or "")[1] or ".img"
            fd, tmp = tempfile.mkstemp(suffix=suffix)
            with _os.fdopen(fd, "wb") as out:
                out.write(await uf.read())
            tmps.append(tmp)
            sources.append((tmp, uf.filename))
        # ⚠️ add_photos — важка синхронна робота (декод + конверт у WebP через Pillow
        # + мережева заливка в R2). Виклик просто в корутині морозив event loop на
        # весь час: завантаження 10 фото = секунди, коли бекенд не відповідає взагалі.
        # Роут лишається async (треба `await uf.read()`), але блокуючу частину
        # віддаємо в threadpool.
        result = await run_in_threadpool(add_photos, pnum, category, sources, kind=kind)
    finally:
        for t in tmps:
            try: _os.unlink(t)
            except OSError: pass
    added = result["added"]
    errors = result["errors"]
    # Жодне фото не збереглось — повертаємо причину (а не сирий 500),
    # щоб фронт показав осмислене сповіщення (типово — HEIC/битий файл).
    if added == 0 and errors:
        reasons = "; ".join(f"{e['file']}: {e['reason']}" for e in errors)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Не вдалося додати фото ({len(errors)}): {reasons}")
    if added:
        _invalidate_photo_cache(pnum, membership_changed=True)
    return {"added": added, "category": category, "kind": kind, "errors": errors}


@router.post("/api/products/{product_id}/photos/move-kind")
def move_product_photos_kind(
    product_id: int = Path(..., ge=1),
    from_kind: str = Query(..., regex="^(official|real|defect)$"),
    to_kind: str = Query(..., regex="^(official|real|defect)$"),
    db: Session = Depends(get_db),
):
    """Перемістити ВСІ фото товару між галереями (official↔real). Перейменовує
    файли в мірорі + видаляє старі R2-ключі + заливає нові. Зберігає порядок."""
    try:
        from services.photo_manager import move_photos_kind
    except ImportError:
        from backend.services.photo_manager import move_photos_kind
    pnum, category = _pnum_and_category(product_id, db)
    try:
        result = move_photos_kind(pnum, category, from_kind, to_kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_photo_cache(pnum)
    return {**result, "from_kind": from_kind, "to_kind": to_kind, "category": category}


@router.post("/api/products/{product_id}/photos/move-one")
def move_one_product_photo(
    product_id: int = Path(..., ge=1),
    filename: str = Query(..., description="ім'я файлу, який переносимо"),
    to_kind: str = Query(..., regex="^(official|real|defect)$"),
    db: Session = Depends(get_db),
):
    """Перенести ОДНЕ фото в інший набір (official/real/defect). Для виправлення
    помилково залитих (напр. дефект потрапив у «Реальні»)."""
    try:
        from services.photo_manager import move_one_photo
    except ImportError:
        from backend.services.photo_manager import move_one_photo
    pnum, category = _pnum_and_category(product_id, db)
    try:
        result = move_one_photo(pnum, category, filename, to_kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_photo_cache(pnum)
    return {**result, "category": category}


@router.put("/api/products/{product_id}/photos/replace")
async def replace_product_photo(
    product_id: int = Path(..., ge=1),
    filename: str = Query(..., description="ім'я фото, яке замінюємо"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Замінити вміст одного офіційного фото (та сама позиція, новий файл)."""
    import tempfile, os as _os
    try:
        from services.photo_manager import replace_photo
    except ImportError:
        from backend.services.photo_manager import replace_photo
    from starlette.concurrency import run_in_threadpool
    pnum, category = _pnum_and_category(product_id, db)
    suffix = _os.path.splitext(file.filename or "")[1] or ".img"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with _os.fdopen(fd, "wb") as out:
            out.write(await file.read())
        # Та сама причина, що й в add_photos: конверт + R2 не мають бути на event loop.
        await run_in_threadpool(replace_photo, pnum, category, filename, tmp)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try: _os.unlink(tmp)
        except OSError: pass
    _invalidate_photo_cache(pnum)
    return {"replaced": filename}


@router.post("/api/products/{product_id}/photos/transform")
async def transform_product_photo(
    product_id: int = Path(..., ge=1),
    filename: str = Query(..., description="ім'я канонічного фото"),
    operation: str = Body(
        ...,
        embed=True,
        regex="^(rotate_left|rotate_180|rotate_right|flip_horizontal)$",
    ),
    db: Session = Depends(get_db),
):
    """Повернути або дзеркально відобразити фото, зберігши ім'я/позицію.

    Важку роботу Pillow + мережеве оновлення одного ключа Cloudflare R2
    виконуємо поза event loop. При збої R2 локальний майстер не змінюється.
    """
    try:
        from services.photo_manager import transform_photo
    except ImportError:
        from backend.services.photo_manager import transform_photo
    from starlette.concurrency import run_in_threadpool

    pnum, category = _photo_owner_and_category(product_id, filename, db)
    try:
        result = await run_in_threadpool(
            transform_photo, pnum, category, filename, operation,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        logger.exception("Photo transform failed locally for product %s", product_id)
        raise HTTPException(
            status_code=500,
            detail=f"Не вдалося обробити фото; оригінал не змінено: {e}",
        )
    except Exception as e:
        logger.exception("Photo transform/R2 sync failed for product %s", product_id)
        raise HTTPException(
            status_code=502,
            detail=f"Не вдалося синхронізувати фото з Cloudflare; оригінал не змінено: {e}",
        )
    _invalidate_photo_cache(pnum)
    return {"transformed": filename, "category": category, **result}


@router.put("/api/products/{product_id}/photos/reorder")
def reorder_product_photos(
    product_id: int = Path(..., ge=1),
    order: List[str] = Body(..., embed=True, description="імена у бажаному порядку"),
    kind: str = Query("official", regex="^(official|real|defect)$"),
    db: Session = Depends(get_db),
):
    """Перенумерувати фото (перше = головне) — official→`_01.._0N`, real→`_001.._00N`."""
    try:
        from services.photo_manager import reorder_photos
    except ImportError:
        from backend.services.photo_manager import reorder_photos
    pnum, category = _pnum_and_category(product_id, db)
    try:
        result = reorder_photos(pnum, category, order, kind=kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_photo_cache(pnum)
    return {"order": result, "kind": kind}


@router.delete("/api/products/{product_id}/photos/{filename}")
def delete_product_photo(
    product_id: int = Path(..., ge=1),
    filename: str = Path(...),
    db: Session = Depends(get_db),
):
    """Видалити одне офіційне фото (мірор + R2)."""
    try:
        from services.photo_manager import delete_photo
    except ImportError:
        from backend.services.photo_manager import delete_photo
    pnum, category = _pnum_and_category(product_id, db)
    try:
        delete_photo(pnum, category, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_photo_cache(pnum, membership_changed=True)
    return {"deleted": filename}


@router.get("/api/products/model-profile")
def get_model_profile(
    brand_name: str = Query(..., min_length=1),
    model: str = Query(..., min_length=2),
    exclude_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """«Профіль моделі» (1.5): агрегат по ВСІХ записах бренд+модель у базі —
    найчастіші model-level характеристики + матеріали. Живить розумне
    заповнення картки/QuickAdd (підтяг у ПОРОЖНІ поля; нічого не пише сам).
    Per-item поля (розмір/колір/ціна/стан/заміри) свідомо НЕ включені."""
    rows = db.execute(text("""
        SELECT p.id, p.productnumber,
               t.typename AS type_name, st.subtypename AS subtype_name,
               sty.stylename AS style_name, g.gendername AS gender_name,
               p.season, p.collection, p.geometric_shape, p.width,
               mc.countryname AS manufacturer_country_name,
               ht.heeltypename AS heel_type_name, lt.lacetypename AS lace_type_name,
               so.soletypename AS sole_type_name, tsh.toeshapename AS toe_shape_name,
               ft.fasteningtypename AS fastening_type_name, li.liningname AS lining_name,
               tech.technologyname AS technology_name, pk.packagingname AS packaging_name
        FROM products p
        JOIN brands b ON b.id = p.brandid
        LEFT JOIN types t ON t.id = p.typeid
        LEFT JOIN subtypes st ON st.id = p.subtypeid
        LEFT JOIN styles sty ON sty.id = p.styleid
        LEFT JOIN genders g ON g.id = p.genderid
        LEFT JOIN countries mc ON mc.id = p.manufacturercountryid
        LEFT JOIN heel_types ht ON ht.id = p.heeltypeid
        LEFT JOIN lace_types lt ON lt.id = p.lacetypeid
        LEFT JOIN sole_types so ON so.id = p.soletypeid
        LEFT JOIN toe_shapes tsh ON tsh.id = p.toeshapeid
        LEFT JOIN fastening_types ft ON ft.id = p.fasteningtypeid
        LEFT JOIN linings li ON li.id = p.liningid
        LEFT JOIN technologies tech ON tech.id = p.technologyid
        LEFT JOIN packaging_types pk ON pk.id = p.packagingid
        WHERE lower(btrim(b.brandname)) = lower(btrim(:brand))
          AND lower(btrim(coalesce(p.model, ''))) = lower(btrim(:model))
          AND (CAST(:exclude_id AS int) IS NULL OR p.id != :exclude_id)
    """), {"brand": brand_name, "model": model, "exclude_id": exclude_id}).mappings().all()

    if not rows:
        return {"records": 0, "numbers": [], "fields": {}, "materials": {}}

    from collections import Counter
    FIELDS = ["type_name", "subtype_name", "style_name", "gender_name", "season",
              "collection", "geometric_shape", "width", "manufacturer_country_name",
              "heel_type_name", "lace_type_name", "sole_type_name", "toe_shape_name",
              "fastening_type_name", "lining_name", "technology_name", "packaging_name"]
    fields_out: Dict[str, Any] = {}
    for f in FIELDS:
        vals = [str(r[f]).strip() for r in rows if r[f] is not None and str(r[f]).strip()]
        if not vals:
            continue
        cnt = Counter(vals)
        top, n = cnt.most_common(1)[0]
        fields_out[f] = {"value": top, "share": n, "total": len(vals),
                         "options": dict(cnt.most_common(3))}

    # Матеріали: CSV на (товар, позиція) → мода по позиції
    ids = [r["id"] for r in rows]
    mat_rows = db.execute(text("""
        SELECT pm.product_id, pm.position, m.materialname
        FROM product_materials pm JOIN materials m ON m.id = pm.material_id
        WHERE pm.product_id = ANY(:ids)
        ORDER BY pm.product_id, pm.position, pm.ord
    """), {"ids": ids}).fetchall()
    per_prod: Dict[tuple, list] = {}
    for pid, pos, name in mat_rows:
        per_prod.setdefault((pid, pos), []).append(name)
    by_pos: Dict[str, Counter] = {}
    for (_pid, pos), names in per_prod.items():
        by_pos.setdefault(pos, Counter())[", ".join(names)] += 1
    materials_out = {}
    for pos, cnt in by_pos.items():
        top, n = cnt.most_common(1)[0]
        materials_out[pos] = {"value": top, "share": n, "total": sum(cnt.values())}

    numbers = sorted({r["productnumber"] for r in rows})
    return {"records": len(rows), "numbers": numbers[:10],
            "fields": fields_out, "materials": materials_out}


@router.get("/api/products/{product_id}/journal-url")
def get_product_journal_url(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """Пряме посилання на аркуш журналу з цим товаром (кнопка «Таблиця» в картці).
    gid вкладки дістаємо через Sheets API за назвою завозу (deliveryname)."""
    row = db.execute(text("SELECT deliveryid FROM products WHERE id = :i"),
                     {"i": product_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    sheet = product_service.get_delivery_name(db, row[0])
    if not sheet:
        raise HTTPException(status_code=404, detail="Товар не прив'язаний до поставки")
    try:
        try:
            from scripts.sheets_parser import get_gc, JOURNAL_ID
        except ImportError:
            from backend.scripts.sheets_parser import get_gc, JOURNAL_ID
        ws = get_gc().open_by_key(JOURNAL_ID).worksheet(sheet)
        return {"url": f"https://docs.google.com/spreadsheets/d/{JOURNAL_ID}/edit#gid={ws.id}",
                "sheet": sheet}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"journal-url failed for product {product_id}: {e}")
        raise HTTPException(status_code=502,
                            detail=f"Не вдалося знайти вкладку «{sheet}» у журналі")


@router.get("/api/products/{product_id}")
def get_product(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """
    Отримати деталі товару за його ID
    """
    try:
        product = product_service.get_product_with_relations(db, product_id)
        
        if not product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Скільки правок цієї картки ще не лягло в журнал (черга + провалені).
        # Без цього числа розсинхрон картки й аркуша був невидимий: правка
        # зберігалась у БД, запис в аркуш падав, і дізнатись про це було ніяк.
        try:
            product["journal_pending"] = journal_sync.pending_by_product(
                db, [product_id]).get(product_id, 0)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"journal_pending failed for {product_id}: {_e}")
            product["journal_pending"] = 0
        return product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні товару: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Публікація товару в публічний інтернет-каталог (Telegram Mini App ~/Desktop/BMS_catalog).
# Окрема additive-таблиця catalog_listings (схему products НЕ чіпаємо). Ключ =
# productnumber → публікується вся картка/ростовка. Вітрина каталогу read-only;
# керування публікацією — ЛИШЕ звідси (back-office). Не плутати з мертвим
# /visibility (is_lost) — це інший, публічний канал.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/products/{product_id}/catalog", response_model=Dict[str, Any])
def get_product_catalog_status(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db),
):
    """Поточний стан публікації товару в публічному каталозі."""
    pnum = db.execute(
        text("SELECT productnumber FROM products WHERE id = :id"), {"id": product_id}
    ).scalar()
    if pnum is None:
        raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
    _ensure_catalog_discount_columns(db)
    try:
        row = db.execute(
            text("SELECT is_published, is_featured, sale_price, is_on_sale "
                 "FROM catalog_listings WHERE productnumber = :pn"),
            {"pn": pnum},
        ).mappings().first()
    except Exception:
        # Колонок знижки ще нема (DDL не пройшов) — показуємо базовий стан,
        # а не 500: картка товару має відкриватись у будь-якому разі.
        db.rollback()
        row = db.execute(
            text("SELECT is_published, is_featured, NULL AS sale_price, "
                 "FALSE AS is_on_sale FROM catalog_listings WHERE productnumber = :pn"),
            {"pn": pnum},
        ).mappings().first()
    return {
        "productnumber": pnum,
        "is_published": bool(row["is_published"]) if row else False,
        "is_featured": bool(row["is_featured"]) if row else False,
        "sale_price": float(row["sale_price"]) if row and row["sale_price"] is not None else None,
        "is_on_sale": bool(row["is_on_sale"]) if row else False,
    }


_CATALOG_DISCOUNT_COLUMNS_READY = False


def _ensure_catalog_discount_columns(db: Session) -> None:
    """Адитивні колонки знижки в catalog_listings (акційна ціна ЛИШЕ для вітрини —
    products.price НЕ чіпаємо). Самостворюються, щоб BMS міг писати їх незалежно
    від того, чи запускався локальний бекенд каталогу.

    ⚠️ DDL, а не звичайний запит. ALTER TABLE бере ACCESS EXCLUSIVE lock, і поки він
    чекає у черзі, за ним стають УСІ наступні читачі catalog_listings (черга блокувань
    у Postgres — FIFO). Один зовнішній клієнт, що тримає відкриту транзакцію (напр.
    завислий cloud-sync), перетворював це на повне зависання застосунку. Тому:
      • виконуємо максимум ОДИН раз за процес (не на кожен відкритий товар),
      • під lock_timeout — краще тихо не виконатись, ніж підвісити UI,
      • перед DDL перевіряємо каталог: якщо колонки вже є (звичайний випадок) —
        жодного ALTER і жодного блокування взагалі."""
    global _CATALOG_DISCOUNT_COLUMNS_READY
    if _CATALOG_DISCOUNT_COLUMNS_READY:
        return
    try:
        present = {r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'catalog_listings'"
        ))}
        missing = {"sale_price", "is_on_sale"} - present
        if not missing:
            _CATALOG_DISCOUNT_COLUMNS_READY = True
            return
        # Ніколи не чекаємо на блокування довше 3с — інакше підвисає весь UI.
        db.execute(text("SET LOCAL lock_timeout = '3s'"))
        if "sale_price" in missing:
            db.execute(text("ALTER TABLE catalog_listings ADD COLUMN IF NOT EXISTS sale_price numeric"))
        if "is_on_sale" in missing:
            db.execute(text("ALTER TABLE catalog_listings ADD COLUMN IF NOT EXISTS "
                            "is_on_sale boolean NOT NULL DEFAULT FALSE"))
        db.commit()
        _CATALOG_DISCOUNT_COLUMNS_READY = True
    except Exception as e:
        # Не змогли зараз (зайнято блокування) — не валимо запит: читання нижче
        # толерантне до відсутніх колонок, а наступний старт спробує ще раз.
        db.rollback()
        logger.warning(f"catalog_listings discount columns not ensured: {e}")


@router.patch("/api/products/{product_id}/catalog/discount", response_model=Dict[str, Any])
def update_product_catalog_discount(
    product_id: int = Path(..., ge=1, description="ID товару"),
    sale_price: Optional[float] = Body(None, embed=True),   # акційна ціна (None → прибрати)
    is_on_sale: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
):
    """Знижка на картку у публічному каталозі (акційна ціна ЛИШЕ для вітрини —
    products.price НЕ змінюємо, тож Prom/OLX/облік лишаються з реальною ціною).
    Upsert catalog_listings за productnumber — діє на всю картку (ростовку)."""
    try:
        pnum = db.execute(
            text("SELECT productnumber FROM products WHERE id = :id"), {"id": product_id}
        ).scalar()
        if pnum is None:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        _ensure_catalog_discount_columns(db)
        price = None if (sale_price is None or sale_price <= 0) else float(sale_price)
        on = bool(is_on_sale and price is not None)   # без валідної ціни знижку не вмикаємо
        db.execute(text("""
            INSERT INTO catalog_listings (productnumber, is_published, is_featured, sale_price, is_on_sale, updated_at)
            VALUES (:pn, FALSE, FALSE, :sp, :on, now())
            ON CONFLICT (productnumber) DO UPDATE SET
                sale_price = EXCLUDED.sale_price,
                is_on_sale = EXCLUDED.is_on_sale,
                updated_at = now()
        """), {"pn": pnum, "sp": price, "on": on})
        db.commit()
        sync_queued = catalog_sync_service.trigger_catalog_cloud_sync(
            f"catalog discount {pnum}: on_sale={on}, sale_price={price}"
        )
        return {
            "success": True,
            "productnumber": pnum,
            "sale_price": price,
            "is_on_sale": on,
            "catalog_sync_queued": sync_queued,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating catalog discount {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні знижки: {str(e)}")


@router.patch("/api/products/{product_id}/catalog", response_model=Dict[str, Any])
def update_product_catalog_status(
    product_id: int = Path(..., ge=1, description="ID товару"),
    is_published: bool = Body(..., embed=True),
    is_featured: bool = Body(False, embed=True),
    clear_lost: bool = Body(False, embed=True),
    db: Session = Depends(get_db),
):
    """Опублікувати/зняти товар у каталозі (+ «Рекомендований»). Upsert у
    catalog_listings за productnumber — діє на всю картку (ростовку).
    clear_lost=True (підтверджено користувачем у діалозі «Загублений») — заразом
    знімає is_lost з усіх рядків номера, інакше публікація без ефекту (каталог
    ховає is_lost незалежно від is_published)."""
    try:
        pnum = db.execute(
            text("SELECT productnumber FROM products WHERE id = :id"), {"id": product_id}
        ).scalar()
        if pnum is None:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        if is_published and clear_lost:
            db.execute(text("UPDATE products SET is_lost = false, updated_at = now() WHERE productnumber = :pn"),
                      {"pn": pnum})
        # «Рекомендований» має сенс лише для опублікованого товару
        feat = bool(is_featured and is_published)
        db.execute(text("""
            INSERT INTO catalog_listings (productnumber, is_published, is_featured, published_at, updated_at)
            VALUES (:pn, :pub, :feat, CASE WHEN :pub THEN now() END, now())
            ON CONFLICT (productnumber) DO UPDATE SET
                is_published = EXCLUDED.is_published,
                is_featured  = EXCLUDED.is_featured,
                published_at = COALESCE(catalog_listings.published_at, EXCLUDED.published_at),
                updated_at   = now()
        """), {"pn": pnum, "pub": is_published, "feat": feat})
        db.commit()
        sync_queued = catalog_sync_service.trigger_catalog_cloud_sync(
            f"catalog toggle {pnum}: published={bool(is_published)}, featured={feat}"
        )
        return {
            "success": True,
            "productnumber": pnum,
            "is_published": is_published,
            "is_featured": feat,
            "catalog_sync_queued": sync_queued,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating catalog status {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні публікації: {str(e)}")

@router.post("/api/products", response_model=schemas.Product, status_code=status.HTTP_201_CREATED)
def create_product(
    product_data: schemas.ProductCreate,
    db: Session = Depends(get_db)
):
    """
    Створити новий товар
    """
    try:
        # Canonicalize the productnumber (#X form, upper-cased letter prefix)
        # so we don't recreate bare/lowercase twins of existing records.
        if product_data.productnumber:
            product_data.productnumber = _norm_pn(product_data.productnumber) or product_data.productnumber

        # Перевіряємо, чи вже існує товар з таким номером
        existing_product = product_service.get_product_by_number(db, product_data.productnumber)

        if existing_product:
            raise HTTPException(
                status_code=400,
                detail=f"Товар з номером {product_data.productnumber} вже існує"
            )

        # Створюємо новий товар
        product = product_service.create_product(db, product_data)
        return product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating product: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при створенні товару: {str(e)}")

@router.put("/api/products/{product_id}", response_model=schemas.Product)
def update_product(
    product_id: int = Path(..., ge=1, description="ID товару"),
    product_data: schemas.ProductUpdate = Body(...),
    db: Session = Depends(get_db)
):
    """
    Оновити існуючий товар
    """
    try:
        # Перевіряємо, чи існує товар з таким ID
        existing_product = product_service.get_product(db, product_id)
        
        if not existing_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Canonicalize incoming productnumber on edit
        if product_data.productnumber:
            product_data.productnumber = _norm_pn(product_data.productnumber) or product_data.productnumber

        # Якщо змінюється номер товару, перевіряємо його унікальність
        if product_data.productnumber and product_data.productnumber != existing_product.productnumber:
            duplicate = product_service.get_product_by_number(db, product_data.productnumber)
            
            if duplicate and duplicate.id != product_id:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Товар з номером {product_data.productnumber} вже існує"
                )
        
        # Оновлюємо товар (+ лок + пропагація на «братів» ростовки + markdown)
        updated_product = product_service.update_product(db, product_id, product_data)

        if not updated_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")

        # Поля для write-back: ті, що реально записані+залочені цим викликом
        # (включно з авто-похідним oldprice від правила уцінки)
        edited_lockable = set(getattr(updated_product, "_writeback_fields", set()))

        # Матеріали по позиціях → синтетичні поля material_<pos> для write-back
        # (значення = CSV назв; колонка резолвиться через WRITEBACK_FIELD_HEADERS).
        material_writeback = getattr(updated_product, "_material_writeback", {}) or {}
        # Заміри → синтетичні meas_<name> (значення = рядок-діапазон).
        measurement_writeback = getattr(updated_product, "_measurement_writeback", {}) or {}

        # Phase 2b: write-back у журнал — через чергу, щоб PUT відповідав миттєво
        # (запис в аркуш ~2-3с мережі не має блокувати UI). Раніше тут стартував
        # daemon-потік напряму: якщо він падав (токен/SSL/мережа), правка лишалась
        # тільки в БД і аркуш відставав назавжди. Тепер поля лягають у
        # journal_writeback_queue, а воркер несе їх в аркуш і повторює спроби.
        if edited_lockable or material_writeback or measurement_writeback:
            sheet_title = product_service.get_delivery_name(db, updated_product.deliveryid)
            pnum = updated_product.productnumber
            # Shoe-lookup FKs are written back as the canonical NAME, not the id.
            # Resolve now (request scope, session alive) — the write-back runs in a
            # background thread where lazy relationship loads would fail.
            field_values = {}
            for f in edited_lockable:
                v = getattr(updated_product, f)
                if f in product_service.SHOE_FK_NAME_FIELDS:
                    v = product_service.resolve_lookup_name(db, f, v)
                field_values[f] = v
            for pos, csv in material_writeback.items():
                field_values[f"material_{pos}"] = csv
            for mkey, rng in measurement_writeback.items():
                field_values[mkey] = rng   # mkey уже 'meas_<name>'

            journal_sync.enqueue_many(db, updated_product.id, pnum, sheet_title, field_values)
            db.commit()
            journal_sync.kick()

        return updated_product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні товару: {str(e)}")

@router.delete("/api/products/{product_id}", response_model=Dict[str, Any])
def delete_product(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """
    Видалити товар
    """
    try:
        # Перевіряємо, чи існує товар з таким ID
        existing_product = product_service.get_product(db, product_id)
        
        if not existing_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Видаляємо товар
        success = product_service.delete_product(db, product_id)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Не вдалося видалити товар з ID {product_id}")
        
        return {"success": True, "message": f"Товар з ID {product_id} видалено"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при видаленні товару: {str(e)}")

@router.patch("/api/products/{product_id}/visibility", response_model=Dict[str, Any])
def update_product_visibility(
    product_id: int = Path(..., ge=1, description="ID товару"),
    is_visible: bool = Body(..., embed=True),
    db: Session = Depends(get_db)
):
    """
    Оновити видимість товару
    """
    try:
        # Перевіряємо, чи існує товар з таким ID
        existing_product = product_service.get_product(db, product_id)
        
        if not existing_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Оновлюємо видимість
        success = product_service.update_product_visibility(db, product_id, is_visible)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Не вдалося оновити видимість товару з ID {product_id}")
        
        return {
            "success": True, 
            "message": f"Видимість товару з ID {product_id} оновлено",
            "is_visible": is_visible
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating product visibility {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні видимості товару: {str(e)}")

@router.patch("/api/products/{product_id}/unlock", response_model=Dict[str, Any])
def unlock_product_fields(
    product_id: int = Path(..., ge=1, description="ID товару"),
    fields: Optional[List[str]] = Body(None, embed=True,
        description="Поля для розблокування; порожньо/null = розблокувати всі"),
    db: Session = Depends(get_db)
):
    """
    Зняти in-app лок з полів товару («скинути до аркуша»). Розблоковані поля
    буде відновлено зі значення аркуша при наступному парсингу.
    """
    try:
        product = product_service.get_product(db, product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")

        current = set()
        if product.manually_edited_fields:
            current = {x.strip() for x in product.manually_edited_fields.split(",") if x.strip()}

        if fields:
            remaining = current - {f.strip() for f in fields}
        else:
            remaining = set()  # unlock all

        product.manually_edited_fields = ",".join(sorted(remaining)) if remaining else None
        if not remaining:
            product.manually_edited_at = None
        db.commit()
        return {
            "success": True,
            "product_id": product_id,
            "remaining_locked": sorted(remaining),
            "message": "Лок знято — значення відновляться з аркуша при наступному парсингу",
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error unlocking product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при розблокуванні товару: {str(e)}")


@router.post("/api/products/bulk-update", response_model=Dict[str, Any])
def bulk_update_products(
    product_ids: List[int] = Body(..., min_items=1),
    update_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db)
):
    """
    Масове оновлення товарів
    """
    try:
        # Перевіряємо, що є хоча б один товар для оновлення
        if not product_ids:
            raise HTTPException(status_code=400, detail="Потрібно вказати хоча б один ID товару")
        
        # Перевіряємо, що є дані для оновлення
        if not update_data:
            raise HTTPException(status_code=400, detail="Потрібно вказати дані для оновлення")
        
        # Оновлюємо товари
        updated_count = product_service.bulk_update_products(db, product_ids, update_data)
        
        return {
            "success": True,
            "message": f"Оновлено {updated_count} товарів",
            "updated_count": updated_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk updating products: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при масовому оновленні товарів: {str(e)}") 

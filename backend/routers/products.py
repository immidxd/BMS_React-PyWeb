from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Path, status, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import logging
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
    from services import product_service
except ImportError:
    from backend.models.database import get_db
    from backend.models import models
    from backend.schemas import product as schemas
    from backend.services import product_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/products", response_model=schemas.ProductListResponse)
async def get_products(
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
    published_on: Optional[List[str]] = Query(None, description="Де опубліковано: telegram|olx"),
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
async def get_product_filters(db: Session = Depends(get_db)):
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
async def get_available_facets(
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
            conditionids=conditionids, styleid=styleid, styleids=styleids,
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

@router.get("/api/products/{product_id}/images")
async def get_product_images(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """Повертає список фото товару (за productnumber).
    Сортовано: фото з меншим суфіксним номером — головне (першим).
    Зараз: локальна папка. Майбутнє: cloud-провайдер з тією ж сигнатурою.
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


def _invalidate_photo_cache():
    """Скинути кеш «чи є фото», щоб маркери в списку оновились одразу."""
    try:
        from services.product_images import get_photo_pnum_set
    except ImportError:
        from backend.services.product_images import get_photo_pnum_set
    try:
        get_photo_pnum_set(force=True)
    except Exception:
        pass


@router.post("/api/products/{product_id}/photos")
async def add_product_photos(
    product_id: int = Path(..., ge=1),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Додати офіційні фото товару (multipart). Конверт у WebP → мірор + R2."""
    import tempfile, os as _os
    try:
        from services.photo_manager import add_photos
    except ImportError:
        from backend.services.photo_manager import add_photos
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
        added = add_photos(pnum, category, sources)
    finally:
        for t in tmps:
            try: _os.unlink(t)
            except OSError: pass
    _invalidate_photo_cache()
    return {"added": added, "category": category}


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
    pnum, category = _pnum_and_category(product_id, db)
    suffix = _os.path.splitext(file.filename or "")[1] or ".img"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with _os.fdopen(fd, "wb") as out:
            out.write(await file.read())
        replace_photo(pnum, category, filename, tmp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try: _os.unlink(tmp)
        except OSError: pass
    return {"replaced": filename}


@router.put("/api/products/{product_id}/photos/reorder")
async def reorder_product_photos(
    product_id: int = Path(..., ge=1),
    order: List[str] = Body(..., embed=True, description="імена у бажаному порядку"),
    db: Session = Depends(get_db),
):
    """Перенумерувати офіційні фото (перше = головне) → `_01.._0N`."""
    try:
        from services.photo_manager import reorder_photos
    except ImportError:
        from backend.services.photo_manager import reorder_photos
    pnum, category = _pnum_and_category(product_id, db)
    try:
        result = reorder_photos(pnum, category, order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"order": result}


@router.delete("/api/products/{product_id}/photos/{filename}")
async def delete_product_photo(
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
    _invalidate_photo_cache()
    return {"deleted": filename}


@router.get("/api/products/{product_id}")
async def get_product(
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
        
        return product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні товару: {str(e)}")

@router.post("/api/products", response_model=schemas.Product, status_code=status.HTTP_201_CREATED)
async def create_product(
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
async def update_product(
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

        # Phase 2b: write-back у журнал — у ФОНІ, щоб PUT відповідав миттєво
        # (запис в аркуш ~2-3с мережі не має блокувати UI). Лок у БД зберігає
        # правку, якщо фоновий запис відстане/впаде.
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

            def _writeback_bg(sheet_title=sheet_title, pnum=pnum, field_values=field_values):
                try:
                    from backend.scripts import sheets_parser as _sp
                except ImportError:
                    from scripts import sheets_parser as _sp
                for f, v in field_values.items():
                    try:
                        res = _sp.writeback_field_to_journal(sheet_title, pnum, f, v)
                        if not res.get("ok"):
                            logger.warning(f"[writeback] {f} skipped: {res.get('reason')}")
                    except Exception as we:
                        logger.error(f"[writeback] {f} failed: {we}")

            threading.Thread(target=_writeback_bg, daemon=True).start()

        return updated_product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні товару: {str(e)}")

@router.delete("/api/products/{product_id}", response_model=Dict[str, Any])
async def delete_product(
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
async def update_product_visibility(
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
async def unlock_product_fields(
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
async def bulk_update_products(
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
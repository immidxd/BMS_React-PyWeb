from typing import Optional, Dict, Any
from datetime import date, datetime
import logging
from fastapi import APIRouter, Depends, Query, Path, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class DeliveryCreate(BaseModel):
    deliverydate: date                       # ISO YYYY-MM-DD з фронту
    supplier_name: Optional[str] = None      # резолвиться get-or-create
    supplier_id: Optional[int] = None        # або прямий id
    purchase_cost: Optional[float] = 0       # «Сума» (закупівля)
    delivery_cost: Optional[float] = 0       # «Сума доставки»
    name_override: Optional[str] = None      # тематична назва (напр. «Валізи(Андрій)»)


@router.post("/api/deliveries", status_code=201)
async def create_delivery(payload: DeliveryCreate = Body(...), db: Session = Depends(get_db)):
    """Створити завіз: клон вкладки «New» у журналі + рядок у deliveries.

    Послідовність (sheet-first, щоб мати gid для БД-рядка; відкат tab при збої БД):
      1. резолв supplier_id (get-or-create за назвою, як парсер);
      2. назва завозу `ДД.ММ.РРРР(Постачальник)` або name_override;
      3. dedup за deliveryname;
      4. create_delivery_tab → sheet_gid;
      5. INSERT deliveries (+sheet_gid). За флагом PARSER_ADD_PRODUCT.
    """
    try:
        from scripts.journal_writer import create_delivery_tab, delete_delivery_tab, ADD_PRODUCT_ENABLED
        from scripts.sheets_parser import _get_or_create_supplier
    except ImportError:
        from backend.scripts.journal_writer import create_delivery_tab, delete_delivery_tab, ADD_PRODUCT_ENABLED
        from backend.scripts.sheets_parser import _get_or_create_supplier

    if not ADD_PRODUCT_ENABLED:
        raise HTTPException(status_code=403, detail="Створення завозу вимкнено (PARSER_ADD_PRODUCT=0)")

    # 1. supplier
    supplier_id = payload.supplier_id
    supplier_name = (payload.supplier_name or "").strip()
    if supplier_id is None and supplier_name:
        supplier_id = _get_or_create_supplier(db, supplier_name)
        db.flush()
    if supplier_id is not None and not supplier_name:
        row = db.execute(text("SELECT company_name FROM suppliers WHERE id=:id"), {"id": supplier_id}).fetchone()
        supplier_name = (row[0] if row else "") or ""

    # 2. deliveryname
    date_str = payload.deliverydate.strftime("%d.%m.%Y")
    deliveryname = (payload.name_override or "").strip() or (
        f"{date_str}({supplier_name})" if supplier_name else date_str
    )

    # 3. dedup
    if db.execute(text("SELECT 1 FROM deliveries WHERE deliveryname=:n"), {"n": deliveryname}).scalar():
        raise HTTPException(status_code=409, detail=f"Завіз «{deliveryname}» вже існує")

    # 4. sheet tab
    try:
        tab = create_delivery_tab(
            deliveryname, deliverydate=date_str,
            purchase_cost=payload.purchase_cost, delivery_cost=payload.delivery_cost,
        )
    except Exception as e:
        logger.error(f"create_delivery_tab failed: {e}")
        raise HTTPException(status_code=502, detail=f"Не вдалося створити вкладку в журналі: {e}")

    # 5. deliveries row (+sheet_gid); відкат вкладки при збої
    try:
        new_id = db.execute(
            text("""INSERT INTO deliveries (deliveryname, deliverydate, supplier_id,
                                            purchase_cost, delivery_cost, sheet_gid)
                    VALUES (:n, :d, :sid, :pc, :dc, :gid) RETURNING id"""),
            {"n": deliveryname, "d": payload.deliverydate, "sid": supplier_id,
             "pc": payload.purchase_cost or 0, "dc": payload.delivery_cost or 0,
             "gid": tab["gid"]},
        ).scalar()
        db.commit()
    except Exception as e:
        db.rollback()
        try:
            delete_delivery_tab(tab["gid"])  # прибрати сирітську вкладку
        except Exception as ce:
            logger.error(f"rollback tab delete failed: {ce}")
        logger.error(f"deliveries insert failed: {e}")
        raise HTTPException(status_code=500, detail=f"Помилка запису завозу в БД: {e}")

    return {"id": new_id, "deliveryname": deliveryname, "sheet_gid": tab["gid"],
            "deliverydate": str(payload.deliverydate), "supplier_id": supplier_id}


class ProductQuickCreate(BaseModel):
    productnumber: str                       # даний або згенерований (/next-number)
    type_name: Optional[str] = None
    subtype_name: Optional[str] = None
    style_name: Optional[str] = None
    brand_name: Optional[str] = None
    gender_name: Optional[str] = None
    color_name: Optional[str] = None
    condition_name: Optional[str] = None     # «Стан»: Новий/Хороший/…
    packaging_name: Optional[str] = None     # «Пакування»: коробка/пакет/…
    manufacturer_name: Optional[str] = None  # «Країна-виробник» → manufacturercountryid
    model: Optional[str] = None
    marking: Optional[str] = None
    season: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None
    extranote: Optional[str] = None
    sizeeu: Optional[str] = None
    measurementscm: Optional[str] = None
    price: Optional[float] = None
    # Опційні скаляри (з хаба «+»)
    collection: Optional[str] = None         # «Колекція»
    gtin: Optional[str] = None               # «GTIN»
    oldprice: Optional[float] = None         # «Стара ціна»
    geometric_shape: Optional[str] = None    # «Геометрична форма»
    width: Optional[str] = None              # «Ширина»
    dimensions: Optional[str] = None         # «Габарити»
    size_letter: Optional[str] = None        # «Буквений»
    # Хаб «Деталі» — взуттєві lookup (name→id) + виміри
    sole_type_name: Optional[str] = None       # «Тип підошви»
    fastening_type_name: Optional[str] = None  # «Застібка»
    toe_shape_name: Optional[str] = None       # «Форма носка»
    technology_name: Optional[str] = None      # «Технології»
    sole_color_name: Optional[str] = None      # «Колір підошви»
    lining_name: Optional[str] = None          # «Підкладка»
    heel_type_name: Optional[str] = None       # «Тип каблука»
    lace_type_name: Optional[str] = None       # «Тип шнурівки»
    height: Optional[float] = None             # «Висота» (measurements_height min=max)
    sole_thickness: Optional[float] = None     # «Товщина підошви»
    # Одягові виміри (min=max)
    chest: Optional[float] = None              # «Груди (н/о)» → measurements_pog
    waist: Optional[float] = None              # «Талія (н/о)» → measurements_pot
    hips: Optional[float] = None               # «Бедра (н/о)» → measurements_pob
    sleeve: Optional[float] = None             # «Рукав»
    length: Optional[float] = None             # «Довжина»
    # Хаб «Матеріали» — позиції → product_materials
    material_upper: Optional[str] = None       # «Верх»
    material_middle: Optional[str] = None      # «Середина»
    material_sole: Optional[str] = None        # «Підошва»
    material_midsole: Optional[str] = None     # «Проміжна підошва»
    material_insole: Optional[str] = None      # «Устілка»
    material_membrane: Optional[str] = None    # «Мембрана»


@router.post("/api/deliveries/{delivery_id}/products", status_code=201)
async def add_product_to_delivery(
    delivery_id: int = Path(..., ge=1),
    payload: ProductQuickCreate = Body(...),
    db: Session = Depends(get_db),
):
    """Додати товар у завіз: create_product (DB, з deliveryid) + append_product_row (аркуш).

    Транзакційно: товар у БД flush'иться, потім append у вкладку; при збої аркуша — rollback
    (товар у БД не лишається). Назви довідників → id через _get_or_create (як парсер); ті ж
    назви пишуться прямо в колонки аркуша за заголовком. За флагом PARSER_ADD_PRODUCT.
    """
    try:
        from scripts.journal_writer import append_product_row, ADD_PRODUCT_ENABLED
        from scripts.sheets_parser import _get_or_create, _apply_product_materials
        from services.product_service import _resolve_lookup_id_by_name, LOOKUP_NAME_FIELDS
        from utils.productnumber_normalizer import normalize as _norm_pn
        from models import models
    except ImportError:
        from backend.scripts.journal_writer import append_product_row, ADD_PRODUCT_ENABLED
        from backend.scripts.sheets_parser import _get_or_create, _apply_product_materials
        from backend.services.product_service import _resolve_lookup_id_by_name, LOOKUP_NAME_FIELDS
        from backend.utils.productnumber_normalizer import normalize as _norm_pn
        from backend.models import models

    if not ADD_PRODUCT_ENABLED:
        raise HTTPException(status_code=403, detail="Додавання товару вимкнено (PARSER_ADD_PRODUCT=0)")

    d = db.execute(text("SELECT id, deliveryname FROM deliveries WHERE id=:i"), {"i": delivery_id}).mappings().first()
    if not d:
        raise HTTPException(status_code=404, detail="Завіз не знайдено")
    deliveryname = d["deliveryname"]

    pn = _norm_pn(payload.productnumber) or (payload.productnumber or "").strip()
    if not pn:
        raise HTTPException(status_code=400, detail="Порожній номер товару")
    if db.execute(text("SELECT 1 FROM products WHERE UPPER(REPLACE(productnumber,'#',''))=UPPER(REPLACE(:p,'#',''))"),
                  {"p": pn}).scalar():
        raise HTTPException(status_code=409, detail=f"Товар «{pn}» уже існує")

    def _rid(model, field, val):
        if not val or not str(val).strip():
            return None
        obj = _get_or_create(db, model, field, str(val).strip())
        return obj.id if obj else None

    try:
        prod = models.Product(
            productnumber=pn, deliveryid=delivery_id, quantity=1,
            typeid=_rid(models.Type, "typename", payload.type_name),
            subtypeid=_rid(models.Subtype, "subtypename", payload.subtype_name),
            styleid=_rid(models.Style, "stylename", payload.style_name),
            brandid=_rid(models.Brand, "brandname", payload.brand_name),
            genderid=_rid(models.Gender, "gendername", payload.gender_name),
            colorid=_rid(models.Color, "colorname", payload.color_name),
            conditionid=_rid(models.Condition, "conditionname", payload.condition_name),
            packagingid=_rid(models.PackagingType, "packagingname", payload.packaging_name),
            manufacturercountryid=_rid(models.Country, "countryname", payload.manufacturer_name),
            statusid=_rid(models.Status, "statusname", "Непродано"),  # новий товар = Непродано
            model=payload.model, marking=payload.marking, season=payload.season,
            year=payload.year, description=payload.description, extranote=payload.extranote,
            sizeeu=payload.sizeeu, measurementscm=payload.measurementscm,
            price=payload.price or 0,
            collection=payload.collection, gtin=payload.gtin, oldprice=payload.oldprice,
            geometric_shape=payload.geometric_shape, width=payload.width,
            dimensions=payload.dimensions, size_letter=payload.size_letter,
        )
        db.add(prod)
        db.flush()  # отримати id, FK-рядки персистовані; ще НЕ commit

        # Хаб «Деталі» — взуттєві lookup (name→id через спільний резолвер)
        for nf in ("sole_type_name", "fastening_type_name", "toe_shape_name", "technology_name",
                   "sole_color_name", "lining_name", "heel_type_name", "lace_type_name"):
            v = getattr(payload, nf, None)
            if v and str(v).strip():
                id_col, table, name_col = LOOKUP_NAME_FIELDS[nf]
                rid = _resolve_lookup_id_by_name(db, table, name_col, str(v).strip())
                if rid:
                    setattr(prod, id_col, rid)
        for attr, mn, mx in (
            ("height", "measurements_height_min", "measurements_height_max"),
            ("sole_thickness", "measurements_sole_thickness_min", "measurements_sole_thickness_max"),
            ("chest", "measurements_pog_min", "measurements_pog_max"),
            ("waist", "measurements_pot_min", "measurements_pot_max"),
            ("hips", "measurements_pob_min", "measurements_pob_max"),
            ("sleeve", "measurements_sleeve_min", "measurements_sleeve_max"),
            ("length", "measurements_length_min", "measurements_length_max"),
        ):
            mv = getattr(payload, attr, None)
            if mv is not None:
                setattr(prod, mn, mv); setattr(prod, mx, mv)

        # Хаб «Матеріали» — позиції → product_materials
        import re as _re_m
        mat: dict = {}
        for f, pos in (("material_upper", "upper"), ("material_middle", "middle"),
                       ("material_sole", "sole"), ("material_midsole", "midsole"),
                       ("material_insole", "insole"), ("material_membrane", "membrane")):
            mv = getattr(payload, f, None)
            if mv and str(mv).strip():
                mat[pos] = [x.strip() for x in _re_m.split(r"[;,/]", str(mv)) if x.strip()]
        if mat:
            _apply_product_materials(db, prod.id, mat, sheet_source=deliveryname)
        db.flush()
    except Exception as e:
        db.rollback()
        logger.error(f"add_product_to_delivery DB build failed: {e}")
        raise HTTPException(status_code=500, detail=f"Помилка створення товару в БД: {e}")

    # Аркуш: ті самі назви в колонки за заголовком
    sheet_map = {
        "Номер": pn, "Вид": payload.type_name, "Підвид": payload.subtype_name,
        "Стиль": payload.style_name, "Бренд": payload.brand_name, "Модель": payload.model,
        "Маркування": payload.marking, "Рік": payload.year, "Стать": payload.gender_name,
        "Сезон": payload.season, "Колір": payload.color_name, "Опис": payload.description,
        "Розмір": payload.sizeeu, "СМ": payload.measurementscm, "Ціна": payload.price,
        "Стан": payload.condition_name, "Пакування": payload.packaging_name,
        "Країна-виробник": payload.manufacturer_name,
        "Статус": "Непродано",  # новий товар за замовчуванням
        "Екстра примітка": payload.extranote,
        "Колекція": payload.collection, "GTIN": payload.gtin, "Стара ціна": payload.oldprice,
        "Геометрична форма": payload.geometric_shape, "Ширина": payload.width,
        "Габарити": payload.dimensions, "Буквений": payload.size_letter,
        # Деталі
        "Тип підошви": payload.sole_type_name, "Застібка": payload.fastening_type_name,
        "Форма носка": payload.toe_shape_name, "Технології": payload.technology_name,
        "Колір підошви": payload.sole_color_name, "Підкладка": payload.lining_name,
        "Тип каблука": payload.heel_type_name, "Тип шнурівки": payload.lace_type_name,
        "Висота": payload.height, "Товщина підошви": payload.sole_thickness,
        # Одягові виміри
        "Груди (н/о)": payload.chest, "Талія (н/о)": payload.waist, "Бедра (н/о)": payload.hips,
        "Рукав": payload.sleeve, "Довжина": payload.length,
        # Матеріали
        "Верх": payload.material_upper, "Середина": payload.material_middle,
        "Підошва": payload.material_sole, "Проміжна підошва": payload.material_midsole,
        "Устілка": payload.material_insole, "Мембрана": payload.material_membrane,
    }
    field_values = {k: v for k, v in sheet_map.items() if v not in (None, "")}
    try:
        row_res = append_product_row(deliveryname, field_values)
    except Exception as e:
        db.rollback()  # товар у БД зникає — узгодженість збережена
        logger.error(f"append_product_row failed, rolled back DB: {e}")
        # Дружнє, конкретне повідомлення для попапа на фронті.
        msg = str(e)
        low = msg.lower()
        if "після" in msg and "спроб" in msg:  # JournalTransientError (вже дружній текст)
            detail = ("⚠️ Тимчасова проблема зв'язку з Google Sheets — товар НЕ додано. "
                      "Перевірте інтернет і натисніть «Зберегти товар» ще раз.")
        elif any(m in low for m in ("ssl", "certificate", "connection", "max retries", "timed out", "handshake")):
            detail = ("⚠️ Не вдалось зв'язатися з Google Sheets (мережа/SSL) — товар НЕ додано. "
                      "Спробуйте ще раз за кілька секунд.")
        elif "немає колонки" in msg or "не знайд" in low or "worksheet" in low:
            detail = (f"Вкладку завозу «{deliveryname}» не знайдено в журналі або змінено її структуру. "
                      "Оновіть сторінку / перевірте назву вкладки.")
        else:
            detail = f"Не вдалося дописати рядок в журнал: {msg}"
        raise HTTPException(status_code=502, detail=detail)

    prod_id = prod.id
    db.commit()
    return {"id": prod_id, "productnumber": pn, "deliveryid": delivery_id,
            "sheet_row": row_res.get("row")}


@router.delete("/api/deliveries/{delivery_id}/products/{product_id}")
async def delete_product_from_delivery(
    delivery_id: int = Path(..., ge=1),
    product_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Видалити товар із завозу: очистити рядок аркуша (cols 1–52) + DELETE з БД.

    ⚠️ Гард: товар із продажами (order_items) НЕ видаляється (щоб не втратити історію).
    """
    try:
        from scripts.journal_writer import delete_product_row, ADD_PRODUCT_ENABLED
    except ImportError:
        from backend.scripts.journal_writer import delete_product_row, ADD_PRODUCT_ENABLED

    if not ADD_PRODUCT_ENABLED:
        raise HTTPException(status_code=403, detail="Видалення вимкнено (PARSER_ADD_PRODUCT=0)")

    p = db.execute(
        text("""SELECT p.productnumber, p.deliveryid, d.deliveryname
                FROM products p LEFT JOIN deliveries d ON d.id = p.deliveryid
                WHERE p.id = :pid"""),
        {"pid": product_id},
    ).mappings().first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    if p["deliveryid"] != delivery_id:
        raise HTTPException(status_code=400, detail="Товар не належить цьому завозу")

    sold = db.execute(text("SELECT COUNT(*) FROM order_items WHERE product_id = :pid"), {"pid": product_id}).scalar()
    if sold:
        raise HTTPException(status_code=409, detail="Товар має продажі — видалення заборонено")

    if p["deliveryname"]:
        try:
            delete_product_row(p["deliveryname"], p["productnumber"])
        except Exception as e:
            logger.error(f"delete_product_row failed: {e}")
            raise HTTPException(status_code=502, detail=f"Не вдалося очистити рядок в аркуші: {e}")

    db.execute(text("DELETE FROM products WHERE id = :pid"), {"pid": product_id})
    db.commit()
    return {"deleted": product_id}


class NumberUpdate(BaseModel):
    productnumber: str


@router.put("/api/deliveries/{delivery_id}/products/{product_id}/number")
async def rename_product_number(
    delivery_id: int = Path(..., ge=1),
    product_id: int = Path(..., ge=1),
    payload: NumberUpdate = Body(...),
    db: Session = Depends(get_db),
):
    """Перейменувати номер товару прямо зі списку завозу. Дедуп-перевірка (дубль →
    409), гард продажів (409), запис у БД + аркуш (як gate: збій аркуша → відкат БД)."""
    try:
        from scripts.journal_writer import rename_product_row, ADD_PRODUCT_ENABLED
        from services.product_service import check_number_conflict
        from utils.productnumber_normalizer import normalize as _norm_pn
    except ImportError:
        from backend.scripts.journal_writer import rename_product_row, ADD_PRODUCT_ENABLED
        from backend.services.product_service import check_number_conflict
        from backend.utils.productnumber_normalizer import normalize as _norm_pn

    if not ADD_PRODUCT_ENABLED:
        raise HTTPException(status_code=403, detail="Редагування вимкнено (PARSER_ADD_PRODUCT=0)")

    p = db.execute(text("""SELECT p.productnumber, p.deliveryid, p.sizeeu, p.size_letter,
                                  p.brandid, p.model, d.deliveryname
                           FROM products p LEFT JOIN deliveries d ON d.id = p.deliveryid
                           WHERE p.id = :pid"""), {"pid": product_id}).mappings().first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    if p["deliveryid"] != delivery_id:
        raise HTTPException(status_code=400, detail="Товар не належить цьому завозу")

    old = p["productnumber"]
    new_norm = _norm_pn(payload.productnumber) or (payload.productnumber or "").strip()
    if not new_norm:
        raise HTTPException(status_code=400, detail="Порожній номер")

    def _canon(s):
        return (s or "").strip().lstrip("#").rstrip(";").strip().upper()
    if _canon(new_norm) == _canon(old):
        return {"renamed": False, "productnumber": old, "note": "без змін"}

    # Гард продажів — переіменування проданого розсинхронізує матчинг замовлень.
    sold = db.execute(text("SELECT COUNT(*) FROM order_items WHERE product_id = :pid"), {"pid": product_id}).scalar()
    if sold:
        raise HTTPException(status_code=409, detail="Товар має продажі — зміна номера заборонена")

    # Дедуп: номер не має збігатися з ІНШИМ товаром (productnumber/clonednumbers).
    # Ростовка-близнюк (той самий бренд+модель, інший розмір) — НЕ дубль.
    conflict = check_number_conflict(
        db, new_norm, exclude_id=product_id,
        rostovka_ref={"brandid": p["brandid"], "model": p["model"],
                      "sizeeu": p["sizeeu"], "size_letter": p["size_letter"]},
    )
    if conflict:
        raise HTTPException(status_code=409,
                            detail=f"Номер «{new_norm}» вже зайнятий іншим товаром ({conflict['productnumber']})")

    # Оновити БД (не комітимо), тоді аркуш як gate.
    db.execute(text("UPDATE products SET productnumber = :n WHERE id = :i"), {"n": new_norm, "i": product_id})
    if p["deliveryname"]:
        try:
            res = rename_product_row(p["deliveryname"], old, new_norm,
                                     size_hint=(p["sizeeu"] or p["size_letter"]))
        except Exception as e:
            db.rollback()
            logger.error(f"rename_product_row failed: {e}")
            raise HTTPException(status_code=502, detail=_journal_err_detail(e, p["deliveryname"]))
        if res.get("ambiguous"):
            db.rollback()
            raise HTTPException(status_code=409,
                                detail=f"Ростовка: кілька рядків з номером «{old}» — уточніть вручну в журналі")
    db.commit()
    return {"renamed": True, "productnumber": new_norm, "old": old}


@router.get("/api/deliveries/{delivery_id}/reconcile")
async def reconcile_delivery(delivery_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """Звірка завозу з журналом (read-only): товари в БД(deliveryid), яких НЕМА у
    вкладці аркуша і БЕЗ продажів → кандидати на видалення (зникли з журналу вручну).

    Безпека: якщо вкладку не вдалось прочитати — 502 (не повертаємо хибних кандидатів).
    Видалення НЕ робиться тут — лише список; підтверджує користувач (DELETE-ендпоінт).
    """
    try:
        from scripts.journal_writer import read_delivery_productnumbers
    except ImportError:
        from backend.scripts.journal_writer import read_delivery_productnumbers

    d = db.execute(text("SELECT deliveryname FROM deliveries WHERE id=:i"), {"i": delivery_id}).mappings().first()
    if not d:
        raise HTTPException(status_code=404, detail="Завіз не знайдено")
    try:
        sheet_nums = read_delivery_productnumbers(d["deliveryname"])
    except Exception as e:
        logger.error(f"reconcile read failed: {e}")
        raise HTTPException(status_code=502, detail=f"Не вдалося прочитати вкладку журналу: {e}")

    rows = db.execute(text("""
        SELECT p.id, p.productnumber,
               EXISTS(SELECT 1 FROM order_items oi WHERE oi.product_id=p.id) AS has_sales
        FROM products p WHERE p.deliveryid = :i
    """), {"i": delivery_id}).mappings().all()

    def _canon(s):
        return (s or "").strip().lstrip("#").rstrip(";").strip().upper()

    missing = [r for r in rows if _canon(r["productnumber"]) not in sheet_nums]
    orphans = [{"id": r["id"], "productnumber": r["productnumber"]} for r in missing if not r["has_sales"]]
    protected = sum(1 for r in missing if r["has_sales"])
    return {
        "delivery_id": delivery_id,
        "sheet_count": len(sheet_nums),
        "orphan_ids": [o["id"] for o in orphans],
        "orphans": orphans,
        "protected_with_sales": protected,
    }


@router.post("/api/deliveries/{delivery_id}/sync")
async def sync_delivery(delivery_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """⚡ Точкова синхронізація вкладки завозу з аркушем → БД (upsert адди/правки +
    видалення орфанів). Для loading-on-open у Картці завозу: картка чекає завершення,
    щоб показати дані, що 100% збігаються з реальним аркушем."""
    try:
        from scripts.sheets_parser import sync_one_delivery_tab
    except ImportError:
        from backend.scripts.sheets_parser import sync_one_delivery_tab

    d = db.execute(text("SELECT deliveryname FROM deliveries WHERE id=:i"), {"i": delivery_id}).mappings().first()
    if not d:
        raise HTTPException(status_code=404, detail="Завіз не знайдено")
    try:
        return sync_one_delivery_tab(db, d["deliveryname"])
    except Exception as e:
        db.rollback()
        logger.error(f"sync_delivery failed: {e}")
        raise HTTPException(status_code=502, detail=f"Не вдалося синхронізувати з журналом: {e}")


def _journal_err_detail(e: Exception, deliveryname: str) -> str:
    """Дружнє повідомлення для попапа (як у add-товару)."""
    msg = str(e); low = msg.lower()
    if "після" in msg and "спроб" in msg:
        return "⚠️ Тимчасова проблема зв'язку з Google Sheets — спробуйте ще раз за кілька секунд."
    if any(m in low for m in ("ssl", "certificate", "connection", "max retries", "timed out", "handshake")):
        return "⚠️ Не вдалось зв'язатися з Google Sheets (мережа/SSL) — спробуйте ще раз."
    if "немає колонки" in msg or "не знайд" in low or "worksheet" in low:
        return f"Вкладку «{deliveryname}» не знайдено або змінено її структуру."
    return f"Помилка журналу: {msg}"


@router.post("/api/deliveries/{delivery_id}/sort-rows")
async def sort_delivery_rows(delivery_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """⇅ Впорядкувати товарні рядки вкладки за номером (зростання) — і в журналі.
    Переписує лише cols1-52; блок «Завоз» недоторканий."""
    try:
        from scripts.journal_writer import reorder_delivery_rows
    except ImportError:
        from backend.scripts.journal_writer import reorder_delivery_rows
    d = db.execute(text("SELECT deliveryname FROM deliveries WHERE id=:i"), {"i": delivery_id}).mappings().first()
    if not d:
        raise HTTPException(status_code=404, detail="Завіз не знайдено")
    try:
        return reorder_delivery_rows(d["deliveryname"])
    except Exception as e:
        logger.error(f"sort_delivery_rows failed: {e}")
        raise HTTPException(status_code=502, detail=_journal_err_detail(e, d["deliveryname"]))


@router.get("/api/deliveries/{delivery_id}/info")
async def get_delivery_info(delivery_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """Редаговані поля блоку «Інформація про завоз» з аркуша (label→значення).
    Авто-логіка: «Дата початку» (час старту запису = created_at) проставляється, якщо
    порожня; «Дата завершення» — час «зараз», якщо к-сть товарів досягла «Очікувана
    к-сть речей». Обидві лишаються редагованими."""
    try:
        from scripts.journal_writer import read_delivery_info_block, update_delivery_info_block
    except ImportError:
        from backend.scripts.journal_writer import read_delivery_info_block, update_delivery_info_block
    d = db.execute(text("SELECT deliveryname, created_at FROM deliveries WHERE id=:i"),
                   {"i": delivery_id}).mappings().first()
    if not d:
        raise HTTPException(status_code=404, detail="Завіз не знайдено")
    try:
        info = read_delivery_info_block(d["deliveryname"])
    except Exception as e:
        logger.error(f"get_delivery_info failed: {e}")
        raise HTTPException(status_code=502, detail=_journal_err_detail(e, d["deliveryname"]))

    by_label = {f["label"]: f for f in info.get("fields", [])}
    items_count = db.execute(text("SELECT COUNT(*) FROM products WHERE deliveryid=:i"),
                             {"i": delivery_id}).scalar() or 0
    auto: Dict[str, str] = {}
    # «Дата початку» — час початку запису (created_at завозу), якщо ще порожня.
    if not (by_label.get("Дата початку", {}).get("value") or "").strip():
        started = d["created_at"]
        if started:
            try:
                auto["Дата початку"] = started.strftime("%d.%m.%Y %H:%M")
            except Exception:
                auto["Дата початку"] = str(started)
    # «Дата завершення» — час «зараз», якщо к-сть досягла очікуваної (і ще порожня).
    if not (by_label.get("Дата завершення", {}).get("value") or "").strip():
        expected_raw = (by_label.get("Очікувана к-сть речей", {}).get("value") or "").strip()
        import re as _re3
        m = _re3.search(r"\d+", expected_raw)
        expected = int(m.group()) if m else 0
        if expected > 0 and items_count >= expected:
            from datetime import datetime as _dt2
            auto["Дата завершення"] = _dt2.now().strftime("%d.%m.%Y %H:%M")
    if auto:
        try:
            update_delivery_info_block(d["deliveryname"], auto)
            for lbl, v in auto.items():  # відобразити одразу у відповіді
                if lbl in by_label:
                    by_label[lbl]["value"] = v
                else:
                    info["fields"].append({"label": lbl, "value": v, "editable": True})
        except Exception as e:
            logger.warning(f"info auto-fill write failed (best-effort): {e}")
    return info


# Поле інфо-блоку → колонка `deliveries` (дзеркало в БД для відомих полів).
_INFO_DB_COLUMNS = {
    "Дата завозу": ("deliverydate", "date"),
    "Сума": ("purchase_cost", "float"),
    "Сума доставки": ("delivery_cost", "float"),
    "Коментар": ("description", "text"),
}


@router.put("/api/deliveries/{delivery_id}/info")
async def update_delivery_info(delivery_id: int = Path(..., ge=1),
                               payload: Dict[str, Any] = Body(...),
                               db: Session = Depends(get_db)):
    """Записати редаговані поля інфо-блоку в аркуш + дзеркалити відомі в БД.
    payload = {label: value}. Промокод/Очікувана/Статус — журнал-only."""
    try:
        from scripts.journal_writer import update_delivery_info_block
    except ImportError:
        from backend.scripts.journal_writer import update_delivery_info_block
    d = db.execute(text("SELECT deliveryname FROM deliveries WHERE id=:i"), {"i": delivery_id}).mappings().first()
    if not d:
        raise HTTPException(status_code=404, detail="Завіз не знайдено")
    changes = {k: v for k, v in payload.items() if isinstance(k, str)}
    try:
        res = update_delivery_info_block(d["deliveryname"], changes)
    except Exception as e:
        logger.error(f"update_delivery_info failed: {e}")
        raise HTTPException(status_code=502, detail=_journal_err_detail(e, d["deliveryname"]))
    # Дзеркало у БД для відомих полів (best-effort; журнал — джерело правди).
    db_sets, db_params = [], {"i": delivery_id}
    for label, (col, typ) in _INFO_DB_COLUMNS.items():
        if label not in changes:
            continue
        raw = changes[label]
        if typ == "float":
            try:
                import re as _re2
                cleaned = _re2.sub(r"[^\d.,]", "", str(raw)).replace(",", ".")
                val = float(cleaned) if cleaned else None
            except (ValueError, TypeError):
                val = None
        elif typ == "date":
            val = None
            for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    val = datetime.strptime(str(raw).strip(), fmt).date(); break
                except (ValueError, TypeError):
                    continue
        else:
            val = (str(raw).strip() or None)
        db_sets.append(f"{col} = :{col}")
        db_params[col] = val
    if db_sets:
        try:
            db.execute(text(f"UPDATE deliveries SET {', '.join(db_sets)} WHERE id=:i"), db_params)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"info DB-mirror failed (sheet ok): {e}")
    return res


@router.get("/api/deliveries")
async def get_deliveries(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    supplier_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("created_at", description="id|created_at|deliverydate|deliveryname"),
    sort_dir: str = Query("desc", description="asc|desc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    where = []
    params: Dict[str, Any] = {}
    if supplier_id is not None:
        where.append("supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id
    if search:
        where.append("deliveryname ILIKE :search")
        params["search"] = f"%{search}%"
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total = db.execute(text(f"SELECT COUNT(*) FROM deliveries{where_sql}"), params).scalar() or 0
    allowed_columns = {
        "id": "d.id",
        "created_at": "d.created_at",
        "deliverydate": "d.deliverydate",
        "deliveryname": "d.deliveryname",
    }
    order_col = allowed_columns.get(sort_by, "d.created_at")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
    list_sql = text(
        f"""
        SELECT d.id, d.deliveryname, d.description, d.created_at, d.deliverydate, d.supplier_id,
               s.company_name AS supplier_name
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        {where_sql}
        ORDER BY {order_col} {order_dir}, d.id DESC
        OFFSET :offset LIMIT :limit
        """
    )
    rows = db.execute(list_sql, {**params, "offset": (page - 1) * per_page, "limit": per_page}).mappings().all()
    return {
        "items": [dict(r) for r in rows],
        "total": int(total),
        "page": page,
        "per_page": per_page,
        "pages": (int(total) + per_page - 1) // per_page,
    }

@router.get("/api/deliveries/{delivery_id}")
async def get_delivery(delivery_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT id, deliveryname, description, created_at, deliverydate, supplier_id FROM deliveries WHERE id = :id"),
        {"id": delivery_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return dict(row)

@router.put("/api/deliveries/{delivery_id}")
async def update_delivery(
    delivery_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = None,
    db: Session = Depends(get_db)
):
    exists = db.execute(text("SELECT 1 FROM deliveries WHERE id = :id"), {"id": delivery_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if not payload:
        raise HTTPException(status_code=400, detail="No data provided")
    allowed = {"deliveryname", "description", "deliverydate", "supplier_id"}
    fields = {k: v for k, v in (payload or {}).items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    set_clause = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    params = {**fields, "id": delivery_id}
    db.execute(text(f"UPDATE deliveries SET {set_clause} WHERE id = :id"), params)
    db.commit()
    row = db.execute(
        text("SELECT id, deliveryname, description, created_at, deliverydate, supplier_id FROM deliveries WHERE id = :id"),
        {"id": delivery_id},
    ).mappings().first()
    return dict(row)



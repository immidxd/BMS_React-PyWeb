from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, text
import hashlib
import logging
from datetime import datetime

from models.database import get_db
from models.models import Client, Gender, ClientAddress
from schemas.reference import (
    Client as ClientSchema, ClientCreate, ClientUpdate, ClientList,
    ClientAddress as ClientAddressSchema, ClientAddressCreate, ClientAddressUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/api/clients", response_model=ClientList, tags=["clients"])
async def get_clients(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    gender_id: Optional[int] = None,
    sort_by: str = Query("last_name", description="id|last_name|first_name|order_count|total_order_amount|confirmed_orders|cancelled_count|rating"),
    sort_dir: str = Query("asc", description="asc|desc"),
    db: Session = Depends(get_db)
):
    """
    Get list of clients with pagination, filtering, and order breakdown counts.
    """
    logger.info(f"Fetching clients: page={page}, per_page={per_page}, search={search}, sort={sort_by} {sort_dir}")

    where_clauses = []
    params: dict = {}

    if search:
        where_clauses.append("""
            (c.first_name ILIKE :search OR c.last_name ILIKE :search
             OR c.phone_number ILIKE :search OR c.email ILIKE :search
             OR c.address ILIKE :search)
        """)
        params["search"] = f"%{search}%"

    if gender_id:
        where_clauses.append("c.gender_id = :gender_id")
        params["gender_id"] = gender_id

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Count total
    count_sql = f"SELECT COUNT(*) FROM clients c {where_sql}"
    total = db.execute(text(count_sql), params).scalar() or 0

    # Allowed sort columns (prevent SQL injection)
    allowed_sorts = {
        "id": "c.id",
        "last_name": "c.last_name",
        "first_name": "c.first_name",
        "order_count": "c.order_count",
        "total_order_amount": "c.total_order_amount",
        "confirmed_orders": "confirmed_orders",
        "cancelled_count": "cancelled_count",
        "ignored_count": "ignored_count",
        "return_exchange_count": "return_exchange_count",
        "rating": "rating",
    }
    sort_col = allowed_sorts.get(sort_by, "c.last_name")
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    params["limit_val"] = per_page
    params["offset_val"] = (page - 1) * per_page

    main_sql = f"""
        SELECT
            c.*,
            COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '') AS full_name,
            COALESCE(stats.confirmed_orders, 0) AS confirmed_orders,
            COALESCE(stats.cancelled_count, 0) AS cancelled_count,
            COALESCE(stats.ignored_count, 0) AS ignored_count,
            COALESCE(stats.return_exchange_count, 0) AS return_exchange_count,
            COALESCE(stats.has_deferred, false) AS has_deferred,
            -- Rating formula: base 5.0 + order bonus - cancel/ignore/return penalties + amount bonus, clamped 0-10
            GREATEST(0, LEAST(10,
                5.0
                + LEAST(COALESCE(stats.confirmed_orders, 0) * 0.5, 3.0)
                - LEAST(COALESCE(stats.cancelled_count, 0) * 1.0, 3.0)
                - LEAST(COALESCE(stats.ignored_count, 0) * 0.5, 2.0)
                - LEAST(COALESCE(stats.return_exchange_count, 0) * 0.3, 1.0)
                + LEAST(COALESCE(c.total_order_amount, 0) / 10000.0, 2.0)
            )) AS rating
        FROM clients c
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) FILTER (WHERE o.order_status_id NOT IN (5, 6, 9, 10)) AS confirmed_orders,
                COUNT(*) FILTER (WHERE o.order_status_id = 5) AS cancelled_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 6) AS ignored_count,
                COUNT(*) FILTER (WHERE o.order_status_id IN (9, 10)) AS return_exchange_count,
                BOOL_OR(o.deferred_until IS NOT NULL) AS has_deferred
            FROM orders o
            WHERE o.client_id = c.id
        ) stats ON true
        {where_sql}
        ORDER BY {sort_col} {direction}, c.id {direction}
        LIMIT :limit_val OFFSET :offset_val
    """

    rows = db.execute(text(main_sql), params).mappings().all()
    client_list = [dict(row) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    logger.info(f"Returning {len(client_list)} clients (total={total})")

    return {
        "items": client_list,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }

@router.get("/api/clients/{client_id}", tags=["clients"])
async def get_client(client_id: int, db: Session = Depends(get_db)):
    """
    Get client by ID with full statistics for the client card.
    Includes order breakdown by status, purchased models count, and recent orders.
    """
    # Основні дані клієнта + повна статистика замовлень
    sql = text("""
        SELECT
            c.*,
            COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '') AS full_name,
            COALESCE(stats.total_orders, 0) AS total_orders,
            COALESCE(stats.confirmed_orders, 0) AS confirmed_orders,
            COALESCE(stats.cancelled_count, 0) AS cancelled_count,
            COALESCE(stats.ignored_count, 0) AS ignored_count,
            COALESCE(stats.return_exchange_count, 0) AS return_exchange_count,
            COALESCE(stats.queue_count, 0) AS queue_count,
            COALESCE(stats.gift_count, 0) AS gift_count,
            COALESCE(stats.clarify_count, 0) AS clarify_count,
            COALESCE(stats.has_deferred, false) AS has_deferred,
            COALESCE(stats.total_amount, 0) AS computed_total_amount,
            COALESCE(stats.avg_amount, 0) AS computed_avg_amount,
            COALESCE(stats.max_amount, 0) AS computed_max_amount,
            COALESCE(stats.first_order, c.first_order_date) AS computed_first_order,
            COALESCE(stats.last_order, c.last_order_date) AS computed_last_order,
            COALESCE(models.purchased_models, 0) AS purchased_models,
            -- Rating formula: base 5.0 + order bonus - penalties + amount bonus, clamped 0-10
            GREATEST(0, LEAST(10,
                5.0
                + LEAST(COALESCE(stats.confirmed_orders, 0) * 0.5, 3.0)
                - LEAST(COALESCE(stats.cancelled_count, 0) * 1.0, 3.0)
                - LEAST(COALESCE(stats.ignored_count, 0) * 0.5, 2.0)
                - LEAST(COALESCE(stats.return_exchange_count, 0) * 0.3, 1.0)
                + LEAST(COALESCE(stats.total_amount, 0) / 10000.0, 2.0)
            )) AS rating
        FROM clients c
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS total_orders,
                COUNT(*) FILTER (WHERE o.order_status_id NOT IN (5, 6, 9, 10)) AS confirmed_orders,
                COUNT(*) FILTER (WHERE o.order_status_id = 5) AS cancelled_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 6) AS ignored_count,
                COUNT(*) FILTER (WHERE o.order_status_id IN (9, 10)) AS return_exchange_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 8) AS queue_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 7) AS gift_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 3) AS clarify_count,
                BOOL_OR(o.deferred_until IS NOT NULL) AS has_deferred,
                SUM(o.total_amount) FILTER (WHERE o.order_status_id NOT IN (5, 6)) AS total_amount,
                AVG(o.total_amount) FILTER (WHERE o.order_status_id NOT IN (5, 6)) AS avg_amount,
                MAX(o.total_amount) FILTER (WHERE o.order_status_id NOT IN (5, 6)) AS max_amount,
                MIN(o.order_date) AS first_order,
                MAX(o.order_date) AS last_order
            FROM orders o
            WHERE o.client_id = c.id
        ) stats ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT oi.product_id) AS purchased_models
            FROM orders o2
            JOIN order_items oi ON oi.order_id = o2.id
            WHERE o2.client_id = c.id
        ) models ON true
        WHERE c.id = :client_id
    """)
    row = db.execute(sql, {"client_id": client_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")

    result = dict(row)

    # Останні замовлення клієнта (до 20)
    orders_sql = text("""
        SELECT
            o.id,
            o.order_date,
            o.total_amount,
            o.tracking_number,
            o.notes,
            o.sales_channel,
            os.status_name AS order_status,
            ps.status_name AS payment_status,
            dm.method_name AS delivery_method,
            COALESCE(items.product_numbers, '') AS product_numbers,
            COALESCE(items.item_count, 0) AS item_count
        FROM orders o
        LEFT JOIN order_statuses os ON os.id = o.order_status_id
        LEFT JOIN payment_statuses ps ON ps.id = o.payment_status_id
        LEFT JOIN delivery_methods dm ON dm.id = o.delivery_method_id
        LEFT JOIN LATERAL (
            SELECT
                STRING_AGG(p.productnumber, ', ' ORDER BY oi.id) AS product_numbers,
                COUNT(*) AS item_count
            FROM order_items oi
            LEFT JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = o.id
        ) items ON true
        WHERE o.client_id = :client_id
        ORDER BY o.order_date DESC, o.id DESC
        LIMIT 20
    """)
    orders_rows = db.execute(orders_sql, {"client_id": client_id}).mappings().all()
    result["recent_orders"] = [dict(r) for r in orders_rows]

    # ── Уподобання: top-N агрегати з історії замовлень ────────────────────
    # Виключаємо відмінені/ігнор замовлення (статуси 5, 6) — вони не показують
    # реальні преференції клієнта. Підраховуємо за кількістю позицій (items),
    # а не унікальних товарів, щоб повтор-замовлення давали більшу вагу.
    prefs_sql = text("""
        WITH client_items AS (
            SELECT oi.id AS item_id, p.id AS product_id, p.brandid, p.typeid,
                   p.colorid, p.sizeeu, p.subtypeid
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN products p ON p.id = oi.product_id
            WHERE o.client_id = :client_id
              AND COALESCE(o.order_status_id, 0) NOT IN (5, 6)
        )
        SELECT
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT b.brandname AS name, COUNT(*) AS cnt
                FROM client_items ci JOIN brands b ON b.id = ci.brandid
                WHERE b.brandname IS NOT NULL
                GROUP BY b.brandname ORDER BY cnt DESC LIMIT 8
            ) t) AS top_brands,
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT tp.typename AS name, COUNT(*) AS cnt
                FROM client_items ci JOIN types tp ON tp.id = ci.typeid
                WHERE tp.typename IS NOT NULL
                GROUP BY tp.typename ORDER BY cnt DESC LIMIT 8
            ) t) AS top_types,
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT cl.colorname AS name, COUNT(*) AS cnt
                FROM client_items ci JOIN colors cl ON cl.id = ci.colorid
                WHERE cl.colorname IS NOT NULL
                GROUP BY cl.colorname ORDER BY cnt DESC LIMIT 8
            ) t) AS top_colors,
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT sizeeu AS name, COUNT(*) AS cnt
                FROM client_items
                WHERE sizeeu IS NOT NULL AND sizeeu <> ''
                GROUP BY sizeeu ORDER BY cnt DESC LIMIT 8
            ) t) AS top_sizes_eu
    """)
    prefs = db.execute(prefs_sql, {"client_id": client_id}).mappings().first() or {}
    result["top_brands"]    = prefs.get("top_brands") or []
    result["top_types"]     = prefs.get("top_types") or []
    result["top_colors"]    = prefs.get("top_colors") or []
    result["top_sizes_eu"]  = prefs.get("top_sizes_eu") or []

    # Розподіл оплат за всю історію
    pay_sql = text("""
        SELECT
            COUNT(*) FILTER (WHERE LOWER(COALESCE(ps.status_name,'')) LIKE 'оплачено%') AS paid,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(ps.status_name,'')) LIKE 'не оплачено%'
                              OR ps.status_name IS NULL) AS unpaid,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(ps.status_name,'')) LIKE 'частково%') AS partial,
            COUNT(*) AS total
        FROM orders o LEFT JOIN payment_statuses ps ON ps.id = o.payment_status_id
        WHERE o.client_id = :client_id
          AND COALESCE(o.order_status_id, 0) NOT IN (5, 6)
    """)
    pay = db.execute(pay_sql, {"client_id": client_id}).mappings().first() or {}
    result["payment_split"] = {
        "paid": pay.get("paid", 0) or 0,
        "unpaid": pay.get("unpaid", 0) or 0,
        "partial": pay.get("partial", 0) or 0,
        "total": pay.get("total", 0) or 0,
    }

    # ── Адресна книга ────────────────────────────────────────────────────
    addrs = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id
    ).order_by(
        ClientAddress.is_primary.desc(),
        ClientAddress.is_active.desc(),
        ClientAddress.usage_count.desc(),
        ClientAddress.id.desc(),
    ).all()
    result["addresses"] = [_addr_to_dict(a) for a in addrs]

    return result


# ── Адреси клієнта ────────────────────────────────────────────────────────
def _addr_to_dict(a: ClientAddress) -> dict:
    return {
        "id": a.id, "client_id": a.client_id,
        "label": a.label, "delivery_type": a.delivery_type,
        "recipient_name": a.recipient_name, "recipient_phone": a.recipient_phone,
        "city": a.city, "city_ref": a.city_ref, "region": a.region,
        "warehouse_number": a.warehouse_number, "warehouse_ref": a.warehouse_ref,
        "street": a.street, "building": a.building,
        "apartment": a.apartment, "postal_code": a.postal_code,
        "is_primary": bool(a.is_primary), "is_active": bool(a.is_active),
        "source": a.source, "source_order_id": a.source_order_id,
        "fingerprint": a.fingerprint, "usage_count": a.usage_count or 0,
        "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
        "notes": a.notes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _addr_fingerprint(payload: dict) -> str:
    """Стабільний md5 для дедупу адрес: тип+місто+відділення+вулиця+будинок+квартира."""
    parts = [
        (payload.get("delivery_type") or "").strip().lower(),
        (payload.get("city") or "").strip().lower(),
        (payload.get("warehouse_number") or "").strip(),
        (payload.get("street") or "").strip().lower(),
        (payload.get("building") or "").strip().lower(),
        (payload.get("apartment") or "").strip().lower(),
        (payload.get("postal_code") or "").strip(),
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def _ensure_single_primary(db: Session, client_id: int, except_id: Optional[int] = None):
    """Знімає is_primary з усіх інших адрес клієнта (для гарантії one-and-only-one)."""
    q = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id,
        ClientAddress.is_primary == True,  # noqa: E712
    )
    if except_id is not None:
        q = q.filter(ClientAddress.id != except_id)
    for other in q.all():
        other.is_primary = False


@router.get("/api/clients/{client_id}/addresses", tags=["clients"])
async def list_client_addresses(client_id: int, db: Session = Depends(get_db)):
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(404, "Client not found")
    addrs = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id
    ).order_by(
        ClientAddress.is_primary.desc(),
        ClientAddress.is_active.desc(),
        ClientAddress.usage_count.desc(),
        ClientAddress.id.desc(),
    ).all()
    return [_addr_to_dict(a) for a in addrs]


@router.post("/api/clients/{client_id}/addresses", tags=["clients"])
async def create_client_address(client_id: int, payload: ClientAddressCreate, db: Session = Depends(get_db)):
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(404, "Client not found")
    data = payload.dict()
    fp = _addr_fingerprint(data)
    # Дедуп: якщо вже є з тим самим fp у цього клієнта — повертаємо існуючу
    existing = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id,
        ClientAddress.fingerprint == fp,
    ).first()
    if existing:
        # Просто оновлюємо «м'які» поля
        for k in ("label", "recipient_name", "recipient_phone", "notes"):
            if data.get(k):
                setattr(existing, k, data[k])
        if data.get("is_primary"):
            _ensure_single_primary(db, client_id, except_id=existing.id)
            existing.is_primary = True
        existing.is_active = bool(data.get("is_active", True))
        db.commit()
        db.refresh(existing)
        return _addr_to_dict(existing)

    if data.get("is_primary"):
        _ensure_single_primary(db, client_id)
    addr = ClientAddress(
        client_id=client_id,
        source="manual",
        fingerprint=fp,
        **data,
    )
    db.add(addr)
    db.commit()
    db.refresh(addr)
    return _addr_to_dict(addr)


@router.put("/api/clients/{client_id}/addresses/{address_id}", tags=["clients"])
async def update_client_address(client_id: int, address_id: int, payload: ClientAddressUpdate, db: Session = Depends(get_db)):
    addr = db.query(ClientAddress).filter(
        ClientAddress.id == address_id,
        ClientAddress.client_id == client_id,
    ).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    data = payload.dict(exclude_unset=True)
    # Якщо примарність вмикають — спершу знімаємо в інших
    if data.get("is_primary") is True:
        _ensure_single_primary(db, client_id, except_id=addr.id)
    for k, v in data.items():
        setattr(addr, k, v)
    # Перерахувати fingerprint, якщо геополя змінилися
    geo_keys = {"delivery_type", "city", "warehouse_number", "street", "building", "apartment", "postal_code"}
    if geo_keys & set(data.keys()):
        addr.fingerprint = _addr_fingerprint({
            "delivery_type": addr.delivery_type, "city": addr.city,
            "warehouse_number": addr.warehouse_number, "street": addr.street,
            "building": addr.building, "apartment": addr.apartment,
            "postal_code": addr.postal_code,
        })
    db.commit()
    db.refresh(addr)
    return _addr_to_dict(addr)


@router.delete("/api/clients/{client_id}/addresses/{address_id}", tags=["clients"])
async def delete_client_address(client_id: int, address_id: int, db: Session = Depends(get_db)):
    addr = db.query(ClientAddress).filter(
        ClientAddress.id == address_id,
        ClientAddress.client_id == client_id,
    ).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    db.delete(addr)
    db.commit()
    return {"ok": True}


@router.post("/api/clients/{client_id}/addresses/{address_id}/set-primary", tags=["clients"])
async def set_primary_address(client_id: int, address_id: int, db: Session = Depends(get_db)):
    addr = db.query(ClientAddress).filter(
        ClientAddress.id == address_id,
        ClientAddress.client_id == client_id,
    ).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    _ensure_single_primary(db, client_id, except_id=addr.id)
    addr.is_primary = True
    addr.is_active = True  # якщо був архівний — повертаємо
    db.commit()
    db.refresh(addr)
    return _addr_to_dict(addr)


@router.post("/api/clients/{client_id}/addresses/import-from-orders", tags=["clients"])
async def import_addresses_from_orders(client_id: int, db: Session = Depends(get_db)):
    """Підтягує всі адреси клієнта з історії його замовлень.
    Дедуплікує по fingerprint. Існуючі адреси не чіпає (тільки збільшує usage_count).
    """
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(404, "Client not found")

    rows = db.execute(text("""
        SELECT a.id AS addr_id, a.address_line1, a.address_line2, a.city, a.state,
               a.postal_code, a.recipient_name,
               COUNT(DISTINCT o.id) AS use_count,
               MAX(o.order_date) AS last_used,
               MAX(o.id) AS last_order_id,
               MAX(o.tracking_number) AS tracking_hint
        FROM orders o
        JOIN addresses a ON a.id = o.address_id
        WHERE o.client_id = :cid
        GROUP BY a.id, a.address_line1, a.address_line2, a.city, a.state,
                 a.postal_code, a.recipient_name
    """), {"cid": client_id}).mappings().all()

    imported = 0
    updated = 0
    skipped = 0

    import re as _re
    for r in rows:
        line1 = (r.get("address_line1") or "").strip()
        line2 = (r.get("address_line2") or "").strip()
        city = (r.get("city") or "").strip()

        # Евристика: якщо в рядку є "Відділення №42" або "Поштомат №..." → НП відділення
        wh_match = _re.search(r"(?:відділ[\w]*|поштомат)\s*[№#]?\s*(\d+)", (line1 + " " + line2), _re.IGNORECASE)
        if wh_match:
            delivery_type = "np_warehouse"
            warehouse_number = wh_match.group(1)
            street = None
            building = None
        elif line1:
            # Спроба вийняти "вул. X, буд. Y, кв. Z"
            delivery_type = "np_courier"
            warehouse_number = None
            street = line1
            building = None
            bm = _re.search(r"(?:буд[\w]*|будинок)\s*[№#]?\s*([\dА-Яа-я/-]+)", line1, _re.IGNORECASE)
            if bm:
                building = bm.group(1)
        else:
            delivery_type = "other"
            warehouse_number = None
            street = None
            building = None

        payload = {
            "delivery_type": delivery_type,
            "city": city or None,
            "warehouse_number": warehouse_number,
            "street": street,
            "building": building,
            "apartment": None,
            "postal_code": (r.get("postal_code") or None),
        }
        fp = _addr_fingerprint(payload)

        existing = db.query(ClientAddress).filter(
            ClientAddress.client_id == client_id,
            ClientAddress.fingerprint == fp,
        ).first()

        last_used = r.get("last_used")
        if isinstance(last_used, str):
            try:
                last_used = datetime.fromisoformat(last_used)
            except Exception:
                last_used = None

        if existing:
            # Оновлюємо лічильник + last_used якщо новіше
            existing.usage_count = max(existing.usage_count or 0, int(r.get("use_count") or 0))
            if last_used and (not existing.last_used_at or last_used > existing.last_used_at):
                existing.last_used_at = last_used
            updated += 1
        else:
            addr = ClientAddress(
                client_id=client_id,
                label=None,
                delivery_type=delivery_type,
                recipient_name=r.get("recipient_name"),
                city=city or None,
                warehouse_number=warehouse_number,
                street=street,
                building=building,
                postal_code=r.get("postal_code"),
                is_primary=False,
                is_active=True,
                source="imported_from_order",
                source_order_id=r.get("last_order_id"),
                fingerprint=fp,
                usage_count=int(r.get("use_count") or 0),
                last_used_at=last_used,
            )
            db.add(addr)
            imported += 1

    # Якщо primary порожній і є саме одна найчастіше використовувана адреса — НЕ ставимо
    # автоматично, просимо користувача підтвердити (smart-suggest).
    db.commit()
    return {"imported": imported, "updated": updated, "skipped": skipped}

@router.post("/api/clients", response_model=ClientSchema, tags=["clients"])
async def create_client(client: ClientCreate, db: Session = Depends(get_db)):
    """
    Create a new client
    """
    # Validate gender if provided
    if client.gender_id:
        gender = db.query(Gender).filter(Gender.id == client.gender_id).first()
        if not gender:
            raise HTTPException(status_code=404, detail="Gender not found")
    
    # Create new client
    db_client = Client(**client.dict())
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    
    # Add full_name field
    client_dict = db_client.__dict__.copy()
    client_dict["full_name"] = f"{db_client.first_name} {db_client.last_name}"
    
    return client_dict

@router.put("/api/clients/{client_id}", response_model=ClientSchema, tags=["clients"])
async def update_client(client_id: int, client: ClientUpdate, db: Session = Depends(get_db)):
    """
    Update an existing client
    """
    db_client = db.query(Client).filter(Client.id == client_id).first()
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate gender if provided
    if client.gender_id:
        gender = db.query(Gender).filter(Gender.id == client.gender_id).first()
        if not gender:
            raise HTTPException(status_code=404, detail="Gender not found")
    
    # Update client fields
    update_data = client.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_client, key, value)
    
    db.commit()
    db.refresh(db_client)
    
    # Add full_name field
    client_dict = db_client.__dict__.copy()
    client_dict["full_name"] = f"{db_client.first_name} {db_client.last_name}"
    
    return client_dict

@router.delete("/api/clients/{client_id}", tags=["clients"])
async def delete_client(client_id: int, db: Session = Depends(get_db)):
    """
    Delete a client
    """
    db_client = db.query(Client).filter(Client.id == client_id).first()
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    db.delete(db_client)
    db.commit()
    return {"message": "Client deleted successfully"} 
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from sqlalchemy.orm import Session
from datetime import date, datetime
import logging

from backend.models.database import get_db
from backend.models.models import Order, OrderItem, Client, Product
from backend.services.order_service import OrderDAO
from backend.schemas.order import (
    OrderCreate, OrderUpdate, OrderResponse, OrderWithDetails, 
    OrderList, OrderFilters, FilterOptions, OrderListItem
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/api/orders", response_model=OrderList)
def get_orders(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    sort_by: str = Query("order_date", description="id|order_date|total_amount|priority"),
    sort_dir: str = Query("desc", description="asc|desc"),
    order_status_ids: Optional[List[int]] = Query(None),
    payment_status_ids: Optional[List[int]] = Query(None),
    payment_method_ids: Optional[List[int]] = Query(None),
    delivery_method_ids: Optional[List[int]] = Query(None),
    delivery_status_ids: Optional[List[int]] = Query(None),
    client_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    month_min: Optional[int] = Query(None),
    month_max: Optional[int] = Query(None),
    year_min: Optional[int] = Query(None),
    year_max: Optional[int] = Query(None),
    priority_min: Optional[int] = Query(None),
    priority_max: Optional[int] = Query(None),
    has_tracking: Optional[bool] = Query(None),
    is_deferred: Optional[bool] = Query(None),
    amount_min: Optional[float] = Query(None),
    amount_max: Optional[float] = Query(None),
    sales_channels: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db)
):
    """Get paginated orders list using raw SQL — no lazy loading."""
    from sqlalchemy import text as sa_text

    # ── Build WHERE clauses ──────────────────────────────────────────────
    where = []
    params: Dict[str, Any] = {}

    if search:
        where.append("""(
            c.first_name ILIKE :search OR c.last_name ILIKE :search
            OR c.phone_number ILIKE :search OR c.email ILIKE :search
            OR o.tracking_number ILIKE :search OR o.notes ILIKE :search
        )""")
        params["search"] = f"%{search}%"

    if client_id:
        where.append("o.client_id = :client_id")
        params["client_id"] = client_id

    if order_status_ids:
        where.append("o.order_status_id = ANY(:order_status_ids)")
        params["order_status_ids"] = order_status_ids

    if payment_status_ids:
        where.append("o.payment_status_id = ANY(:payment_status_ids)")
        params["payment_status_ids"] = payment_status_ids

    if payment_method_ids:
        where.append("o.payment_method_id = ANY(:payment_method_ids)")
        params["payment_method_ids"] = payment_method_ids

    if delivery_method_ids:
        where.append("o.delivery_method_id = ANY(:delivery_method_ids)")
        params["delivery_method_ids"] = delivery_method_ids

    if delivery_status_ids:
        where.append("o.delivery_status_id = ANY(:delivery_status_ids)")
        params["delivery_status_ids"] = delivery_status_ids

    if date_from:
        where.append("o.order_date >= :date_from")
        params["date_from"] = date_from

    if date_to:
        where.append("o.order_date <= :date_to")
        params["date_to"] = date_to

    if month_min is not None:
        where.append("EXTRACT(MONTH FROM o.order_date) >= :month_min")
        params["month_min"] = month_min

    if month_max is not None:
        where.append("EXTRACT(MONTH FROM o.order_date) <= :month_max")
        params["month_max"] = month_max

    if year_min is not None:
        where.append("EXTRACT(YEAR FROM o.order_date) >= :year_min")
        params["year_min"] = year_min

    if year_max is not None:
        where.append("EXTRACT(YEAR FROM o.order_date) <= :year_max")
        params["year_max"] = year_max

    if priority_min is not None:
        where.append("o.priority >= :priority_min")
        params["priority_min"] = priority_min

    if priority_max is not None:
        where.append("o.priority <= :priority_max")
        params["priority_max"] = priority_max

    if amount_min is not None:
        where.append("o.total_amount >= :amount_min")
        params["amount_min"] = amount_min

    if amount_max is not None:
        where.append("o.total_amount <= :amount_max")
        params["amount_max"] = amount_max

    if has_tracking is True:
        where.append("o.tracking_number IS NOT NULL AND o.tracking_number != ''")
    elif has_tracking is False:
        where.append("(o.tracking_number IS NULL OR o.tracking_number = '')")

    if is_deferred:
        where.append("o.deferred_until IS NOT NULL")

    if sales_channels:
        where.append("o.sales_channel = ANY(:sales_channels)")
        params["sales_channels"] = sales_channels

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # ── Sort ─────────────────────────────────────────────────────────────
    allowed_sort = {"id": "o.id", "order_date": "o.order_date",
                    "total_amount": "o.total_amount", "priority": "o.priority",
                    "client_name": "client_name"}
    sort_col = allowed_sort.get(sort_by, "o.order_date")
    sort_dir_sql = "ASC" if sort_dir.lower() == "asc" else "DESC"

    # ── Count ────────────────────────────────────────────────────────────
    count_sql = f"""
        SELECT COUNT(*) FROM orders o
        LEFT JOIN clients c ON o.client_id = c.id
        {where_sql}
    """
    total = db.execute(sa_text(count_sql), params).scalar() or 0
    pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    # ── Main query — single JOIN for all needed data ─────────────────────
    main_sql = f"""
        SELECT
            o.id, o.client_id, o.order_date, o.order_status_id, o.total_amount,
            o.payment_method_id, o.payment_status, o.payment_status_id,
            o.delivery_method_id, o.delivery_address_id, o.tracking_number,
            o.delivery_status_id, o.notes, o.deferred_until, o.priority,
            o.broadcast_id, o.sales_channel, o.created_at, o.updated_at,
            TRIM(COALESCE(c.first_name,'') || ' ' || COALESCE(c.last_name,'')) AS client_name,
            os2.status_name  AS order_status_name,
            COALESCE(o.payment_status, ps.status_name) AS payment_status_name,
            pm.method_name   AS payment_method_name,
            dm.method_name   AS delivery_method_name,
            ds.status_name   AS delivery_status_name
        FROM orders o
        LEFT JOIN clients c          ON o.client_id          = c.id
        LEFT JOIN order_statuses os2 ON o.order_status_id    = os2.id
        LEFT JOIN payment_statuses ps ON o.payment_status_id = ps.id
        LEFT JOIN payment_methods pm ON o.payment_method_id  = pm.id
        LEFT JOIN delivery_methods dm ON o.delivery_method_id = dm.id
        LEFT JOIN delivery_statuses ds ON o.delivery_status_id = ds.id
        {where_sql}
        ORDER BY {sort_col} {sort_dir_sql} NULLS LAST, o.id {sort_dir_sql}
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = per_page
    params["offset"] = offset
    rows = db.execute(sa_text(main_sql), params).mappings().all()

    order_ids = [r["id"] for r in rows]

    # ── Fetch order items for this page in one query ─────────────────────
    items_by_order: Dict[int, list] = {oid: [] for oid in order_ids}
    if order_ids:
        items_sql = """
            SELECT oi.id, oi.order_id, oi.product_id, oi.quantity,
                   CASE WHEN COALESCE(oi.price, 0) > 0 THEN oi.price
                        ELSE COALESCE(p.price, 0) END AS price,
                   oi.discount_type, oi.discount_value,
                   oi.additional_operation, oi.additional_operation_value,
                   oi.notes, oi.created_at, oi.updated_at,
                   p.productnumber, p.model, p.marking
            FROM order_items oi
            LEFT JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ANY(:oids)
            ORDER BY oi.id
        """
        item_rows = db.execute(sa_text(items_sql), {"oids": order_ids}).mappings().all()
        for ir in item_rows:
            pnum = ir["productnumber"] or ""
            pnum_display = pnum.lstrip("#") if pnum else (ir["notes"] or "—")
            pname = " ".join(filter(None, [ir["model"], ir["marking"]])) or "—"
            items_by_order[ir["order_id"]].append({
                "id": ir["id"],
                "order_id": ir["order_id"],
                "product_id": ir["product_id"],
                "product_number": pnum_display,
                "product_name": pname,
                "quantity": ir["quantity"],
                "price": float(ir["price"] or 0),
                "discount_type": ir["discount_type"],
                "discount_value": ir["discount_value"],
                "additional_operation": ir["additional_operation"],
                "additional_operation_value": ir["additional_operation_value"],
                "notes": ir["notes"],
                "created_at": ir["created_at"],
                "updated_at": ir["updated_at"],
            })

    # ── Build response ────────────────────────────────────────────────────
    items = []
    for r in rows:
        cn = (r["client_name"] or "").strip() or None
        items.append({
            "id": r["id"],
            "client_id": r["client_id"],
            "client_name": cn,
            "order_date": r["order_date"],
            "order_status_id": r["order_status_id"],
            "order_status_name": r["order_status_name"],
            "payment_status_id": r["payment_status_id"],
            "payment_status": r["payment_status"],
            "payment_status_name": r["payment_status_name"],
            "payment_method_id": r["payment_method_id"],
            "payment_method_name": r["payment_method_name"],
            "delivery_method_id": r["delivery_method_id"],
            "delivery_method_name": r["delivery_method_name"],
            "delivery_status_id": r["delivery_status_id"],
            "delivery_status_name": r["delivery_status_name"],
            "delivery_address_id": r["delivery_address_id"],
            "delivery_address_details": None,
            "tracking_number": r["tracking_number"],
            "total_amount": float(r["total_amount"] or 0) or sum(
                it["price"] * it["quantity"] for it in items_by_order.get(r["id"], [])
            ),
            "notes": r["notes"],
            "deferred_until": r["deferred_until"],
            "priority": r["priority"] or 0,
            "broadcast_id": r["broadcast_id"],
            "broadcast_name": None,
            "sales_channel": r["sales_channel"] or "Ефір",
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "order_items": items_by_order.get(r["id"], []),
        })

    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": pages}

@router.post("/api/orders/bulk-update")
async def bulk_update_orders(
    update_data: Dict[str, Any] = Body(...),
    order_ids: List[int] = Query(None),
    db: Session = Depends(get_db)
):
    if not order_ids:
        raise HTTPException(status_code=400, detail="Потрібно вказати принаймні один ID замовлення")
    if not update_data:
        raise HTTPException(status_code=400, detail="Потрібно вказати дані для оновлення")
    allowed_fields = {
        "order_status_id",
        "payment_status_id",
        "payment_method_id",
        "delivery_method_id",
        "delivery_status_id",
        "tracking_number",
        "notes",
        "priority",
    }
    filtered = {k: v for k, v in update_data.items() if k in allowed_fields}
    if not filtered:
        raise HTTPException(status_code=400, detail="Немає валідних полів для оновлення")
    try:
        updated = db.query(Order).filter(Order.id.in_(order_ids)).update(filtered, synchronize_session=False)
        db.commit()
        return {"updated_count": updated}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/orders/filters", response_model=FilterOptions)
async def get_order_filters(db: Session = Depends(get_db)):
    """
    Get all filter options for orders
    """
    order_dao = OrderDAO(db)
    return order_dao.get_filter_options()

@router.get("/api/orders/{order_id}", response_model=OrderWithDetails)
async def get_order(order_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """
    Get a specific order by ID with all details
    """
    order_dao = OrderDAO(db)
    order = order_dao.get_order_by_id(order_id)
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get client name
    client_name = f"{order.client.first_name or ''} {order.client.last_name or ''}".strip() if order.client else None
    
    # Get order status name
    order_status_name = order.order_status.status_name if order.order_status else None
    
    payment_status_name = order.payment_status
    payment_method_name = None
    if order.payment_method_id and order.payment_method:
        payment_method_name = getattr(order.payment_method, 'name', None) or getattr(order.payment_method, 'method_name', None)
    delivery_method_name = None
    if order.delivery_method_id and order.delivery_method:
        delivery_method_name = getattr(order.delivery_method, 'name', None) or getattr(order.delivery_method, 'method_name', None)
    delivery_status_name = None
    if order.delivery_status_id and order.delivery_status:
        delivery_status_name = getattr(order.delivery_status, 'name', None) or getattr(order.delivery_status, 'status_name', None)
    
    # Get broadcast name
    broadcast_name = order.broadcast.name if order.broadcast else None
    
    # Prepare order items
    order_items = []
    for item in order.items:
        product_number = item.product.productnumber if item.product else (item.notes or "—")
        product_name = (f"{item.product.model or ''} {item.product.marking or ''}".strip() if item.product else "") or "—"
        
        order_items.append({
            "id": item.id,
            "order_id": item.order_id,
            "product_id": item.product_id,
            "product_number": product_number,
            "product_name": product_name,
            "quantity": item.quantity,
            "price": item.price,
            "discount_type": item.discount_type,
            "discount_value": item.discount_value,
            "additional_operation": item.additional_operation,
            "additional_operation_value": item.additional_operation_value,
            "notes": item.notes,
            "created_at": item.created_at,
            "updated_at": item.updated_at
        })
    
    # Create delivery address details
    address_details = None
    if order.delivery_address:
        address = order.delivery_address
        address_details = {
            "id": address.id,
            "city": address.city,
            "street": address.street,
            "building": address.building,
            "apartment": address.apartment,
            "postal_code": address.postal_code,
            "notes": address.notes
        }
    
    # Return transformed order
    return {
        "id": order.id,
        "client_id": order.client_id,
        "client_name": client_name,
        "order_date": order.order_date,
        "order_status_id": order.order_status_id,
        "order_status_name": order_status_name,
        "payment_status_id": order.payment_status_id,
        "payment_status_name": payment_status_name,
        "payment_status": order.payment_status,
        "payment_method_id": order.payment_method_id,
        "payment_method_name": payment_method_name,
        "delivery_method_id": order.delivery_method_id,
        "delivery_method_name": delivery_method_name,
        "delivery_status_id": order.delivery_status_id,
        "delivery_status_name": delivery_status_name,
        "delivery_address_id": order.delivery_address_id,
        "delivery_address_details": address_details,
        "tracking_number": order.tracking_number,
        "total_amount": order.total_amount,
        "notes": order.notes,
        "deferred_until": order.deferred_until,
        "priority": order.priority,
        "broadcast_id": order.broadcast_id,
        "broadcast_name": broadcast_name,
        "sales_channel": getattr(order, 'sales_channel', None) or 'Ефір',
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "order_items": order_items
    }

@router.post("/api/orders", response_model=OrderWithDetails)
async def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    """
    Create a new order with order items
    """
    # Validate client exists (if specified)
    if order.client_id is not None:
        client = db.query(Client).filter(Client.id == order.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate products exist
    for item in order.order_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product with ID {item.product_id} not found")
    
    # Create order with DAO
    order_dao = OrderDAO(db)
    new_order = order_dao.create_order(order.dict())
    
    # Recalculate order total
    order_dao.recalculate_order_total(new_order.id)
    
    # Get complete order with details
    return await get_order(new_order.id, db)

@router.put("/api/orders/{order_id}", response_model=OrderWithDetails)
async def update_order(
    order: OrderUpdate, 
    order_id: int = Path(..., ge=1), 
    db: Session = Depends(get_db)
):
    """
    Update an existing order
    """
    # Validate order exists
    order_dao = OrderDAO(db)
    existing_order = order_dao.get_order_by_id(order_id)
    if not existing_order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Validate client exists if changing
    if order.client_id is not None:
        client = db.query(Client).filter(Client.id == order.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate products exist
    if order.order_items:
        for item in order.order_items:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Product with ID {item.product_id} not found")
    
    # Update order
    updated_order = order_dao.update_order(order_id, order.dict(exclude_unset=True))
    
    # Recalculate order total
    order_dao.recalculate_order_total(order_id)
    
    # Get complete order with details
    return await get_order(order_id, db)

@router.delete("/api/orders/{order_id}")
async def delete_order(order_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """
    Delete an order by ID
    """
    order_dao = OrderDAO(db)
    success = order_dao.delete_order(order_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return {"message": "Order successfully deleted", "id": order_id} 

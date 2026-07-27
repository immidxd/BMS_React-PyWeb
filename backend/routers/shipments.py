from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

try:
    from backend.models.database import get_db
except ImportError:
    from models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# Real DB: deliveries(id, deliveryname, description, created_at, deliverydate, supplier_id)
# No shipments or shipment_groups tables — map /api/shipments to deliveries

@router.get("/api/shipments")
def get_shipments(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    sort_by: str = Query("shipment_date"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    conditions = []
    params: Dict[str, Any] = {}

    if search:
        conditions.append("(d.deliveryname ILIKE :search OR s.company_name ILIKE :search)")
        params["search"] = f"%{search}%"
    if supplier_id:
        conditions.append("d.supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        {where}
    """), params).scalar() or 0

    allowed = {
        "id": "d.id",
        "shipment_date": "d.deliverydate",
        "supplier_name": "s.company_name",
        "items_count": "items_count",
        "total_cost": "total_cost",
        "created_at": "d.created_at",
    }
    order_col = allowed.get(sort_by, "d.deliverydate")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    rows = db.execute(text(f"""
        SELECT d.id,
               d.deliveryname AS sheet_name,
               d.deliverydate AS shipment_date,
               d.supplier_id,
               s.company_name AS supplier_name,
               COALESCE(ps.items_count, 0) AS items_count,
               COALESCE(ps.total_cost, 0)::float AS total_cost,
               0::float AS delivery_cost,
               d.description AS notes,
               NULL::int AS group_id,
               NULL::text AS group_name,
               d.created_at,
               d.created_at AS updated_at
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        LEFT JOIN LATERAL (
            -- Ростовка зберігається ОДНИМ рядком на розмір із quantity>1 (унікальний
            -- індекс (номер,розмір,колір) не дає завести однакові рядки). Тому
            -- «речей у завозі» — це SUM(quantity), а не COUNT(*): 5 розмірів
            -- Ф4083 = 10 фізичних пар. Сума — так само з урахуванням кількості.
            SELECT COALESCE(SUM(GREATEST(COALESCE(p.quantity, 1), 1)), 0) AS items_count,
                   COALESCE(SUM(p.price * GREATEST(COALESCE(p.quantity, 1), 1)), 0) AS total_cost
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        {where}
        ORDER BY {order_col} {order_dir} NULLS LAST, d.id DESC
        OFFSET :offset LIMIT :limit
    """), {**params, "offset": (page - 1) * per_page, "limit": per_page}).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": int(total),
        "page": page,
        "per_page": per_page,
        "pages": max(1, (int(total) + per_page - 1) // per_page),
    }


@router.get("/api/shipments/{shipment_id}")
def get_shipment(shipment_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT d.id, d.deliveryname AS sheet_name, d.deliverydate AS shipment_date,
               d.supplier_id, s.company_name AS supplier_name,
               COALESCE(ps.items_count, 0) AS items_count,
               COALESCE(ps.total_cost, 0)::float AS total_cost,
               0::float AS delivery_cost,
               d.description AS notes,
               NULL::int AS group_id, NULL::text AS group_name,
               d.created_at, d.created_at AS updated_at
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS items_count, COALESCE(SUM(p.price), 0) AS total_cost
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        WHERE d.id = :id
    """), {"id": shipment_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Shipment not found")
    result = dict(row)

    brands = db.execute(text("""
        SELECT b.brandname, COUNT(*) as cnt
        FROM products p JOIN brands b ON b.id = p.brandid
        WHERE p.deliveryid = :id
        GROUP BY b.brandname ORDER BY cnt DESC LIMIT 5
    """), {"id": shipment_id}).mappings().all()
    result["top_brands"] = [dict(b) for b in brands]
    return result


@router.put("/api/shipments/{shipment_id}")
def update_shipment(
    shipment_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    exists = db.execute(text("SELECT 1 FROM deliveries WHERE id = :id"), {"id": shipment_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Shipment not found")
    allowed = {"description", "deliverydate", "supplier_id", "deliveryname"}
    fields = {k: v for k, v in payload.items() if k in allowed}
    if "notes" in payload and "description" not in fields:
        fields["description"] = payload["notes"]
    if "sheet_name" in payload and "deliveryname" not in fields:
        fields["deliveryname"] = payload["sheet_name"]
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    db.execute(text(f"UPDATE deliveries SET {set_clause} WHERE id = :id"), {**fields, "id": shipment_id})
    db.commit()
    return get_shipment(shipment_id, db)


# Shipment groups — not supported (no shipment_groups table), return empty
@router.get("/api/shipment-groups")
def get_shipment_groups(db: Session = Depends(get_db)):
    return []

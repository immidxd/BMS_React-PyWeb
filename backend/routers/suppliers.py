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


# Real DB: suppliers(id, company_name, contact_person, synonyms_json,
#   country_location_id, country_dispatch_id, city_location, address_location,
#   address_dispatch, supply_volume, payment_requisites, description, status, priority)
# Products linked via: products.deliveryid -> deliveries.id -> deliveries.supplier_id
# No supplier_aliases table; no shipments table (use deliveries)

@router.get("/api/suppliers")
async def get_suppliers(
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort_by: str = Query("name"),
    sort_dir: str = Query("asc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    where = ""
    params: Dict[str, Any] = {}
    if search:
        where = "WHERE s.company_name ILIKE :search"
        params["search"] = f"%{search}%"

    total = db.execute(text(f"SELECT COUNT(*) FROM suppliers s {where}"), params).scalar() or 0

    allowed = {
        "id": "s.id", "name": "s.company_name",
        "product_count": "product_count",
        "shipments_count": "deliveries_count",
        "total_spent": "total_spent",
        "avg_price": "avg_price",
        "revenue": "revenue",
    }
    order_col = allowed.get(sort_by, "s.company_name")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    rows = db.execute(text(f"""
        SELECT s.id,
               s.company_name AS name,
               s.description AS notes,
               s.contact_person, s.status, s.priority,
               COALESCE(ds.deliveries_count, 0) AS shipments_count,
               COALESCE(ds.product_count, 0) AS product_count,
               COALESCE(ds.total_spent, 0)::float AS total_spent,
               CASE WHEN COALESCE(ds.product_count, 0) > 0
                    THEN ROUND((ds.total_spent / ds.product_count)::numeric, 2)::float
                    ELSE 0 END AS avg_price,
               ds.top_brands,
               COALESCE(rev.revenue, 0)::float AS revenue
        FROM suppliers s
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT d.id) AS deliveries_count,
                   COUNT(DISTINCT p.id) AS product_count,
                   COALESCE(SUM(p.price), 0) AS total_spent,
                   string_agg(DISTINCT b.brandname, ', ' ORDER BY b.brandname) AS top_brands
            FROM deliveries d
            LEFT JOIN products p ON p.deliveryid = d.id
            LEFT JOIN brands b ON b.id = p.brandid
            WHERE d.supplier_id = s.id
        ) ds ON true
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            JOIN deliveries d ON d.id = p.deliveryid
            WHERE d.supplier_id = s.id
        ) rev ON true
        {where}
        ORDER BY {order_col} {order_dir}, s.id
        OFFSET :offset LIMIT :limit
    """), {**params, "offset": (page - 1) * per_page, "limit": per_page}).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": int(total),
        "page": page,
        "per_page": per_page,
        "pages": max(1, (int(total) + per_page - 1) // per_page),
    }


@router.get("/api/suppliers/{supplier_id}")
async def get_supplier(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT s.id, s.company_name AS name, s.description AS notes,
               s.contact_person, s.status, s.priority,
               COALESCE(ds.deliveries_count, 0) AS shipments_count,
               COALESCE(ds.product_count, 0) AS product_count,
               COALESCE(ds.total_spent, 0)::float AS total_spent,
               CASE WHEN COALESCE(ds.product_count, 0) > 0
                    THEN ROUND((ds.total_spent / ds.product_count)::numeric, 2)::float
                    ELSE 0 END AS avg_price
        FROM suppliers s
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT d.id) AS deliveries_count,
                   COUNT(DISTINCT p.id) AS product_count,
                   COALESCE(SUM(p.price), 0) AS total_spent
            FROM deliveries d
            LEFT JOIN products p ON p.deliveryid = d.id
            WHERE d.supplier_id = s.id
        ) ds ON true
        WHERE s.id = :id
    """), {"id": supplier_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier not found")
    result = dict(row)

    brands = db.execute(text("""
        SELECT b.brandname, COUNT(*) as cnt
        FROM deliveries d JOIN products p ON p.deliveryid = d.id
        JOIN brands b ON b.id = p.brandid
        WHERE d.supplier_id = :id
        GROUP BY b.brandname ORDER BY cnt DESC LIMIT 5
    """), {"id": supplier_id}).mappings().all()
    result["top_brands"] = [dict(b) for b in brands]

    rev = db.execute(text("""
        SELECT COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue
        FROM order_items oi JOIN products p ON p.id = oi.product_id
        JOIN deliveries d ON d.id = p.deliveryid
        WHERE d.supplier_id = :id
    """), {"id": supplier_id}).scalar() or 0
    result["revenue"] = rev
    return result


@router.put("/api/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    exists = db.execute(text("SELECT 1 FROM suppliers WHERE id = :id"), {"id": supplier_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Supplier not found")
    allowed = {"company_name", "description", "contact_person", "status", "priority"}
    fields = {k: v for k, v in payload.items() if k in allowed and v is not None}
    if "name" in payload and "company_name" not in fields:
        fields["company_name"] = payload["name"]
    if "notes" in payload and "description" not in fields:
        fields["description"] = payload["notes"]
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    db.execute(text(f"UPDATE suppliers SET {set_clause} WHERE id = :id"), {**fields, "id": supplier_id})
    db.commit()
    return await get_supplier(supplier_id, db)


@router.delete("/api/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    cnt = db.execute(text("""
        SELECT COUNT(DISTINCT p.id) FROM deliveries d
        JOIN products p ON p.deliveryid = d.id WHERE d.supplier_id = :id
    """), {"id": supplier_id}).scalar()
    if cnt and cnt > 0:
        raise HTTPException(status_code=400, detail=f"Постачальник має {cnt} товарів. Спочатку перепризначте їх.")
    db.execute(text("UPDATE deliveries SET supplier_id = NULL WHERE supplier_id = :id"), {"id": supplier_id})
    db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": supplier_id})
    db.commit()
    return {"ok": True}


@router.post("/api/suppliers/merge")
async def merge_suppliers(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    target_id = payload.get("target_id")
    source_ids = payload.get("source_ids", [])
    new_name = (payload.get("new_name") or "").strip()
    if not target_id or not source_ids:
        raise HTTPException(status_code=400, detail="target_id and source_ids required")
    target = db.execute(text("SELECT id, company_name FROM suppliers WHERE id = :id"), {"id": target_id}).mappings().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target supplier not found")
    moved_deliveries = 0
    for sid in source_ids:
        if sid == target_id:
            continue
        if not db.execute(text("SELECT 1 FROM suppliers WHERE id = :id"), {"id": sid}).scalar():
            continue
        cnt = db.execute(text("UPDATE deliveries SET supplier_id = :target WHERE supplier_id = :source"),
                         {"target": target_id, "source": sid}).rowcount
        moved_deliveries += cnt
        db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": sid})
    if new_name:
        db.execute(text("UPDATE suppliers SET company_name = :name WHERE id = :id"), {"name": new_name, "id": target_id})
    db.commit()
    deleted = len([s for s in source_ids if s != target_id])
    return {"ok": True, "target_id": target_id, "moved_deliveries": moved_deliveries, "deleted_suppliers": deleted}

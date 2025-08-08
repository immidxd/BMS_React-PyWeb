from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, Path, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.models.database import get_db

router = APIRouter()

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



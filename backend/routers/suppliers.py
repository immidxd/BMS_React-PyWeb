from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from sqlalchemy import text

from models.database import get_db

router = APIRouter()

@router.get("/api/suppliers")
async def get_suppliers(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    sort_by: str = Query("priority", description="id|company_name|priority"),
    sort_dir: str = Query("desc", description="asc|desc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    where = ""
    params: Dict[str, Any] = {}
    if search:
        where = "WHERE company_name ILIKE :search"
        params["search"] = f"%{search}%"

    total_sql = text(f"SELECT COUNT(*) FROM suppliers {where}")
    total = db.execute(total_sql, params).scalar() or 0

    allowed_columns = {"id": "id", "company_name": "company_name", "priority": "priority"}
    order_col = allowed_columns.get(sort_by, "priority")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
    list_sql = text(
        f"""
        SELECT id, company_name, contact_person, city_location, status, priority,
               country_location_id, country_dispatch_id
        FROM suppliers
        {where}
        ORDER BY {order_col} {order_dir}, id DESC
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

@router.get("/api/suppliers/{supplier_id}")
async def get_supplier(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    row = db.execute(
        text(
            "SELECT id, company_name, contact_person, city_location, status, priority, country_location_id, country_dispatch_id "
            "FROM suppliers WHERE id = :id"
        ),
        {"id": supplier_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return dict(row)

@router.put("/api/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = None,
    db: Session = Depends(get_db)
):
    # Check exists
    exists = db.execute(text("SELECT 1 FROM suppliers WHERE id = :id"), {"id": supplier_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Supplier not found")
    if not payload:
        raise HTTPException(status_code=400, detail="No data provided")
    # Whitelist columns
    allowed = {
        "company_name", "contact_person", "city_location", "status", "priority",
        "country_location_id", "country_dispatch_id",
    }
    fields = {k: v for k, v in (payload or {}).items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    set_clause = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    params = {**fields, "id": supplier_id}
    db.execute(text(f"UPDATE suppliers SET {set_clause} WHERE id = :id"), params)
    db.commit()
    # Return updated
    row = db.execute(
        text(
            "SELECT id, company_name, contact_person, city_location, status, priority, country_location_id, country_dispatch_id "
            "FROM suppliers WHERE id = :id"
        ),
        {"id": supplier_id},
    ).mappings().first()
    return dict(row)


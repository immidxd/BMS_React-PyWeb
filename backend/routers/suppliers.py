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


@router.get("/api/suppliers")
async def get_suppliers(
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort_by: str = Query("name", description="id|name|product_count"),
    sort_dir: str = Query("asc", description="asc|desc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    where = ""
    params: Dict[str, Any] = {}
    if search:
        where = "WHERE s.name ILIKE :search"
        params["search"] = f"%{search}%"

    total = db.execute(
        text(f"SELECT COUNT(*) FROM suppliers s {where}"), params
    ).scalar() or 0

    allowed = {"id": "s.id", "name": "s.name", "product_count": "product_count"}
    order_col = allowed.get(sort_by, "s.name")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    rows = db.execute(text(f"""
        SELECT s.id, s.name, s.notes,
               COUNT(p.id) AS product_count,
               s.created_at, s.updated_at
        FROM suppliers s
        LEFT JOIN products p ON p.supplierid = s.id
        {where}
        GROUP BY s.id
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
        SELECT s.id, s.name, s.notes, COUNT(p.id) AS product_count
        FROM suppliers s LEFT JOIN products p ON p.supplierid = s.id
        WHERE s.id = :id GROUP BY s.id
    """), {"id": supplier_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return dict(row)


@router.put("/api/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    exists = db.execute(text("SELECT 1 FROM suppliers WHERE id = :id"), {"id": supplier_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Supplier not found")
    allowed = {"name", "notes"}
    fields = {k: v for k, v in payload.items() if k in allowed and v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    db.execute(text(f"UPDATE suppliers SET {set_clause}, updated_at = NOW() WHERE id = :id"), {**fields, "id": supplier_id})
    db.commit()
    return await get_supplier(supplier_id, db)


@router.delete("/api/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    cnt = db.execute(text("SELECT COUNT(*) FROM products WHERE supplierid = :id"), {"id": supplier_id}).scalar()
    if cnt and cnt > 0:
        raise HTTPException(status_code=400, detail=f"Постачальник має {cnt} товарів. Спочатку перепризначте їх.")
    db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": supplier_id})
    db.commit()
    return {"ok": True}


@router.post("/api/suppliers/merge")
async def merge_suppliers(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """Злити кількох постачальників в одного.
    payload: { target_id: int, source_ids: [int, ...] }
    Усі товари source_ids переприв'язуються до target_id, source видаляються.
    """
    target_id = payload.get("target_id")
    source_ids = payload.get("source_ids", [])
    if not target_id or not source_ids:
        raise HTTPException(status_code=400, detail="target_id and source_ids required")
    # Verify target exists
    target = db.execute(text("SELECT id, name FROM suppliers WHERE id = :id"), {"id": target_id}).mappings().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target supplier not found")
    # Reassign products and delete sources
    moved = 0
    for sid in source_ids:
        if sid == target_id:
            continue
        cnt = db.execute(
            text("UPDATE products SET supplierid = :target WHERE supplierid = :source"),
            {"target": target_id, "source": sid},
        ).rowcount
        moved += cnt
        db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": sid})
    db.commit()
    logger.info(f"Merged suppliers {source_ids} → {target_id}, moved {moved} products")
    return {"ok": True, "target_id": target_id, "moved_products": moved, "deleted_suppliers": len([s for s in source_ids if s != target_id])}


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


# ── Shipments list ───────────────────────────────────────────────────────────
@router.get("/api/shipments")
async def get_shipments(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    group_id: Optional[int] = Query(None),
    sort_by: str = Query("shipment_date"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    conditions = []
    params: Dict[str, Any] = {}

    if search:
        conditions.append("(sh.sheet_name ILIKE :search OR s.name ILIKE :search)")
        params["search"] = f"%{search}%"
    if supplier_id:
        conditions.append("sh.supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id
    if group_id:
        conditions.append("sh.group_id = :group_id")
        params["group_id"] = group_id

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM shipments sh
        LEFT JOIN suppliers s ON s.id = sh.supplier_id
        {where}
    """), params).scalar() or 0

    allowed = {
        "id": "sh.id",
        "shipment_date": "sh.shipment_date",
        "supplier_name": "s.name",
        "items_count": "sh.items_count",
        "total_cost": "sh.total_cost",
        "created_at": "sh.created_at",
    }
    order_col = allowed.get(sort_by, "sh.shipment_date")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    rows = db.execute(text(f"""
        SELECT sh.id, sh.sheet_name, sh.shipment_date,
               sh.supplier_id, s.name AS supplier_name,
               sh.items_count, sh.total_cost::float,
               sh.delivery_cost::float,
               sh.notes, sh.group_id,
               g.name AS group_name,
               sh.created_at, sh.updated_at
        FROM shipments sh
        LEFT JOIN suppliers s ON s.id = sh.supplier_id
        LEFT JOIN shipment_groups g ON g.id = sh.group_id
        {where}
        ORDER BY {order_col} {order_dir} NULLS LAST, sh.id DESC
        OFFSET :offset LIMIT :limit
    """), {**params, "offset": (page - 1) * per_page, "limit": per_page}).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": int(total),
        "page": page,
        "per_page": per_page,
        "pages": max(1, (int(total) + per_page - 1) // per_page),
    }


# ── Shipment detail ──────────────────────────────────────────────────────────
@router.get("/api/shipments/{shipment_id}")
async def get_shipment(shipment_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT sh.id, sh.sheet_name, sh.shipment_date,
               sh.supplier_id, s.name AS supplier_name,
               sh.items_count, sh.total_cost::float,
               sh.delivery_cost::float,
               sh.notes, sh.group_id,
               g.name AS group_name,
               sh.created_at, sh.updated_at
        FROM shipments sh
        LEFT JOIN suppliers s ON s.id = sh.supplier_id
        LEFT JOIN shipment_groups g ON g.id = sh.group_id
        WHERE sh.id = :id
    """), {"id": shipment_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Shipment not found")
    result = dict(row)

    # Top brands in this shipment
    brands = db.execute(text("""
        SELECT b.brandname, COUNT(*) as cnt
        FROM products p JOIN brands b ON b.id = p.brandid
        WHERE p.shipment_id = :id
        GROUP BY b.brandname ORDER BY cnt DESC LIMIT 5
    """), {"id": shipment_id}).mappings().all()
    result["top_brands"] = [dict(b) for b in brands]

    return result


# ── Update shipment ──────────────────────────────────────────────────────────
@router.put("/api/shipments/{shipment_id}")
async def update_shipment(
    shipment_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    exists = db.execute(text("SELECT 1 FROM shipments WHERE id = :id"), {"id": shipment_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Shipment not found")
    allowed = {"notes", "delivery_cost", "group_id"}
    fields = {k: v for k, v in payload.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    db.execute(text(f"UPDATE shipments SET {set_clause}, updated_at = NOW() WHERE id = :id"),
               {**fields, "id": shipment_id})
    db.commit()
    return await get_shipment(shipment_id, db)


# ── Shipment groups ──────────────────────────────────────────────────────────
@router.get("/api/shipment-groups")
async def get_shipment_groups(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT g.id, g.name, g.notes,
               COUNT(sh.id) AS shipments_count,
               COALESCE(SUM(sh.total_cost), 0)::float AS total_cost,
               COALESCE(SUM(sh.items_count), 0) AS total_items,
               g.created_at, g.updated_at
        FROM shipment_groups g
        LEFT JOIN shipments sh ON sh.group_id = g.id
        GROUP BY g.id
        ORDER BY g.created_at DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/api/shipment-groups")
async def create_shipment_group(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    notes = payload.get("notes")
    shipment_ids = payload.get("shipment_ids", [])

    row = db.execute(text(
        "INSERT INTO shipment_groups (name, notes) VALUES (:name, :notes) RETURNING id"
    ), {"name": name, "notes": notes}).fetchone()
    db.flush()
    group_id = row[0]

    if shipment_ids:
        db.execute(text(
            "UPDATE shipments SET group_id = :gid WHERE id = ANY(:ids)"
        ), {"gid": group_id, "ids": shipment_ids})

    db.commit()
    return {"ok": True, "id": group_id, "name": name}


@router.put("/api/shipment-groups/{group_id}")
async def update_shipment_group(
    group_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    exists = db.execute(text("SELECT 1 FROM shipment_groups WHERE id = :id"), {"id": group_id}).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Group not found")
    allowed = {"name", "notes"}
    fields = {k: v for k, v in payload.items() if k in allowed and v is not None}
    if fields:
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        db.execute(text(f"UPDATE shipment_groups SET {set_clause}, updated_at = NOW() WHERE id = :id"),
                   {**fields, "id": group_id})
    db.commit()
    return {"ok": True}


@router.delete("/api/shipment-groups/{group_id}")
async def delete_shipment_group(group_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    # Unlink shipments first (don't delete them)
    db.execute(text("UPDATE shipments SET group_id = NULL WHERE group_id = :id"), {"id": group_id})
    db.execute(text("DELETE FROM shipment_groups WHERE id = :id"), {"id": group_id})
    db.commit()
    return {"ok": True}


# ── Group/ungroup shipments ──────────────────────────────────────────────────
@router.post("/api/shipments/group")
async def group_shipments(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """Add shipments to a group. Creates group if group_id not provided."""
    group_id = payload.get("group_id")
    shipment_ids = payload.get("shipment_ids", [])
    group_name = (payload.get("group_name") or "").strip()

    if not shipment_ids:
        raise HTTPException(status_code=400, detail="shipment_ids required")

    if not group_id:
        if not group_name:
            group_name = "Група поставок"
        row = db.execute(text(
            "INSERT INTO shipment_groups (name) VALUES (:name) RETURNING id"
        ), {"name": group_name}).fetchone()
        db.flush()
        group_id = row[0]

    db.execute(text(
        "UPDATE shipments SET group_id = :gid WHERE id = ANY(:ids)"
    ), {"gid": group_id, "ids": shipment_ids})
    db.commit()
    return {"ok": True, "group_id": group_id}


@router.post("/api/shipments/ungroup")
async def ungroup_shipments(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """Remove shipments from their group."""
    shipment_ids = payload.get("shipment_ids", [])
    if not shipment_ids:
        raise HTTPException(status_code=400, detail="shipment_ids required")
    db.execute(text(
        "UPDATE shipments SET group_id = NULL WHERE id = ANY(:ids)"
    ), {"ids": shipment_ids})
    db.commit()
    return {"ok": True}

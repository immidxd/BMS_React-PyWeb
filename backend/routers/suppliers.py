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


# ── Suppliers list with rich stats ───────────────────────────────────────────
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
        where = "WHERE (s.name ILIKE :search OR sa.alias_name ILIKE :search)"
        params["search"] = f"%{search}%"

    total = db.execute(text(f"""
        SELECT COUNT(DISTINCT s.id) FROM suppliers s
        LEFT JOIN supplier_aliases sa ON sa.supplier_id = s.id
        {where}
    """), params).scalar() or 0

    allowed = {
        "id": "s.id", "name": "s.name",
        "product_count": "product_count",
        "shipments_count": "shipments_count",
        "total_spent": "total_spent",
        "avg_price": "avg_price",
    }
    order_col = allowed.get(sort_by, "s.name")
    order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    rows = db.execute(text(f"""
        SELECT s.id, s.name, s.notes,
               COUNT(DISTINCT p.id)  AS product_count,
               COUNT(DISTINCT sh.id) AS shipments_count,
               COALESCE(SUM(p.price), 0)::float AS total_spent,
               CASE WHEN COUNT(p.id) > 0
                    THEN ROUND((SUM(p.price) / COUNT(p.id))::numeric, 2)::float
                    ELSE 0 END AS avg_price,
               (SELECT string_agg(DISTINCT b.brandname, ', ' ORDER BY b.brandname)
                FROM products p2
                JOIN brands b ON b.id = p2.brandid
                WHERE p2.supplierid = s.id
                LIMIT 1) AS top_brands,
               s.created_at, s.updated_at
        FROM suppliers s
        LEFT JOIN supplier_aliases sa ON sa.supplier_id = s.id
        LEFT JOIN products p ON p.supplierid = s.id
        LEFT JOIN shipments sh ON sh.supplier_id = s.id
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


# ── Supplier detail with full stats ─────────────────────────────────────────
@router.get("/api/suppliers/{supplier_id}")
async def get_supplier(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT s.id, s.name, s.notes,
               COUNT(DISTINCT p.id) AS product_count,
               COUNT(DISTINCT sh.id) AS shipments_count,
               COALESCE(SUM(p.price), 0)::float AS total_spent,
               CASE WHEN COUNT(p.id) > 0
                    THEN ROUND((SUM(p.price) / COUNT(p.id))::numeric, 2)::float
                    ELSE 0 END AS avg_price,
               s.created_at, s.updated_at
        FROM suppliers s
        LEFT JOIN products p ON p.supplierid = s.id
        LEFT JOIN shipments sh ON sh.supplier_id = s.id
        WHERE s.id = :id
        GROUP BY s.id
    """), {"id": supplier_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier not found")
    result = dict(row)

    # Aliases
    aliases = db.execute(text(
        "SELECT id, alias_name FROM supplier_aliases WHERE supplier_id = :id ORDER BY alias_name"
    ), {"id": supplier_id}).mappings().all()
    result["aliases"] = [dict(a) for a in aliases]

    # Top brands
    brands = db.execute(text("""
        SELECT b.brandname, COUNT(*) as cnt
        FROM products p JOIN brands b ON b.id = p.brandid
        WHERE p.supplierid = :id
        GROUP BY b.brandname ORDER BY cnt DESC LIMIT 5
    """), {"id": supplier_id}).mappings().all()
    result["top_brands"] = [dict(b) for b in brands]

    # Revenue from sold products (via order_items)
    rev = db.execute(text("""
        SELECT COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        WHERE p.supplierid = :id
    """), {"id": supplier_id}).scalar() or 0
    result["revenue"] = rev

    return result


# ── Supplier aliases ─────────────────────────────────────────────────────────
@router.get("/api/suppliers/{supplier_id}/aliases")
async def get_aliases(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, alias_name, created_at FROM supplier_aliases WHERE supplier_id = :id ORDER BY alias_name"
    ), {"id": supplier_id}).mappings().all()
    return [dict(r) for r in rows]


@router.post("/api/suppliers/{supplier_id}/aliases")
async def add_alias(
    supplier_id: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    alias_name = (payload.get("alias_name") or "").strip()
    if not alias_name:
        raise HTTPException(status_code=400, detail="alias_name required")
    # Check uniqueness
    existing = db.execute(text(
        "SELECT supplier_id FROM supplier_aliases WHERE alias_name = :n"
    ), {"n": alias_name}).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail=f"Alias '{alias_name}' вже належить постачальнику ID {existing[0]}")
    db.execute(text(
        "INSERT INTO supplier_aliases (alias_name, supplier_id) VALUES (:n, :sid)"
    ), {"n": alias_name, "sid": supplier_id})
    db.commit()
    return {"ok": True, "alias_name": alias_name}


@router.delete("/api/suppliers/aliases/{alias_id}")
async def delete_alias(alias_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM supplier_aliases WHERE id = :id"), {"id": alias_id})
    db.commit()
    return {"ok": True}


# ── Update supplier ──────────────────────────────────────────────────────────
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


# ── Delete supplier ──────────────────────────────────────────────────────────
@router.delete("/api/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    cnt = db.execute(text("SELECT COUNT(*) FROM products WHERE supplierid = :id"), {"id": supplier_id}).scalar()
    if cnt and cnt > 0:
        raise HTTPException(status_code=400, detail=f"Постачальник має {cnt} товарів. Спочатку перепризначте їх.")
    db.execute(text("DELETE FROM supplier_aliases WHERE supplier_id = :id"), {"id": supplier_id})
    db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": supplier_id})
    db.commit()
    return {"ok": True}


# ── Merge suppliers (with aliases + shipments) ───────────────────────────────
@router.post("/api/suppliers/merge")
async def merge_suppliers(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """Merge multiple suppliers into one.
    payload: { target_id: int, source_ids: [int, ...], new_name: str (optional) }
    Moves all aliases, shipments, and products from sources to target.
    Source suppliers are deleted. Their names become aliases of target.
    """
    target_id = payload.get("target_id")
    source_ids = payload.get("source_ids", [])
    new_name = (payload.get("new_name") or "").strip()
    if not target_id or not source_ids:
        raise HTTPException(status_code=400, detail="target_id and source_ids required")

    target = db.execute(text("SELECT id, name FROM suppliers WHERE id = :id"), {"id": target_id}).mappings().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target supplier not found")

    moved_products = moved_shipments = 0
    for sid in source_ids:
        if sid == target_id:
            continue
        source = db.execute(text("SELECT name FROM suppliers WHERE id = :id"), {"id": sid}).fetchone()
        if not source:
            continue

        # Move products
        cnt = db.execute(
            text("UPDATE products SET supplierid = :target WHERE supplierid = :source"),
            {"target": target_id, "source": sid},
        ).rowcount
        moved_products += cnt

        # Move shipments
        cnt2 = db.execute(
            text("UPDATE shipments SET supplier_id = :target WHERE supplier_id = :source"),
            {"target": target_id, "source": sid},
        ).rowcount
        moved_shipments += cnt2

        # Move aliases to target
        db.execute(
            text("UPDATE supplier_aliases SET supplier_id = :target WHERE supplier_id = :source"),
            {"target": target_id, "source": sid},
        )

        # Source name becomes alias of target (if not already)
        db.execute(text(
            "INSERT INTO supplier_aliases (alias_name, supplier_id) VALUES (:n, :sid) ON CONFLICT DO NOTHING"
        ), {"n": source[0], "sid": target_id})

        # Delete source supplier
        db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": sid})

    # Optionally rename target
    if new_name:
        db.execute(text("UPDATE suppliers SET name = :name, updated_at = NOW() WHERE id = :id"),
                   {"name": new_name, "id": target_id})

    db.commit()
    deleted = len([s for s in source_ids if s != target_id])
    logger.info(f"Merged suppliers {source_ids} → {target_id}, products={moved_products}, shipments={moved_shipments}")
    return {
        "ok": True,
        "target_id": target_id,
        "moved_products": moved_products,
        "moved_shipments": moved_shipments,
        "deleted_suppliers": deleted,
    }


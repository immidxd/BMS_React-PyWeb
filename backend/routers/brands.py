"""
Brands management router — list, merge, block, concerns.
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from models.database import get_db

try:
    from scripts.brand_utils import normalize_brand
    from services.brand_normalization import canonicalize_brand_name
except ImportError:
    from backend.scripts.brand_utils import normalize_brand
    from backend.services.brand_normalization import canonicalize_brand_name

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic schemas ────────────────────────────────────────────────

class BrandItem(BaseModel):
    id: int
    brandname: str
    normalized_name: Optional[str] = None
    concern_id: Optional[int] = None
    concern_name: Optional[str] = None
    total_products: int = 0
    available_pairs: int = 0


class BrandListResponse(BaseModel):
    items: List[BrandItem] = []
    total: int = 0
    page: int = 1
    per_page: int = 20
    pages: int = 1


class BrandUpdate(BaseModel):
    brandname: Optional[str] = None
    concern_id: Optional[int] = None


class BrandMergeRequest(BaseModel):
    target_id: int
    source_ids: List[int]
    new_name: Optional[str] = None


class BrandConcernItem(BaseModel):
    id: int
    name: str
    country: Optional[str] = None
    description: Optional[str] = None
    brand_count: int = 0


class BrandConcernCreate(BaseModel):
    name: str
    country: Optional[str] = None
    description: Optional[str] = None


class BrandConcernUpdate(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    description: Optional[str] = None


# ── GET /api/brands ─────────────────────────────────────────────────

@router.get("/api/brands", response_model=BrandListResponse, tags=["brands"])
def get_brands(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    concern_id: Optional[int] = None,
    has_products: Optional[bool] = None,
    sort_by: str = Query("brandname"),
    sort_dir: str = Query("asc"),
    db: Session = Depends(get_db),
):
    where_clauses: list[str] = []
    params: dict = {}

    if search:
        where_clauses.append("b.brandname ILIKE :search")
        params["search"] = f"%{search}%"

    if concern_id is not None:
        where_clauses.append("b.concern_id = :concern_id")
        params["concern_id"] = concern_id

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Stats subquery — reuses the same sold_count logic as product_service.py
    stats_lateral = """
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::int AS total_products,
                SUM(CASE WHEN GREATEST(COALESCE(p.quantity,0) - COALESCE(sold.sold_count,0), 0) > 0
                         THEN 1 ELSE 0 END)::int AS available_pairs
            FROM products p
            LEFT JOIN (
                SELECT oi.product_id, COUNT(*)::int AS sold_count
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE o.order_status_id IN (1, 7) AND oi.product_id IS NOT NULL
                GROUP BY oi.product_id
            ) sold ON sold.product_id = p.id
            WHERE p.brandid = b.id
        ) stats ON true
    """

    # Having filter for has_products (applied after LATERAL)
    having_sql = ""
    if has_products is True:
        having_sql = "AND COALESCE(stats.total_products, 0) > 0"
    elif has_products is False:
        having_sql = "AND COALESCE(stats.total_products, 0) = 0"

    # Count
    count_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT b.id
            FROM brands b
            LEFT JOIN brand_concerns bc ON bc.id = b.concern_id
            {stats_lateral}
            {where_sql}
            {"" if not having_sql else having_sql.replace("AND", "WHERE" if not where_sql else "AND", 1) if not where_sql else ""}
        ) sub
    """
    # Simplify: build full WHERE including has_products
    full_where_parts = list(where_clauses)
    if has_products is True:
        full_where_parts.append("COALESCE(stats.total_products, 0) > 0")
    elif has_products is False:
        full_where_parts.append("COALESCE(stats.total_products, 0) = 0")

    full_where_sql = ("WHERE " + " AND ".join(full_where_parts)) if full_where_parts else ""

    count_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT b.id
            FROM brands b
            LEFT JOIN brand_concerns bc ON bc.id = b.concern_id
            {stats_lateral}
            {full_where_sql}
        ) sub
    """
    total = db.execute(text(count_sql), params).scalar() or 0

    pages = max(1, (total + per_page - 1) // per_page)

    # Sort whitelist
    allowed_sorts = {
        "id": "b.id",
        "brandname": "b.brandname",
        "total_products": "COALESCE(stats.total_products, 0)",
        "available_pairs": "COALESCE(stats.available_pairs, 0)",
        "concern_name": "bc.name",
    }
    sort_col = allowed_sorts.get(sort_by, "b.brandname")
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    nulls = "NULLS LAST" if direction == "ASC" else "NULLS FIRST"

    params["limit_val"] = per_page
    params["offset_val"] = (page - 1) * per_page

    main_sql = f"""
        SELECT
            b.id, b.brandname, b.normalized_name, b.concern_id,
            bc.name AS concern_name,
            COALESCE(stats.total_products, 0)::int AS total_products,
            COALESCE(stats.available_pairs, 0)::int AS available_pairs
        FROM brands b
        LEFT JOIN brand_concerns bc ON bc.id = b.concern_id
        {stats_lateral}
        {full_where_sql}
        ORDER BY {sort_col} {direction} {nulls}, b.id
        LIMIT :limit_val OFFSET :offset_val
    """
    rows = db.execute(text(main_sql), params).mappings().all()

    items = [
        BrandItem(
            id=r["id"],
            brandname=r["brandname"],
            normalized_name=r.get("normalized_name"),
            concern_id=r.get("concern_id"),
            concern_name=r.get("concern_name"),
            total_products=r["total_products"],
            available_pairs=r["available_pairs"],
        )
        for r in rows
    ]

    return BrandListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)


# ── PUT /api/brands/{id} ───────────────────────────────────────────

@router.put("/api/brands/{brand_id}", tags=["brands"])
def update_brand(brand_id: int, body: BrandUpdate, db: Session = Depends(get_db)):
    existing = db.execute(text("SELECT id FROM brands WHERE id = :id"), {"id": brand_id}).fetchone()
    if not existing:
        raise HTTPException(404, "Brand not found")

    updates = []
    params: dict = {"id": brand_id}

    if body.brandname is not None:
        new_name = canonicalize_brand_name(body.brandname) or ""
        if not new_name:
            raise HTTPException(400, "Назва бренду порожня")
        normalized_name = normalize_brand(new_name)
        conflict = db.execute(
            text("""
                SELECT id, brandname FROM brands
                WHERE normalized_name = :nn AND id <> :id
                LIMIT 1
            """),
            {"nn": normalized_name, "id": brand_id},
        ).fetchone()
        if conflict:
            raise HTTPException(
                409,
                f"Такий бренд уже існує: {conflict.brandname}. Використайте об’єднання.",
            )
        # Save old name as alias so parser won't recreate old brand
        old_brand = db.execute(
            text("SELECT brandname FROM brands WHERE id = :id"), {"id": brand_id}
        ).fetchone()
        if old_brand and old_brand.brandname != new_name:
            db.execute(
                text("""
                    INSERT INTO brand_aliases (alias_name, brand_id)
                    VALUES (:name, :bid)
                    ON CONFLICT (alias_name) DO UPDATE SET brand_id = EXCLUDED.brand_id
                """),
                {"name": old_brand.brandname, "bid": brand_id},
            )
        updates.append("brandname = :brandname")
        params["brandname"] = new_name
        updates.append("normalized_name = :normalized_name")
        params["normalized_name"] = normalized_name
    if body.concern_id is not None:
        if body.concern_id == 0:
            updates.append("concern_id = NULL")
        else:
            updates.append("concern_id = :concern_id")
            params["concern_id"] = body.concern_id

    if not updates:
        raise HTTPException(400, "Nothing to update")

    sql = f"UPDATE brands SET {', '.join(updates)} WHERE id = :id"
    db.execute(text(sql), params)
    db.commit()
    return {"ok": True}


# ── DELETE /api/brands/{id} ─────────────────────────────────────────

@router.delete("/api/brands/{brand_id}", tags=["brands"])
def delete_brand(brand_id: int, db: Session = Depends(get_db)):
    count = db.execute(
        text("SELECT COUNT(*) FROM products WHERE brandid = :id"), {"id": brand_id}
    ).scalar() or 0
    if count > 0:
        raise HTTPException(400, f"Бренд має {count} товарів. Спочатку перемістіть товари.")

    # Save brand name to blocklist so parser won't recreate it
    brand = db.execute(
        text("SELECT brandname, normalized_name FROM brands WHERE id = :id"), {"id": brand_id}
    ).fetchone()
    if brand:
        normalized = brand.normalized_name or brand.brandname.strip().lower()
        db.execute(
            text("""INSERT INTO brand_blocklist (normalized_name, reason)
                    VALUES (:nn, :reason) ON CONFLICT DO NOTHING"""),
            {"nn": normalized, "reason": f"Видалено вручну (was: {brand.brandname})"},
        )

    db.execute(text("DELETE FROM brands WHERE id = :id"), {"id": brand_id})
    db.commit()
    return {"ok": True}


# ── POST /api/brands/merge ──────────────────────────────────────────

@router.post("/api/brands/merge", tags=["brands"])
def merge_brands(body: BrandMergeRequest, db: Session = Depends(get_db)):
    if body.target_id in body.source_ids:
        raise HTTPException(400, "target_id не повинен бути в source_ids")
    if not body.source_ids:
        raise HTTPException(400, "source_ids порожній")

    # Verify target exists
    target = db.execute(
        text("SELECT id, brandname, concern_id FROM brands WHERE id = :id"),
        {"id": body.target_id},
    ).fetchone()
    if not target:
        raise HTTPException(404, "Цільовий бренд не знайдено")

    source_ids = sorted(set(body.source_ids))
    source_brands = db.execute(
        text("SELECT id, brandname, concern_id FROM brands WHERE id = ANY(:ids)"),
        {"ids": source_ids},
    ).fetchall()
    if len(source_brands) != len(source_ids):
        found_ids = {row.id for row in source_brands}
        missing = [source_id for source_id in source_ids if source_id not in found_ids]
        raise HTTPException(404, f"Не знайдено бренди для об’єднання: {missing}")

    canonical_name = canonicalize_brand_name(body.new_name or target.brandname) or ""
    if not canonical_name:
        raise HTTPException(400, "Канонічна назва бренду порожня")
    canonical_normalized = normalize_brand(canonical_name)
    conflict = db.execute(
        text("""
            SELECT id, brandname FROM brands
            WHERE normalized_name = :nn
              AND id <> :target
              AND NOT (id = ANY(:sources))
            LIMIT 1
        """),
        {"nn": canonical_normalized, "target": body.target_id, "sources": source_ids},
    ).fetchone()
    if conflict:
        raise HTTPException(
            409,
            f"Такий бренд уже існує: {conflict.brandname}. Оберіть його цільовим.",
        )

    # Reassign products
    result = db.execute(
        text("UPDATE products SET brandid = :target WHERE brandid = ANY(:sources)"),
        {"target": body.target_id, "sources": source_ids},
    )
    moved = result.rowcount

    # Existing aliases of source brands must survive source deletion.
    db.execute(
        text("UPDATE brand_aliases SET brand_id = :target WHERE brand_id = ANY(:sources)"),
        {"target": body.target_id, "sources": source_ids},
    )

    # Save source brand names as aliases → parser will respect merges
    for sb in source_brands:
        db.execute(
            text("""
                INSERT INTO brand_aliases (alias_name, brand_id)
                VALUES (:name, :bid)
                ON CONFLICT (alias_name) DO UPDATE SET brand_id = EXCLUDED.brand_id
            """),
            {"name": sb.brandname, "bid": body.target_id},
        )

    # Якщо ціль ще без альянсу, а джерела належать одному альянсу — зберігаємо
    # його. Різні альянси ніколи не зливаємо мовчки.
    if target.concern_id is None:
        source_concerns = db.execute(
            text("""
                SELECT DISTINCT concern_id FROM brands
                WHERE id = ANY(:ids) AND concern_id IS NOT NULL
            """),
            {"ids": source_ids},
        ).fetchall()
        if len(source_concerns) == 1:
            db.execute(
                text("UPDATE brands SET concern_id = :cid WHERE id = :id"),
                {"cid": source_concerns[0].concern_id, "id": body.target_id},
            )

    # Save the previous target name before a canonical rename. The actual rename
    # happens after source deletion so a source row with the same normalized key
    # cannot trip the partial unique index mid-transaction.
    if target.brandname != canonical_name:
        db.execute(
            text("""
                INSERT INTO brand_aliases (alias_name, brand_id)
                VALUES (:name, :bid)
                ON CONFLICT (alias_name) DO UPDATE SET brand_id = EXCLUDED.brand_id
            """),
            {"name": target.brandname, "bid": body.target_id},
        )

    # Переносимо country override зі старих назв на канонічну. Наявне ручне
    # значення канонічної назви має пріоритет.
    country_rows = db.execute(
        text("""
            SELECT bc.country
            FROM brand_countries bc
            JOIN brands b ON lower(b.brandname) = lower(bc.brand)
            WHERE b.id = ANY(:ids)
            ORDER BY b.id
        """),
        {"ids": source_ids},
    ).fetchall()
    if country_rows:
        db.execute(
            text("""
                INSERT INTO brand_countries (brand, country, updated_at)
                VALUES (lower(:brand), :country, now())
                ON CONFLICT (brand) DO NOTHING
            """),
            {"brand": canonical_name, "country": country_rows[0].country},
        )

    # Delete source brands
    db.execute(
        text("DELETE FROM brands WHERE id = ANY(:sources)"),
        {"sources": source_ids},
    )
    db.execute(
        text("""
            UPDATE brands
            SET brandname = :name, normalized_name = :normalized
            WHERE id = :id
        """),
        {
            "name": canonical_name,
            "normalized": canonical_normalized,
            "id": body.target_id,
        },
    )
    db.commit()

    return {"ok": True, "moved_products": moved, "deleted_brands": len(source_ids)}


# ── POST /api/brands/{id}/block ─────────────────────────────────────

@router.post("/api/brands/{brand_id}/block", tags=["brands"])
def block_brand(brand_id: int, db: Session = Depends(get_db)):
    brand = db.execute(
        text("SELECT id, brandname, normalized_name FROM brands WHERE id = :id"),
        {"id": brand_id},
    ).fetchone()
    if not brand:
        raise HTTPException(404, "Brand not found")

    normalized = brand.normalized_name or brand.brandname.strip().lower()

    # Add to blocklist
    db.execute(
        text("""
            INSERT INTO brand_blocklist (normalized_name, reason)
            VALUES (:nn, :reason)
            ON CONFLICT DO NOTHING
        """),
        {"nn": normalized, "reason": f"Заблоковано вручну (was: {brand.brandname})"},
    )

    # Unlink products
    result = db.execute(text("UPDATE products SET brandid = NULL WHERE brandid = :id"), {"id": brand_id})

    # Delete brand
    db.execute(text("DELETE FROM brands WHERE id = :id"), {"id": brand_id})
    db.commit()

    return {"ok": True, "unlinked_products": result.rowcount}


# ── GET /api/brand-concerns ─────────────────────────────────────────

@router.get("/api/brand-concerns", tags=["brands"])
def get_brand_concerns(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT bc.id, bc.name, bc.country, bc.description,
               COUNT(b.id)::int AS brand_count
        FROM brand_concerns bc
        LEFT JOIN brands b ON b.concern_id = bc.id
        GROUP BY bc.id, bc.name, bc.country, bc.description
        ORDER BY bc.name
    """)).mappings().all()

    return [
        BrandConcernItem(
            id=r["id"], name=r["name"], country=r.get("country"),
            description=r.get("description"), brand_count=r["brand_count"],
        )
        for r in rows
    ]


# ── POST /api/brand-concerns ────────────────────────────────────────

@router.post("/api/brand-concerns", tags=["brands"])
def create_brand_concern(body: BrandConcernCreate, db: Session = Depends(get_db)):
    result = db.execute(
        text("""
            INSERT INTO brand_concerns (name, country, description)
            VALUES (:name, :country, :desc)
            RETURNING id
        """),
        {"name": body.name.strip(), "country": body.country, "desc": body.description},
    )
    new_id = result.scalar()
    db.commit()
    return {"ok": True, "id": new_id}


# ── PUT /api/brand-concerns/{id} ────────────────────────────────────

@router.put("/api/brand-concerns/{concern_id}", tags=["brands"])
def update_brand_concern(concern_id: int, body: BrandConcernUpdate, db: Session = Depends(get_db)):
    updates = []
    params: dict = {"id": concern_id}

    if body.name is not None:
        updates.append("name = :name")
        params["name"] = body.name.strip()
    if body.country is not None:
        updates.append("country = :country")
        params["country"] = body.country.strip() or None
    if body.description is not None:
        updates.append("description = :desc")
        params["desc"] = body.description.strip() or None

    if not updates:
        raise HTTPException(400, "Nothing to update")

    db.execute(text(f"UPDATE brand_concerns SET {', '.join(updates)} WHERE id = :id"), params)
    db.commit()
    return {"ok": True}


# ── DELETE /api/brand-concerns/{id} ─────────────────────────────────

@router.delete("/api/brand-concerns/{concern_id}", tags=["brands"])
def delete_brand_concern(concern_id: int, db: Session = Depends(get_db)):
    db.execute(text("UPDATE brands SET concern_id = NULL WHERE concern_id = :id"), {"id": concern_id})
    db.execute(text("DELETE FROM brand_concerns WHERE id = :id"), {"id": concern_id})
    db.commit()
    return {"ok": True}

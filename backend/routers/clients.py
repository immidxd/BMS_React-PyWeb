from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, text
import logging

from models.database import get_db
from models.models import Client, Gender
from schemas.reference import Client as ClientSchema, ClientCreate, ClientUpdate, ClientList

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

@router.get("/api/clients/{client_id}", response_model=ClientSchema, tags=["clients"])
async def get_client(client_id: int, db: Session = Depends(get_db)):
    """
    Get client by ID with order breakdown counts and rating.
    """
    sql = text("""
        SELECT
            c.*,
            COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '') AS full_name,
            COALESCE(stats.confirmed_orders, 0) AS confirmed_orders,
            COALESCE(stats.cancelled_count, 0) AS cancelled_count,
            COALESCE(stats.ignored_count, 0) AS ignored_count,
            COALESCE(stats.return_exchange_count, 0) AS return_exchange_count,
            COALESCE(stats.has_deferred, false) AS has_deferred,
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
        WHERE c.id = :client_id
    """)
    row = db.execute(sql, {"client_id": client_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")
    return dict(row)

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
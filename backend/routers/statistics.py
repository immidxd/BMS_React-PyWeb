from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

try:
    from backend.models.database import get_db
except ImportError:
    from models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Sales statistics ─────────────────────────────────────────────────────────
@router.get("/api/statistics/sales")
async def get_sales_stats(
    period: str = Query("month", regex="^(month|quarter|year)$"),
    year: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Sales/revenue by month/quarter/year.

    Returns: revenue (sum of order_items.price), orders count, items sold count.
    Only counts orders with status != 5 (Скасовано).
    """
    conditions = ["o.order_status_id != 5", "o.order_date IS NOT NULL"]
    params: Dict[str, Any] = {}

    if year:
        conditions.append("EXTRACT(YEAR FROM o.order_date) = :year")
        params["year"] = year
    if supplier_id:
        conditions.append("EXISTS (SELECT 1 FROM deliveries d WHERE d.id = p.deliveryid AND d.supplier_id = :supplier_id)")
        params["supplier_id"] = supplier_id

    where = " AND ".join(conditions)

    if period == "month":
        group_expr = "TO_CHAR(o.order_date, 'YYYY-MM')"
        label_expr = "TO_CHAR(o.order_date, 'YYYY-MM')"
    elif period == "quarter":
        group_expr = "TO_CHAR(o.order_date, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM o.order_date)::int"
        label_expr = group_expr
    else:  # year
        group_expr = "TO_CHAR(o.order_date, 'YYYY')"
        label_expr = "TO_CHAR(o.order_date, 'YYYY')"

    rows = db.execute(text(f"""
        SELECT {label_expr} AS period_label,
               COUNT(DISTINCT o.id) AS orders_count,
               COUNT(oi.id) AS items_sold,
               COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        LEFT JOIN products p ON p.id = oi.product_id
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    # Also compute cost basis (purchase price) for net profit
    cost_rows = db.execute(text(f"""
        SELECT {label_expr} AS period_label,
               COALESCE(SUM(p.price * oi.quantity), 0)::float AS cost
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        LEFT JOIN products p ON p.id = oi.product_id
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    cost_map = {r["period_label"]: r["cost"] for r in cost_rows}

    data = []
    for r in rows:
        label = r["period_label"]
        revenue = r["revenue"]
        cost = cost_map.get(label, 0)
        data.append({
            "period": label,
            "orders": r["orders_count"],
            "items_sold": r["items_sold"],
            "revenue": round(revenue, 2),
            "cost": round(cost, 2),
            "profit": round(revenue - cost, 2),
        })

    return {"period_type": period, "data": data}


# ── Deliveries (shipments) statistics ─────────────────────────────────────────
@router.get("/api/statistics/shipments")
async def get_shipments_stats(
    period: str = Query("month", regex="^(month|quarter|year)$"),
    year: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Delivery stats: total cost, avg price per item, items count, sell efficiency."""
    conditions = ["d.deliverydate IS NOT NULL"]
    params: Dict[str, Any] = {}

    if year:
        conditions.append("EXTRACT(YEAR FROM d.deliverydate) = :year")
        params["year"] = year
    if supplier_id:
        conditions.append("d.supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id

    where = " AND ".join(conditions)

    if period == "month":
        group_expr = "TO_CHAR(d.deliverydate, 'YYYY-MM')"
    elif period == "quarter":
        group_expr = "TO_CHAR(d.deliverydate, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM d.deliverydate)::int"
    else:
        group_expr = "TO_CHAR(d.deliverydate, 'YYYY')"

    rows = db.execute(text(f"""
        SELECT {group_expr} AS period_label,
               COUNT(DISTINCT d.id) AS shipments_count,
               COALESCE(SUM(ps.items_count), 0) AS total_items,
               COALESCE(SUM(ps.total_cost), 0)::float AS total_cost,
               CASE WHEN SUM(ps.items_count) > 0
                    THEN ROUND((SUM(ps.total_cost) / SUM(ps.items_count))::numeric, 2)::float
                    ELSE 0 END AS avg_item_price
        FROM deliveries d
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS items_count, COALESCE(SUM(p.price), 0) AS total_cost
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    # Revenue from products of these deliveries (sell efficiency)
    revenue_rows = db.execute(text(f"""
        SELECT {group_expr} AS period_label,
               COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue,
               COUNT(DISTINCT oi.product_id) AS sold_items
        FROM deliveries d
        JOIN products p ON p.deliveryid = d.id
        JOIN order_items oi ON oi.product_id = p.id
        JOIN orders o ON o.id = oi.order_id AND o.order_status_id != 5
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    rev_map = {r["period_label"]: r for r in revenue_rows}

    data = []
    for r in rows:
        label = r["period_label"]
        rev = rev_map.get(label, {})
        revenue = rev.get("revenue", 0)
        cost = r["total_cost"]
        data.append({
            "period": label,
            "shipments": r["shipments_count"],
            "items": r["total_items"],
            "total_cost": round(cost, 2),
            "avg_price": r["avg_item_price"],
            "revenue": round(revenue, 2),
            "profit": round(revenue - cost, 2),
            "sold_items": rev.get("sold_items", 0),
            "sell_rate": round(rev.get("sold_items", 0) / r["total_items"] * 100, 1) if r["total_items"] > 0 else 0,
        })

    return {"period_type": period, "data": data}


# ── Suppliers statistics ─────────────────────────────────────────────────────
@router.get("/api/statistics/suppliers")
async def get_suppliers_stats(
    period: str = Query("total", regex="^(month|quarter|year|total)$"),
    year: Optional[int] = Query(None),
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Top suppliers by total cost / avg price, optionally by period."""
    params: Dict[str, Any] = {"lim": limit}

    if period == "total":
        rows = db.execute(text(f"""
            SELECT s.id, s.company_name AS name,
                   COALESCE(ps.product_count, 0) AS product_count,
                   COALESCE(ps.total_cost, 0)::float AS total_cost,
                   COALESCE(ps.avg_price, 0)::float AS avg_price,
                   COALESCE(rev.revenue, 0)::float AS revenue,
                   COALESCE(rev.sold_items, 0) AS sold_items
            FROM suppliers s
            LEFT JOIN LATERAL (
                SELECT COUNT(DISTINCT p.id) AS product_count,
                       COALESCE(SUM(p.price), 0) AS total_cost,
                       CASE WHEN COUNT(DISTINCT p.id) > 0
                            THEN ROUND((SUM(p.price) / COUNT(DISTINCT p.id))::numeric, 2)
                            ELSE 0 END AS avg_price
                FROM deliveries d
                JOIN products p ON p.deliveryid = d.id
                WHERE d.supplier_id = s.id
            ) ps ON true
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue,
                       COUNT(DISTINCT oi.product_id) AS sold_items
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id AND o.order_status_id != 5
                JOIN products p ON p.id = oi.product_id
                JOIN deliveries d ON d.id = p.deliveryid
                WHERE d.supplier_id = s.id
            ) rev ON true
            WHERE ps.product_count > 0
            ORDER BY ps.total_cost DESC
            LIMIT :lim
        """), params).mappings().all()

        return {
            "period_type": "total",
            "data": [dict(r) for r in rows],
        }
    else:
        conditions = ["d.deliverydate IS NOT NULL"]
        if year:
            conditions.append("EXTRACT(YEAR FROM d.deliverydate) = :year")
            params["year"] = year
        where = " AND ".join(conditions)

        if period == "month":
            group_expr = "TO_CHAR(d.deliverydate, 'YYYY-MM')"
        elif period == "quarter":
            group_expr = "TO_CHAR(d.deliverydate, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM d.deliverydate)::int"
        else:
            group_expr = "TO_CHAR(d.deliverydate, 'YYYY')"

        rows = db.execute(text(f"""
            SELECT s.company_name AS supplier_name,
                   {group_expr} AS period_label,
                   COALESCE(SUM(ps.total_cost), 0)::float AS total_cost,
                   COALESCE(SUM(ps.items_count), 0) AS items_count,
                   CASE WHEN SUM(ps.items_count) > 0
                        THEN ROUND((SUM(ps.total_cost) / SUM(ps.items_count))::numeric, 2)::float
                        ELSE 0 END AS avg_price
            FROM deliveries d
            JOIN suppliers s ON s.id = d.supplier_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS items_count, COALESCE(SUM(p.price), 0) AS total_cost
                FROM products p WHERE p.deliveryid = d.id
            ) ps ON true
            WHERE {where}
            GROUP BY s.company_name, {group_expr}
            ORDER BY {group_expr}, total_cost DESC
        """), params).mappings().all()

        return {
            "period_type": period,
            "data": [dict(r) for r in rows],
        }


# ── Summary KPIs ─────────────────────────────────────────────────────────────
@router.get("/api/statistics/summary")
async def get_summary_stats(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Key performance indicators for dashboard cards."""
    row = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM products) AS total_products,
            (SELECT COUNT(*) FROM orders WHERE order_status_id != 5) AS total_orders,
            (SELECT COUNT(DISTINCT oi.product_id)
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id AND o.order_status_id != 5) AS products_sold,
            (SELECT COALESCE(SUM(oi.price * oi.quantity), 0)::float
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id AND o.order_status_id != 5) AS total_revenue,
            (SELECT COALESCE(SUM(p.price), 0)::float
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id AND o.order_status_id != 5
             JOIN products p ON p.id = oi.product_id) AS total_purchase_cost,
            (SELECT COALESCE(SUM(price), 0)::float FROM products) AS total_inventory_cost,
            (SELECT COUNT(*) FROM suppliers) AS total_suppliers,
            (SELECT COUNT(*) FROM deliveries) AS total_shipments,
            (SELECT COALESCE(SUM(p.price), 0)::float FROM products p WHERE p.deliveryid IS NOT NULL) AS total_shipment_cost
    """)).mappings().first()

    return dict(row) if row else {}


# ── Available years for period selectors ─────────────────────────────────────
@router.get("/api/statistics/years")
async def get_available_years(db: Session = Depends(get_db)) -> Dict[str, Any]:
    order_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM order_date)::int AS yr FROM orders WHERE order_date IS NOT NULL ORDER BY yr"
    )).scalars().all()
    shipment_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM deliverydate)::int AS yr FROM deliveries WHERE deliverydate IS NOT NULL ORDER BY yr"
    )).scalars().all()
    all_years = sorted(set(order_years) | set(shipment_years))
    return {"years": all_years}

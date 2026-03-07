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
        conditions.append("p.supplierid = :supplier_id")
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


# ── Shipments statistics ─────────────────────────────────────────────────────
@router.get("/api/statistics/shipments")
async def get_shipments_stats(
    period: str = Query("month", regex="^(month|quarter|year)$"),
    year: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Shipment stats: total cost, avg price per item, items count, sell efficiency."""
    conditions = ["sh.shipment_date IS NOT NULL"]
    params: Dict[str, Any] = {}

    if year:
        conditions.append("EXTRACT(YEAR FROM sh.shipment_date) = :year")
        params["year"] = year
    if supplier_id:
        conditions.append("sh.supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id

    where = " AND ".join(conditions)

    if period == "month":
        group_expr = "TO_CHAR(sh.shipment_date, 'YYYY-MM')"
    elif period == "quarter":
        group_expr = "TO_CHAR(sh.shipment_date, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM sh.shipment_date)::int"
    else:
        group_expr = "TO_CHAR(sh.shipment_date, 'YYYY')"

    rows = db.execute(text(f"""
        SELECT {group_expr} AS period_label,
               COUNT(DISTINCT sh.id) AS shipments_count,
               COALESCE(SUM(sh.items_count), 0) AS total_items,
               COALESCE(SUM(sh.total_cost), 0)::float AS total_cost,
               CASE WHEN SUM(sh.items_count) > 0
                    THEN ROUND((SUM(sh.total_cost) / SUM(sh.items_count))::numeric, 2)::float
                    ELSE 0 END AS avg_item_price
        FROM shipments sh
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    # Revenue from products of these shipments (sell efficiency)
    revenue_rows = db.execute(text(f"""
        SELECT {group_expr} AS period_label,
               COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue,
               COUNT(DISTINCT oi.product_id) AS sold_items
        FROM shipments sh
        JOIN products p ON p.shipment_id = sh.id
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
        # Overall stats per supplier
        conditions = []
        if year:
            conditions.append("EXTRACT(YEAR FROM sh.shipment_date) = :year")
            params["year"] = year
        where_sh = (" AND " + " AND ".join(conditions)) if conditions else ""

        rows = db.execute(text(f"""
            SELECT s.id, s.name,
                   COALESCE(ps.product_count, 0) AS product_count,
                   COALESCE(ps.total_cost, 0)::float AS total_cost,
                   COALESCE(ps.avg_price, 0)::float AS avg_price,
                   COALESCE(rev.revenue, 0)::float AS revenue,
                   COALESCE(rev.sold_items, 0) AS sold_items
            FROM suppliers s
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS product_count,
                       COALESCE(SUM(p.price), 0) AS total_cost,
                       CASE WHEN COUNT(*) > 0
                            THEN ROUND((SUM(p.price) / COUNT(*))::numeric, 2)
                            ELSE 0 END AS avg_price
                FROM products p
                WHERE p.supplierid = s.id
            ) ps ON true
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue,
                       COUNT(DISTINCT oi.product_id) AS sold_items
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id AND o.order_status_id != 5
                JOIN products p ON p.id = oi.product_id
                WHERE p.supplierid = s.id
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
        # By time period
        conditions = ["sh.shipment_date IS NOT NULL"]
        if year:
            conditions.append("EXTRACT(YEAR FROM sh.shipment_date) = :year")
            params["year"] = year
        where = " AND ".join(conditions)

        if period == "month":
            group_expr = "TO_CHAR(sh.shipment_date, 'YYYY-MM')"
        elif period == "quarter":
            group_expr = "TO_CHAR(sh.shipment_date, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM sh.shipment_date)::int"
        else:
            group_expr = "TO_CHAR(sh.shipment_date, 'YYYY')"

        rows = db.execute(text(f"""
            SELECT s.name AS supplier_name,
                   {group_expr} AS period_label,
                   SUM(sh.total_cost)::float AS total_cost,
                   SUM(sh.items_count) AS items_count,
                   CASE WHEN SUM(sh.items_count) > 0
                        THEN ROUND((SUM(sh.total_cost) / SUM(sh.items_count))::numeric, 2)::float
                        ELSE 0 END AS avg_price
            FROM shipments sh
            JOIN suppliers s ON s.id = sh.supplier_id
            WHERE {where}
            GROUP BY s.name, {group_expr}
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
            (SELECT COUNT(*) FROM shipments) AS total_shipments,
            (SELECT COALESCE(SUM(total_cost), 0)::float FROM shipments) AS total_shipment_cost
    """)).mappings().first()

    return dict(row) if row else {}


# ── Available years for period selectors ─────────────────────────────────────
@router.get("/api/statistics/years")
async def get_available_years(db: Session = Depends(get_db)) -> Dict[str, Any]:
    order_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM order_date)::int AS yr FROM orders WHERE order_date IS NOT NULL ORDER BY yr"
    )).scalars().all()
    shipment_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM shipment_date)::int AS yr FROM shipments WHERE shipment_date IS NOT NULL ORDER BY yr"
    )).scalars().all()
    all_years = sorted(set(order_years) | set(shipment_years))
    return {"years": all_years}

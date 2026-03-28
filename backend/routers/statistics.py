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


# ── Delivery (shipment) detail statistics ────────────────────────────────────
@router.get("/api/statistics/delivery/{delivery_id}")
async def get_delivery_detail_stats(
    delivery_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Detailed statistics for a single delivery/shipment."""
    logger.info(f"Fetching delivery detail stats for delivery_id={delivery_id}")

    # Basic delivery info
    delivery = db.execute(text("""
        SELECT d.id, d.deliveryname, d.deliverydate, d.delivery_cost,
               s.company_name AS supplier_name
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        WHERE d.id = :id
    """), {"id": delivery_id}).mappings().first()
    if not delivery:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Delivery not found")

    # Aggregated product stats
    stats = db.execute(text("""
        SELECT
            COUNT(*) AS total_pairs,
            COALESCE(SUM(p.price), 0)::float AS purchase_cost,
            COALESCE(SUM(CASE WHEN sold.product_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS sold_count,
            COUNT(*) - COALESCE(SUM(CASE WHEN sold.product_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS remaining_count
        FROM products p
        LEFT JOIN LATERAL (
            SELECT DISTINCT oi.product_id
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id AND o.order_status_id NOT IN (5, 6)
            WHERE oi.product_id = p.id
        ) sold ON true
        WHERE p.deliveryid = :id
    """), {"id": delivery_id}).mappings().first()

    # Revenue from sold items
    revenue = db.execute(text("""
        SELECT COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id AND o.order_status_id NOT IN (5, 6)
        JOIN products p ON p.id = oi.product_id
        WHERE p.deliveryid = :id
    """), {"id": delivery_id}).scalar() or 0

    total_pairs = stats["total_pairs"] or 0
    purchase_cost = stats["purchase_cost"] or 0
    delivery_cost = float(delivery["delivery_cost"] or 0)
    total_cost = purchase_cost + delivery_cost
    sold_count = stats["sold_count"] or 0
    remaining = stats["remaining_count"] or 0
    sell_rate = round(sold_count / total_pairs * 100, 1) if total_pairs > 0 else 0
    cost_per_pair = round(total_cost / total_pairs, 2) if total_pairs > 0 else 0
    net_revenue = round(revenue - total_cost, 2)

    # Size distribution (EU)
    sizes = db.execute(text("""
        SELECT p.sizeeu AS size, COUNT(*) AS count
        FROM products p WHERE p.deliveryid = :id AND p.sizeeu IS NOT NULL AND p.sizeeu != ''
        GROUP BY p.sizeeu ORDER BY p.sizeeu
    """), {"id": delivery_id}).mappings().all()

    # Measurement distribution (CM)
    measurements = db.execute(text("""
        SELECT p.measurementscm AS measurement, COUNT(*) AS count
        FROM products p WHERE p.deliveryid = :id AND p.measurementscm IS NOT NULL AND p.measurementscm != ''
        GROUP BY p.measurementscm ORDER BY p.measurementscm
    """), {"id": delivery_id}).mappings().all()

    # Type distribution
    types = db.execute(text("""
        SELECT COALESCE(t.typename, 'Без типу') AS type_name, COUNT(*) AS count
        FROM products p LEFT JOIN product_types t ON t.id = p.typeid
        WHERE p.deliveryid = :id
        GROUP BY t.typename ORDER BY count DESC
    """), {"id": delivery_id}).mappings().all()

    # Status distribution
    statuses = db.execute(text("""
        SELECT COALESCE(ps.statusname, 'Без статусу') AS status_name, COUNT(*) AS count
        FROM products p LEFT JOIN product_statuses ps ON ps.id = p.statusid
        WHERE p.deliveryid = :id
        GROUP BY ps.statusname ORDER BY count DESC
    """), {"id": delivery_id}).mappings().all()

    return {
        "delivery": dict(delivery),
        "total_pairs": total_pairs,
        "sold_count": sold_count,
        "remaining_count": remaining,
        "sell_rate": sell_rate,
        "purchase_cost": round(purchase_cost, 2),
        "delivery_cost": delivery_cost,
        "total_cost": round(total_cost, 2),
        "cost_per_pair": cost_per_pair,
        "revenue": round(revenue, 2),
        "net_revenue": net_revenue,
        "size_distribution": [dict(s) for s in sizes],
        "measurement_distribution": [dict(m) for m in measurements],
        "type_distribution": [dict(t) for t in types],
        "status_distribution": [dict(s) for s in statuses],
    }


# ── Deliveries list with basic metrics ───────────────────────────────────────
@router.get("/api/statistics/deliveries")
async def get_deliveries_stats(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    supplier_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List all deliveries with summary metrics for the statistics page."""
    logger.info(f"Fetching deliveries stats list: page={page}, supplier_id={supplier_id}, year={year}")

    conditions: List[str] = []
    params: Dict[str, Any] = {}
    if supplier_id:
        conditions.append("d.supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id
    if year:
        conditions.append("EXTRACT(YEAR FROM d.deliverydate) = :year")
        params["year"] = year

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = db.execute(text(f"SELECT COUNT(*) FROM deliveries d {where}"), params).scalar() or 0

    params["limit_val"] = per_page
    params["offset_val"] = (page - 1) * per_page

    rows = db.execute(text(f"""
        SELECT d.id, d.deliveryname, d.deliverydate, d.delivery_cost,
               s.company_name AS supplier_name,
               COALESCE(ps.total_pairs, 0) AS total_pairs,
               COALESCE(ps.purchase_cost, 0)::float AS purchase_cost,
               COALESCE(ps.sold_count, 0) AS sold_count,
               CASE WHEN COALESCE(ps.total_pairs, 0) > 0
                    THEN ROUND(ps.sold_count::numeric / ps.total_pairs * 100, 1)::float
                    ELSE 0 END AS sell_rate,
               COALESCE(rev.revenue, 0)::float AS revenue,
               COALESCE(rev.revenue, 0)::float - COALESCE(ps.purchase_cost, 0)::float - COALESCE(d.delivery_cost, 0)::float AS profit
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS total_pairs,
                   COALESCE(SUM(p.price), 0) AS purchase_cost,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM order_items oi JOIN orders o ON o.id = oi.order_id AND o.order_status_id NOT IN (5,6)
                       WHERE oi.product_id = p.id
                   )) AS sold_count
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id AND o.order_status_id NOT IN (5,6)
            JOIN products p ON p.id = oi.product_id
            WHERE p.deliveryid = d.id
        ) rev ON true
        {where}
        ORDER BY d.deliverydate DESC NULLS LAST, d.id DESC
        LIMIT :limit_val OFFSET :offset_val
    """), params).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": int(total),
        "page": page,
        "per_page": per_page,
        "pages": max(1, (int(total) + per_page - 1) // per_page),
    }


# ── Supplier detail statistics ───────────────────────────────────────────────
@router.get("/api/statistics/supplier/{supplier_id}")
async def get_supplier_detail_stats(
    supplier_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Detailed statistics for a single supplier."""
    logger.info(f"Fetching supplier detail stats for supplier_id={supplier_id}")

    supplier = db.execute(text(
        "SELECT id, company_name AS name FROM suppliers WHERE id = :id"
    ), {"id": supplier_id}).mappings().first()
    if not supplier:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Supplier not found")

    # Overview
    overview = db.execute(text("""
        SELECT
            COUNT(DISTINCT d.id) AS total_deliveries,
            COUNT(DISTINCT p.id) AS total_products,
            COALESCE(SUM(p.price), 0)::float AS total_spent,
            COALESCE(rev.revenue, 0)::float AS revenue,
            COALESCE(rev.sold_items, 0) AS sold_items,
            CASE WHEN COUNT(DISTINCT p.id) > 0
                 THEN ROUND(COALESCE(rev.sold_items, 0)::numeric / COUNT(DISTINCT p.id) * 100, 1)::float
                 ELSE 0 END AS sell_through_rate
        FROM deliveries d
        JOIN products p ON p.deliveryid = d.id
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue,
                   COUNT(DISTINCT oi.product_id) AS sold_items
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id AND o.order_status_id NOT IN (5,6)
            JOIN products p2 ON p2.id = oi.product_id
            JOIN deliveries d2 ON d2.id = p2.deliveryid
            WHERE d2.supplier_id = :id
        ) rev ON true
        WHERE d.supplier_id = :id
    """), {"id": supplier_id}).mappings().first()

    total_spent = (overview["total_spent"] or 0) if overview else 0
    revenue = (overview["revenue"] or 0) if overview else 0
    profit = round(revenue - total_spent, 2)

    # Top brands
    top_brands = db.execute(text("""
        SELECT b.brandname AS name, COUNT(*) AS count
        FROM deliveries d JOIN products p ON p.deliveryid = d.id
        JOIN brands b ON b.id = p.brandid
        WHERE d.supplier_id = :id
        GROUP BY b.brandname ORDER BY count DESC LIMIT 10
    """), {"id": supplier_id}).mappings().all()

    # Top types
    top_types = db.execute(text("""
        SELECT t.typename AS name, COUNT(*) AS count
        FROM deliveries d JOIN products p ON p.deliveryid = d.id
        JOIN product_types t ON t.id = p.typeid
        WHERE d.supplier_id = :id
        GROUP BY t.typename ORDER BY count DESC LIMIT 10
    """), {"id": supplier_id}).mappings().all()

    # Monthly trend (last 12 months)
    trend = db.execute(text("""
        SELECT TO_CHAR(d.deliverydate, 'YYYY-MM') AS month,
               COUNT(DISTINCT p.id) AS products,
               COALESCE(SUM(p.price), 0)::float AS cost,
               COALESCE(SUM(
                   CASE WHEN oi.id IS NOT NULL THEN oi.price * oi.quantity ELSE 0 END
               ), 0)::float AS revenue
        FROM deliveries d
        JOIN products p ON p.deliveryid = d.id
        LEFT JOIN order_items oi ON oi.product_id = p.id
        LEFT JOIN orders o ON o.id = oi.order_id AND o.order_status_id NOT IN (5,6)
        WHERE d.supplier_id = :id AND d.deliverydate IS NOT NULL
        GROUP BY TO_CHAR(d.deliverydate, 'YYYY-MM')
        ORDER BY month
    """), {"id": supplier_id}).mappings().all()

    return {
        "supplier": dict(supplier),
        "total_deliveries": overview["total_deliveries"] if overview else 0,
        "total_products": overview["total_products"] if overview else 0,
        "total_spent": round(total_spent, 2),
        "revenue": round(revenue, 2),
        "profit": profit,
        "sell_through_rate": overview["sell_through_rate"] if overview else 0,
        "sold_items": overview["sold_items"] if overview else 0,
        "top_brands": [dict(b) for b in top_brands],
        "top_types": [dict(t) for t in top_types],
        "monthly_trend": [dict(t) for t in trend],
    }


# ── Client statistics ────────────────────────────────────────────────────────
@router.get("/api/statistics/clients")
async def get_clients_stats(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Client analytics: top clients, new clients trend, avg check trend, rating distribution."""
    logger.info("Fetching client statistics")

    # Top clients by revenue
    top_by_revenue = db.execute(text("""
        SELECT c.id, c.first_name || ' ' || c.last_name AS name,
               COUNT(DISTINCT o.id) AS orders_count,
               COALESCE(SUM(oi.price * oi.quantity), 0)::float AS total_revenue
        FROM clients c
        JOIN orders o ON o.client_id = c.id AND o.order_status_id NOT IN (5,6)
        JOIN order_items oi ON oi.order_id = o.id
        GROUP BY c.id, c.first_name, c.last_name
        ORDER BY total_revenue DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()

    # Top clients by order count
    top_by_orders = db.execute(text("""
        SELECT c.id, c.first_name || ' ' || c.last_name AS name,
               COUNT(DISTINCT o.id) AS orders_count,
               COALESCE(SUM(oi.price * oi.quantity), 0)::float AS total_revenue
        FROM clients c
        JOIN orders o ON o.client_id = c.id AND o.order_status_id NOT IN (5,6)
        JOIN order_items oi ON oi.order_id = o.id
        GROUP BY c.id, c.first_name, c.last_name
        ORDER BY orders_count DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()

    # New clients per month
    new_clients_trend = db.execute(text("""
        SELECT TO_CHAR(first_order_date, 'YYYY-MM') AS month,
               COUNT(*) AS new_clients
        FROM clients
        WHERE first_order_date IS NOT NULL
        GROUP BY TO_CHAR(first_order_date, 'YYYY-MM')
        ORDER BY month
    """)).mappings().all()

    # Average check trend (by month)
    avg_check_trend = db.execute(text("""
        SELECT TO_CHAR(o.order_date, 'YYYY-MM') AS month,
               ROUND(AVG(order_total)::numeric, 2)::float AS avg_check,
               COUNT(*) AS orders_count
        FROM (
            SELECT o.id, o.order_date, SUM(oi.price * oi.quantity) AS order_total
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            WHERE o.order_status_id NOT IN (5,6) AND o.order_date IS NOT NULL
            GROUP BY o.id, o.order_date
        ) sub
        JOIN orders o ON o.id = sub.id
        GROUP BY TO_CHAR(o.order_date, 'YYYY-MM')
        ORDER BY month
    """)).mappings().all()

    # Rating distribution (using same formula as in clients endpoint)
    rating_dist = db.execute(text("""
        SELECT
            CASE
                WHEN rating >= 8 THEN 'excellent'
                WHEN rating >= 6 THEN 'good'
                WHEN rating >= 4 THEN 'average'
                ELSE 'low'
            END AS category,
            COUNT(*) AS count
        FROM (
            SELECT c.id,
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
                    COUNT(*) FILTER (WHERE o.order_status_id NOT IN (5,6,9,10)) AS confirmed_orders,
                    COUNT(*) FILTER (WHERE o.order_status_id = 5) AS cancelled_count,
                    COUNT(*) FILTER (WHERE o.order_status_id = 6) AS ignored_count,
                    COUNT(*) FILTER (WHERE o.order_status_id IN (9,10)) AS return_exchange_count
                FROM orders o WHERE o.client_id = c.id
            ) stats ON true
        ) rated
        GROUP BY category
        ORDER BY CASE category WHEN 'excellent' THEN 1 WHEN 'good' THEN 2 WHEN 'average' THEN 3 ELSE 4 END
    """)).mappings().all()

    return {
        "top_by_revenue": [dict(r) for r in top_by_revenue],
        "top_by_orders": [dict(r) for r in top_by_orders],
        "new_clients_trend": [dict(r) for r in new_clients_trend],
        "avg_check_trend": [dict(r) for r in avg_check_trend],
        "rating_distribution": [dict(r) for r in rating_dist],
    }

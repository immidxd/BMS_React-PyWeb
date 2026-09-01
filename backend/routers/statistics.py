from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

try:
    from backend.models.database import get_db
    from backend.utils.order_status_logic import (
        REVENUE_GENERATING, CONFIRMED_SOLD, CANCELLED_OR_RETURNED, sql_in_list,
        PAID_STATUS_ID, STATUS_CONFIRMED, STATUS_GIFT, STATUS_RETURNED, real_order_sql,
    )
    from backend.utils.cost_allocation import COST_RATIO_CTE, PRODUCT_COST_CTE
except ImportError:
    from models.database import get_db
    from utils.order_status_logic import (
        REVENUE_GENERATING, CONFIRMED_SOLD, CANCELLED_OR_RETURNED, sql_in_list,
        PAID_STATUS_ID, STATUS_CONFIRMED, STATUS_GIFT, STATUS_RETURNED, real_order_sql,
    )
    from utils.cost_allocation import COST_RATIO_CTE, PRODUCT_COST_CTE

try:
    from backend.utils.client_rating import client_rating_sql
except ImportError:
    from utils.client_rating import client_rating_sql

logger = logging.getLogger(__name__)
router = APIRouter()


def _catalog_period_sql(days: int, column: str = "received_at") -> str:
    return "TRUE" if days == 0 else f"{column} >= now() - (:catalog_days || ' days')::interval"


@router.get("/api/statistics/catalog")
def get_catalog_stats(
    days: int = Query(30),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Clean public-catalog engagement plus BMS sales, ready for Top-9 decisions.

    Product views come only from explicit active-card events. The historical GET
    counter is returned solely as a labelled audit value and never affects ranking.
    """
    if days not in (0, 7, 30, 90):
        raise HTTPException(status_code=400, detail="Період має бути 7, 30, 90 днів або весь час")
    from datetime import datetime, timezone
    try:
        from backend.services import catalog_analytics_sync_service
    except ImportError:
        from services import catalog_analytics_sync_service

    params: Dict[str, Any] = {"catalog_days": days, "limit": limit}
    event_where = _catalog_period_sql(days, "received_at")
    sales_where = _catalog_period_sql(days, "o.order_date")

    try:
        state = db.execute(text("""
            SELECT last_synced_at, last_received_at, rows_synced, last_error,
                   (last_synced_at IS NULL OR last_synced_at < now() - interval '5 minutes') AS stale
            FROM catalog_analytics_sync_state WHERE source='catalog_cloud'
        """)).mappings().first()
    except Exception:
        db.rollback()
        state = None

    if not state or state["stale"]:
        catalog_analytics_sync_service.trigger("statistics page")
    syncing = catalog_analytics_sync_service.is_running()

    try:
        summary = db.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE event_type='catalog_open')::int AS sessions,
                COUNT(DISTINCT visitor_key) FILTER (WHERE event_type='catalog_open')::int AS visitors,
                COUNT(*) FILTER (WHERE event_type='product_view')::int AS product_views,
                COUNT(DISTINCT productnumber) FILTER (WHERE event_type='product_view')::int AS viewed_products,
                COUNT(*) FILTER (WHERE event_type='favorite_add')::int AS favorite_adds,
                COUNT(*) FILTER (WHERE event_type='favorite_remove')::int AS favorite_removes,
                COUNT(*) FILTER (WHERE event_type='contact_click')::int AS contact_clicks,
                MIN(received_at) AS period_first_event
            FROM catalog_events WHERE {event_where}
        """), params).mappings().one()

        totals = db.execute(text("""
            SELECT COALESCE(SUM(active_favorites), 0)::int AS active_favorites,
                   COALESCE(SUM(legacy_views), 0)::int AS legacy_views
            FROM catalog_analytics_product_snapshot
        """)).mappings().one()

        tracking_started_at = db.execute(text(
            "SELECT MIN(received_at) FROM catalog_events"
        )).scalar()

        trend = [dict(r) for r in db.execute(text(f"""
            SELECT received_at::date AS date,
                   COUNT(DISTINCT visitor_key) FILTER (WHERE event_type='catalog_open')::int AS visitors,
                   COUNT(*) FILTER (WHERE event_type='catalog_open')::int AS sessions,
                   COUNT(*) FILTER (WHERE event_type='product_view')::int AS views,
                   COUNT(*) FILTER (WHERE event_type='favorite_add')::int AS favorite_adds,
                   COUNT(*) FILTER (WHERE event_type='contact_click')::int AS contact_clicks
            FROM catalog_events WHERE {event_where}
            GROUP BY received_at::date ORDER BY received_at::date
        """), params).mappings().all()]

        event_rows = db.execute(text(f"""
            SELECT productnumber,
                   COUNT(*) FILTER (WHERE event_type='product_view')::int AS views,
                   COUNT(DISTINCT visitor_key) FILTER (WHERE event_type='product_view')::int AS unique_viewers,
                   COUNT(*) FILTER (WHERE event_type='favorite_add')::int AS favorite_adds,
                   COUNT(*) FILTER (WHERE event_type='favorite_remove')::int AS favorite_removes,
                   COUNT(*) FILTER (WHERE event_type='contact_click')::int AS contact_clicks
            FROM catalog_events
            WHERE productnumber IS NOT NULL AND {event_where}
            GROUP BY productnumber
        """), params).mappings().all()

        favorite_rows = db.execute(text("""
            SELECT productnumber, active_favorites, legacy_views
            FROM catalog_analytics_product_snapshot
        """)).mappings().all()

        sales_rows = db.execute(text(f"""
            WITH per_client AS (
                SELECT p.productnumber, o.client_id,
                       COALESCE(SUM(oi.quantity) FILTER (
                           WHERE o.order_status_id={STATUS_GIFT}
                              OR (o.order_status_id={STATUS_CONFIRMED}
                                  AND o.payment_status_id={PAID_STATUS_ID})
                       ),0) AS paid_sold,
                       COALESCE(SUM(oi.quantity) FILTER (
                           WHERE o.order_status_id={STATUS_RETURNED}
                       ),0) AS returns,
                       COALESCE(SUM(oi.price*oi.quantity) FILTER (
                           WHERE o.order_status_id={STATUS_CONFIRMED}
                             AND o.payment_status_id={PAID_STATUS_ID}
                       ),0)::float AS revenue
                FROM order_items oi
                JOIN orders o ON o.id=oi.order_id
                JOIN products p ON p.id=oi.product_id
                WHERE p.productnumber IS NOT NULL
                  AND o.order_status_id IN ({STATUS_CONFIRMED},{STATUS_GIFT},{STATUS_RETURNED})
                  AND {sales_where}
                GROUP BY p.productnumber,o.client_id
            )
            SELECT productnumber,
                   GREATEST(SUM(paid_sold)-SUM(LEAST(paid_sold,returns)),0)::int AS sold_count,
                   COALESCE(SUM(revenue),0)::float AS revenue
            FROM per_client GROUP BY productnumber
        """), params).mappings().all()

        product_rows = db.execute(text(f"""
            WITH sold_per_client AS (
                SELECT oi.product_id,o.client_id,
                       COALESCE(SUM(oi.quantity) FILTER (
                           WHERE o.order_status_id={STATUS_GIFT}
                              OR (o.order_status_id={STATUS_CONFIRMED}
                                  AND o.payment_status_id={PAID_STATUS_ID})
                       ),0) AS paid_sold,
                       COALESCE(SUM(oi.quantity) FILTER (
                           WHERE o.order_status_id={STATUS_RETURNED}
                       ),0) AS returns
                FROM order_items oi JOIN orders o ON o.id=oi.order_id
                WHERE o.order_status_id IN ({STATUS_CONFIRMED},{STATUS_GIFT},{STATUS_RETURNED})
                GROUP BY oi.product_id,o.client_id
            ), sold AS (
                SELECT product_id,
                       GREATEST(SUM(paid_sold)-SUM(LEAST(paid_sold,returns)),0)::int AS sold_count
                FROM sold_per_client GROUP BY product_id
            )
            SELECT p.productnumber, MAX(p.id)::int AS product_id,
                   MAX(b.brandname) AS brand, MAX(p.model) AS model,
                   MAX(t.typename) AS type,
                   MIN(p.price)::float AS price,
                   SUM(GREATEST(COALESCE(p.quantity,0)-COALESCE(sold.sold_count,0),0))::int AS available,
                   BOOL_OR(COALESCE(cl.is_published, false)) AS published,
                   BOOL_OR(COALESCE(p.is_lost, false)) AS has_lost_row
            FROM products p
            LEFT JOIN brands b ON b.id=p.brandid
            LEFT JOIN types t ON t.id=p.typeid
            LEFT JOIN sold ON sold.product_id=p.id
            LEFT JOIN catalog_listings cl ON cl.productnumber=p.productnumber
            WHERE p.productnumber IS NOT NULL AND BTRIM(p.productnumber) <> ''
            GROUP BY p.productnumber
        """)).mappings().all()
    except Exception as exc:
        db.rollback()
        logger.warning("Catalog statistics unavailable: %s", exc)
        return {
            "period_days": days,
            "summary": {"visitors": 0, "sessions": 0, "product_views": 0,
                        "viewed_products": 0, "active_favorites": 0, "favorite_adds": 0,
                        "favorite_removes": 0, "contact_clicks": 0,
                        "like_rate": 0, "contact_rate": 0},
            "trend": [], "top_products": [], "tracking_started_at": None,
            "sync": {"syncing": syncing, "last_synced_at": None,
                     "last_received_at": None, "last_error": str(exc)},
            "legacy": {"total_views": 0, "used_in_rankings": False},
        }

    metrics: Dict[str, Dict[str, Any]] = {}
    for row in event_rows:
        metrics.setdefault(row["productnumber"], {}).update(dict(row))
    for row in favorite_rows:
        metrics.setdefault(row["productnumber"], {}).update(dict(row))
    for row in sales_rows:
        metrics.setdefault(row["productnumber"], {}).update(dict(row))
    product_map = {r["productnumber"]: dict(r) for r in product_rows}

    top_products = []
    for pnum, metric in metrics.items():
        product = product_map.get(pnum, {})
        views = int(metric.get("views") or 0)
        unique_viewers = int(metric.get("unique_viewers") or 0)
        favorite_adds = int(metric.get("favorite_adds") or 0)
        active_favorites = int(metric.get("active_favorites") or 0)
        contacts = int(metric.get("contact_clicks") or 0)
        sold_count = int(metric.get("sold_count") or 0)
        # Transparent, deterministic interest score. Components stay visible and
        # independently sortable; this score is only a convenient first ranking.
        score = unique_viewers + 3 * active_favorites + 4 * favorite_adds + 8 * contacts + 12 * sold_count
        available = int(product.get("available") or 0)
        published = bool(product.get("published"))
        top_products.append({
            "productnumber": pnum,
            "product_id": product.get("product_id"),
            "brand": product.get("brand"),
            "model": product.get("model"),
            "type": product.get("type"),
            "price": float(product.get("price") or 0),
            "views": views,
            "unique_viewers": unique_viewers,
            "active_favorites": active_favorites,
            "favorite_adds": favorite_adds,
            "contact_clicks": contacts,
            "sold_count": sold_count,
            "revenue": round(float(metric.get("revenue") or 0), 2),
            "popularity_score": score,
            "available": available,
            "published": published,
            "eligible_for_autopost": published and available > 0 and not bool(product.get("has_lost_row")),
        })
    top_products.sort(key=lambda x: (-x["popularity_score"], -x["views"], x["productnumber"]))
    top_products = top_products[:limit]

    product_views = int(summary["product_views"] or 0)
    clean_summary = {
        "visitors": int(summary["visitors"] or 0),
        "sessions": int(summary["sessions"] or 0),
        "product_views": product_views,
        "viewed_products": int(summary["viewed_products"] or 0),
        "active_favorites": int(totals["active_favorites"] or 0),
        "favorite_adds": int(summary["favorite_adds"] or 0),
        "favorite_removes": int(summary["favorite_removes"] or 0),
        "contact_clicks": int(summary["contact_clicks"] or 0),
        "like_rate": round((int(summary["favorite_adds"] or 0) / product_views * 100), 1) if product_views else 0,
        "contact_rate": round((int(summary["contact_clicks"] or 0) / product_views * 100), 1) if product_views else 0,
    }
    state_dict = dict(state) if state else {}
    return {
        "period_days": days,
        "summary": clean_summary,
        "trend": trend,
        "top_products": top_products,
        "tracking_started_at": tracking_started_at,
        "sync": {
            "syncing": syncing,
            "last_synced_at": state_dict.get("last_synced_at"),
            "last_received_at": state_dict.get("last_received_at"),
            "last_error": state_dict.get("last_error"),
            "is_stale": bool(state_dict.get("stale", True)),
        },
        "legacy": {"total_views": int(totals["legacy_views"] or 0), "used_in_rankings": False},
        "generated_at": datetime.now(timezone.utc),
    }


# ── Semantic SQL fragments ───────────────────────────────────────────────────
# All status logic in this module routes through these constants. Do NOT inline
# raw status IDs (see backend/utils/order_status_logic.py).
#
# REVENUE_SQL          – orders that produce revenue (Підтверджено only).
# CONFIRMED_SOLD_SQL   – orders that consume stock (Підтверджено + Подарунок).
# CANCELLED_OR_RET_SQL – orders that returned stock to the shelf
#                        (Відміна/Ігнорування/Повернення); used as a "skip these"
#                        filter for "active" order counts.
REVENUE_SQL = sql_in_list(REVENUE_GENERATING)              # (1)
CONFIRMED_SOLD_SQL = sql_in_list(CONFIRMED_SOLD)           # (1, 7)
CANCELLED_OR_RET_SQL = sql_in_list(CANCELLED_OR_RETURNED)  # (5, 6, 9)
REAL_ORDER_SQL = real_order_sql("o")

# Реалізований виторг = Підтверджено AND Оплачено. Передбачає, що таблиця orders
# має алієс `o`. Собівартість/прибуток рахуються через cost_allocation
# (deliveries.purchase_cost), а НЕ через products.price (= продажна ціна).
PAID_REVENUE = f"o.order_status_id IN {REVENUE_SQL} AND o.payment_status_id = {PAID_STATUS_ID}"


# Канал продажу для статистики. Явно задане не-дефолтне значення завжди має
# пріоритет; для старих замовлень із sales_channel='Ефір' дочитуємо маркер із
# notes, щоб історичні PROM / MONO / CT / CG / Catalog не губилися в «Ефірі».
# Межі — лише пробіли/пунктуація: це свідомо НЕ ловить «промасляні», monobank,
# catalogue або CGI як назви платформ.
_CHANNEL_DELIM = r"[[:space:][:punct:]]"
_PROM_NOTE_RE = rf"(^|{_CHANNEL_DELIM})(prom|[пП][рР][оО][мМ])($|{_CHANNEL_DELIM})"
_MONO_NOTE_RE = rf"(^|{_CHANNEL_DELIM})(mono|[мМ][оО][нН][оО])($|{_CHANNEL_DELIM})"
_CATALOG_NOTE_RE = rf"(^|{_CHANNEL_DELIM})(ct|cg|catalog)($|{_CHANNEL_DELIM})"


def _effective_sales_channel(order_alias: str = "o") -> str:
    raw = f"BTRIM(COALESCE({order_alias}.sales_channel, ''))"
    raw_lower = f"LOWER({raw})"
    notes = f"LOWER(COALESCE({order_alias}.notes, ''))"
    return f"""CASE
        WHEN {raw_lower} = 'prom' OR {raw} ~ '^[пП][рР][оО][мМ]$' THEN 'Prom'
        WHEN {raw_lower} = 'mono' OR {raw} ~ '^[мМ][оО][нН][оО]$' THEN 'MONO'
        WHEN {raw_lower} IN ('catalog', 'ct', 'cg')
             OR {raw} ~ '^[кК][аА][тТ][аА][лЛ][оО][гГ]$' THEN 'Каталог'
        WHEN {raw} <> '' AND {raw} !~ '^[еЕ][фФ][іІ][рР]$'
            THEN {raw}
        WHEN {notes} ~ '{_PROM_NOTE_RE}' THEN 'Prom'
        WHEN {notes} ~ '{_MONO_NOTE_RE}' THEN 'MONO'
        WHEN {notes} ~ '{_CATALOG_NOTE_RE}' THEN 'Каталог'
        ELSE 'Ефір'
    END"""


# ── Sales statistics ─────────────────────────────────────────────────────────
@router.get("/api/statistics/sales")
def get_sales_stats(
    period: str = Query("month", regex="^(month|quarter|year)$"),
    year: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Sales/revenue by month/quarter/year.

    Semantics (2026-06-15 redesign):
      • orders     – distinct orders NOT in (Відміна/Ігнорування/Повернення)
      • items_sold – order_items where order status IN (Підтверджено, Подарунок)
      • revenue    – SUM(orders.total_amount) for ОПЛАЧЕНИХ замовлень (money in)
      • cost       – собівартість проданого: розподілена закупівля позицій
                     (cost_allocation) + оцінка для оплачених ордерів без позицій
      • ship       – розподілена доставка проданих позицій
      • advertising_cost – витрати на рекламу ефіру з вкладок «Замовлення»
      • profit_before_advertising – revenue − cost − ship
      • profit     – revenue − cost − ship − advertising_cost
    """
    params: Dict[str, Any] = {}
    date_conds = ["o.order_date IS NOT NULL", REAL_ORDER_SQL]
    if year:
        date_conds.append("EXTRACT(YEAR FROM o.order_date) = :year")
        params["year"] = year
    supplier_exists = ""
    if supplier_id:
        supplier_exists = (
            " AND EXISTS (SELECT 1 FROM order_items oi2 JOIN products p2 ON p2.id = oi2.product_id "
            "JOIN deliveries d2 ON d2.id = p2.deliveryid "
            "WHERE oi2.order_id = o.id AND d2.supplier_id = :supplier_id)")
        params["supplier_id"] = supplier_id
    date_where = " AND ".join(date_conds)

    if period == "month":
        group_expr = "TO_CHAR(o.order_date, 'YYYY-MM')"
    elif period == "quarter":
        group_expr = "TO_CHAR(o.order_date, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM o.order_date)::int"
    else:
        group_expr = "TO_CHAR(o.order_date, 'YYYY')"

    # Order-level aggregates: orders count + realised revenue + estimated COGS
    # for paid orders that have no parsed order_items.
    order_rows = db.execute(text(f"""
        WITH {COST_RATIO_CTE}
        SELECT {group_expr} AS period_label,
               COUNT(*) FILTER (WHERE o.order_status_id NOT IN {CANCELLED_OR_RET_SQL}) AS orders_count,
               COALESCE(SUM(o.total_amount) FILTER (WHERE {PAID_REVENUE}), 0)::float AS revenue,
               COALESCE(SUM(o.total_amount * (SELECT ratio FROM cost_ratio))
                        FILTER (WHERE {PAID_REVENUE}
                                AND NOT EXISTS (SELECT 1 FROM order_items x WHERE x.order_id = o.id)),
                        0)::float AS cogs_itemless
        FROM orders o
        WHERE {date_where}{supplier_exists}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    # Item-level aggregates: units sold + allocated purchase cost + shipping.
    item_rows = db.execute(text(f"""
        WITH {PRODUCT_COST_CTE}
        SELECT {group_expr} AS period_label,
               COUNT(oi.id) FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}) AS items_sold,
               COALESCE(SUM(pc.unit_cost * oi.quantity) FILTER (WHERE {PAID_REVENUE}), 0)::float AS cogs_items,
               COALESCE(SUM(pc.unit_ship * oi.quantity) FILTER (WHERE {PAID_REVENUE}), 0)::float AS ship_items
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN product_cost pc ON pc.product_id = oi.product_id
        WHERE {date_where}{supplier_exists}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    item_map = {r["period_label"]: r for r in item_rows}

    # Реклама — операційна витрата ефіру за датою вкладки «Замовлення».
    # Вона не має прив'язки до постачальника, тому у supplier-filtered зрізі не
    # розподіляємо її довільно (і не віднімаємо повну суму від кожного).
    if supplier_id:
        advertising_rows = []
    else:
        if period == "month":
            expense_group_expr = "TO_CHAR(e.expense_date, 'YYYY-MM')"
        elif period == "quarter":
            expense_group_expr = (
                "TO_CHAR(e.expense_date, 'YYYY') || '-Q' || "
                "EXTRACT(QUARTER FROM e.expense_date)::int"
            )
        else:
            expense_group_expr = "TO_CHAR(e.expense_date, 'YYYY')"
        expense_where = ["e.sales_channel = 'Ефір'", "e.expense_date IS NOT NULL"]
        expense_params: Dict[str, Any] = {}
        if year:
            expense_where.append("EXTRACT(YEAR FROM e.expense_date) = :year")
            expense_params["year"] = year
        advertising_rows = db.execute(text(f"""
            SELECT {expense_group_expr} AS period_label,
                   COALESCE(SUM(e.amount), 0)::float AS advertising_cost
            FROM advertising_expenses e
            WHERE {' AND '.join(expense_where)}
            GROUP BY {expense_group_expr}
            ORDER BY {expense_group_expr}
        """), expense_params).mappings().all()

    order_map = {r["period_label"]: r for r in order_rows}
    advertising_map = {r["period_label"]: r["advertising_cost"] for r in advertising_rows}
    period_labels = sorted(set(order_map) | set(advertising_map))

    data = []
    for label in period_labels:
        r = order_map.get(label, {})
        it = item_map.get(label, {})
        revenue = r.get("revenue", 0) or 0
        cost = (r.get("cogs_itemless", 0) or 0) + (it.get("cogs_items", 0) or 0)
        ship = it.get("ship_items", 0) or 0
        advertising_cost = advertising_map.get(label, 0) or 0
        profit_before_advertising = revenue - cost - ship
        data.append({
            "period": label,
            "orders": r.get("orders_count", 0) or 0,
            "items_sold": it.get("items_sold", 0) or 0,
            "revenue": round(revenue, 2),
            "cost": round(cost, 2),
            "ship": round(ship, 2),
            "advertising_cost": round(advertising_cost, 2),
            "profit_before_advertising": round(profit_before_advertising, 2),
            "profit": round(profit_before_advertising - advertising_cost, 2),
        })

    return {"period_type": period, "data": data}


# ── Deliveries (shipments) statistics ─────────────────────────────────────────
@router.get("/api/statistics/shipments")
def get_shipments_stats(
    period: str = Query("month", regex="^(month|quarter|year)$"),
    year: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Delivery stats: total cost, avg price per item, items count, sell efficiency.

    Sold-rate denominator: count of products in the delivery. Numerator:
    products that have ≥1 order_item in CONFIRMED_SOLD (stock consumed)."""
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

    # Собівартість завозу = deliveries.purchase_cost (реальна закупівля); якщо не
    # заповнена — оцінка SUM(p.price) × global_ratio. НЕ SUM(p.price) (= продажна).
    rows = db.execute(text(f"""
        WITH {COST_RATIO_CTE}
        SELECT {group_expr} AS period_label,
               COUNT(DISTINCT d.id) AS shipments_count,
               COALESCE(SUM(ps.items_count), 0) AS total_items,
               COALESCE(SUM(COALESCE(d.purchase_cost, ps.price_sum * (SELECT ratio FROM cost_ratio))), 0)::float AS total_cost,
               COALESCE(SUM(d.delivery_cost), 0)::float AS delivery_cost,
               CASE WHEN SUM(ps.items_count) > 0
                    THEN ROUND((SUM(COALESCE(d.purchase_cost, ps.price_sum * (SELECT ratio FROM cost_ratio)))
                                / SUM(ps.items_count))::numeric, 2)::float
                    ELSE 0 END AS avg_item_price
        FROM deliveries d
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS items_count, COALESCE(SUM(p.price), 0) AS price_sum
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    # Revenue (оплачено) + sold units from products of these deliveries
    revenue_rows = db.execute(text(f"""
        SELECT {group_expr} AS period_label,
               COALESCE(SUM((oi.price * oi.quantity))
                        FILTER (WHERE {PAID_REVENUE}), 0)::float AS revenue,
               COUNT(DISTINCT p.id)
                   FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}) AS sold_items
        FROM deliveries d
        JOIN products p ON p.deliveryid = d.id
        LEFT JOIN order_items oi ON oi.product_id = p.id
        LEFT JOIN orders o ON o.id = oi.order_id
        WHERE {where}
        GROUP BY {group_expr}
        ORDER BY {group_expr}
    """), params).mappings().all()

    rev_map = {r["period_label"]: r for r in revenue_rows}

    data = []
    for r in rows:
        label = r["period_label"]
        rev = rev_map.get(label, {})
        revenue = rev.get("revenue", 0) if rev else 0
        cost = r["total_cost"]
        delivery_cost = r["delivery_cost"] or 0
        data.append({
            "period": label,
            "shipments": r["shipments_count"],
            "items": r["total_items"],
            "total_cost": round(cost, 2),
            "delivery_cost": round(delivery_cost, 2),
            "avg_price": r["avg_item_price"],
            "revenue": round(revenue, 2),
            "profit": round(revenue - cost - delivery_cost, 2),
            "sold_items": rev.get("sold_items", 0) if rev else 0,
            "sell_rate": round((rev.get("sold_items", 0) or 0) / r["total_items"] * 100, 1) if r["total_items"] else 0,
        })

    return {"period_type": period, "data": data}


# ── Suppliers statistics ─────────────────────────────────────────────────────
@router.get("/api/statistics/suppliers")
def get_suppliers_stats(
    period: str = Query("total", regex="^(month|quarter|year|total)$"),
    year: Optional[int] = Query(None),
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Top suppliers by total purchase cost, optionally split by period."""
    params: Dict[str, Any] = {"lim": limit}

    if period == "total":
        # total_cost = реальна закупівля (deliveries.purchase_cost, фолбек оцінка
        # SUM(p.price)×ratio), а НЕ продажна сума товарів. revenue = оплачено.
        rows = db.execute(text(f"""
            WITH {COST_RATIO_CTE}
            SELECT s.id, s.company_name AS name,
                   COALESCE(ps.product_count, 0) AS product_count,
                   COALESCE(ps.total_cost, 0)::float AS total_cost,
                   CASE WHEN COALESCE(ps.product_count, 0) > 0
                        THEN ROUND((ps.total_cost / ps.product_count)::numeric, 2)::float
                        ELSE 0 END AS avg_price,
                   COALESCE(rev.revenue, 0)::float AS revenue,
                   COALESCE(rev.sold_items, 0) AS sold_items
            FROM suppliers s
            LEFT JOIN LATERAL (
                -- Спершу собівартість ПО ПОСТАВЦІ (інакше purchase_cost помножиться
                -- на кількість товарів), потім сума по всіх поставках постачальника.
                SELECT COALESCE(SUM(dd.pcost), 0) AS total_cost,
                       COALESCE(SUM(dd.pcount), 0) AS product_count
                FROM (
                    SELECT COALESCE(d.purchase_cost, SUM(p.price) * (SELECT ratio FROM cost_ratio)) AS pcost,
                           COUNT(p.id) AS pcount
                    FROM deliveries d
                    JOIN products p ON p.deliveryid = d.id
                    WHERE d.supplier_id = s.id
                    GROUP BY d.id, d.purchase_cost
                ) dd
            ) ps ON true
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue,
                       COUNT(DISTINCT oi.product_id) AS sold_items
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id AND {PAID_REVENUE}
                JOIN products p ON p.id = oi.product_id
                JOIN deliveries d ON d.id = p.deliveryid
                WHERE d.supplier_id = s.id
            ) rev ON true
            WHERE ps.product_count > 0
            ORDER BY ps.total_cost DESC
            LIMIT :lim
        """), params).mappings().all()

        return {"period_type": "total", "data": [dict(r) for r in rows]}

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
        WITH {COST_RATIO_CTE}
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
            SELECT COUNT(*) AS items_count,
                   COALESCE(d.purchase_cost, COALESCE(SUM(p.price), 0) * (SELECT ratio FROM cost_ratio)) AS total_cost
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        WHERE {where}
        GROUP BY s.company_name, {group_expr}
        ORDER BY {group_expr}, total_cost DESC
    """), params).mappings().all()

    return {"period_type": period, "data": [dict(r) for r in rows]}


# ── Summary KPIs ─────────────────────────────────────────────────────────────
@router.get("/api/statistics/summary")
def get_summary_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Key performance indicators for dashboard cards.

    Authoritative definitions for the whole stats UI:
      • total_products          – COUNT(*) FROM products
      • total_units             – SUM(quantity) – respects ростовки
      • products_sold           – products with at least one CONFIRMED_SOLD order item
      • products_fully_sold     – products where confirmed-sold units ≥ quantity
      • products_partially_sold – partial sale, still has stock
      • products_unsold         – never sold
      • total_orders            – orders NOT cancelled/ignored/returned
      • confirmed_orders        – orders with status = Підтверджено
      • paid_orders             – confirmed AND Оплачено (payment_status = 1)
      • total_revenue           – SUM(orders.total_amount) WHERE оплачено (money in)
      • total_purchase_cost     – COGS: розподілена закупівля проданих позицій
                                  (cost_allocation) + оцінка для оплачених ордерів
                                  без позицій (total_amount × ratio)
      • total_delivery_cost     – розподілена доставка проданих позицій
      • total_advertising_cost  – реклама ефіру з датованих вкладок «Замовлення»
      • net_profit              – revenue − purchase_cost − delivery_cost − advertising
      • unsold_inventory_cost   – SUM(p.price) for products with remaining stock
                                  (ПРОДАЖНА ціна = потенційний виторг залишку)
      • total_inventory_cost    – SUM(p.price) FROM products (raw)
    """
    row = db.execute(text(f"""
        WITH {PRODUCT_COST_CTE},
        product_sales AS (
            SELECT oi.product_id,
                   COUNT(*) FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}) AS sold_units
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            GROUP BY oi.product_id
        )
        SELECT
            (SELECT COUNT(*) FROM products) AS total_products,
            (SELECT COALESCE(SUM(quantity), 0) FROM products) AS total_units,

            (SELECT COUNT(*) FROM products p
                JOIN product_sales ps ON ps.product_id = p.id
                WHERE ps.sold_units > 0) AS products_sold,
            (SELECT COUNT(*) FROM products p
                JOIN product_sales ps ON ps.product_id = p.id
                WHERE ps.sold_units >= COALESCE(NULLIF(p.quantity, 0), 1)) AS products_fully_sold,
            (SELECT COUNT(*) FROM products p
                JOIN product_sales ps ON ps.product_id = p.id
                WHERE ps.sold_units > 0
                  AND ps.sold_units < COALESCE(NULLIF(p.quantity, 0), 1)) AS products_partially_sold,
            (SELECT COUNT(*) FROM products p
                LEFT JOIN product_sales ps ON ps.product_id = p.id
                WHERE COALESCE(ps.sold_units, 0) = 0) AS products_unsold,

            (SELECT COUNT(*) FROM orders o
                WHERE {REAL_ORDER_SQL}
                  AND o.order_status_id NOT IN {CANCELLED_OR_RET_SQL}) AS total_orders,
            (SELECT COUNT(*) FROM orders o
                WHERE {REAL_ORDER_SQL}
                  AND o.order_status_id IN {REVENUE_SQL}) AS confirmed_orders,
            (SELECT COUNT(*) FROM orders o
                WHERE {REAL_ORDER_SQL}
                  AND o.order_status_id IN {REVENUE_SQL}
                  AND o.payment_status_id = {PAID_STATUS_ID}) AS paid_orders,

            (SELECT COALESCE(SUM(o.total_amount), 0)::float
             FROM orders o WHERE {PAID_REVENUE}) AS total_revenue,

            ((SELECT COALESCE(SUM(pc.unit_cost * oi.quantity), 0)::float
              FROM order_items oi
              JOIN orders o ON o.id = oi.order_id
              JOIN product_cost pc ON pc.product_id = oi.product_id
              WHERE {PAID_REVENUE})
             + (SELECT COALESCE(SUM(o.total_amount * (SELECT ratio FROM cost_ratio)), 0)::float
                FROM orders o WHERE {PAID_REVENUE}
                  AND NOT EXISTS (SELECT 1 FROM order_items x WHERE x.order_id = o.id))
            ) AS total_purchase_cost,

            (SELECT COALESCE(SUM(pc.unit_ship * oi.quantity), 0)::float
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id
             JOIN product_cost pc ON pc.product_id = oi.product_id
             WHERE {PAID_REVENUE}) AS total_delivery_cost,

            (SELECT COALESCE(SUM(e.amount), 0)::float
             FROM advertising_expenses e
             WHERE e.sales_channel = 'Ефір') AS total_advertising_cost,

            (SELECT COALESCE(SUM(p.price), 0)::float FROM products p
                LEFT JOIN product_sales ps ON ps.product_id = p.id
                WHERE COALESCE(ps.sold_units, 0) < COALESCE(NULLIF(p.quantity, 0), 1)) AS unsold_inventory_cost,

            (SELECT COALESCE(SUM(price), 0)::float FROM products) AS total_inventory_cost,

            (SELECT COUNT(*) FROM suppliers) AS total_suppliers,
            (SELECT COUNT(*) FROM deliveries) AS total_shipments,
            (SELECT COALESCE(SUM(p.price), 0)::float FROM products p WHERE p.deliveryid IS NOT NULL) AS total_shipment_cost
    """)).mappings().first()

    if not row:
        return {}

    data = dict(row)
    rev = data["total_revenue"] or 0
    cost = data["total_purchase_cost"] or 0
    dcost = data["total_delivery_cost"] or 0
    advertising = data["total_advertising_cost"] or 0
    data["net_profit"] = round(rev - cost - dcost - advertising, 2)
    return data


# ── Available years ──────────────────────────────────────────────────────────
@router.get("/api/statistics/years")
def get_available_years(db: Session = Depends(get_db)) -> Dict[str, Any]:
    order_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM order_date)::int AS yr FROM orders WHERE order_date IS NOT NULL ORDER BY yr"
    )).scalars().all()
    shipment_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM deliverydate)::int AS yr FROM deliveries WHERE deliverydate IS NOT NULL ORDER BY yr"
    )).scalars().all()
    expense_years = db.execute(text(
        "SELECT DISTINCT EXTRACT(YEAR FROM expense_date)::int AS yr "
        "FROM advertising_expenses WHERE expense_date IS NOT NULL ORDER BY yr"
    )).scalars().all()
    all_years = sorted(set(order_years) | set(shipment_years) | set(expense_years))
    return {"years": all_years}


# ── Delivery detail ──────────────────────────────────────────────────────────
@router.get("/api/statistics/delivery/{delivery_id}")
def get_delivery_detail_stats(
    delivery_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Detailed statistics for a single delivery."""
    logger.info(f"Fetching delivery detail stats for delivery_id={delivery_id}")

    delivery = db.execute(text("""
        SELECT d.id, d.deliveryname, d.deliverydate, d.delivery_cost, d.purchase_cost,
               s.company_name AS supplier_name
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        WHERE d.id = :id
    """), {"id": delivery_id}).mappings().first()
    if not delivery:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Delivery not found")

    stats = db.execute(text(f"""
        SELECT
            COUNT(*) AS total_pairs,
            COALESCE(SUM(p.price), 0)::float AS price_sum,
            COALESCE(SUM(CASE WHEN sold.product_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS sold_count,
            COUNT(*) - COALESCE(SUM(CASE WHEN sold.product_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS remaining_count
        FROM products p
        LEFT JOIN LATERAL (
            SELECT DISTINCT oi.product_id
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id AND o.order_status_id IN {CONFIRMED_SOLD_SQL}
            WHERE oi.product_id = p.id
        ) sold ON true
        WHERE p.deliveryid = :id
    """), {"id": delivery_id}).mappings().first()

    revenue = db.execute(text(f"""
        SELECT COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id AND {PAID_REVENUE}
        JOIN products p ON p.id = oi.product_id
        WHERE p.deliveryid = :id
    """), {"id": delivery_id}).scalar() or 0

    # Собівартість поставки = deliveries.purchase_cost (реальна закупівля); якщо не
    # заповнена — оцінка SUM(p.price) × global_ratio. НЕ SUM(p.price) (= продажна).
    cost_ratio = db.execute(text(f"WITH {COST_RATIO_CTE} SELECT ratio FROM cost_ratio")).scalar() or 0.6
    total_pairs = stats["total_pairs"] or 0
    price_sum = stats["price_sum"] or 0
    purchase_cost = float(delivery["purchase_cost"]) if delivery["purchase_cost"] else float(price_sum) * float(cost_ratio)
    delivery_cost = float(delivery["delivery_cost"] or 0)
    total_cost = purchase_cost + delivery_cost
    sold_count = stats["sold_count"] or 0
    remaining = stats["remaining_count"] or 0
    sell_rate = round(sold_count / total_pairs * 100, 1) if total_pairs > 0 else 0
    cost_per_pair = round(total_cost / total_pairs, 2) if total_pairs > 0 else 0
    net_revenue = round(revenue - total_cost, 2)

    sizes = db.execute(text("""
        SELECT p.sizeeu AS size, COUNT(*) AS count
        FROM products p WHERE p.deliveryid = :id AND p.sizeeu IS NOT NULL AND p.sizeeu != ''
        GROUP BY p.sizeeu ORDER BY p.sizeeu
    """), {"id": delivery_id}).mappings().all()

    measurements = db.execute(text("""
        SELECT p.measurementscm AS measurement, COUNT(*) AS count
        FROM products p WHERE p.deliveryid = :id AND p.measurementscm IS NOT NULL AND p.measurementscm != ''
        GROUP BY p.measurementscm ORDER BY p.measurementscm
    """), {"id": delivery_id}).mappings().all()

    # FIX: was `product_types` (junction table, always 0 rows) → must be `types`
    types = db.execute(text("""
        SELECT COALESCE(t.typename, 'Без типу') AS type_name, COUNT(*) AS count
        FROM products p LEFT JOIN types t ON t.id = p.typeid
        WHERE p.deliveryid = :id
        GROUP BY t.typename ORDER BY count DESC
    """), {"id": delivery_id}).mappings().all()

    # FIX: table is `statuses`, NOT `product_statuses` (яка не існує → 500 при
    # відкритті деталей завозу).
    statuses = db.execute(text("""
        SELECT COALESCE(ps.statusname, 'Без статусу') AS status_name, COUNT(*) AS count
        FROM products p LEFT JOIN statuses ps ON ps.id = p.statusid
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


# ── Deliveries list with metrics ─────────────────────────────────────────────
@router.get("/api/statistics/deliveries")
def get_deliveries_stats(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    supplier_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Per-delivery summary table for the shipments grid."""
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
        WITH {COST_RATIO_CTE}
        SELECT d.id, d.deliveryname, d.deliverydate,
               COALESCE(d.delivery_cost, 0)::float AS delivery_cost,
               -- Собівартість = deliveries.purchase_cost; якщо порожня —
               -- оцінка SUM(p.price) × global_ratio (НЕ продажна сума!).
               COALESCE(d.purchase_cost, ps.price_sum * (SELECT ratio FROM cost_ratio), 0)::float AS purchase_cost,
               (d.purchase_cost IS NULL OR d.purchase_cost = 0) AS cost_estimated,
               s.company_name AS supplier_name,
               COALESCE(ps.total_pairs, 0) AS total_pairs,
               COALESCE(ps.sold_count, 0) AS sold_count,
               CASE WHEN COALESCE(ps.total_pairs, 0) > 0
                    THEN ROUND(ps.sold_count::numeric / ps.total_pairs * 100, 1)::float
                    ELSE 0 END AS sell_rate,
               COALESCE(rev.revenue, 0)::float AS revenue,
               -- Прибуток = виторг (оплачено) − собівартість проданого − доставка.
               COALESCE(rev.revenue, 0)::float
                   - COALESCE(d.purchase_cost, ps.price_sum * (SELECT ratio FROM cost_ratio), 0)::float
                   - COALESCE(d.delivery_cost, 0)::float AS profit
        FROM deliveries d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS total_pairs,
                   COALESCE(SUM(p.price), 0) AS price_sum,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM order_items oi
                       JOIN orders o ON o.id = oi.order_id AND o.order_status_id IN {CONFIRMED_SOLD_SQL}
                       WHERE oi.product_id = p.id
                   )) AS sold_count
            FROM products p WHERE p.deliveryid = d.id
        ) ps ON true
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(oi.price * oi.quantity), 0) AS revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id AND {PAID_REVENUE}
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


# ── Supplier detail ──────────────────────────────────────────────────────────
@router.get("/api/statistics/supplier/{supplier_id}")
def get_supplier_detail_stats(
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

    # Скалярні підзапити (без mix агрегат+LATERAL → без GROUP BY-конфліктів).
    # total_spent = реальна закупівля (по поставці), НЕ продажна SUM(p.price).
    overview = db.execute(text(f"""
        WITH {COST_RATIO_CTE}
        SELECT
            (SELECT COUNT(DISTINCT d.id) FROM deliveries d WHERE d.supplier_id = :id) AS total_deliveries,
            (SELECT COUNT(*) FROM products p JOIN deliveries d ON d.id = p.deliveryid
                WHERE d.supplier_id = :id) AS total_products,
            (SELECT COALESCE(SUM(COALESCE(dd.purchase_cost, dd.price_sum * (SELECT ratio FROM cost_ratio))), 0)::float
                FROM (
                    SELECT d.id, d.purchase_cost, COALESCE(SUM(p.price), 0) AS price_sum
                    FROM deliveries d JOIN products p ON p.deliveryid = d.id
                    WHERE d.supplier_id = :id
                    GROUP BY d.id, d.purchase_cost
                ) dd) AS total_spent,
            (SELECT COALESCE(SUM(oi.price * oi.quantity) FILTER (WHERE {PAID_REVENUE}), 0)::float
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                JOIN products p ON p.id = oi.product_id
                JOIN deliveries d ON d.id = p.deliveryid
                WHERE d.supplier_id = :id) AS revenue,
            (SELECT COUNT(DISTINCT oi.product_id) FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL})
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                JOIN products p ON p.id = oi.product_id
                JOIN deliveries d ON d.id = p.deliveryid
                WHERE d.supplier_id = :id) AS sold_items
    """), {"id": supplier_id}).mappings().first()

    total_spent = (overview["total_spent"] or 0) if overview else 0
    revenue = (overview["revenue"] or 0) if overview else 0
    total_products = (overview["total_products"] or 0) if overview else 0
    sold_items = (overview["sold_items"] or 0) if overview else 0
    profit = round(revenue - total_spent, 2)
    sell_through_rate = round(sold_items / total_products * 100, 1) if total_products else 0

    top_brands = db.execute(text("""
        SELECT b.brandname AS name, COUNT(*) AS count
        FROM deliveries d JOIN products p ON p.deliveryid = d.id
        JOIN brands b ON b.id = p.brandid
        WHERE d.supplier_id = :id
        GROUP BY b.brandname ORDER BY count DESC LIMIT 10
    """), {"id": supplier_id}).mappings().all()

    # FIX: was `product_types` (junction, 0 rows) → must be `types`
    top_types = db.execute(text("""
        SELECT t.typename AS name, COUNT(*) AS count
        FROM deliveries d JOIN products p ON p.deliveryid = d.id
        JOIN types t ON t.id = p.typeid
        WHERE d.supplier_id = :id
        GROUP BY t.typename ORDER BY count DESC LIMIT 10
    """), {"id": supplier_id}).mappings().all()

    # Per-delivery cost (закупівля з фолбеком) by month + paid revenue by month,
    # рахуються ОКРЕМО щоб LEFT JOIN order_items не роздував собівартість.
    trend = db.execute(text(f"""
        WITH {COST_RATIO_CTE},
        deliv AS (
            SELECT TO_CHAR(d.deliverydate, 'YYYY-MM') AS month,
                   COUNT(p.id) AS products,
                   COALESCE(d.purchase_cost, COALESCE(SUM(p.price), 0) * (SELECT ratio FROM cost_ratio)) AS cost
            FROM deliveries d JOIN products p ON p.deliveryid = d.id
            WHERE d.supplier_id = :id AND d.deliverydate IS NOT NULL
            GROUP BY d.id, d.purchase_cost, TO_CHAR(d.deliverydate, 'YYYY-MM')
        ),
        rev AS (
            SELECT TO_CHAR(d.deliverydate, 'YYYY-MM') AS month,
                   COALESCE(SUM(oi.price * oi.quantity) FILTER (WHERE {PAID_REVENUE}), 0) AS revenue
            FROM deliveries d
            JOIN products p ON p.deliveryid = d.id
            JOIN order_items oi ON oi.product_id = p.id
            JOIN orders o ON o.id = oi.order_id
            WHERE d.supplier_id = :id AND d.deliverydate IS NOT NULL
            GROUP BY TO_CHAR(d.deliverydate, 'YYYY-MM')
        )
        SELECT deliv.month,
               SUM(deliv.products) AS products,
               SUM(deliv.cost)::float AS cost,
               COALESCE(MAX(rev.revenue), 0)::float AS revenue
        FROM deliv LEFT JOIN rev ON rev.month = deliv.month
        GROUP BY deliv.month
        ORDER BY deliv.month
    """), {"id": supplier_id}).mappings().all()

    return {
        "supplier": dict(supplier),
        "total_deliveries": overview["total_deliveries"] if overview else 0,
        "total_products": overview["total_products"] if overview else 0,
        "total_spent": round(total_spent, 2),
        "revenue": round(revenue, 2),
        "profit": profit,
        "sell_through_rate": sell_through_rate,
        "sold_items": sold_items,
        "top_brands": [dict(b) for b in top_brands],
        "top_types": [dict(t) for t in top_types],
        "monthly_trend": [dict(t) for t in trend],
    }


# ── Client statistics ────────────────────────────────────────────────────────
@router.get("/api/statistics/clients")
def get_clients_stats(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Client analytics. Per BMS convention top-client metrics are strictly
    confirmed (status=Підтверджено) — gifts/pending don't count as a client's
    realized spend or order tally.
    """
    logger.info("Fetching client statistics")

    # Беремо метрики з orders.total_amount, БЕЗ JOIN order_items.
    # Раніше JOIN order_items відкидав легасі-ордери без позицій (parser не зміг
    # розпізнати productnumber у sheet) — у Светлани #9226: 467 confirmed,
    # але тільки 169 з items → stats показувала 169/328k, картка 467/869k.
    # Тепер обидва ендпоінти показують однакові числа з orders.total_amount.
    top_by_revenue = db.execute(text(f"""
        SELECT c.id, c.first_name || ' ' || c.last_name AS name,
               COUNT(o.id) AS orders_count,
               COALESCE(SUM(o.total_amount), 0)::float AS total_revenue
        FROM clients c
        JOIN orders o ON o.client_id = c.id AND o.order_status_id IN {REVENUE_SQL}
        GROUP BY c.id, c.first_name, c.last_name
        ORDER BY total_revenue DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()

    top_by_orders = db.execute(text(f"""
        SELECT c.id, c.first_name || ' ' || c.last_name AS name,
               COUNT(o.id) AS orders_count,
               COALESCE(SUM(o.total_amount), 0)::float AS total_revenue
        FROM clients c
        JOIN orders o ON o.client_id = c.id AND o.order_status_id IN {REVENUE_SQL}
        GROUP BY c.id, c.first_name, c.last_name
        ORDER BY orders_count DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()

    new_clients_trend = db.execute(text("""
        SELECT TO_CHAR(first_order_date, 'YYYY-MM') AS month,
               COUNT(*) AS new_clients
        FROM clients
        WHERE first_order_date IS NOT NULL
        GROUP BY TO_CHAR(first_order_date, 'YYYY-MM')
        ORDER BY month
    """)).mappings().all()

    # Avg check — теж з orders.total_amount, не з items (див. коментар вище).
    avg_check_trend = db.execute(text(f"""
        SELECT TO_CHAR(order_date, 'YYYY-MM') AS month,
               ROUND(AVG(total_amount)::numeric, 2)::float AS avg_check,
               COUNT(*) AS orders_count
        FROM orders
        WHERE order_status_id IN {REVENUE_SQL}
          AND order_date IS NOT NULL
          AND total_amount IS NOT NULL
        GROUP BY TO_CHAR(order_date, 'YYYY-MM')
        ORDER BY month
    """)).mappings().all()

    _rating_expr_dist = client_rating_sql(
        confirmed="stats.confirmed_orders",
        revenue="stats.confirmed_total_amount",
        cancelled="stats.cancelled_count",
        ignored="stats.ignored_count",
        returns="stats.return_exchange_count",
    )
    rating_dist = db.execute(text(f"""
        SELECT category, COUNT(*) AS count
        FROM (
            SELECT
                CASE
                    WHEN rating >= 8 THEN 'excellent'
                    WHEN rating >= 6 THEN 'good'
                    WHEN rating >= 4 THEN 'average'
                    ELSE 'low'
                END AS category
            FROM (
                SELECT c.id,
                    {_rating_expr_dist} AS rating
                FROM clients c
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) FILTER (WHERE o.order_status_id IN {REVENUE_SQL}) AS confirmed_orders,
                        COALESCE(SUM(o.total_amount) FILTER (WHERE o.order_status_id IN {REVENUE_SQL}), 0) AS confirmed_total_amount,
                        COUNT(*) FILTER (WHERE o.order_status_id = 5) AS cancelled_count,
                        COUNT(*) FILTER (WHERE o.order_status_id = 6) AS ignored_count,
                        COUNT(*) FILTER (WHERE o.order_status_id IN (9,10)) AS return_exchange_count
                    FROM orders o WHERE o.client_id = c.id
                ) stats ON true
            ) rated
        ) categorized
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


# ── Products statistics ──────────────────────────────────────────────────────
@router.get("/api/statistics/products")
def get_products_stats(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Top selling products, top brands, type/channel distribution, inventory summary.

    "Sold" everywhere = CONFIRMED_SOLD (Підтверджено + Подарунок).
    "Revenue" = реалізований виторг (Підтверджено AND Оплачено).
    """
    logger.info("Fetching product statistics")

    top_products = db.execute(text(f"""
        SELECT p.productnumber, p.model,
               b.brandname AS brand,
               t.typename AS type,
               COUNT(oi.id) FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}) AS sold_count,
               COALESCE(SUM(oi.price * oi.quantity)
                        FILTER (WHERE {PAID_REVENUE}), 0)::float AS revenue
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        LEFT JOIN brands b ON b.id = p.brandid
        LEFT JOIN types t ON t.id = p.typeid
        JOIN orders o ON o.id = oi.order_id
        WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}
        GROUP BY p.productnumber, p.model, b.brandname, t.typename
        ORDER BY sold_count DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    top_brands = db.execute(text(f"""
        SELECT b.brandname AS brand,
               COUNT(DISTINCT o.id) AS orders_count,
               COUNT(oi.id) FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}) AS sold_count,
               COALESCE(SUM(oi.price * oi.quantity)
                        FILTER (WHERE {PAID_REVENUE}), 0)::float AS revenue
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        JOIN brands b ON b.id = p.brandid
        JOIN orders o ON o.id = oi.order_id
        WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}
          AND b.brandname IS NOT NULL
        GROUP BY b.brandname
        ORDER BY revenue DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    type_dist = db.execute(text(f"""
        SELECT t.typename AS type,
               COUNT(oi.id) FILTER (WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}) AS sold_count,
               COALESCE(SUM(oi.price * oi.quantity)
                        FILTER (WHERE {PAID_REVENUE}), 0)::float AS revenue
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        JOIN types t ON t.id = p.typeid
        JOIN orders o ON o.id = oi.order_id
        WHERE o.order_status_id IN {CONFIRMED_SOLD_SQL}
          AND t.typename IS NOT NULL
        GROUP BY t.typename
        ORDER BY sold_count DESC
        LIMIT 10
    """)).fetchall()

    channel_expr = _effective_sales_channel("o")
    channel_rows = db.execute(text(f"""
        SELECT {channel_expr} AS channel,
               COUNT(DISTINCT o.id) AS orders_count,
               COALESCE(SUM(oi.price * oi.quantity)
                        FILTER (WHERE {PAID_REVENUE}), 0)::float AS revenue
        FROM orders o
        LEFT JOIN order_items oi ON oi.order_id = o.id
        WHERE {REAL_ORDER_SQL}
          AND o.order_status_id NOT IN {CANCELLED_OR_RET_SQL}
        GROUP BY 1
        ORDER BY orders_count DESC
    """)).mappings().all()

    total_advertising_cost = db.execute(text("""
        SELECT COALESCE(SUM(amount), 0)::float
        FROM advertising_expenses
        WHERE sales_channel = 'Ефір'
    """)).scalar() or 0
    channel_dist = []
    for row in channel_rows:
        item = dict(row)
        advertising_cost = total_advertising_cost if item["channel"] == "Ефір" else 0
        item["advertising_cost"] = round(advertising_cost, 2)
        item["net_revenue"] = round((item["revenue"] or 0) - advertising_cost, 2)
        channel_dist.append(item)

    # Inventory summary: total / fully sold / partially sold / available / rostovkas.
    # "fully_sold" uses confirmed-sold units ≥ quantity (so multi-unit products
    # aren't marked sold until all units leave).
    inventory_summary = db.execute(text(f"""
        WITH s AS (
            SELECT oi.product_id, COUNT(*) AS sold_count
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id AND o.order_status_id IN {CONFIRMED_SOLD_SQL}
            GROUP BY oi.product_id
        )
        SELECT
            COUNT(*) AS total_products,
            COALESCE(SUM(p.quantity), 0) AS total_units,
            COUNT(*) FILTER (WHERE COALESCE(s.sold_count, 0) >= COALESCE(NULLIF(p.quantity, 0), 1)) AS fully_sold,
            COUNT(*) FILTER (WHERE COALESCE(s.sold_count, 0) = 0) AS fully_available,
            COUNT(*) FILTER (WHERE COALESCE(s.sold_count, 0) > 0
                              AND COALESCE(s.sold_count, 0) < COALESCE(NULLIF(p.quantity, 0), 1)) AS partially_sold,
            COUNT(*) FILTER (WHERE p.quantity > 1) AS rostovkas
        FROM products p
        LEFT JOIN s ON s.product_id = p.id
    """)).fetchone()

    return {
        "top_products": [dict(r._mapping) for r in top_products],
        "top_brands": [dict(r._mapping) for r in top_brands],
        "type_distribution": [dict(r._mapping) for r in type_dist],
        "channel_distribution": channel_dist,
        "inventory_summary": dict(inventory_summary._mapping) if inventory_summary else {},
    }


@router.get("/api/statistics/advertising")
def advertising_stats(
    period: str = Query("month", pattern="^(month|quarter|year|total)$"),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Реклама: скільки, з чого складається і що з того вийшло.

    Два джерела, і вони НЕ дублюються, а доповнюють одне одного:

    * `advertising_expenses` — УСЯ реклама ефіру. Дзеркало комірки «Витрати на
      рекламу» в аркуші «Замовлення»; там сидить і Meta, і все інше
      (Telegram, блогери), про що знає лише власник.
    * `meta_ad_charges` — списання Meta з картки, точні до копійки з виписки
      банку.

    Звідси «інша реклама» = всього − Meta. Вона може вийти відʼємною, і це не
    помилка даних, а чесний сигнал: у комірку ще не дописали свіже списання
    Meta. Тому не обрізаємо в нуль — інакше розбіжність стала б невидимою.
    """
    if period == "month":
        total_expr = "TO_CHAR(e.expense_date, 'YYYY-MM')"
        meta_expr = "TO_CHAR(c.air_date, 'YYYY-MM')"
    elif period == "quarter":
        total_expr = "TO_CHAR(e.expense_date, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM e.expense_date)::int"
        meta_expr = "TO_CHAR(c.air_date, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM c.air_date)::int"
    else:
        total_expr = "TO_CHAR(e.expense_date, 'YYYY')"
        meta_expr = "TO_CHAR(c.air_date, 'YYYY')"

    year_filter_e = "AND EXTRACT(YEAR FROM e.expense_date) = :year" if year else ""
    year_filter_c = "AND EXTRACT(YEAR FROM c.air_date) = :year" if year else ""
    params: Dict[str, Any] = {"year": year} if year else {}

    total_rows = db.execute(text(f"""
        SELECT {total_expr} AS period_label,
               COALESCE(SUM(e.amount), 0)::float AS total_cost
        FROM advertising_expenses e
        WHERE e.sales_channel = 'Ефір' AND e.expense_date IS NOT NULL {year_filter_e}
        GROUP BY 1 ORDER BY 1
    """), params).mappings().all()

    # Meta групуємо за ЕФІРОМ, а не за датою списання: у комірку сума лягла
    # саме до того ефіру, тож інакше два ряди не зійшлися б по періодах.
    meta_rows = db.execute(text(f"""
        SELECT {meta_expr} AS period_label,
               COALESCE(SUM(c.amount_uah), 0)::float AS meta_cost,
               COUNT(*)::int AS meta_charges
        FROM meta_ad_charges c
        WHERE c.air_date IS NOT NULL {year_filter_c}
        GROUP BY 1 ORDER BY 1
    """), params).mappings().all()

    # Виторг і замовлення — щоб показати ціну реклами, а не лише її суму.
    sales_expr = total_expr.replace("e.expense_date", "o.order_date")
    year_filter_o = "AND EXTRACT(YEAR FROM o.order_date) = :year" if year else ""
    sales_rows = db.execute(text(f"""
        SELECT {sales_expr} AS period_label,
               COALESCE(SUM(oi.price * oi.quantity), 0)::float AS revenue,
               COUNT(DISTINCT o.id)::int AS orders
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE o.sales_channel = 'Ефір' AND o.order_date IS NOT NULL
          AND o.order_status_id IN (1, 7) {year_filter_o}
        GROUP BY 1 ORDER BY 1
    """), params).mappings().all()

    total_map = {r["period_label"]: r for r in total_rows}
    meta_map = {r["period_label"]: r for r in meta_rows}
    sales_map = {r["period_label"]: r for r in sales_rows}

    data = []
    for label in sorted(set(total_map) | set(meta_map)):
        total_cost = float(total_map.get(label, {}).get("total_cost", 0) or 0)
        meta_cost = float(meta_map.get(label, {}).get("meta_cost", 0) or 0)
        revenue = float(sales_map.get(label, {}).get("revenue", 0) or 0)
        orders = int(sales_map.get(label, {}).get("orders", 0) or 0)
        data.append({
            "period": label,
            "total_cost": round(total_cost, 2),
            "meta_cost": round(meta_cost, 2),
            "other_cost": round(total_cost - meta_cost, 2),
            "meta_charges": int(meta_map.get(label, {}).get("meta_charges", 0) or 0),
            "revenue": round(revenue, 2),
            "orders": orders,
            "share_of_revenue": round(total_cost / revenue * 100, 2) if revenue else None,
            "cost_per_order": round(total_cost / orders, 2) if orders else None,
        })

    charges = db.execute(text(f"""
        SELECT c.charge_date, c.amount_uah::float AS amount_uah,
               c.operation_amount::float AS amount_orig, c.operation_currency,
               c.air_date, c.receipt_id, c.write_status, c.description,
               RIGHT(COALESCE(s.masked_pan, ''), 4) AS card
        FROM meta_ad_charges c
        LEFT JOIN mono_sync_state s ON s.account_id = c.bank_account_id
        WHERE TRUE {year_filter_c.replace('c.air_date', 'COALESCE(c.air_date, c.charge_date)')}
        ORDER BY c.charge_date DESC
        LIMIT 300
    """), params).mappings().all()

    totals = db.execute(text("""
        SELECT
          (SELECT COALESCE(SUM(amount), 0)::float FROM advertising_expenses
            WHERE sales_channel = 'Ефір') AS total_all,
          (SELECT COALESCE(SUM(amount_uah), 0)::float FROM meta_ad_charges) AS meta_all,
          (SELECT COUNT(*)::int FROM meta_ad_charges) AS meta_count,
          (SELECT MIN(charge_date) FROM meta_ad_charges) AS meta_from,
          (SELECT MAX(charge_date) FROM meta_ad_charges) AS meta_to,
          (SELECT COUNT(*)::int FROM meta_ad_charges WHERE air_date IS NULL) AS waiting_air
    """)).mappings().first()

    return {
        "period_type": period,
        "data": data,
        "charges": [dict(r) for r in charges],
        "totals": dict(totals) if totals else {},
    }

"""Read-only Top-9 selection for future automated social collections.

This phase only creates a deterministic preview draft. It never writes a schedule,
uploads media or calls Viber/Facebook. Publication-time revalidation will be added
with the scheduler in a separate guarded phase.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from backend.utils.order_status_logic import (
        PAID_STATUS_ID, STATUS_CONFIRMED, STATUS_GIFT, STATUS_RETURNED,
    )
except ImportError:
    from utils.order_status_logic import (
        PAID_STATUS_ID, STATUS_CONFIRMED, STATUS_GIFT, STATUS_RETURNED,
    )

DEFAULT_COUNT = 9
DEFAULT_PERIOD_DAYS = 30
DEFAULT_COOLDOWN_DAYS = 14
MAX_POOL = 120

# Transparent weights shared with the catalog statistics ranking.
WEIGHTS = {
    "unique_viewers": 1,
    "active_favorites": 3,
    "favorite_adds": 4,
    "contact_clicks": 8,
    "sold_count": 12,
}


def normalize_number(value: Any) -> str:
    return str(value or "").strip().lstrip("#").casefold()


def score_candidate(candidate: Dict[str, Any]) -> int:
    return sum(int(candidate.get(key) or 0) * weight for key, weight in WEIGHTS.items())


def rank_candidates(
    candidates: Sequence[Dict[str, Any]],
    blocked_numbers: Iterable[str],
    *,
    count: int = DEFAULT_COUNT,
    reserve_count: int = DEFAULT_COUNT,
) -> Dict[str, List[Dict[str, Any]]]:
    """Pure deterministic ranking: cooldown first, then score and stable ties."""
    blocked = {normalize_number(value) for value in blocked_numbers if normalize_number(value)}
    ready: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for raw in candidates:
        item = dict(raw)
        item["popularity_score"] = score_candidate(item)
        if normalize_number(item.get("productnumber")) in blocked:
            item["excluded_reason"] = "cooldown"
            skipped.append(item)
        else:
            ready.append(item)
    ready.sort(key=lambda row: (
        -int(row.get("popularity_score") or 0),
        -int(row.get("unique_viewers") or 0),
        -int(row.get("active_favorites") or 0),
        -int(row.get("sold_count") or 0),
        str(row.get("productnumber") or "").casefold(),
        int(row.get("product_id") or 0),
    ))
    take = max(1, min(int(count), DEFAULT_COUNT))
    # The preview normally exposes only ``count`` reserves, but the first
    # internal pass may deliberately inspect the whole pool for photos.  Do
    # not cap that safety scan at 18 candidates: a long run of missing images
    # must still fall through to the next valid products.
    reserve_take = max(0, min(int(reserve_count), len(ready)))
    return {
        "selected": ready[:take],
        "reserves": ready[take:take + reserve_take],
        "cooldown_skipped": skipped,
    }


def _collection_service():
    try:
        from backend.services import collection_collage
    except ImportError:
        from services import collection_collage
    return collection_collage


def _period_clause(days: int, column: str) -> str:
    return "TRUE" if days == 0 else f"{column} >= now() - (:period_days || ' days')::interval"


def _candidate_rows(db: Session, period_days: int) -> List[Dict[str, Any]]:
    event_period = _period_clause(period_days, "ce.received_at")
    sales_period = _period_clause(period_days, "o.order_date")
    params = {"period_days": period_days, "pool": MAX_POOL}
    rows = db.execute(text(f"""
        WITH sold_per_client AS (
            SELECT oi.product_id, o.client_id,
                   COALESCE(SUM(oi.quantity) FILTER (
                       WHERE o.order_status_id={STATUS_GIFT}
                          OR (o.order_status_id={STATUS_CONFIRMED}
                              AND o.payment_status_id={PAID_STATUS_ID})
                   ),0) AS paid_sold,
                   COALESCE(SUM(oi.quantity) FILTER (
                       WHERE o.order_status_id={STATUS_RETURNED}
                   ),0) AS returns
            FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id IN ({STATUS_CONFIRMED}, {STATUS_GIFT}, {STATUS_RETURNED})
            GROUP BY oi.product_id, o.client_id
        ), sold AS (
            SELECT product_id,
                   GREATEST(SUM(paid_sold)-SUM(LEAST(paid_sold, returns)), 0)::int AS sold_count
            FROM sold_per_client GROUP BY product_id
        ), eligible AS (
            SELECT p.productnumber, MIN(p.id)::int AS product_id,
                   MAX(b.brandname) AS brand, MAX(p.model) AS model, MAX(t.typename) AS type,
                   MIN(p.price)::float AS price, MAX(p.dateadded) AS dateadded,
                   SUM(GREATEST(COALESCE(p.quantity,0)-COALESCE(sold.sold_count,0),0))::int AS available
            FROM products p
            LEFT JOIN brands b ON b.id=p.brandid
            LEFT JOIN types t ON t.id=p.typeid
            LEFT JOIN statuses s ON s.id=p.statusid
            LEFT JOIN sold ON sold.product_id=p.id
            JOIN catalog_listings cl ON cl.productnumber=p.productnumber AND cl.is_published=TRUE
            WHERE GREATEST(COALESCE(p.quantity,0)-COALESCE(sold.sold_count,0),0) > 0
              AND (s.statusname IS NULL OR s.statusname NOT IN ('Продано','Подаровано','Повернуто'))
              AND COALESCE(p.is_lost,FALSE)=FALSE
              AND p.productnumber IS NOT NULL AND BTRIM(p.productnumber) <> ''
              AND p.productnumber <> '???'
              AND p.productnumber NOT LIKE '???\\_%'
              AND p.productnumber NOT LIKE '\\_\\_tmp\\_rename\\_%'
              AND COALESCE(p.price,0) > 0
            GROUP BY p.productnumber
        ), engagement AS (
            SELECT ce.productnumber,
                   COUNT(*) FILTER (WHERE ce.event_type='product_view')::int AS views,
                   COUNT(DISTINCT ce.visitor_key) FILTER (WHERE ce.event_type='product_view')::int AS unique_viewers,
                   COUNT(*) FILTER (WHERE ce.event_type='favorite_add')::int AS favorite_adds,
                   COUNT(*) FILTER (WHERE ce.event_type='contact_click')::int AS contact_clicks
            FROM catalog_events ce WHERE ce.productnumber IS NOT NULL AND {event_period}
            GROUP BY ce.productnumber
        ), period_sales_client AS (
            SELECT p.productnumber, o.client_id,
                   COALESCE(SUM(oi.quantity) FILTER (
                       WHERE o.order_status_id={STATUS_GIFT}
                          OR (o.order_status_id={STATUS_CONFIRMED}
                              AND o.payment_status_id={PAID_STATUS_ID})
                   ),0) AS paid_sold,
                   COALESCE(SUM(oi.quantity) FILTER (
                       WHERE o.order_status_id={STATUS_RETURNED}
                   ),0) AS returns
            FROM order_items oi
            JOIN orders o ON o.id=oi.order_id
            JOIN products p ON p.id=oi.product_id
            WHERE p.productnumber IS NOT NULL
              AND o.order_status_id IN ({STATUS_CONFIRMED}, {STATUS_GIFT}, {STATUS_RETURNED})
              AND {sales_period}
            GROUP BY p.productnumber, o.client_id
        ), period_sales AS (
            SELECT productnumber,
                   GREATEST(SUM(paid_sold)-SUM(LEAST(paid_sold,returns)),0)::int AS sold_count
            FROM period_sales_client GROUP BY productnumber
        )
        SELECT e.*,
               COALESCE(g.views,0)::int AS views,
               COALESCE(g.unique_viewers,0)::int AS unique_viewers,
               COALESCE(snap.active_favorites,0)::int AS active_favorites,
               COALESCE(g.favorite_adds,0)::int AS favorite_adds,
               COALESCE(g.contact_clicks,0)::int AS contact_clicks,
               COALESCE(ps.sold_count,0)::int AS sold_count
        FROM eligible e
        LEFT JOIN engagement g ON g.productnumber=e.productnumber
        LEFT JOIN catalog_analytics_product_snapshot snap ON snap.productnumber=e.productnumber
        LEFT JOIN period_sales ps ON ps.productnumber=e.productnumber
        ORDER BY (
            COALESCE(g.unique_viewers,0) * {WEIGHTS['unique_viewers']} +
            COALESCE(snap.active_favorites,0) * {WEIGHTS['active_favorites']} +
            COALESCE(g.favorite_adds,0) * {WEIGHTS['favorite_adds']} +
            COALESCE(g.contact_clicks,0) * {WEIGHTS['contact_clicks']} +
            COALESCE(ps.sold_count,0) * {WEIGHTS['sold_count']}
        ) DESC, e.dateadded DESC NULLS LAST, e.productnumber
        LIMIT :pool
    """), params).mappings().all()
    return [dict(row) for row in rows]


def _cooldown_numbers(db: Session, cooldown_days: int) -> List[str]:
    rows = db.execute(text("""
        SELECT DISTINCT value
        FROM social_collection_posts sc
        CROSS JOIN LATERAL jsonb_array_elements_text(sc.product_numbers) AS value
        WHERE sc.status NOT IN ('failed','error','cancelled')
          AND COALESCE(sc.published_at,sc.scheduled_at,sc.created_at)
              >= now() - (:cooldown_days || ' days')::interval
    """), {"cooldown_days": cooldown_days}).scalars().all()
    return [str(row) for row in rows]


def create_preview_draft(
    db: Session,
    *,
    platform: str,
    count: int = DEFAULT_COUNT,
    period_days: int = DEFAULT_PERIOD_DAYS,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
) -> Dict[str, Any]:
    collection = _collection_service()
    config = collection.platform_config(platform)
    count = max(2, min(int(count), DEFAULT_COUNT))
    period_days = int(period_days)
    cooldown_days = max(DEFAULT_COOLDOWN_DAYS, min(int(cooldown_days), 90))
    if period_days not in (0, 7, 30, 90):
        raise ValueError("Період рейтингу має бути 7, 30, 90 днів або весь чистий період")

    raw = _candidate_rows(db, period_days)
    cooldown = _cooldown_numbers(db, cooldown_days)
    first_pass = rank_candidates(raw, cooldown, count=DEFAULT_COUNT, reserve_count=MAX_POOL)

    # Check actual source photos before final selection. This is read-only and uses
    # the same cached image discovery as the existing manual collection editor.
    unblocked = [*first_pass["selected"], *first_pass["reserves"]]
    photo_ready: List[Dict[str, Any]] = []
    no_photo: List[Dict[str, Any]] = []
    target_with_reserves = count * 2
    # Usually this checks only the first 18 candidates. If several lack a
    # usable source image, continue in bounded batches until both the Top list
    # and its reserve are full (or the candidate pool is exhausted).
    photo_batch_size = DEFAULT_COUNT * 2
    for offset in range(0, len(unblocked), photo_batch_size):
        batch = unblocked[offset:offset + photo_batch_size]
        loaded, _missing = collection._load_items(db, [row["product_id"] for row in batch])
        photo_ids = {
            int(row["product_id"])
            for row in loaded
            if int(row.get("image_count") or 0) > 0
        }
        photo_ready.extend(row for row in batch if int(row["product_id"]) in photo_ids)
        no_photo.extend(row for row in batch if int(row["product_id"]) not in photo_ids)
        if len(photo_ready) >= target_with_reserves:
            break
    ranked = rank_candidates(photo_ready, [], count=count, reserve_count=count)

    selected = ranked["selected"]
    reserves = ranked["reserves"]
    warnings: List[str] = []
    if len(selected) < count:
        warnings.append(f"Знайдено лише {len(selected)} із {count} безпечних товарів із фото.")
    if first_pass["cooldown_skipped"]:
        warnings.append(
            f"Через захист від повторів пропущено {len(first_pass['cooldown_skipped'])} товарів."
        )
    if no_photo:
        warnings.append(f"Без придатного фото пропущено {len(no_photo)} товарів.")

    for position, item in enumerate(selected, 1):
        item["position"] = position
    for position, item in enumerate(reserves, 1):
        item["reserve_position"] = position

    signature = "|".join([
        config["key"], str(period_days), str(cooldown_days),
        *(normalize_number(row["productnumber"]) for row in selected),
    ])
    return {
        "ok": len(selected) >= 2,
        "mode": "preview_only",
        "platform": config["key"],
        "platform_label": config["label"],
        "product_ids": [int(row["product_id"]) for row in selected],
        "selected": selected,
        "reserves": reserves,
        "warnings": warnings,
        "policy": {
            "count": count,
            "period_days": period_days,
            "cooldown_days": cooldown_days,
            "weights": WEIGHTS,
            "requires_available_stock": True,
            "requires_catalog_publication": True,
            "requires_photo": True,
            "revalidate_before_publish": True,
        },
        "audit": {
            "eligible_pool": len(raw),
            "cooldown_skipped": len(first_pass["cooldown_skipped"]),
            "no_photo_skipped": len(no_photo),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "selection_key": hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24],
        },
    }

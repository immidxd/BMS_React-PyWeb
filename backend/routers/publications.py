"""
Publications router — manages cross-channel publications (Telegram, future: Instagram, etc.)

PHASE 1 (READ-ONLY): Scan Telegram, view publication status, no writes.
"""

import os
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from dotenv import load_dotenv

# Ensure .env is loaded regardless of working directory
_ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env'))
load_dotenv(_ENV_PATH, override=False)

try:
    from models.database import get_db
    from utils.order_status_logic import (
        latest_order_confirmed_sold as _latest_order_confirmed_sold,
        latest_order_reserved as _latest_order_reserved,
        product_fully_consumed as _product_fully_consumed,
        CONFIRMED_SOLD as _CONFIRMED_SOLD_STATUS_IDS,
        PAID_STATUS_ID as _PAID_STATUS_ID,
        STATUS_CONFIRMED as _STATUS_CONFIRMED,
        STATUS_GIFT as _STATUS_GIFT,
        STATUS_RETURNED as _STATUS_RETURNED,
        sql_in_list as _sql_in_list,
    )
except ImportError:
    from backend.models.database import get_db
    from backend.utils.order_status_logic import (
        latest_order_confirmed_sold as _latest_order_confirmed_sold,
        latest_order_reserved as _latest_order_reserved,
        product_fully_consumed as _product_fully_consumed,
        CONFIRMED_SOLD as _CONFIRMED_SOLD_STATUS_IDS,
        PAID_STATUS_ID as _PAID_STATUS_ID,
        STATUS_CONFIRMED as _STATUS_CONFIRMED,
        STATUS_GIFT as _STATUS_GIFT,
        STATUS_RETURNED as _STATUS_RETURNED,
        sql_in_list as _sql_in_list,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


# Order-status semantics live in utils/order_status_logic.py — see imports above.
# Back-compat alias for older call sites that meant "confirmed sold".
_latest_order_sold = _latest_order_confirmed_sold


def _sold_units_join(pid_ref: str) -> str:
    """LATERAL-джойн фактично вибулих одиниць — формула з «Товарів».

    Підтверджене замовлення споживає сток лише після оплати, подарунок — завжди,
    а повернення того самого клієнта кредитує одиницю назад. Тримати цей фільтр
    синхронним критично: «Тільки непродані» в обох вкладках має показувати один
    і той самий фізичний залишок.
    """
    return f"""LEFT JOIN LATERAL (
        SELECT GREATEST(
            COALESCE(SUM(per_client.paid_sold), 0)
            - COALESCE(SUM(LEAST(per_client.paid_sold, per_client.returns)), 0),
            0
        ) AS sold_count
        FROM (
            SELECT
                COUNT(*) FILTER (
                    WHERE o.order_status_id = {_STATUS_GIFT}
                       OR (o.order_status_id = {_STATUS_CONFIRMED}
                           AND o.payment_status_id = {_PAID_STATUS_ID})
                ) AS paid_sold,
                COUNT(*) FILTER (WHERE o.order_status_id = {_STATUS_RETURNED}) AS returns
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id = {pid_ref}
              AND o.order_status_id IN ({_STATUS_CONFIRMED}, {_STATUS_GIFT}, {_STATUS_RETURNED})
            GROUP BY o.client_id
        ) per_client
    ) sold_filter ON true"""


# ─────────────────────────────────────────────────────────────────────────────
# Auto-relink SQL — used by /sync, /sync-all, /relink, and startup auto-refresh.
#
# Matches each telegram_post to the single best product, in priority order:
#   1. Size match — if the post advertises a size and one product variant has
#      that size, prefer it (deterministic on multi-size product numbers).
#   2. Productnumber form — prefer canonical `#Ф{n}` over `Ф{n}` / `#{n}` / `{n}`.
#   3. Lowest p.id — deterministic tiebreaker when everything else ties.
#
# Without (1) and (3), DISTINCT ON would pick arbitrarily among products that
# share productnumber (e.g. two rows of `#Ф3009` with sizes 41 and 44), so on
# successive re-syncs the same post could flip between products → "two rows"
# in the publications UI for one product number.
_RELINK_SQL = """
WITH best AS (
    SELECT DISTINCT ON (tp.id)
        tp.id AS tp_id,
        p.id  AS p_id
    FROM telegram_posts tp
    JOIN products p ON p.productnumber IN (
        tp.product_number_raw,
        'Ф'  || tp.product_number_raw,
        '#Ф' || tp.product_number_raw,
        '#'  || tp.product_number_raw
    )
    ORDER BY tp.id,
        -- Stage 1: post-size matches the product's sizeeu (most specific signal)
        CASE
            WHEN tp.sizes_in_post IS NOT NULL
             AND tp.sizes_in_post <> ''
             AND tp.sizes_in_post <> '[]'
             AND EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(tp.sizes_in_post::jsonb) AS sz(v)
                WHERE sz.v = COALESCE(p.sizeeu, '')
                   OR sz.v = split_part(COALESCE(p.sizeeu, ''), '.', 1)
             )
            THEN 0 ELSE 1
        END,
        -- Stage 2: productnumber form priority
        CASE p.productnumber
            WHEN '#Ф' || tp.product_number_raw THEN 1
            WHEN 'Ф'  || tp.product_number_raw THEN 2
            WHEN '#'  || tp.product_number_raw THEN 3
            WHEN tp.product_number_raw         THEN 4
            ELSE 5
        END,
        -- Stage 3: deterministic tiebreaker — oldest product wins
        p.id
)
UPDATE telegram_posts tp
SET product_id = best.p_id
FROM best
WHERE tp.id = best.tp_id
  AND (tp.product_id IS NULL OR tp.product_id <> best.p_id)
"""


# ─────────────────────────────────────────────────────────────────────────────
# Read-only endpoints (safe — no writes to Telegram)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/publications/overview")
def get_publications_overview(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    filter_mode: Optional[str] = Query(None, description="all|published|problematic|unpublished|unlinked"),
    search: Optional[str] = Query(None),
    only_unsold: bool = Query(True, description="Only products with physical stock remaining"),
    only_rostovka: bool = Query(False, description="Only size runs / multi-unit products"),
    db: Session = Depends(get_db),
):
    """Overview of all products + their Telegram/Viber/Instagram status.

    Filter modes:
      - all: all products
      - published: products with at least 1 live Telegram, Viber or Instagram post
      - problematic: SOLD products that still have live posts
      - unpublished: products NOT in any channel
      - unlinked: telegram_posts with no matching product (separate query)
    """
    try:
        offset = (page - 1) * per_page

        # Special mode: unlinked posts (no product_id)
        if filter_mode == "unlinked":
            search_clause = ""
            params: Dict[str, Any] = {"limit": per_page, "offset": offset}
            if search:
                search_clause = "AND tp.product_number_raw ILIKE :search"
                params["search"] = f"%{search}%"

            total_row = db.execute(
                text(f"SELECT COUNT(*) FROM telegram_posts tp WHERE tp.product_id IS NULL AND tp.tg_status = 'published' {search_clause}"),
                {k: v for k, v in params.items() if k not in ('limit', 'offset')}
            ).fetchone()
            total = total_row[0] if total_row else 0

            rows = db.execute(
                text(f"""
                    SELECT
                        tp.id,
                        tp.product_number_raw,
                        tp.chat_title,
                        COALESCE(tp.thread_title, '') AS thread_title,
                        tp.message_date,
                        COUNT(*) OVER (PARTITION BY tp.product_number_raw) AS post_count
                    FROM telegram_posts tp
                    WHERE tp.product_id IS NULL AND tp.tg_status = 'published' {search_clause}
                    GROUP BY tp.id, tp.product_number_raw, tp.chat_title, tp.thread_title, tp.message_date
                    ORDER BY tp.product_number_raw, tp.message_date DESC
                    LIMIT :limit OFFSET :offset
                """),
                params
            ).fetchall()

            items = []
            seen_raw = set()
            for row in rows:
                raw_num = row[1]
                if raw_num in seen_raw:
                    continue
                seen_raw.add(raw_num)
                items.append({
                    "product_id": None,
                    "productnumber": (raw_num or "").lstrip('#'),
                    "model": None,
                    "price": None,
                    "status": "Проблемний",
                    "publication_count": int(row[5]),
                    "channels": row[2] or "",
                    "threads": row[3] or "",
                    "is_unlinked": True,
                })

            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
            }

        # Normal product-centric mode
        where_parts = []
        params = {"limit": per_page, "offset": offset}
        # «Продані, але висять» навмисно показує продані товари для очищення
        # Telegram. У цьому єдиному режимі базовий фільтр наявності не діє.
        apply_only_unsold = only_unsold and filter_mode not in ("problematic", "sold_live")
        sold_filter_join = _sold_units_join("p.id")
        sold_filter_count_join = sold_filter_join if apply_only_unsold else ""

        if search:
            where_parts.append("(p.productnumber ILIKE :search OR p.model ILIKE :search)")
            params["search"] = f"%{search}%"

        if apply_only_unsold:
            # Ідентично вкладці «Товари»: залишок quantity мінус фактичні
            # оплачені продажі/подарунки з урахуванням повернень. Старий знімок
            # status='Продано' не приховує повернений товар, якщо є історія
            # замовлень і фізичний залишок знову додатний.
            where_parts.append(f"""(
                GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold_filter.sold_count, 0), 0) > 0
                AND (
                    s.statusname IS NULL
                    OR s.statusname NOT IN ('Продано', 'Подаровано', 'Повернуто')
                    OR (
                        s.statusname IN ('Продано', 'Подаровано')
                        AND COALESCE(sold_filter.sold_count, 0) < COALESCE(NULLIF(p.quantity, 0), 1)
                        AND EXISTS (SELECT 1 FROM order_items oi_uns WHERE oi_uns.product_id = p.id)
                    )
                )
            )""")

        if only_rostovka:
            # Та сама ознака ростовки, що у вкладці «Товари».
            where_parts.append("""(
                p.quantity > 1
                OR LOWER(COALESCE(p.extranote, '')) LIKE '%ростовка%'
                OR p.productnumber ~ '^.+\\([0-9]+\\)$'
                OR EXISTS (
                    SELECT 1 FROM products p_sib
                    WHERE p_sib.productnumber = p.productnumber || '(1)'
                )
            )""")

        if filter_mode == "published":
            where_parts.append("""
                (
                    EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id AND tp.tg_status = 'published')
                    OR EXISTS (SELECT 1 FROM viber_publications vp WHERE vp.product_id = p.id AND vp.status = 'published')
                    OR EXISTS (SELECT 1 FROM instagram_publications ip WHERE ip.product_id = p.id AND ip.status = 'published')
                )
            """)
        elif filter_mode in ("problematic", "sold_live"):
            # Product row is problematic when:
            # 1. p is sold (status='Продано' OR latest order is in active/confirmed state)
            # 2. No other product row with same productnumber+sizeeu is still available
            #    (this exact size is fully out of stock)
            # 3. Has at least one published TG post linked to this product
            #
            # Note: we DO NOT cross-check sizes_in_post here — `sizes_in_post` is a
            # parser-side cache that's often stale/incomplete, and the post is linked
            # to p directly, so if p is sold and the post is live → it IS problematic
            # regardless of which sizes the post text mentions. Unlinked posts are
            # handled separately by filter_mode='unlinked'.
            where_parts.append(f"""
                (
                    p.statusid IN (SELECT id FROM statuses WHERE statusname = 'Продано')
                    OR {_product_fully_consumed('p.id')}
                )
                AND NOT EXISTS (
                    SELECT 1 FROM products p2
                    LEFT JOIN statuses s2 ON s2.id = p2.statusid
                    WHERE p2.id != p.id
                      AND TRIM(LEADING '#' FROM p2.productnumber) = TRIM(LEADING '#' FROM p.productnumber)
                      AND COALESCE(p2.sizeeu, '') = COALESCE(p.sizeeu, '')
                      AND COALESCE(s2.statusname, '') != 'Продано'
                      AND NOT {_product_fully_consumed('p2.id')}
                )
                AND EXISTS (
                    SELECT 1 FROM telegram_posts tp
                    WHERE tp.product_id = p.id AND tp.tg_status = 'published'
                )
            """)
        elif filter_mode == "unpublished":
            where_parts.append("""
                NOT EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id AND tp.tg_status = 'published')
                AND NOT EXISTS (
                    SELECT 1 FROM viber_publications vp
                    WHERE vp.product_id = p.id
                      AND vp.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
                )
                AND NOT EXISTS (
                    SELECT 1 FROM instagram_publications ip
                    WHERE ip.product_id = p.id
                      AND ip.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
                )
                AND p.statusid NOT IN (SELECT id FROM statuses WHERE statusname = 'Продано')
            """)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        total_row = db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                {sold_filter_count_join}
                WHERE {where_clause}
            """),
            {k: v for k, v in params.items() if k not in ('limit', 'offset')}
        ).fetchone()
        total = total_row[0] if total_row else 0

        rows = db.execute(
            text(f"""
                SELECT
                    p.id, p.productnumber, p.model, p.price,
                    CASE
                        WHEN COALESCE(sold_filter.sold_count, 0) >= COALESCE(NULLIF(p.quantity, 0), 1)
                            THEN 'Продано'
                        WHEN COALESCE(sold_filter.sold_count, 0) > 0
                            THEN 'Частково продано'
                        WHEN s.statusname IN ('Продано', 'Подаровано')
                         AND EXISTS (SELECT 1 FROM order_items oi_status WHERE oi_status.product_id = p.id)
                            THEN 'Непродано'
                        ELSE COALESCE(s.statusname, 'Невідомо')
                    END AS status,
                    COALESCE(pubs.pub_count, 0) AS pub_count,
                    COALESCE(pubs.channels, '') AS channels,
                    COALESCE(pubs.threads, '') AS threads,
                    COALESCE(pubs.needs_manual_edit, false) AS needs_manual_edit,
                    COALESCE(pubs.telegram_count, 0) AS telegram_count,
                    COALESCE(pubs.viber_count, 0) AS viber_count,
                    COALESCE(pubs.viber_pending_count, 0) AS viber_pending_count,
                    COALESCE(pubs.instagram_count, 0) AS instagram_count,
                    COALESCE(pubs.instagram_pending_count, 0) AS instagram_pending_count,
                    b.brandname AS brand_name,
                    t.typename  AS type_name,
                    st.subtypename AS subtype_name,
                    p.sizeeu, p.marking, p.year
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                {sold_filter_join}
                LEFT JOIN brands   b ON b.id = p.brandid
                LEFT JOIN types    t ON t.id = p.typeid
                LEFT JOIN subtypes st ON st.id = p.subtypeid
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) AS pub_count,
                        STRING_AGG(DISTINCT chat_title, ', ') AS channels,
                        STRING_AGG(DISTINCT COALESCE(thread_title, ''), ', ') AS threads,
                        BOOL_OR(COALESCE(needs_manual_edit, false)) AS needs_manual_edit,
                        COUNT(*) FILTER (WHERE platform = 'telegram') AS telegram_count,
                        COUNT(*) FILTER (WHERE platform = 'viber' AND publication_status = 'published') AS viber_count,
                        COUNT(*) FILTER (WHERE platform = 'viber' AND publication_status <> 'published') AS viber_pending_count,
                        COUNT(*) FILTER (WHERE platform = 'instagram' AND publication_status = 'published') AS instagram_count,
                        COUNT(*) FILTER (WHERE platform = 'instagram' AND publication_status <> 'published') AS instagram_pending_count
                    FROM (
                        SELECT tp.chat_title, tp.thread_title, tp.needs_manual_edit,
                               'telegram' AS platform, tp.tg_status AS publication_status
                        FROM telegram_posts tp
                        WHERE tp.product_id = p.id AND tp.tg_status = 'published'
                        UNION ALL
                        SELECT
                            COALESCE(vp.channel_title, 'Viber') ||
                                CASE vp.status
                                    WHEN 'scheduled' THEN ' · заплановано'
                                    WHEN 'queued' THEN ' · у черзі'
                                    WHEN 'processing' THEN ' · публікується'
                                    WHEN 'retrying' THEN ' · повторна спроба'
                                    ELSE ''
                                END AS chat_title,
                            '' AS thread_title,
                            false AS needs_manual_edit,
                            'viber' AS platform,
                            vp.status AS publication_status
                        FROM viber_publications vp
                        WHERE vp.product_id = p.id
                          AND vp.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
                        UNION ALL
                        SELECT
                            'Instagram' ||
                                CASE ip.status
                                    WHEN 'scheduled' THEN ' · заплановано'
                                    WHEN 'queued' THEN ' · у черзі'
                                    WHEN 'processing' THEN ' · публікується'
                                    WHEN 'retrying' THEN ' · повторна спроба'
                                    ELSE ''
                                END AS chat_title,
                            '' AS thread_title,
                            false AS needs_manual_edit,
                            'instagram' AS platform,
                            ip.status AS publication_status
                        FROM instagram_publications ip
                        WHERE ip.product_id = p.id
                          AND ip.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
                    ) social_posts
                ) pubs ON true
                WHERE {where_clause}
                ORDER BY pub_count DESC NULLS LAST, p.id DESC
                LIMIT :limit OFFSET :offset
            """),
            params
        ).fetchall()

        items = []
        for row in rows:
            pnum = (row[1] or "").lstrip('#')
            items.append({
                "product_id": row[0],
                "productnumber": pnum,
                "model": row[2],
                "price": float(row[3]) if row[3] else None,
                "status": row[4],
                "publication_count": row[5],
                "channels": row[6],
                "threads": row[7],
                "is_unlinked": False,
                "needs_manual_edit": bool(row[8]),
                "telegram_publication_count": int(row[9] or 0),
                "viber_publication_count": int(row[10] or 0),
                "viber_pending_count": int(row[11] or 0),
                "instagram_publication_count": int(row[12] or 0),
                "instagram_pending_count": int(row[13] or 0),
                "brand_name":   row[14],
                "type_name":    row[15],
                "subtype_name": row[16],
                "sizeeu":       row[17],
                "marking":      row[18],
                "year":         row[19],
            })

        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }
    except Exception as e:
        logger.error(f"Error fetching publications overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/publications/product/{product_id}")
def get_product_publications(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Get all publications for a specific product (across all channels)."""
    try:
        rows = db.execute(
            text("""
                SELECT
                    tp.id, tp.chat_id, tp.chat_title, tp.chat_type,
                    tp.thread_id, tp.thread_title, tp.message_id,
                    tp.message_text, tp.message_date, tp.is_master, tp.tg_status,
                    COALESCE(tp.is_multi_size, false), tp.sizes_in_post
                FROM telegram_posts tp
                WHERE tp.product_id = :pid
                   OR tp.product_number_raw = (SELECT productnumber FROM products WHERE id = :pid)
                ORDER BY tp.message_date DESC NULLS LAST
            """),
            {"pid": product_id}
        ).fetchall()

        publications = []
        for row in rows:
            publications.append({
                "id": row[0],
                "platform": "telegram",
                "chat_id": row[1],
                "chat_title": row[2],
                "chat_type": row[3],
                "thread_id": row[4],
                "thread_title": row[5],
                "message_id": row[6],
                "message_text": row[7],
                "message_date": row[8].isoformat() if row[8] else None,
                "is_master": row[9],
                "tg_status": row[10],
                "is_multi_size": row[11],
                "sizes_in_post": row[12],
            })

        viber_rows = db.execute(text("""
            SELECT id, channel_title, status, caption, scheduled_at,
                   published_at, created_at, collage_url, error
            FROM viber_publications
            WHERE product_id = :pid
               OR product_number = (SELECT productnumber FROM products WHERE id = :pid)
            ORDER BY COALESCE(published_at, scheduled_at, created_at) DESC
        """), {"pid": product_id}).mappings().all()
        for row in viber_rows:
            publications.append({
                "id": f"viber-{row['id']}",
                "platform": "viber",
                "chat_id": 0,
                "chat_title": row["channel_title"] or "Viber",
                "chat_type": "viber",
                "thread_id": None,
                "thread_title": None,
                "message_id": 0,
                "message_text": row["caption"],
                "message_date": (
                    row["published_at"] or row["scheduled_at"] or row["created_at"]
                ).isoformat() if (row["published_at"] or row["scheduled_at"] or row["created_at"]) else None,
                "is_master": True,
                "tg_status": row["status"],
                "is_multi_size": False,
                "sizes_in_post": None,
                "collage_url": row["collage_url"],
                "error": row["error"],
            })

        instagram_rows = db.execute(text("""
            SELECT id, status, media_type, caption, scheduled_at, published_at,
                   created_at, media_urls, error, payload_json->>'permalink' AS permalink
            FROM instagram_publications
            WHERE product_id = :pid
               OR product_number = (SELECT productnumber FROM products WHERE id = :pid)
            ORDER BY COALESCE(published_at, scheduled_at, created_at) DESC
        """), {"pid": product_id}).mappings().all()
        for row in instagram_rows:
            media_urls = row["media_urls"] if isinstance(row["media_urls"], list) else []
            first_media = media_urls[0].get("url") if media_urls and isinstance(media_urls[0], dict) else None
            publications.append({
                "id": f"instagram-{row['id']}",
                "local_publication_id": row["id"],
                "platform": "instagram",
                "chat_id": 0,
                "chat_title": f"Instagram · {row['media_type']}",
                "chat_type": "instagram",
                "thread_id": None,
                "thread_title": None,
                "message_id": 0,
                "message_text": row["caption"],
                "message_date": (
                    row["published_at"] or row["scheduled_at"] or row["created_at"]
                ).isoformat() if (row["published_at"] or row["scheduled_at"] or row["created_at"]) else None,
                "scheduled_at": row["scheduled_at"].isoformat() if row["scheduled_at"] else None,
                "is_master": True,
                "tg_status": row["status"],
                "is_multi_size": False,
                "sizes_in_post": None,
                "collage_url": first_media,
                "permalink": row["permalink"],
                "error": row["error"],
            })

        publications.sort(
            key=lambda item: item.get("message_date") or "", reverse=True,
        )

        return {"product_id": product_id, "publications": publications}
    except Exception as e:
        logger.error(f"Error fetching product publications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/publications/product-detail/{product_id}")
def get_product_detail_for_publication(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Get detailed info for a product in publications context:
    - All size variants (sold/available) for this product number
    - Buyer info for sold sizes (from orders)
    """
    try:
        # Get product number
        prod = db.execute(
            text("SELECT productnumber FROM products WHERE id = :pid"),
            {"pid": product_id}
        ).fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")
        prod_number = prod[0]

        # Build number variants — preserve letter prefix (Ф, Р, Н etc.)
        # #Ф3631 → variants: [#Ф3631, Ф3631] (NOT #3631 or bare 3631)
        # #3631 → variants: [#3631, 3631]
        raw = (prod_number or "").lstrip('#')  # remove only #, keep letter prefix
        variants = list({v for v in [prod_number, raw, f"#{raw}"] if v})

        # All size variants for this product number
        sizes_rows = db.execute(
            text("""
                SELECT p.id, p.sizeeu, s.statusname,
                       p.productnumber, p.model, p.price
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.productnumber = ANY(:variants)
                ORDER BY p.sizeeu
            """),
            {"variants": variants}
        ).fetchall()

        # Check which products are sold by LATEST order (not just any confirmed)
        all_pids = [row[0] for row in sizes_rows]
        order_sold_pids = set()
        if all_pids:
            # A size variant is "Продано" in the detail view only if its stock
            # is fully consumed: confirmed-sold order_items count meets or
            # exceeds the row's quantity. A multi-unit product (quantity=3)
            # with a single buyer is still partially available.
            order_rows = db.execute(
                text(f"""
                    SELECT p.id
                    FROM products p
                    WHERE p.id = ANY(:pids)
                      AND COALESCE((
                          SELECT COUNT(*) FROM order_items oi
                          JOIN orders o ON o.id = oi.order_id
                          WHERE oi.product_id = p.id
                            AND o.order_status_id IN {_sql_in_list(_CONFIRMED_SOLD_STATUS_IDS)}
                      ), 0) >= COALESCE(NULLIF(p.quantity, 0), 1)
                """),
                {"pids": all_pids}
            ).fetchall()
            order_sold_pids = {r[0] for r in order_rows}

        sizes = []
        sold_product_ids = []
        for row in sizes_rows:
            is_sold = (row[2] == 'Продано') or (row[0] in order_sold_pids)
            sizes.append({
                "product_id": row[0],
                "size": row[1],
                "status": "Продано" if is_sold else (row[2] or "—"),
                "productnumber": (row[3] or "").lstrip('#'),
                "model": row[4],
                "price": float(row[5]) if row[5] else None,
            })
            if is_sold:
                sold_product_ids.append(row[0])

        # Get buyer info for sold products
        buyers = []
        if sold_product_ids:
            buyer_rows = db.execute(
                text("""
                    SELECT oi.product_id, p.sizeeu,
                           COALESCE(NULLIF(TRIM(COALESCE(c.last_name,'') || ' ' || COALESCE(c.first_name,'')), ''), c.nickname) AS client_name,
                           c.phone_number, c.nickname,
                           o.id AS order_id, o.order_date,
                           os.status_name AS order_status
                    FROM order_items oi
                    JOIN orders o ON o.id = oi.order_id
                    JOIN products p ON p.id = oi.product_id
                    LEFT JOIN clients c ON c.id = o.client_id
                    LEFT JOIN order_statuses os ON os.id = o.order_status_id
                    WHERE oi.product_id = ANY(:pids)
                    ORDER BY o.order_date DESC
                """),
                {"pids": sold_product_ids}
            ).fetchall()
            for row in buyer_rows:
                buyers.append({
                    "product_id": row[0],
                    "size": row[1],
                    "client_name": row[2] or row[4] or "—",
                    "phone": row[3] or "—",
                    "order_id": row[5],
                    "order_date": row[6].isoformat() if row[6] else None,
                    "order_status": row[7] or "—",
                })

        return {
            "product_id": product_id,
            "productnumber": (prod_number or "").lstrip('#'),
            "sizes": sizes,
            "buyers": buyers,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching product detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/publications/stats")
def get_publications_stats(db: Session = Depends(get_db)):
    """Aggregated stats across all publications."""
    try:
        stats = db.execute(text("""
            SELECT
                COUNT(DISTINCT chat_id) AS total_chats,
                COUNT(DISTINCT product_id) AS published_products,
                COUNT(*) AS total_posts,
                COUNT(*) FILTER (WHERE chat_type = 'channel') AS channel_posts,
                COUNT(*) FILTER (WHERE chat_type = 'forum') AS forum_posts,
                COUNT(*) FILTER (WHERE chat_type = 'archive') AS archive_posts,
                COUNT(DISTINCT product_id) FILTER (WHERE chat_type = 'forum') AS forum_products,
                COUNT(DISTINCT product_id) FILTER (WHERE chat_type = 'channel') AS channel_products
            FROM telegram_posts
            WHERE tg_status = 'published'
        """)).fetchone()

        viber = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'published') AS published_posts,
                COUNT(DISTINCT product_id) FILTER (WHERE status = 'published') AS published_products,
                COUNT(*) FILTER (WHERE status IN ('queued', 'scheduled', 'processing', 'retrying')) AS pending_posts,
                COUNT(DISTINCT channel_title) FILTER (WHERE status = 'published') AS channel_count
            FROM viber_publications
        """)).fetchone()

        instagram = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'published') AS published_posts,
                COUNT(DISTINCT product_id) FILTER (WHERE status = 'published') AS published_products,
                COUNT(*) FILTER (WHERE status IN ('queued', 'scheduled', 'processing', 'retrying')) AS pending_posts
            FROM instagram_publications
        """)).fetchone()

        all_published_products = db.execute(text("""
            SELECT COUNT(DISTINCT product_id)
            FROM (
                SELECT product_id FROM telegram_posts
                WHERE tg_status = 'published' AND product_id IS NOT NULL
                UNION ALL
                SELECT product_id FROM viber_publications
                WHERE status = 'published' AND product_id IS NOT NULL
                UNION ALL
                SELECT product_id FROM instagram_publications
                WHERE status = 'published' AND product_id IS NOT NULL
            ) social_products
        """)).scalar() or 0

        # Sold-but-live count is currently the actionable Telegram cleanup
        # queue. Viber Channels API has no delete endpoint; do not imply that
        # the existing cleanup button can remove a Viber post.
        sold_live = db.execute(text("""
            SELECT COUNT(DISTINCT p.id)
            FROM products p
            JOIN telegram_posts tp ON tp.product_id = p.id
            WHERE p.statusid IN (SELECT id FROM statuses WHERE statusname = 'Продано')
              AND tp.tg_status = 'published'
        """)).fetchone()

        # Unlinked posts count (no matching product, only published)
        unlinked = db.execute(text("""
            SELECT COUNT(DISTINCT product_number_raw)
            FROM telegram_posts
            WHERE product_id IS NULL AND tg_status = 'published'
        """)).fetchone()

        # Channels breakdown (only published)
        channels = db.execute(text("""
            SELECT chat_title, chat_type, SUM(post_count) AS post_count,
                   SUM(unique_products) AS unique_products
            FROM (
                SELECT chat_title, chat_type, COUNT(*) AS post_count,
                       COUNT(DISTINCT product_id) AS unique_products
                FROM telegram_posts
                WHERE tg_status = 'published'
                GROUP BY chat_title, chat_type
                UNION ALL
                SELECT COALESCE(channel_title, 'Viber'), 'viber', COUNT(*),
                       COUNT(DISTINCT product_id)
                FROM viber_publications
                WHERE status = 'published'
                GROUP BY channel_title
                UNION ALL
                SELECT 'Instagram', 'instagram', COUNT(*),
                       COUNT(DISTINCT product_id)
                FROM instagram_publications
                WHERE status = 'published'
            ) social_channels
            GROUP BY chat_title, chat_type
            HAVING SUM(post_count) > 0
            ORDER BY post_count DESC
        """)).fetchall()

        viber_posts = int(viber[0] or 0) if viber else 0
        viber_products = int(viber[1] or 0) if viber else 0
        viber_pending = int(viber[2] or 0) if viber else 0
        viber_channels = int(viber[3] or 0) if viber else 0
        instagram_posts = int(instagram[0] or 0) if instagram else 0
        instagram_products = int(instagram[1] or 0) if instagram else 0
        instagram_pending = int(instagram[2] or 0) if instagram else 0
        instagram_channels = 1 if instagram_posts else 0

        return {
            "total_chats": (stats[0] if stats else 0) + viber_channels + instagram_channels,
            "published_products": int(all_published_products),
            "total_posts": (stats[2] if stats else 0) + viber_posts + instagram_posts,
            "channel_posts": stats[3] if stats else 0,
            "forum_posts": stats[4] if stats else 0,
            "archive_posts": stats[5] if stats else 0,
            "forum_products": stats[6] if stats else 0,
            "channel_products": stats[7] if stats else 0,
            "viber_posts": viber_posts,
            "viber_products": viber_products,
            "viber_pending": viber_pending,
            "instagram_posts": instagram_posts,
            "instagram_products": instagram_products,
            "instagram_pending": instagram_pending,
            "sold_but_live_count": sold_live[0] if sold_live else 0,
            "unlinked_count": unlinked[0] if unlinked else 0,
            "channels": [
                {
                    "chat_title": c[0],
                    "chat_type": c[1],
                    "post_count": c[2],
                    "unique_products": c[3],
                }
                for c in channels
            ],
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/publications/threads")
def get_threads(db: Session = Depends(get_db)):
    """Get all detected forum threads with their mappings."""
    try:
        rows = db.execute(text("""
            SELECT DISTINCT
                tp.chat_id, tp.chat_title, tp.thread_id, tp.thread_title,
                tm.type_id, tm.subtype_id, tm.gender_id, tm.is_master,
                COUNT(tp.id) OVER (PARTITION BY tp.chat_id, tp.thread_id) AS post_count
            FROM telegram_posts tp
            LEFT JOIN telegram_thread_mapping tm
                ON tm.chat_id = tp.chat_id AND tm.thread_id = tp.thread_id
            WHERE tp.thread_id IS NOT NULL
            ORDER BY tp.chat_title, tp.thread_title
        """)).fetchall()

        threads = []
        seen = set()
        for row in rows:
            key = (row[0], row[2])
            if key in seen:
                continue
            seen.add(key)
            threads.append({
                "chat_id": row[0],
                "chat_title": row[1],
                "thread_id": row[2],
                "thread_title": row[3],
                "type_id": row[4],
                "subtype_id": row[5],
                "gender_id": row[6],
                "is_master": row[7],
                "post_count": row[8],
            })
        return {"threads": threads}
    except Exception as e:
        logger.error(f"Error fetching threads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Sync endpoint (read-only — pulls from Telegram)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/publications/sync")
async def sync_telegram(
    chat_username: str = Body(..., embed=True),
    chat_type: str = Body("channel", embed=True),
    db: Session = Depends(get_db),
):
    """Trigger a sync from Telegram. READ-ONLY operation.

    Pulls posts from the specified chat, extracts product numbers,
    saves metadata to telegram_posts. Does NOT modify anything in Telegram.
    """
    try:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        phone = os.getenv("TELEGRAM_PHONE")

        if not all([api_id, api_hash, phone]):
            raise HTTPException(
                status_code=400,
                detail="Telegram credentials not configured. Set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE in .env"
            )

        try:
            from services.telegram_service import TelegramScanner
        except ImportError:
            from backend.services.telegram_service import TelegramScanner

        scanner = TelegramScanner(
            api_id=int(api_id),
            api_hash=api_hash,
            phone=phone,
        )

        connected = await scanner.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to connect to Telegram")

        try:
            result = await scanner.scan_channel(db, chat_username, chat_type)
        finally:
            await scanner.disconnect()

        # Auto-relink: fix any wrongly-linked posts (#3716 vs #Ф3716 collisions,
        # multi-size product numbers like #Ф3009 with sizes 41 and 44).
        try:
            relink_res = db.execute(text(_RELINK_SQL))
            db.commit()
            result["auto_relinked"] = relink_res.rowcount
        except Exception as re:
            logger.warning(f"Post-sync auto-relink failed: {re}")
            db.rollback()

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/publications/sync-all")
async def sync_all_telegram(db: Session = Depends(get_db)):
    """One-click sync: scan ALL known publishing channels and auto-relink.

    Walks through TelegramScanner.KNOWN_CHANNELS, scans each, then runs the
    same auto-relink as /sync. Returns aggregated stats.
    """
    try:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        phone = os.getenv("TELEGRAM_PHONE")

        if not all([api_id, api_hash, phone]):
            raise HTTPException(
                status_code=400,
                detail="Telegram credentials not configured. Set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE in .env"
            )

        try:
            from services.telegram_service import TelegramScanner
        except ImportError:
            from backend.services.telegram_service import TelegramScanner

        scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
        connected = await scanner.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to connect to Telegram")

        per_channel = []
        totals = {"posts_scanned": 0, "posts_with_products": 0, "new_posts_saved": 0}
        try:
            for chat_id, info in TelegramScanner.KNOWN_CHANNELS.items():
                try:
                    res = await scanner.scan_channel(db, str(chat_id), info["type"])
                    if isinstance(res, dict) and "error" not in res:
                        for k in totals:
                            totals[k] += int(res.get(k, 0) or 0)
                        per_channel.append({
                            "chat_id": chat_id,
                            "chat_title": info["title"],
                            "posts_scanned": res.get("posts_scanned", 0),
                            "new_posts_saved": res.get("new_posts_saved", 0),
                        })
                    else:
                        per_channel.append({
                            "chat_id": chat_id,
                            "chat_title": info["title"],
                            "error": (res or {}).get("error", "unknown"),
                        })
                except Exception as ce:
                    logger.warning(f"sync-all: channel {chat_id} failed: {ce}")
                    per_channel.append({"chat_id": chat_id, "chat_title": info["title"], "error": str(ce)})
        finally:
            await scanner.disconnect()

        # Auto-relink (same logic as /sync)
        relinked = 0
        try:
            r = db.execute(text(_RELINK_SQL))
            db.commit()
            relinked = r.rowcount
        except Exception as re:
            logger.warning(f"sync-all auto-relink failed: {re}")
            db.rollback()

        return {
            "success": True,
            "totals": totals,
            "auto_relinked": relinked,
            "channels": per_channel,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync-all error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/publications/sold-action-plan/{product_id}")
def get_sold_action_plan(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Analyze what TG actions are needed for a SOLD product.

    Returns per-post action plan:
      - delete_post: single-size post or last size in multi-size
      - remove_size_line: multi-size post, only this size sold
      - no_action: size still available elsewhere

    READ-ONLY: this only ANALYZES, does not execute anything.
    """
    try:
        try:
            from services.telegram_service import TelegramScanner
        except ImportError:
            from backend.services.telegram_service import TelegramScanner

        # We don't need actual TG connection for analysis — just the methods
        scanner = TelegramScanner(api_id=0, api_hash="", phone="")
        plan = scanner.analyze_sold_action(db, product_id)
        return plan
    except Exception as e:
        logger.error(f"Error analyzing sold action plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/publications/clear-manual-edit/{product_id}")
def clear_manual_edit(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Clear needs_manual_edit flag for a product's posts.

    Called when user confirms they manually fixed the post in Telegram.
    Also auto-checks: if the post was deleted from TG, mark as archived.
    """
    try:
        db.execute(
            text("""
                UPDATE telegram_posts
                SET needs_manual_edit = false
                WHERE product_id = :pid AND needs_manual_edit = true
            """),
            {"pid": product_id}
        )
        db.commit()
        return {"status": "ok", "product_id": product_id}
    except Exception as e:
        logger.error(f"Error clearing manual edit flag: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/publications/unpublish/{product_id}")
async def unpublish_product(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Remove a sold product from ALL Telegram channels/threads.

    1. Forwards one backup copy to WORKSHOP archive
    2. Deletes ALL posts for this product across all channels/threads
    3. Updates tg_status = 'archived' in DB
    """
    try:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        phone = os.getenv("TELEGRAM_PHONE")
        archive_chat = os.getenv("TELEGRAM_ARCHIVE_CHAT", "")

        if not all([api_id, api_hash, phone]):
            raise HTTPException(status_code=400, detail="Telegram credentials not configured")
        if not archive_chat:
            raise HTTPException(status_code=400, detail="TELEGRAM_ARCHIVE_CHAT not configured in .env (WORKSHOP username or ID)")

        try:
            from services.telegram_service import TelegramScanner
        except ImportError:
            from backend.services.telegram_service import TelegramScanner

        scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
        connected = await scanner.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to connect to Telegram")

        try:
            result = await scanner.unpublish_product(db, product_id, archive_chat)
        finally:
            await scanner.disconnect()

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unpublish error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/publications/unpublish-bulk")
async def unpublish_bulk(
    product_ids: List[int] = Body(..., embed=True),
    db: Session = Depends(get_db),
):
    """Bulk unpublish — remove multiple sold products from Telegram at once."""
    try:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        phone = os.getenv("TELEGRAM_PHONE")
        archive_chat = os.getenv("TELEGRAM_ARCHIVE_CHAT", "")

        if not all([api_id, api_hash, phone]):
            raise HTTPException(status_code=400, detail="Telegram credentials not configured")
        if not archive_chat:
            raise HTTPException(status_code=400, detail="TELEGRAM_ARCHIVE_CHAT not configured in .env")

        try:
            from services.telegram_service import TelegramScanner
        except ImportError:
            from backend.services.telegram_service import TelegramScanner

        scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
        connected = await scanner.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to connect to Telegram")

        try:
            result = await scanner.unpublish_bulk(db, product_ids, archive_chat)
        finally:
            await scanner.disconnect()

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk unpublish error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/publications/verify-archived")
async def verify_archived(
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Walk through archived telegram_posts and verify each is really deleted
    in Telegram. Posts that are still live get flipped back to 'published'.

    Idempotent — safe to run repeatedly. Used by auto-startup recovery after
    the B2 bug (which falsely marked posts archived when TG delete had failed).
    """
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    if not all([api_id, api_hash, phone]):
        raise HTTPException(status_code=400, detail="Telegram credentials not configured")

    try:
        from services.telegram_service import TelegramScanner
    except ImportError:
        from backend.services.telegram_service import TelegramScanner

    scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
    if not await scanner.connect():
        raise HTTPException(status_code=500, detail="Failed to connect to Telegram")
    try:
        return await scanner.verify_archived_posts(db, limit=limit)
    finally:
        await scanner.disconnect()


@router.post("/api/publications/relink")
def relink_publications(db: Session = Depends(get_db)):
    """Re-link telegram_posts to products by matching product_number_raw → products.productnumber.

    Always picks the BEST candidate (priority: #Ф{n} > Ф{n} > #{n} > {n}) so that
    duplicates like '#3716' + '#Ф3716' don't cause the post to attach to the wrong
    (no-Ф) product. Re-links existing rows too — fixes historical wrong matches.

    Useful after products are imported or product numbers normalized.
    READ-ONLY for Telegram, only updates local DB.
    """
    try:
        result = db.execute(text(_RELINK_SQL))
        db.commit()
        return {"success": True, "rows_affected": result.rowcount}
    except Exception as e:
        logger.error(f"Relink error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Telegram — СТВОРЕННЯ постів (запис). Дзеркалить флоу Prom: прев'ю → діалог
# редагування → публікація. Прев'ю нічого не створює й безпечне для кліків.
# ─────────────────────────────────────────────────────────────────────────────
def _tg_pub():
    try:
        from services import telegram_publisher
    except ImportError:
        from backend.services import telegram_publisher
    return telegram_publisher


def _viber_pub():
    try:
        from services import viber_publisher
    except ImportError:
        from backend.services import viber_publisher
    return viber_publisher


def _instagram_pub():
    try:
        from services import instagram_publisher
    except ImportError:
        from backend.services import instagram_publisher
    return instagram_publisher


@router.get("/api/publications/telegram/threads")
def telegram_threads(db: Session = Depends(get_db)):
    """Кеш гілок форуму — без мережі, миттєво."""
    return {"threads": _tg_pub().get_threads(db)}


@router.post("/api/publications/telegram/refresh-threads")
async def telegram_refresh_threads(db: Session = Depends(get_db)):
    """Перечитати живий список гілок форуму з Telegram (read-only)."""
    r = await _tg_pub().refresh_threads(db)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Не вдалося оновити гілки"))
    return r


@router.post("/api/publications/telegram/preview-post")
def telegram_preview_post(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Зібрати текст поста, фото, розміри й запропоновані гілки. НІЧОГО не створює."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _tg_pub().preview_post(db, int(pid))
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r.get("error", "Не вдалося зібрати прев'ю"))
    return r


@router.post("/api/publications/telegram/preview-posts-batch")
def telegram_preview_posts_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Зібрати до 10 УНІКАЛЬНИХ прев'ю. Рядки однієї ростовки об'єднуються."""
    product_ids = body.get("product_ids")
    if not isinstance(product_ids, list) or not product_ids:
        raise HTTPException(status_code=400, detail="Не вибрано товари")
    r = _tg_pub().preview_posts_batch(db, product_ids)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Не вдалося зібрати пакет"))
    return r


@router.post("/api/publications/telegram/build-caption")
def telegram_build_caption(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Перезібрати підпис із відредагованих частин — щоб живе прев'ю в діалозі
    показувало РІВНО те, що піде в канал, а не наближення на фронті."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _tg_pub().rebuild_caption(db, int(pid), body)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Не вдалося зібрати текст"))
    return r


@router.post("/api/publications/telegram/create-post")
async def telegram_create_post(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """ОПУБЛІКУВАТИ: альбом у «ВСІ ПРОПОЗИЦІЇ» → копії в обрані гілки →
    форвард у канал BrandStore (зараз або за розкладом)."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = await _tg_pub().create_post(db, int(pid), body)
    if not r.get("ok"):
        # «вже опубліковано» — не помилка, а сигнал фронту показати підтвердження
        if r.get("already_published"):
            return r
        raise HTTPException(status_code=400, detail=r.get("error", "Публікація не вдалася"))
    return r


@router.post("/api/publications/telegram/create-posts-batch")
async def telegram_create_posts_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Послідовно опублікувати повністю відредагований пакет через одну сесію.

    Відповідь завжди містить результат кожної картки; частковий успіх — штатний
    результат, який фронт заносить у Центр сповіщень жовтим статусом.
    """
    items = body.get("items")
    batch_id = body.get("batch_id")
    r = await _tg_pub().create_posts_batch(db, items, batch_id)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Пакетна публікація не вдалася"))
    return r


@router.get("/api/publications/telegram/product-status/{product_id}")
def telegram_product_status(product_id: int, db: Session = Depends(get_db)):
    """Де товар уже є в Telegram + що заплановано в канал."""
    return _tg_pub().product_status(db, product_id)


# ─────────────────────────────────────────────────────────────────────────────
# Viber Channel — одна JPEG-картка/колаж на товар. Preview і render не мають
# зовнішніх побічних ефектів; create передає незмінний snapshot диспетчеру.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/publications/viber/status")
def viber_connection_status():
    """Безпечний стан конфігурації без секретів і без мережевих викликів."""
    return _viber_pub().connection_status()


@router.post("/api/publications/viber/preview-post")
def viber_preview_post(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    result = _viber_pub().preview_post(db, int(pid))
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "Не вдалося зібрати Viber-прев'ю"))
    return result


@router.post("/api/publications/viber/preview-posts-batch")
def viber_preview_posts_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    product_ids = body.get("product_ids")
    if not isinstance(product_ids, list) or not product_ids:
        raise HTTPException(status_code=400, detail="Не вибрано товари")
    result = _viber_pub().preview_posts_batch(db, product_ids)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не вдалося зібрати пакет Viber"))
    return result


@router.post("/api/publications/viber/render-collage")
async def viber_render_collage(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Точний JPEG-прев'ю з backend renderer. Нічого не завантажує у R2."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    try:
        from starlette.concurrency import run_in_threadpool
        main, _thumb, _spec = await run_in_threadpool(
            _viber_pub().render_for_product, db, int(pid), body.get("collage") or body,
            include_thumbnail=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=main,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-BMS-Image-Bytes": str(len(main)),
            "Content-Disposition": 'inline; filename="bms-viber-card.jpeg"',
        },
    )


@router.post("/api/publications/viber/create-post")
async def viber_create_post(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    result = await _viber_pub().create_post(db, int(pid), body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Публікація Viber не вдалася"))
    return result


@router.post("/api/publications/viber/create-posts-batch")
async def viber_create_posts_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    result = await _viber_pub().create_posts_batch(
        db, body.get("items"), body.get("batch_id"), dry_run=body.get("dry_run") is True,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Пакет Viber не виконано"))
    return result


@router.get("/api/publications/viber/product-status/{product_id}")
def viber_product_status(product_id: int, db: Session = Depends(get_db)):
    return _viber_pub().product_status(db, product_id)


@router.post("/api/publications/viber/sync-status")
async def viber_sync_status(body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    """Звірити незавершені локальні job із Cloudflare, нічого не публікуючи."""
    raw_pid = body.get("product_id") if isinstance(body, dict) else None
    result = await _viber_pub().sync_statuses(
        db, product_id=int(raw_pid) if raw_pid else None,
    )
    if not result.get("ok") and not result.get("errors"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не вдалося оновити Viber-стан"))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Instagram Platform — офіційний Graph API через захищений Cloudflare Worker.
# Секрети й access token ніколи не проходять через frontend або PostgreSQL.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/publications/instagram/status")
async def instagram_connection_status():
    return await _instagram_pub().dispatcher_status()


@router.post("/api/publications/instagram/oauth/start")
async def instagram_oauth_start():
    try:
        return await _instagram_pub().oauth_start()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/publications/instagram/account-check")
async def instagram_account_check():
    try:
        return await _instagram_pub().account_check()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/publications/instagram/preview-post")
async def instagram_preview_post(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    product_id = body.get("product_id")
    if not product_id:
        raise HTTPException(status_code=400, detail="Немає product_id")
    result = _instagram_pub().preview_post(db, int(product_id))
    if not result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail=result.get("error", "Не вдалося зібрати Instagram-прев'ю"),
        )
    result["connection"] = await _instagram_pub().dispatcher_status()
    return result


@router.post("/api/publications/instagram/preview-posts-batch")
async def instagram_preview_posts_batch(
    body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
):
    product_ids = body.get("product_ids")
    if not isinstance(product_ids, list) or not product_ids:
        raise HTTPException(status_code=400, detail="Не вибрано товари")
    result = _instagram_pub().preview_posts_batch(db, product_ids)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Не вдалося зібрати пакет Instagram"),
        )
    connection = await _instagram_pub().dispatcher_status()
    for item in result.get("items", []):
        if item.get("preview"):
            item["preview"]["connection"] = connection
    return result


@router.post("/api/publications/instagram/dry-run")
def instagram_dry_run(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    product_id = body.get("product_id")
    if not product_id:
        raise HTTPException(status_code=400, detail="Немає product_id")
    result = _instagram_pub().dry_run(db, int(product_id), body)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Instagram dry-run не пройдено"),
        )
    return result


@router.post("/api/publications/instagram/dry-run-batch")
def instagram_dry_run_batch(
    body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
):
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Не вибрано Instagram-чернетки")
    result = _instagram_pub().dry_run_batch(db, items)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/api/publications/instagram/render-preview")
def instagram_render_preview(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    product_id = body.get("product_id")
    if not product_id:
        raise HTTPException(status_code=400, detail="Немає product_id")
    try:
        image = _instagram_pub().render_preview_jpeg(db, int(product_id), body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="bms-instagram-preview.jpeg"'},
    )


@router.post("/api/publications/instagram/create-post")
async def instagram_create_post(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    product_id = body.get("product_id")
    if not product_id:
        raise HTTPException(status_code=400, detail="Немає product_id")
    result = await _instagram_pub().create_post(db, int(product_id), body)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Instagram-публікація не вдалася"))
    return result


@router.post("/api/publications/instagram/create-posts-batch")
async def instagram_create_posts_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    result = await _instagram_pub().create_posts_batch(
        db, body.get("items"), body.get("batch_id"), dry_run_only=body.get("dry_run") is True,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Instagram-пакет не виконано"))
    return result


@router.get("/api/publications/instagram/product-status/{product_id}")
def instagram_product_status(product_id: int, db: Session = Depends(get_db)):
    return _instagram_pub().product_status(db, product_id)


@router.post("/api/publications/instagram/sync-status")
async def instagram_sync_status(body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    raw_product_id = body.get("product_id") if isinstance(body, dict) else None
    result = await _instagram_pub().sync_statuses(
        db, product_id=int(raw_product_id) if raw_product_id else None,
    )
    if not result.get("ok") and not result.get("errors"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не вдалося оновити Instagram-стан"))
    return result


@router.post("/api/publications/instagram/publications/{publication_id}/cancel")
async def instagram_cancel_publication(publication_id: int, db: Session = Depends(get_db)):
    result = await _instagram_pub().cancel_publication(db, publication_id)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Не вдалося скасувати Instagram-публікацію"))
    return result


@router.post("/api/publications/instagram/publications/{publication_id}/reschedule")
async def instagram_reschedule_publication(
    publication_id: int,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    result = await _instagram_pub().reschedule_publication(db, publication_id, body.get("publish_at"))
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Не вдалося перенести Instagram-публікацію"))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# OLX (read-only v1) — офіційний OLX API, OAuth2. Дзеркало Telegram-флоу.
# ─────────────────────────────────────────────────────────────────────────────

# Relink OLX-оголошень до products за номером (без size-стадії — OLX-оголошення
# зазвичай одне на номер). Форм-пріоритет #Ф > Ф > # > raw, найстаріший товар.
_RELINK_OLX_SQL = """
WITH best AS (
    SELECT DISTINCT ON (oa.id)
        oa.id AS oa_id,
        p.id  AS p_id
    FROM olx_adverts oa
    JOIN products p ON p.productnumber IN (
        oa.product_number_raw,
        'Ф'  || oa.product_number_raw,
        '#Ф' || oa.product_number_raw,
        '#'  || oa.product_number_raw
    )
    WHERE oa.product_number_raw IS NOT NULL AND oa.product_number_raw <> ''
    ORDER BY oa.id,
        CASE p.productnumber
            WHEN '#Ф' || oa.product_number_raw THEN 1
            WHEN 'Ф'  || oa.product_number_raw THEN 2
            WHEN '#'  || oa.product_number_raw THEN 3
            WHEN oa.product_number_raw         THEN 4
            ELSE 5
        END,
        p.id
)
UPDATE olx_adverts oa
SET product_id = best.p_id
FROM best
WHERE oa.id = best.oa_id
  AND (oa.product_id IS NULL OR oa.product_id <> best.p_id)
"""


def _olx():
    try:
        from services import olx_service
    except ImportError:
        from backend.services import olx_service
    return olx_service


@router.get("/api/publications/olx/status")
def olx_status(db: Session = Depends(get_db)):
    """Статус OLX-інтеграції: налаштовано / авторизовано / лічильники."""
    return _olx().get_status(db)


@router.get("/api/publications/olx/oauth/start")
def olx_oauth_start():
    """Повертає URL авторизації OLX — фронт відкриває його у браузері."""
    olx = _olx()
    if not olx.is_configured():
        raise HTTPException(
            status_code=400,
            detail="OLX не налаштовано. Додай OLX_CLIENT_ID та OLX_CLIENT_SECRET у .env",
        )
    return {"authorize_url": olx.build_authorize_url()}


@router.get("/api/publications/olx/oauth/callback")
def olx_oauth_callback(
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Callback від OLX: міняє code на токени. Повертає просту HTML-сторінку."""
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(f"<h3>OLX авторизація відхилена: {error}</h3>", status_code=400)
    if not code:
        return HTMLResponse("<h3>OLX: відсутній code у callback</h3>", status_code=400)
    try:
        _olx().exchange_code(db, code)
    except Exception as e:
        logger.error(f"OLX oauth callback failed: {e}")
        return HTMLResponse(f"<h3>OLX: помилка обміну токена</h3><pre>{e}</pre>", status_code=500)
    return HTMLResponse(
        "<h3>✅ OLX підключено. Можна закрити це вікно й повернутись у BMS.</h3>"
    )


@router.post("/api/publications/sync-olx")
def sync_olx(db: Session = Depends(get_db)):
    """Синхронізувати оголошення OLX + relink до товарів."""
    olx = _olx()
    result = olx.sync_adverts(db)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "OLX sync failed"))
    try:
        r = db.execute(text(_RELINK_OLX_SQL))
        db.commit()
        result["auto_relinked"] = r.rowcount
    except Exception as re:
        db.rollback()
        logger.warning(f"OLX post-sync relink failed: {re}")
    return result


@router.post("/api/publications/relink-olx")
def relink_olx(db: Session = Depends(get_db)):
    """Перелінкувати OLX-оголошення до товарів за номером (без мережі)."""
    try:
        r = db.execute(text(_RELINK_OLX_SQL))
        db.commit()
        return {"success": True, "rows_affected": r.rowcount}
    except Exception as e:
        db.rollback()
        logger.error(f"OLX relink error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── OLX створення оголошень (write v2) ───────────────────────────────────────
@router.get("/api/publications/olx/product-status/{product_id}")
def olx_product_status(product_id: int, db: Session = Depends(get_db)):
    r = _olx().olx_product_status(db, product_id)
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r.get("error", "Товар не знайдено"))
    return r


@router.get("/api/publications/olx/packets/{category_id}")
def olx_packets(category_id: int, db: Session = Depends(get_db)):
    return _olx().get_packets(db, category_id)


@router.post("/api/publications/olx/config")
def olx_config(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    return _olx().save_config(db, **{k: v for k, v in body.items() if v is not None})


@router.post("/api/publications/olx/preview-advert")
def olx_preview_advert(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Прев'ю перед публікацією (нічого не створює) — для діалогу редагування."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _olx().preview_advert(db, int(pid))
    if not r.get("ok"):
        raise HTTPException(status_code=409 if r.get("need_category") else 400,
                            detail=r.get("error", "OLX preview failed"))
    return r


@router.post("/api/publications/olx/create-advert")
def olx_create_advert(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Створити (опублікувати) оголошення OLX з картки товару."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _olx().create_advert(db, int(pid), price=body.get("price"),
                             force=bool(body.get("force")),
                             overrides=body.get("overrides"))
    if not r.get("ok"):
        # «вже на OLX» — не помилка, а сигнал фронту показати підтвердження
        if r.get("already_on_olx"):
            return r
        raise HTTPException(
            status_code=409 if r.get("need_category") else 400,
            detail=r.get("error", "OLX create failed"),
        )
    return r


@router.post("/api/publications/olx/create-batch")
def olx_create_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    ids = body.get("product_ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="Немає product_ids")
    return _olx().create_adverts_batch(db, [int(x) for x in ids if x])


# ── monoБазар: READ-верифікація (публічний API) + write-блокер ────────────────
def _monobazar():
    try:
        from services import monobazar
    except ImportError:
        from backend.services import monobazar
    return monobazar


def _monobazar_reader():
    try:
        from services import monobazar_reader
    except ImportError:
        from backend.services import monobazar_reader
    return monobazar_reader


@router.get("/api/publications/monobazar/status")
def monobazar_status(db: Session = Depends(get_db)):
    """Статус monoБазар: READ-верифікація активна, створення оголошень заблоковано."""
    return _monobazar().get_status(db)


@router.post("/api/publications/monobazar/sync")
def monobazar_sync(db: Session = Depends(get_db)):
    """Синхронізувати активні оголошення продавця (публічний API, без токенів)."""
    r = _monobazar_reader().sync_listings(db)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "monoБазар sync failed"))
    return r


@router.post("/api/publications/monobazar/config")
def monobazar_config(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    username = str(body.get("seller_username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Немає seller_username")
    _monobazar_reader().set_seller_username(db, username)
    return _monobazar().get_status(db)


@router.get("/api/publications/monobazar/product-status/{product_id}")
def monobazar_product_status(product_id: int, db: Session = Depends(get_db)):
    """Стан товару щодо monoБазар для картки — лише перегляд (verified listing)."""
    listing = _monobazar_reader().listing_status(db, product_id)
    return {"ok": True, "tracked": bool(listing), **listing}


# ── Prom.ua інтеграція (Фаза 2) ──────────────────────────────────────────────
def _prom():
    try:
        from services import prom_service
    except ImportError:
        from backend.services import prom_service
    return prom_service


@router.get("/api/publications/prom/status")
def prom_status(db: Session = Depends(get_db)):
    """Статус Prom: налаштовано, термін токена (+ попередження), лічильники."""
    return _prom().get_status(db)


@router.post("/api/publications/prom/save-token")
def prom_save_token(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Зберегти/оновити API-токен Prom (+ опційно дату закінчення)."""
    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Порожній токен")
    _prom().save_token(db, token, body.get("expires_at"))
    return _prom().get_status(db)


@router.post("/api/publications/sync-prom-products")
def sync_prom_products(db: Session = Depends(get_db)):
    """Синхронізувати товари Prom (дзеркало + лінк за sku)."""
    r = _prom().sync_products(db)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Prom sync failed"))
    r["shafa_reconciliation"] = _reconcile_shafa_after_prom(db)
    return r


@router.post("/api/publications/sync-prom-orders")
def sync_prom_orders(body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    """Синхронізувати замовлення Prom (окреме дзеркало). date_from опційно."""
    r = _prom().sync_orders(db, date_from=body.get("date_from"))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Prom orders sync failed"))
    return r


@router.post("/api/publications/prom/push-availability")
def prom_push_availability(body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    """Оновити наявність на Prom за станом BMS. dry_run=true — лише прев'ю змін."""
    r = _prom().push_availability(db, dry_run=bool(body.get("dry_run")))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Prom push failed"))
    return r


@router.post("/api/publications/prom/export-product")
def prom_export_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Виставити товар BMS на Prom (Фаза 3). preview=true — прев'ю автозаповнення БЕЗ
    створення; інакше публікуємо ЖИВИМ. Sync-роут (def) — блокуючий імпорт/очікування
    слота йде в пулі потоків, не блокує сервер (Prom: 1 імпорт за раз)."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _prom().export_product_to_prom(
        db, int(pid), as_draft=body.get("as_draft", False), preview=bool(body.get("preview")),
        force=bool(body.get("force")),
        overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else None,
    )
    if not body.get("preview") and (r.get("ok") or r.get("already_on_prom")):
        r["shafa_reconciliation"] = _reconcile_shafa_after_prom(db)
    # «вже на Prom» — не помилка, а сигнал фронту показати підтвердження перезапису
    if not r.get("ok") and not r.get("already_on_prom"):
        raise HTTPException(status_code=400, detail=r.get("error", "Prom export failed"))
    return r


@router.get("/api/publications/prom/import-limit")
def prom_import_limit(db: Session = Depends(get_db)):
    """Стан денного ліміту імпортів Prom (запобіжник): скільки наших імпортів сьогодні,
    чи/коли спрацьовував ліміт + готовий limit_warning для попередження ДО публікації."""
    return _prom().import_limit_status(db)


@router.post("/api/publications/prom/import-progress")
def prom_import_progress(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Read-only completion check for the extra UI notification after a Prom import."""
    import_id = body.get("import_id")
    skus = body.get("skus") or []
    if not isinstance(skus, list):
        raise HTTPException(status_code=400, detail="skus має бути списком")
    if len(skus) > 500:
        raise HTTPException(status_code=400, detail="Максимум 500 SKU за одну перевірку")
    if import_id in (None, "") and not skus:
        raise HTTPException(status_code=400, detail="Потрібен import_id або список SKU")
    return _prom().prom_import_progress(db, import_id=import_id, skus=skus)


@router.post("/api/publications/prom/export-products-batch")
def prom_export_products_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """МАСОВА публікація: N товарів → ОДИН import_file (обходить денний ліміт Prom на
    к-сть імпортів). product_ids — список id з буфера виділення. Пропущені (без фото/ціни)
    повертаються в summary, а не як помилка."""
    ids = body.get("product_ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="Немає product_ids")
    r = _prom().export_products_batch(db, [int(p) for p in ids if p])
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Prom batch export failed"))
    r["shafa_reconciliation"] = _reconcile_shafa_after_prom(db)
    return r


@router.post("/api/publications/prom/delete-product")
def prom_delete_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Прибрати товар з Prom (усі лістинги/розміри) — status=deleted + чистка дзеркала."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _prom().delete_product_from_prom(db, int(pid))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Prom delete failed"))
    return r


@router.get("/api/publications/prom/product-status/{product_id}")
def prom_product_status(product_id: int, db: Session = Depends(get_db)):
    """Статус товару на Prom для чіпа «Prom» (on_prom + status: draft/on_display/pending)."""
    return _prom().prom_product_status(db, int(product_id))


@router.get("/api/publications/prom/orders")
def prom_orders_list(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Список дзеркала замовлень Prom (для панелі огляду)."""
    rows = db.execute(text("""
        SELECT prom_id, status, source, date_created, client_name, phone,
               price_text, price_num, products, linked_count, client_notes
        FROM prom_orders ORDER BY date_created DESC NULLS LAST LIMIT :lim
    """), {"lim": limit}).mappings().all()
    return {"orders": [dict(r) for r in rows], "total": len(rows)}


# ── Shafa: офіційний глобальний міст через Prom (без приватних API) ──────────
def _shafa():
    try:
        from services import shafa_service
    except ImportError:
        from backend.services import shafa_service
    return shafa_service


def _reconcile_shafa_after_prom(db: Session) -> dict:
    """Shafa не повинна ламати успішну Prom-дію, навіть якщо її локальна звірка впала."""
    try:
        return _shafa().reconcile_expected_from_prom(db)
    except Exception as exc:
        db.rollback()
        logger.warning("Shafa reconciliation after Prom action failed: %s", exc)
        return {"ok": False, "error": str(exc)}


@router.get("/api/publications/shafa/status")
def shafa_status(db: Session = Depends(get_db)):
    return _shafa().get_status(db)


@router.post("/api/publications/shafa/reconcile")
def shafa_reconcile(db: Session = Depends(get_db)):
    """Примусово перерахувати очікувані Shafa-стани з локального дзеркала Prom."""
    r = _shafa().reconcile_expected_from_prom(db)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa reconcile failed"))
    return r


@router.post("/api/publications/shafa/config")
def shafa_config(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    enabled = body.get("bridge_enabled") if "bridge_enabled" in body else None
    r = _shafa().save_bridge_config(db, enabled=enabled)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa config failed"))
    return r


@router.get("/api/publications/shafa/product-status/{product_id}")
def shafa_product_status(product_id: int, db: Session = Depends(get_db)):
    r = _shafa().product_status(db, product_id)
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r.get("error", "Товар не знайдено"))
    return r


@router.post("/api/publications/shafa/verify-product")
def shafa_verify_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Публічно (без токенів) перечитати відоме оголошення Shafa зараз:
    звірити наявність і власника без чекання фонового циклу."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    try:
        from services import shafa_reader
    except ImportError:
        from backend.services import shafa_reader
    r = shafa_reader.verify_product(db, int(pid))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa verify failed"))
    # Повертаємо оновлений повний статус (з новою наявністю/перевіркою).
    return _shafa().product_status(db, int(pid))


@router.post("/api/publications/shafa/prepare-product")
def shafa_prepare_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _shafa().prepare_product(db, int(pid), force=bool(body.get("force")))
    if not r.get("ok"):
        raise HTTPException(
            status_code=409 if r.get("duplicate_risk") else 400,
            detail=r.get("error", "Shafa prepare failed"),
        )
    return r


@router.post("/api/publications/shafa/publish-product")
def shafa_publish_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """One-click: захищена ціна -> Prom -> офіційний глобальний міст Shafa."""
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _shafa().publish_product(db, int(pid), force=bool(body.get("force")))
    if not r.get("ok"):
        raise HTTPException(
            status_code=409 if r.get("duplicate_risk") else 400,
            detail=r.get("error", "Shafa publish failed"),
        )
    return r


@router.post("/api/publications/shafa/finalize-product")
def shafa_finalize_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Точкове завершення після офіційного SUCCESS імпорту Prom."""
    pid = body.get("product_id")
    skus = body.get("skus") or []
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    if not isinstance(skus, list) or not skus:
        raise HTTPException(status_code=400, detail="Немає skus")
    r = _shafa().finalize_product(db, int(pid), skus)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa finalize failed"))
    return r


@router.post("/api/publications/shafa/finalize-products-batch")
def shafa_finalize_products_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    ids = body.get("product_ids") or []
    skus = body.get("skus") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="Немає product_ids")
    if not isinstance(skus, list) or not skus:
        raise HTTPException(status_code=400, detail="Немає skus")
    return _shafa().finalize_products_batch(db, ids, skus)


@router.post("/api/publications/shafa/confirm-product")
def shafa_confirm_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _shafa().confirm_product(db, int(pid), body.get("url"))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa confirm failed"))
    return r


@router.post("/api/publications/shafa/link-existing")
def shafa_link_existing(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _shafa().link_existing(db, int(pid), body.get("url"))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa link failed"))
    return r


@router.post("/api/publications/shafa/untrack-product")
def shafa_untrack_product(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    pid = body.get("product_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Немає product_id")
    r = _shafa().untrack_product(db, int(pid))
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa untrack failed"))
    return r


@router.post("/api/publications/shafa/prepare-products-batch")
def shafa_prepare_products_batch(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    ids = body.get("product_ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="Немає product_ids")
    r = _shafa().prepare_products_batch(db, ids)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "Shafa batch failed"))
    return r

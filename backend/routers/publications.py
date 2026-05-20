"""
Publications router — manages cross-channel publications (Telegram, future: Instagram, etc.)

PHASE 1 (READ-ONLY): Scan Telegram, view publication status, no writes.
"""

import os
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body
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
        sql_in_list as _sql_in_list,
    )
except ImportError:
    from backend.models.database import get_db
    from backend.utils.order_status_logic import (
        latest_order_confirmed_sold as _latest_order_confirmed_sold,
        latest_order_reserved as _latest_order_reserved,
        product_fully_consumed as _product_fully_consumed,
        CONFIRMED_SOLD as _CONFIRMED_SOLD_STATUS_IDS,
        sql_in_list as _sql_in_list,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


# Order-status semantics live in utils/order_status_logic.py — see imports above.
# Back-compat alias for older call sites that meant "confirmed sold".
_latest_order_sold = _latest_order_confirmed_sold


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
async def get_publications_overview(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    filter_mode: Optional[str] = Query(None, description="all|published|problematic|unpublished|unlinked"),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Overview of all products + their publication status across channels.

    Filter modes:
      - all: all products
      - published: products with at least 1 TG post
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

        if search:
            where_parts.append("(p.productnumber ILIKE :search OR p.model ILIKE :search)")
            params["search"] = f"%{search}%"

        if filter_mode == "published":
            where_parts.append("EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id AND tp.tg_status = 'published')")
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
                AND p.statusid NOT IN (SELECT id FROM statuses WHERE statusname = 'Продано')
            """)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        total_row = db.execute(
            text(f"SELECT COUNT(*) FROM products p WHERE {where_clause}"),
            {k: v for k, v in params.items() if k not in ('limit', 'offset')}
        ).fetchone()
        total = total_row[0] if total_row else 0

        rows = db.execute(
            text(f"""
                SELECT
                    p.id, p.productnumber, p.model, p.price,
                    CASE
                        WHEN s.statusname = 'Продано' THEN 'Продано'
                        WHEN {_product_fully_consumed('p.id')} THEN 'Продано'
                        ELSE COALESCE(s.statusname, 'Невідомо')
                    END AS status,
                    COALESCE(pubs.pub_count, 0) AS pub_count,
                    COALESCE(pubs.channels, '') AS channels,
                    COALESCE(pubs.threads, '') AS threads,
                    COALESCE(pubs.needs_manual_edit, false) AS needs_manual_edit,
                    b.brandname AS brand_name,
                    t.typename  AS type_name,
                    st.subtypename AS subtype_name,
                    p.sizeeu, p.marking, p.year
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                LEFT JOIN brands   b ON b.id = p.brandid
                LEFT JOIN types    t ON t.id = p.typeid
                LEFT JOIN subtypes st ON st.id = p.subtypeid
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) AS pub_count,
                        STRING_AGG(DISTINCT chat_title, ', ') AS channels,
                        STRING_AGG(DISTINCT COALESCE(thread_title, ''), ', ') AS threads,
                        BOOL_OR(COALESCE(needs_manual_edit, false)) AS needs_manual_edit
                    FROM telegram_posts
                    WHERE product_id = p.id AND tg_status = 'published'
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
                "brand_name":   row[9],
                "type_name":    row[10],
                "subtype_name": row[11],
                "sizeeu":       row[12],
                "marking":      row[13],
                "year":         row[14],
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
async def get_product_publications(
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

        return {"product_id": product_id, "publications": publications}
    except Exception as e:
        logger.error(f"Error fetching product publications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/publications/product-detail/{product_id}")
async def get_product_detail_for_publication(
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
async def get_publications_stats(db: Session = Depends(get_db)):
    """Aggregated stats across all publications."""
    try:
        stats = db.execute(text("""
            SELECT
                COUNT(DISTINCT chat_id) AS total_chats,
                COUNT(DISTINCT product_id) AS published_products,
                COUNT(*) AS total_posts,
                COUNT(*) FILTER (WHERE chat_type = 'channel') AS channel_posts,
                COUNT(*) FILTER (WHERE chat_type = 'forum') AS forum_posts,
                COUNT(*) FILTER (WHERE chat_type = 'archive') AS archive_posts
            FROM telegram_posts
            WHERE tg_status = 'published'
        """)).fetchone()

        # Sold-but-live count (only published posts matter)
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
            SELECT chat_title, chat_type, COUNT(*) AS post_count,
                   COUNT(DISTINCT product_id) AS unique_products
            FROM telegram_posts
            WHERE tg_status = 'published'
            GROUP BY chat_title, chat_type
            ORDER BY post_count DESC
        """)).fetchall()

        return {
            "total_chats": stats[0] if stats else 0,
            "published_products": stats[1] if stats else 0,
            "total_posts": stats[2] if stats else 0,
            "channel_posts": stats[3] if stats else 0,
            "forum_posts": stats[4] if stats else 0,
            "archive_posts": stats[5] if stats else 0,
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
async def get_threads(db: Session = Depends(get_db)):
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
async def get_sold_action_plan(
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
async def clear_manual_edit(
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
async def relink_publications(db: Session = Depends(get_db)):
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

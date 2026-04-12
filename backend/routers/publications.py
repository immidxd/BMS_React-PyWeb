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
except ImportError:
    from backend.models.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


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
            # Product is sold (by status OR by confirmed order) AND has live posts
            # AND no other unit of the same product+size is still available
            where_parts.append("""
                (
                    p.statusid IN (SELECT id FROM statuses WHERE statusname = 'Продано')
                    OR EXISTS (
                        SELECT 1 FROM order_items oi
                        JOIN orders o ON o.id = oi.order_id
                        WHERE oi.product_id = p.id AND o.order_status_id IN (1, 7)
                    )
                )
                AND EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id AND tp.tg_status = 'published')
                AND NOT EXISTS (
                    SELECT 1 FROM products p2
                    LEFT JOIN statuses s2 ON s2.id = p2.statusid
                    WHERE p2.id != p.id
                      AND p2.productnumber = p.productnumber
                      AND COALESCE(p2.sizeeu, '') = COALESCE(p.sizeeu, '')
                      AND COALESCE(s2.statusname, '') != 'Продано'
                      AND NOT EXISTS (
                          SELECT 1 FROM order_items oi2
                          JOIN orders o2 ON o2.id = oi2.order_id
                          WHERE oi2.product_id = p2.id AND o2.order_status_id IN (1, 7)
                      )
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
                        WHEN EXISTS (
                            SELECT 1 FROM order_items oi
                            JOIN orders o ON o.id = oi.order_id
                            WHERE oi.product_id = p.id AND o.order_status_id IN (1, 7)
                        ) THEN 'Продано'
                        ELSE COALESCE(s.statusname, 'Невідомо')
                    END AS status,
                    COALESCE(pubs.pub_count, 0) AS pub_count,
                    COALESCE(pubs.channels, '') AS channels,
                    COALESCE(pubs.threads, '') AS threads
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) AS pub_count,
                        STRING_AGG(DISTINCT chat_title, ', ') AS channels,
                        STRING_AGG(DISTINCT COALESCE(thread_title, ''), ', ') AS threads
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

        # Build number variants
        bare = (prod_number or "").lstrip('#').lstrip('Ф').lstrip('ф').lstrip('Р').lstrip('р')
        variants = list({v for v in [
            prod_number, bare,
            f"Ф{bare}", f"#{bare}", f"#Ф{bare}",
            f"Р{bare}", f"#Р{bare}",
        ] if v})

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

        sizes = []
        sold_product_ids = []
        for row in sizes_rows:
            is_sold = (row[2] == 'Продано')
            sizes.append({
                "product_id": row[0],
                "size": row[1],
                "status": row[2] or "—",
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
                           os.statusname AS order_status
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

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync error: {e}")
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


@router.post("/api/publications/relink")
async def relink_publications(db: Session = Depends(get_db)):
    """Re-link telegram_posts to products by matching product_number_raw → products.productnumber.

    Useful after products are imported or product numbers normalized.
    READ-ONLY for Telegram, only updates local DB.
    """
    try:
        result = db.execute(text("""
            UPDATE telegram_posts tp
            SET product_id = p.id
            FROM products p
            WHERE tp.product_id IS NULL
              AND (
                tp.product_number_raw = p.productnumber
                OR ('Ф' || tp.product_number_raw) = p.productnumber
                OR ('#Ф' || tp.product_number_raw) = p.productnumber
                OR ('#' || tp.product_number_raw) = p.productnumber
                OR tp.product_number_raw = REPLACE(REPLACE(p.productnumber, '#', ''), 'Ф', '')
              )
        """))
        db.commit()
        return {"success": True, "rows_affected": result.rowcount}
    except Exception as e:
        logger.error(f"Relink error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

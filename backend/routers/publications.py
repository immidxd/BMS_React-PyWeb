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
    filter_mode: Optional[str] = Query(None, description="all|problematic|unpublished|sold_live"),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Overview of all products + their publication status across channels.

    Filter modes:
      - all: all products
      - problematic: SOLD products that still have live posts
      - unpublished: products NOT in any channel
      - sold_live: same as problematic (SOLD but live)
    """
    try:
        offset = (page - 1) * per_page

        # Build base query joining products with telegram_posts
        where_parts = []
        params = {"limit": per_page, "offset": offset}

        if search:
            where_parts.append("(p.productnumber ILIKE :search OR p.model ILIKE :search)")
            params["search"] = f"%{search}%"

        if filter_mode == "published":
            # Only products that have at least 1 active TG post
            where_parts.append("EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id)")
        elif filter_mode == "problematic" or filter_mode == "sold_live":
            # SOLD products that still have active posts (exact match on 'Продано')
            where_parts.append("""
                p.statusid IN (SELECT id FROM statuses WHERE lower(statusname) = 'продано')
                AND EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id)
            """)
        elif filter_mode == "unpublished":
            where_parts.append("""
                NOT EXISTS (SELECT 1 FROM telegram_posts tp WHERE tp.product_id = p.id)
                AND p.statusid NOT IN (SELECT id FROM statuses WHERE lower(statusname) = 'продано')
            """)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        # Count total
        total_row = db.execute(
            text(f"SELECT COUNT(*) FROM products p WHERE {where_clause}"),
            {k: v for k, v in params.items() if k not in ('limit', 'offset')}
        ).fetchone()
        total = total_row[0] if total_row else 0

        # Main query — products with their publication summary
        rows = db.execute(
            text(f"""
                SELECT
                    p.id, p.productnumber, p.model, p.price,
                    s.statusname AS status,
                    COALESCE(pubs.pub_count, 0) AS pub_count,
                    COALESCE(pubs.channels, '') AS channels,
                    COALESCE(pubs.threads, '') AS threads
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) AS pub_count,
                        STRING_AGG(DISTINCT chat_title, ', ') AS channels,
                        STRING_AGG(DISTINCT COALESCE(thread_title, '—'), ', ') AS threads
                    FROM telegram_posts
                    WHERE product_id = p.id
                ) pubs ON true
                WHERE {where_clause}
                ORDER BY pub_count DESC NULLS LAST, p.id DESC
                LIMIT :limit OFFSET :offset
            """),
            params
        ).fetchall()

        items = []
        for row in rows:
            items.append({
                "product_id": row[0],
                "productnumber": row[1],
                "model": row[2],
                "price": float(row[3]) if row[3] else None,
                "status": row[4],
                "publication_count": row[5],
                "channels": row[6],
                "threads": row[7],
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
        """)).fetchone()

        # Sold-but-live count
        sold_live = db.execute(text("""
            SELECT COUNT(DISTINCT p.id)
            FROM products p
            JOIN telegram_posts tp ON tp.product_id = p.id
            WHERE p.statusid IN (SELECT id FROM statuses WHERE statusname ILIKE 'продано')
        """)).fetchone()

        # Channels breakdown
        channels = db.execute(text("""
            SELECT chat_title, chat_type, COUNT(*) AS post_count,
                   COUNT(DISTINCT product_id) AS unique_products
            FROM telegram_posts
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
                OR tp.product_number_raw = REPLACE(p.productnumber, 'Ф', '')
              )
        """))
        db.commit()
        return {"success": True, "rows_affected": result.rowcount}
    except Exception as e:
        logger.error(f"Relink error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

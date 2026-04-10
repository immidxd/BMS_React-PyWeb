"""
Telegram integration service for BMS (using Telethon).

Handles scanning Telegram channels/forums for product posts,
extracting product numbers, and syncing with the database.

PHASE 1 (READ-ONLY): Scan posts, extract product numbers, store metadata.
"""

import re
import json
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

# Product number: #Ф3635, #ф3635, Ф3635 (with optional Ф prefix)
PRODUCT_NUM_CYRILLIC = re.compile(r'#?[Фф](\d{1,6}(?:-\d+)?)', re.UNICODE)

# Fallback: #XXXX generic
PRODUCT_NUM_PATTERN = re.compile(r'#[\w\-]{2,10}', re.UNICODE)

# Single-size: "Розмір: 42"
SINGLE_SIZE_PATTERN = re.compile(r'[Рр]озмір[:\s]+(\d{2}(?:[.,]\d)?)', re.UNICODE)

# Multi-size header
MULTI_SIZE_HEADER = re.compile(r'[Рр]озміри[:\s]', re.UNICODE)

# Multi-size lines: "— 37", "- 38", "• 39"
MULTI_SIZE_PATTERN = re.compile(r'[—\-•·]\s*(\d{2}(?:[.,]\d)?)', re.UNICODE)


def extract_product_numbers(text: str) -> List[str]:
    """Extract all product numbers from post text."""
    if not text:
        return []
    matches = PRODUCT_NUM_CYRILLIC.findall(text)
    if matches:
        return [m for m in matches if m]
    matches = PRODUCT_NUM_PATTERN.findall(text)
    if matches:
        return [m.lstrip('#') for m in matches if m]
    return []


def extract_sizes(text: str) -> Tuple[List[str], bool]:
    """Extract sizes from post text. Returns (sizes, is_multi_size)."""
    if not text:
        return ([], False)
    is_multi = bool(MULTI_SIZE_HEADER.search(text))
    if is_multi:
        sizes = MULTI_SIZE_PATTERN.findall(text)
        sizes = [s.replace(',', '.') for s in sizes]
        seen, unique = set(), []
        for s in sizes:
            if s not in seen:
                seen.add(s)
                unique.append(s)
        return (unique, True)
    else:
        m = SINGLE_SIZE_PATTERN.search(text)
        if m:
            return ([m.group(1).replace(',', '.')], False)
        return ([], False)


class TelegramScanner:
    """Read-only Telegram scanner using Telethon (user session)."""

    def __init__(self, api_id: int, api_hash: str, phone: str, session_name: str = "bms"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_name = session_name
        self.client = None

    async def connect(self) -> bool:
        """Connect using saved .session file (must run auth_telegram.py first)."""
        import os
        try:
            from telethon import TelegramClient
            session_dir = os.path.join(os.path.dirname(__file__), '../.telegram_session')
            session_file = os.path.join(session_dir, self.session_name)

            if not os.path.exists(session_file + ".session"):
                logger.error(f"Session file not found: {session_file}.session — run auth_telegram.py first")
                return False

            self.client = TelegramClient(session_file, self.api_id, self.api_hash)
            await self.client.connect()

            if not await self.client.is_user_authorized():
                logger.error("Session expired — run auth_telegram.py again")
                return False

            me = await self.client.get_me()
            logger.info(f"✅ Connected as {me.first_name} (@{me.username})")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to Telegram: {e}")
            return False

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()

    async def scan_channel(self, db: Session, chat_username: str, chat_type: str = "forum") -> Dict:
        """Scan a channel/forum for product posts. READ-ONLY."""
        if not self.client:
            return {"error": "Not connected"}

        try:
            from telethon.tl.types import Channel
            entity = await self.client.get_entity(chat_username)
            chat_title = getattr(entity, 'title', chat_username)
            chat_id = entity.id

            logger.info(f"📱 Scanning: {chat_title} (ID: {chat_id})")

            result = {
                "chat_id": chat_id,
                "chat_title": chat_title,
                "chat_type": chat_type,
                "posts_scanned": 0,
                "posts_with_products": 0,
                "products_found": set(),
                "new_posts_saved": 0,
                "errors": [],
            }

            async for message in self.client.iter_messages(entity, limit=None):
                result["posts_scanned"] += 1
                text_content = message.text or ""
                if not text_content:
                    continue

                product_nums = extract_product_numbers(text_content)
                if not product_nums:
                    continue

                result["posts_with_products"] += 1
                sizes_list, is_multi = extract_sizes(text_content)

                # thread_id for forum topics
                thread_id = None
                thread_title = None
                if hasattr(message, 'reply_to') and message.reply_to:
                    thread_id = getattr(message.reply_to, 'reply_to_top_id', None) or \
                                getattr(message.reply_to, 'reply_to_msg_id', None)

                for product_num in product_nums:
                    try:
                        _save_telegram_post(
                            db=db,
                            chat_id=chat_id,
                            chat_title=chat_title,
                            chat_type=chat_type,
                            thread_id=thread_id,
                            thread_title=thread_title,
                            message_id=message.id,
                            message_text=text_content[:2000],
                            message_date=message.date.replace(tzinfo=None) if message.date else None,
                            product_number_raw=product_num,
                            sizes_in_post=sizes_list,
                            is_multi_size=is_multi,
                        )
                        result["new_posts_saved"] += 1
                        result["products_found"].add(product_num)
                    except Exception as e:
                        logger.warning(f"Error saving post {message.id}: {e}")
                        result["errors"].append(str(e))

            result["products_found"] = list(result["products_found"])
            logger.info(f"✅ Done: {result['posts_with_products']} posts, {result['new_posts_saved']} saved")
            return result

        except Exception as e:
            logger.error(f"❌ Scan error: {e}")
            return {"error": str(e)}

    def analyze_sold_action(self, db: Session, product_id: int) -> Dict:
        """Analyze what TG action is needed for a SOLD product (read-only analysis)."""
        prod_row = db.execute(
            text("""
                SELECT p.id, p.productnumber, p.sizeeu, s.statusname
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.id = :pid
            """),
            {"pid": product_id}
        ).fetchone()

        if not prod_row:
            return {"error": "Product not found"}

        prod_number, size_sold, status = prod_row[1], prod_row[2], prod_row[3]

        posts = db.execute(
            text("""
                SELECT tp.id, tp.chat_title, tp.thread_title, tp.message_id,
                       tp.is_multi_size, tp.sizes_in_post, tp.chat_id, tp.thread_id
                FROM telegram_posts tp
                WHERE tp.product_number_raw = :pnum OR tp.product_id = :pid
                ORDER BY tp.is_master DESC, tp.message_date DESC
            """),
            {"pnum": prod_number, "pid": product_id}
        ).fetchall()

        actions = []
        for p in posts:
            tg_post_id, chat_title, thread_title, msg_id, is_multi, sizes_json, chat_id, thread_id = p
            try:
                current_sizes = json.loads(sizes_json) if sizes_json else []
            except Exception:
                current_sizes = []

            if not is_multi:
                actions.append({
                    "telegram_post_id": tg_post_id,
                    "chat_id": chat_id, "thread_id": thread_id,
                    "chat_title": chat_title, "thread_title": thread_title,
                    "message_id": msg_id,
                    "action": "delete_post",
                    "reason": "Одиночний пост — видалити (з форвардом в WORKSHOP)",
                    "is_multi_size": False,
                    "current_sizes_in_post": current_sizes,
                    "sizes_after_action": [],
                })
                continue

            if size_sold not in current_sizes:
                continue

            # Check if still any available stock
            still_available = db.execute(
                text("""
                    SELECT COUNT(*) FROM products p
                    LEFT JOIN statuses s ON s.id = p.statusid
                    WHERE p.productnumber = :pnum AND p.sizeeu = :sz
                      AND p.id != :pid
                      AND COALESCE(p.quantity, 0) > 0
                      AND COALESCE(s.statusname, '') NOT ILIKE 'продано'
                """),
                {"pnum": prod_number, "sz": size_sold, "pid": product_id}
            ).fetchone()

            available_count = still_available[0] if still_available else 0
            sizes_after = [s for s in current_sizes if s != size_sold]

            if available_count > 0:
                continue  # Still in stock, no action needed

            action = "delete_post" if not sizes_after else "remove_size_line"
            actions.append({
                "telegram_post_id": tg_post_id,
                "chat_id": chat_id, "thread_id": thread_id,
                "chat_title": chat_title, "thread_title": thread_title,
                "message_id": msg_id,
                "action": action,
                "reason": "Останній розмір — видалити пост" if not sizes_after
                         else f"Multi-size — прибрати рядок «{size_sold}»",
                "is_multi_size": True,
                "current_sizes_in_post": current_sizes,
                "sizes_after_action": sizes_after,
            })

        return {
            "product_id": product_id,
            "product_number": prod_number,
            "size_sold": size_sold,
            "status": status,
            "actions": actions,
        }


# ── DB helpers (module-level, reusable) ───────────────────────────────────────

def _save_telegram_post(
    db: Session,
    chat_id: int,
    chat_title: str,
    chat_type: str,
    message_id: int,
    message_text: str,
    message_date: Optional[datetime],
    product_number_raw: str,
    thread_id: Optional[int] = None,
    thread_title: Optional[str] = None,
    sizes_in_post: Optional[List[str]] = None,
    is_multi_size: bool = False,
):
    """Save Telegram post metadata to DB (idempotent)."""
    existing = db.execute(
        text("SELECT id FROM telegram_posts WHERE chat_id = :c AND message_id = :m"),
        {"c": chat_id, "m": message_id}
    ).fetchone()
    if existing:
        return

    # Resolve product_id
    product_id = None
    prod = db.execute(
        text("SELECT id FROM products WHERE productnumber = :pnum LIMIT 1"),
        {"pnum": product_number_raw}
    ).fetchone()
    if prod:
        product_id = prod[0]

    sizes_json = json.dumps(sizes_in_post or [])

    result = db.execute(
        text("""
            INSERT INTO telegram_posts (
                product_id, product_number_raw, chat_id, chat_title, chat_type,
                thread_id, thread_title, message_id, message_text, message_date,
                sizes_in_post, is_multi_size
            ) VALUES (
                :prod_id, :pnum, :chat_id, :chat_title, :chat_type,
                :thread_id, :thread_title, :msg_id, :text, :date,
                :sizes, :multi
            )
            ON CONFLICT (chat_id, message_id) DO NOTHING
            RETURNING id
        """),
        {
            "prod_id": product_id, "pnum": product_number_raw,
            "chat_id": chat_id, "chat_title": chat_title, "chat_type": chat_type,
            "thread_id": thread_id, "thread_title": thread_title,
            "msg_id": message_id, "text": message_text, "date": message_date,
            "sizes": sizes_json, "multi": is_multi_size,
        }
    )
    post_row = result.fetchone()
    post_db_id = post_row[0] if post_row else None

    # Per-size mapping for multi-size posts
    if post_db_id and is_multi_size and sizes_in_post:
        for size in sizes_in_post:
            matched = db.execute(
                text("""
                    SELECT id FROM products
                    WHERE productnumber = :pnum
                      AND (sizeeu = :sz OR sizeeu = :sz_int)
                    LIMIT 1
                """),
                {"pnum": product_number_raw, "sz": size,
                 "sz_int": size.split('.')[0] if '.' in size else size}
            ).fetchone()
            db.execute(
                text("""
                    INSERT INTO telegram_post_sizes (telegram_post_id, size_eu, product_id)
                    VALUES (:tp_id, :sz, :pid)
                    ON CONFLICT DO NOTHING
                """),
                {"tp_id": post_db_id, "sz": size, "pid": matched[0] if matched else None}
            )

    db.commit()

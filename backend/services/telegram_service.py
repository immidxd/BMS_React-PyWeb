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

# Single-size: "Розмір: 42", "**Розмір**: 42", "**Розміри**: 36"
SINGLE_SIZE_PATTERN = re.compile(r'[Рр]озмір[иі]?\**[:\s*]+(\d{2}(?:[.,]\d)?)', re.UNICODE)

# Multi-size header: "Розміри:", "**Розміри:**"
MULTI_SIZE_HEADER = re.compile(r'[Рр]озміри\**[:\s*]', re.UNICODE)

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
    """Extract sizes from post text. Returns (sizes, is_multi_size).

    Handles Markdown formatting: **Розмір**: 36, **Розміри:**
    Fallback: if "Розміри" header found but no bullet-list sizes (— 36),
    try single-size pattern as fallback (the post may say "Розміри: 36"
    for a single size).
    """
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
        if unique:
            return (unique, True)
        # Fallback: "Розміри: 36" (header says plural but only one size, no bullets)
        m = SINGLE_SIZE_PATTERN.search(text)
        if m:
            return ([m.group(1).replace(',', '.')], False)
        return ([], False)
    else:
        m = SINGLE_SIZE_PATTERN.search(text)
        if m:
            return ([m.group(1).replace(',', '.')], False)
        return ([], False)


class TelegramScanner:
    """Telegram scanner using Telethon (user session).

    Supports scanning, forwarding albums, deleting, and editing multi-size posts.
    """

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

    async def _resolve_entity(self, chat_ref: str):
        """Resolve a chat reference (username, invite link, or numeric ID) to an entity."""
        # Numeric ID (e.g. -1002182178232)
        try:
            chat_id = int(chat_ref)
            # For supergroups/channels: strip -100 prefix to get raw channel ID
            if chat_id < -1000000000000:
                raw_id = int(str(chat_id).replace('-100', '', 1))
                from telethon.tl.types import PeerChannel
                return await self.client.get_entity(PeerChannel(raw_id))
            return await self.client.get_entity(chat_id)
        except (ValueError, TypeError):
            pass
        # Username or invite link
        return await self.client.get_entity(chat_ref)

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()

    async def _fetch_forum_topics(self, entity) -> Dict[int, str]:
        """Fetch forum topic titles. Returns {topic_id: title}."""
        topic_titles = {}
        try:
            from telethon.tl.functions.channels import GetForumTopicsRequest
            offset_date = 0
            offset_id = 0
            offset_topic = 0
            while True:
                result = await self.client(GetForumTopicsRequest(
                    channel=entity,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=100,
                    q="",
                ))
                if not result.topics:
                    break
                for topic in result.topics:
                    topic_titles[topic.id] = topic.title
                if len(result.topics) < 100:
                    break
                last = result.topics[-1]
                offset_id = last.top_message
                offset_topic = last.id
                offset_date = getattr(last, 'date', 0)
            logger.info(f"📋 Fetched {len(topic_titles)} forum topics")
        except Exception as e:
            logger.warning(f"⚠️ Could not fetch forum topics: {e}")
        return topic_titles

    async def scan_channel(self, db: Session, chat_username: str, chat_type: str = "forum") -> Dict:
        """Scan a channel/forum for product posts. READ-ONLY."""
        if not self.client:
            return {"error": "Not connected"}

        try:
            entity = await self._resolve_entity(chat_username)
            chat_title = getattr(entity, 'title', chat_username)
            chat_id = entity.id

            logger.info(f"📱 Scanning: {chat_title} (ID: {chat_id})")

            # Pre-fetch forum topic titles
            topic_titles = {}
            if chat_type == "forum":
                topic_titles = await self._fetch_forum_topics(entity)

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
                if thread_id and thread_id in topic_titles:
                    thread_title = topic_titles[thread_id]

                grouped_id = getattr(message, 'grouped_id', None)

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
                            grouped_id=grouped_id,
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

    async def _get_album_message_ids(self, entity, message_id: int, grouped_id: Optional[int] = None) -> List[int]:
        """Find all message IDs in a media album (grouped_id).

        Scans ±15 messages around the known message_id looking for messages
        with the same grouped_id. Returns all IDs sorted ascending.

        If grouped_id is unknown (NULL in DB), fetches the live message first
        to discover its grouped_id. If the message has no grouped_id at all
        (not part of an album), returns just [message_id].
        """
        # If grouped_id unknown, try to discover it from the live message
        if not grouped_id:
            try:
                live_msg = await self.client.get_messages(entity, ids=message_id)
                if live_msg:
                    grouped_id = getattr(live_msg, 'grouped_id', None)
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch msg {message_id} to discover grouped_id: {e}")

        if not grouped_id:
            return [message_id]

        album_ids = set()
        # Scan nearby messages: 15 before + 15 after the known message
        try:
            async for msg in self.client.iter_messages(
                entity, min_id=message_id - 16, max_id=message_id + 16,
                limit=40
            ):
                if getattr(msg, 'grouped_id', None) == grouped_id:
                    album_ids.add(msg.id)
        except Exception as e:
            logger.warning(f"⚠️ Album scan failed for msg {message_id}: {e}")

        if not album_ids:
            album_ids.add(message_id)

        return sorted(album_ids)

    async def unpublish_product(self, db: Session, product_id: int, archive_chat: str) -> Dict:
        """Remove a sold product from ALL channels/threads.

        LOGIC (per post):
          1. Fetch LIVE text from Telegram (never trust stale DB data)
          2. Re-extract sizes from live text → determine if multi-size (ростовка)
          3. If multi-size AND other sizes remain unsold:
             → EDIT post: remove only the sold size line. NO delete, NO forward.
          4. If single-size OR all sizes sold:
             → Forward full album to WORKSHOP, then delete all album messages.

        Key: multi-size detection is ALWAYS from live post text, not DB flag.
        """
        if not self.client:
            return {"error": "Not connected to Telegram"}

        # ── 1. Get product info ──
        prod_row = db.execute(
            text("""
                SELECT p.id, p.productnumber, p.sizeeu, s.statusname
                FROM products p LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.id = :pid
            """),
            {"pid": product_id}
        ).fetchone()
        if not prod_row:
            return {"product_id": product_id, "error": "Product not found"}
        prod_number, size_sold = prod_row[1], prod_row[2]

        # ── 2. Build all product number variants (used for both DB queries and TG post search) ──
        bare_num = (prod_number or "").lstrip('#').lstrip('Ф').lstrip('ф').lstrip('Р').lstrip('р')
        number_variants = list({v for v in [
            prod_number, bare_num,
            f"Ф{bare_num}", f"ф{bare_num}",
            f"#{bare_num}", f"#Ф{bare_num}", f"#ф{bare_num}",
            f"Р{bare_num}", f"#Р{bare_num}", f"р{bare_num}",
        ] if v})

        # ── 3. Find ALL sold sizes for this product number (by ALL variants) ──
        # A size is "sold" if status = 'Продано' OR has a confirmed order
        sold_sizes_rows = db.execute(
            text("""
                SELECT DISTINCT p.sizeeu
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.productnumber = ANY(:variants)
                  AND p.sizeeu IS NOT NULL
                  AND (
                      COALESCE(s.statusname, '') = 'Продано'
                      OR EXISTS (
                          SELECT 1 FROM order_items oi JOIN orders o ON o.id = oi.order_id
                          WHERE oi.product_id = p.id AND o.order_status_id IN (1, 7)
                      )
                  )
            """),
            {"variants": number_variants}
        ).fetchall()
        all_sold_sizes = {row[0] for row in sold_sizes_rows if row[0]}

        # Which sizes still have available (not sold) stock?
        # A size is "available" if status != 'Продано' AND no confirmed order
        available_sizes_rows = db.execute(
            text("""
                SELECT DISTINCT p.sizeeu
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.productnumber = ANY(:variants)
                  AND p.sizeeu IS NOT NULL
                  AND COALESCE(s.statusname, '') != 'Продано'
                  AND NOT EXISTS (
                      SELECT 1 FROM order_items oi JOIN orders o ON o.id = oi.order_id
                      WHERE oi.product_id = p.id AND o.order_status_id IN (1, 7)
                  )
            """),
            {"variants": number_variants}
        ).fetchall()
        available_sizes = {row[0] for row in available_sizes_rows if row[0]}

        logger.info(f"📊 Product {prod_number}: sold_sizes={all_sold_sizes}, available_sizes={available_sizes}")

        # ── 4. Get all DB posts for this product ──
        posts = db.execute(
            text("""
                SELECT tp.id, tp.chat_id, tp.message_id, tp.chat_title,
                       tp.thread_id, tp.thread_title, tp.tg_status,
                       tp.grouped_id
                FROM telegram_posts tp
                WHERE (tp.product_id = :pid OR tp.product_number_raw = ANY(:variants))
                  AND tp.tg_status = 'published'
                ORDER BY tp.message_date ASC
            """),
            {"pid": product_id, "variants": number_variants}
        ).fetchall()

        if not posts:
            return {"product_id": product_id, "error": "No published posts found"}

        # Resolve archive channel entity
        try:
            archive_entity = await self._resolve_entity(archive_chat)
        except Exception as e:
            return {"product_id": product_id, "error": f"Cannot find WORKSHOP channel '{archive_chat}': {e}"}

        result = {
            "product_id": product_id,
            "forwarded": 0,
            "deleted": 0,
            "edited": 0,
            "skipped": 0,
            "failed": [],
            "total_posts": len(posts),
        }

        already_forwarded_backup = False
        already_deleted_msgs = set()

        for post in posts:
            db_id, chat_id, msg_id, chat_title, thread_id, thread_title, _, grouped_id = post

            try:
                chat_entity = await self._resolve_entity(str(chat_id))

                # ── ALWAYS fetch live message from Telegram ──
                tg_msg = await self.client.get_messages(chat_entity, ids=int(msg_id))
                if not tg_msg:
                    logger.warning(f"⚠️ Message {msg_id} not found in {chat_title} — already deleted?")
                    db.execute(text("UPDATE telegram_posts SET tg_status = 'archived' WHERE id = :id"), {"id": db_id})
                    continue

                live_text = tg_msg.text or ""

                # ── Re-extract sizes from LIVE text (never trust DB flag) ──
                live_sizes, is_multi = extract_sizes(live_text)

                # Decide action based on live post content
                if live_sizes:
                    # Check which sizes in this post are fully sold (no available stock)
                    sizes_to_remove = []
                    for sz in live_sizes:
                        sz_int = sz.split('.')[0]
                        if (sz in all_sold_sizes or sz_int in all_sold_sizes) and \
                           sz not in available_sizes and sz_int not in available_sizes:
                            sizes_to_remove.append(sz)

                    if not sizes_to_remove:
                        # All sizes in this post still have stock — skip entirely
                        result["skipped"] += 1
                        logger.info(f"⏭ Skipped msg {msg_id} in {chat_title} — all sizes still available: {live_sizes}")
                        continue

                    sizes_remaining = [s for s in live_sizes if s not in sizes_to_remove]

                    if sizes_remaining and is_multi:
                        # ── РОСТОВКА with remaining sizes: EDIT post ──
                        new_text = live_text
                        for sz in sizes_to_remove:
                            new_text = self._remove_size_line(new_text, sz)

                        if new_text != live_text:
                            await self.client.edit_message(chat_entity, int(msg_id), new_text)
                            db.execute(
                                text("UPDATE telegram_posts SET sizes_in_post = :sizes, is_multi_size = true WHERE id = :id"),
                                {"sizes": json.dumps(sizes_remaining), "id": db_id}
                            )
                            result["edited"] += 1
                            logger.info(f"✏️ Edited msg {msg_id} in {chat_title}: removed sizes {sizes_to_remove}, kept {sizes_remaining}")
                        else:
                            logger.warning(f"⚠️ Could not match size lines {sizes_to_remove} in msg {msg_id}")
                        continue  # DO NOT delete, DO NOT forward
                    elif sizes_remaining and not is_multi:
                        # Single-size post but size is still available — SKIP
                        result["skipped"] += 1
                        logger.info(f"⏭ Skipped single-size msg {msg_id} in {chat_title} — size {sizes_remaining[0]} still available")
                        continue
                    else:
                        # All sizes sold — fall through to delete
                        pass

                # ── ALL-SIZES-SOLD or no sizes detected: forward album + delete ──
                album_ids = await self._get_album_message_ids(chat_entity, int(msg_id), grouped_id)

                # Forward full album to WORKSHOP (only once per product)
                if not already_forwarded_backup:
                    try:
                        await self.client.forward_messages(
                            entity=archive_entity,
                            messages=album_ids,
                            from_peer=chat_entity,
                        )
                        result["forwarded"] = len(album_ids)
                        already_forwarded_backup = True
                        logger.info(f"📦 Forwarded {len(album_ids)} msgs from {chat_title} → WORKSHOP")
                    except Exception as e:
                        logger.warning(f"⚠️ Forward failed (msg {msg_id}): {e}")
                        result["failed"].append({"chat": chat_title, "msg_id": msg_id, "action": "forward", "error": str(e)})

                # Delete ALL album messages
                to_delete = [mid for mid in album_ids if (chat_id, mid) not in already_deleted_msgs]
                if to_delete:
                    try:
                        await self.client.delete_messages(entity=chat_entity, message_ids=to_delete)
                        for mid in to_delete:
                            already_deleted_msgs.add((chat_id, mid))
                        result["deleted"] += len(to_delete)
                        logger.info(f"🗑 Deleted {len(to_delete)} msgs from {chat_title}" +
                                    (f" / {thread_title}" if thread_title else ""))
                    except Exception as e:
                        logger.warning(f"⚠️ Delete failed (msgs {to_delete} in {chat_title}): {e}")
                        result["failed"].append({"chat": chat_title, "msg_id": msg_id, "action": "delete", "error": str(e)})

                # Update DB status
                db.execute(
                    text("UPDATE telegram_posts SET tg_status = 'archived' WHERE id = :id"),
                    {"id": db_id}
                )

            except Exception as e:
                logger.warning(f"⚠️ Error processing post {msg_id} in {chat_title}: {e}")
                result["failed"].append({"chat": chat_title, "msg_id": msg_id, "action": "process", "error": str(e)})

        db.commit()
        logger.info(f"✅ Unpublished product {product_id}: forwarded={result['forwarded']}, deleted={result['deleted']}, edited={result['edited']}, skipped={result['skipped']}")
        return result

    @staticmethod
    def _remove_size_line(text_content: str, size: str) -> str:
        """Remove a specific size line from multi-size post text.

        Handles real Telegram formats like:
          — 40 (на ніжку 26 см)
          — 42.5 (на ніжку 27 см)
          - 38
          • 39 ✅
          — 37 (в наявності)

        Also cleans up orphaned "Розміри:" header if no size lines remain after removal.
        """
        lines = text_content.split('\n')
        new_lines = []
        # Normalize size for comparison: "37.5" → also match "37", "37,5"
        size_norm = size.replace(',', '.')
        size_int = size_norm.split('.')[0]
        for line in lines:
            # Match size entry lines: bullet/dash + number + optional decimal + any trailing text
            m = re.match(r'^[\s]*[—\-•·]\s*(\d{2}(?:[.,]\d{1,2})?)\b', line)
            if m:
                line_size = m.group(1).replace(',', '.')
                line_size_int = line_size.split('.')[0]
                # Match exact ("42.5" == "42.5") or integer ("42" == "42")
                if line_size == size_norm or (line_size_int == size_int and '.' not in size_norm):
                    continue  # Remove this line
            new_lines.append(line)

        # Clean up orphaned "Розміри:" header if no size lines remain
        result_text = '\n'.join(new_lines)
        if MULTI_SIZE_HEADER.search(result_text) and not MULTI_SIZE_PATTERN.search(result_text):
            # No size lines left — remove the header line too
            cleaned = []
            for line in new_lines:
                if MULTI_SIZE_HEADER.search(line):
                    continue
                cleaned.append(line)
            result_text = '\n'.join(cleaned)

        # Collapse multiple consecutive blank lines into one
        result_text = re.sub(r'\n{3,}', '\n\n', result_text)
        return result_text

    async def unpublish_bulk(self, db: Session, product_ids: List[int], archive_chat: str) -> Dict:
        """Unpublish multiple products at once."""
        results = []
        for pid in product_ids:
            r = await self.unpublish_product(db, pid, archive_chat)
            results.append(r)
        total_deleted = sum(r.get("deleted", 0) for r in results)
        total_failed = sum(len(r.get("failed", [])) for r in results)
        return {
            "products_processed": len(product_ids),
            "total_deleted": total_deleted,
            "total_failed": total_failed,
            "details": results,
        }

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
    grouped_id: Optional[int] = None,
):
    """Save Telegram post metadata to DB (idempotent)."""
    existing = db.execute(
        text("SELECT id FROM telegram_posts WHERE chat_id = :c AND message_id = :m"),
        {"c": chat_id, "m": message_id}
    ).fetchone()
    if existing:
        return

    # Resolve product_id — try all known prefix combinations (#Ф, Ф, #, bare)
    product_id = None
    prod = db.execute(
        text("""SELECT id FROM products
                WHERE productnumber IN (:raw, :f, :hf, :h)
                LIMIT 1"""),
        {
            "raw": product_number_raw,
            "f": f"\u0424{product_number_raw}",
            "hf": f"#\u0424{product_number_raw}",
            "h": f"#{product_number_raw}",
        }
    ).fetchone()
    if prod:
        product_id = prod[0]

    sizes_json = json.dumps(sizes_in_post or [])

    result = db.execute(
        text("""
            INSERT INTO telegram_posts (
                product_id, product_number_raw, chat_id, chat_title, chat_type,
                thread_id, thread_title, message_id, message_text, message_date,
                sizes_in_post, is_multi_size, grouped_id
            ) VALUES (
                :prod_id, :pnum, :chat_id, :chat_title, :chat_type,
                :thread_id, :thread_title, :msg_id, :text, :date,
                :sizes, :multi, :grouped_id
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
            "grouped_id": grouped_id,
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
                    WHERE productnumber IN (:raw, :f, :hf, :h)
                      AND (sizeeu = :sz OR sizeeu = :sz_int)
                    LIMIT 1
                """),
                {"raw": product_number_raw,
                 "f": f"\u0424{product_number_raw}",
                 "hf": f"#\u0424{product_number_raw}",
                 "h": f"#{product_number_raw}",
                 "sz": size,
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

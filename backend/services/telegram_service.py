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

# ── Size extraction patterns ──

# Size header: "Розмір:", "Розміри:", "Заміри:", with optional Markdown ** and emoji
SIZE_HEADER = re.compile(
    r'(?:[Рр]озмір[иі]?|[Зз]аміри)\**[:\s*]',
    re.UNICODE
)

# Multi-size header: plural "Розміри" or "Заміри" (indicates multiple sizes expected)
MULTI_SIZE_HEADER = re.compile(
    r'(?:[Рр]озміри|[Зз]аміри)\**[:\s*]',
    re.UNICODE
)

# Bullet-list sizes: "— 37", "- 38", "• 39" at START of line only (re.MULTILINE)
# This prevents matching range dashes like "38-39" mid-line
# Uses [ ]* (not \s*) in range part to avoid matching across lines
BULLET_SIZE_PATTERN = re.compile(
    r'^\s*[—\-•·]\s*(\d{2}(?:[.,]\d{1,2})?(?:[ ]*[-–—][ ]*\d{2}(?:[.,]\d{1,2})?)?)',
    re.UNICODE | re.MULTILINE
)

# Inline number (for extracting from header line)
INLINE_SIZE_PATTERN = re.compile(r'(\d{2}(?:[.,]\d{1,2})?)', re.UNICODE)

# Range-pair size: "38-39", "40-41" — a size expressed as EU range
RANGE_SIZE_PATTERN = re.compile(r'(\d{2}(?:[.,]\d{1,2})?)\s*[-–—]\s*(\d{2}(?:[.,]\d{1,2})?)', re.UNICODE)

# Physical dimensions pattern: "44 × 30 × 14 см" — NOT clothing/shoe sizes
DIMENSIONS_PATTERN = re.compile(r'\d+\s*[×xXхХ]\s*\d+', re.UNICODE)

# Unicode vulgar-fraction → decimal so "46½" matches DB "46.5"
_UNICODE_FRACTIONS = {
    '½': '.5', '¼': '.25', '¾': '.75',
    '⅓': '.33', '⅔': '.67',
    '⅕': '.2', '⅖': '.4', '⅗': '.6', '⅘': '.8',
    '⅙': '.17', '⅚': '.83', '⅛': '.125', '⅜': '.375', '⅝': '.625', '⅞': '.875',
}


def _normalize_fractions(text: str) -> str:
    if not text:
        return text
    for frac, dec in _UNICODE_FRACTIONS.items():
        if frac in text:
            text = text.replace(frac, dec)
    return text


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

    Supported formats:
      1. Bullet-list:  "Розміри:\n— 40 (на ніжку 26 см)\n— 41 ..."
      2. Inline list:  "Розміри: 40, 41, 42"
      3. Range pairs:  "Розміри: 38-39; 40-41"
      4. Single:       "Розмір: 42"
      5. Заміри:       "Заміри:\n— 40\n— 41" (but NOT "Заміри: 44 × 30 × 14 см")
      6. Mixed:        "Розмір: 39, 40-41"

    Skips:
      - Physical dimensions with × (e.g. "Заміри: 44 × 30 × 14 см")
      - Numbers inside parentheses (e.g. "(на ніжку 26 см)" — 26 is NOT a size)

    Headers recognized: Розмір/Розміри/Заміри (with optional ** markdown, emoji prefix)
    is_multi_size = True if more than one size found (regardless of header form).
    """
    if not text:
        return ([], False)

    # Normalize unicode fractions: "Розмір: 46½" → "Розмір: 46.5"
    text = _normalize_fractions(text)

    # Check if any size header exists
    header_match = SIZE_HEADER.search(text)
    if not header_match:
        return ([], False)

    # Find the header line
    header_line = None
    for line in text.split('\n'):
        if SIZE_HEADER.search(line):
            header_line = line
            break

    # Skip physical dimensions: "Заміри: 44 × 30 × 14 см"
    if header_line and DIMENSIONS_PATTERN.search(header_line):
        return ([], False)

    # Step 1: Try bullet-list sizes at start of lines (— 40, • 41, - 42)
    bullet_sizes = BULLET_SIZE_PATTERN.findall(text)
    bullet_sizes = [s.replace(',', '.') for s in bullet_sizes]

    # Step 2: Try inline sizes on the header line itself (only if no bullets)
    inline_sizes = []
    if not bullet_sizes and header_line:
        hm = SIZE_HEADER.search(header_line)
        if hm:
            after_header = header_line[hm.end():]
            # Strip parenthesized content (measurements, not sizes)
            clean_after = re.sub(r'\([^)]*\)', '', after_header)
            # Skip if no digits remain (letter sizes like M, L, XL)
            if not re.search(r'\d{2}', clean_after):
                pass
            else:
                # Extract all size tokens: ranges ("38-39") and standalone ("40")
                # First, find and extract ranges
                ranges = RANGE_SIZE_PATTERN.findall(clean_after)
                range_strings = set()
                for r_start, r_end in ranges:
                    rs = f"{r_start.replace(',','.')}-{r_end.replace(',','.')}"
                    inline_sizes.append(rs)
                    range_strings.add(r_start)
                    range_strings.add(r_end)
                # Then extract standalone numbers not part of ranges
                nums = INLINE_SIZE_PATTERN.findall(clean_after)
                for n in nums:
                    n_clean = n.replace(',', '.')
                    if n_clean not in range_strings:
                        inline_sizes.append(n_clean)

    # Combine: prefer bullet sizes, fallback to inline
    all_sizes = []
    seen = set()

    source = bullet_sizes if bullet_sizes else inline_sizes
    for s in source:
        if s not in seen:
            seen.add(s)
            all_sizes.append(s)

    is_multi = len(all_sizes) > 1
    return (all_sizes, is_multi)


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
        from telethon.tl.types import PeerChannel
        # Numeric ID (e.g. -1002182178232 or raw 1201323714)
        try:
            chat_id = int(chat_ref)
            # For supergroups/channels: strip -100 prefix to get raw channel ID
            if chat_id < -1000000000000:
                raw_id = int(str(chat_id).replace('-100', '', 1))
                return await self.client.get_entity(PeerChannel(raw_id))
            # Large positive IDs (> 1 billion) are likely raw channel IDs from DB
            # Try PeerChannel first, fall back to plain get_entity
            if chat_id > 1_000_000_000:
                try:
                    return await self.client.get_entity(PeerChannel(chat_id))
                except Exception:
                    pass
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

    # ── Known publishing channels (chat_id → chat_title, chat_type) ──
    KNOWN_CHANNELS = {
        2373506200: {"title": "КАТАЛОГ ТОВАРУ", "type": "forum"},
        1201323714: {"title": "BrandStore 👟 │ Брендове взуття", "type": "channel"},
    }

    async def quick_scan_product(self, db: Session, number_variants: List[str]) -> int:
        """Quick targeted scan: search known channels for a specific product number.

        Searches all KNOWN_CHANNELS for posts containing any of the number_variants,
        and saves any newly discovered posts to the DB.

        Returns the count of newly discovered posts.
        """
        if not self.client:
            logger.warning("quick_scan_product: not connected to Telegram")
            return 0

        new_posts = 0
        # Build search queries — use the shortest/most common variant for Telegram search
        # e.g. for ["#Ф1803", "Ф1803", "#1803", "1803"] → search "Ф1803" and "1803"
        search_terms = set()
        for v in number_variants:
            clean = v.lstrip('#')
            search_terms.add(clean)
        # Remove subsets: if we have "Ф1803" and "1803", keep both — TG search is substring-based
        search_terms = list(search_terms)

        for chat_id, info in self.KNOWN_CHANNELS.items():
            chat_title = info["title"]
            chat_type = info["type"]
            try:
                # Resolve via PeerChannel (chat_id is the raw channel ID without -100 prefix)
                from telethon.tl.types import PeerChannel
                entity = await self.client.get_entity(PeerChannel(chat_id))
            except Exception as e:
                logger.warning(f"⚠️ quick_scan: cannot resolve channel {chat_title} ({chat_id}): {e}")
                continue

            for search_q in search_terms:
                try:
                    async for message in self.client.iter_messages(entity, search=search_q, limit=50):
                        text_content = message.text or ""
                        if not text_content:
                            continue

                        product_nums = extract_product_numbers(text_content)
                        if not product_nums:
                            continue

                        # Verify this message actually contains our product (not a substring match)
                        matched = False
                        for pn in product_nums:
                            if pn in search_terms or f"#{pn}" in number_variants or pn in number_variants:
                                matched = True
                                break
                        if not matched:
                            continue

                        # Check if already in DB
                        existing = db.execute(
                            text("SELECT id FROM telegram_posts WHERE chat_id = :c AND message_id = :m"),
                            {"c": chat_id, "m": message.id}
                        ).fetchone()
                        if existing:
                            continue

                        # Extract sizes and metadata
                        sizes_list, is_multi = extract_sizes(text_content)
                        thread_id = None
                        thread_title = None
                        if hasattr(message, 'reply_to') and message.reply_to:
                            thread_id = getattr(message.reply_to, 'reply_to_top_id', None) or \
                                        getattr(message.reply_to, 'reply_to_msg_id', None)
                        grouped_id = getattr(message, 'grouped_id', None)

                        for product_num in product_nums:
                            # Only save our product, not others mentioned in the same post
                            if product_num not in search_terms and f"#{product_num}" not in number_variants and product_num not in number_variants:
                                continue
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
                                new_posts += 1
                                logger.info(f"🔍 quick_scan: found NEW post in {chat_title} — msg {message.id}, product {product_num}")
                            except Exception as e:
                                logger.warning(f"quick_scan: error saving post {message.id}: {e}")
                except Exception as e:
                    logger.warning(f"⚠️ quick_scan: search '{search_q}' in {chat_title} failed: {e}")

        if new_posts:
            logger.info(f"🔍 quick_scan_product: discovered {new_posts} new posts for variants {number_variants}")
        else:
            logger.info(f"🔍 quick_scan_product: no new posts found for variants {number_variants}")

        return new_posts

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
        # Preserve letter prefix (Ф, Р, Н etc.) and suffix (-2, -3 etc.)
        raw = (prod_number or "").lstrip('#')  # remove only #, keep letter prefix + suffix
        number_variants = list({v for v in [prod_number, raw, f"#{raw}"] if v})
        # Also add bare number (without letter prefix) for telegram_posts.product_number_raw matching
        import re as _re
        bare_match = _re.match(r'^[ФфРрНнЛлЗз]', raw)
        if bare_match:
            bare_num = raw[len(bare_match.group(0)):]
            number_variants.extend([bare_num, f"#{bare_num}"])
        number_variants = list(set(number_variants))

        # ── 2.5 Mini-sync: search Telegram channels for posts not yet in DB ──
        try:
            new_found = await self.quick_scan_product(db, number_variants)
            if new_found:
                logger.info(f"🔍 Pre-unpublish scan found {new_found} new post(s) for {prod_number}")
        except Exception as e:
            logger.warning(f"⚠️ Pre-unpublish scan failed (continuing anyway): {e}")

        # ── 3. Find ALL sold sizes for this product number (by ALL variants) ──
        # A size is "sold" if status = 'Продано' OR the LATEST order on the
        # product is in active state (1, 7). Cancelled latest orders override
        # older confirmed ones — the product is back in stock.
        # Інверсна: вільний лише при 5=Відміна, 6=Ігнорування, 9=Повернення.
        _LATEST_SOLD = """COALESCE((
            SELECT o.order_status_id
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id = p.id
            ORDER BY o.created_at DESC LIMIT 1
        ), 0) NOT IN (0, 5, 6, 9)"""
        sold_sizes_rows = db.execute(
            text(f"""
                SELECT DISTINCT COALESCE(p.sizeeu, '__NULL__')
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.productnumber = ANY(:variants)
                  AND (
                      COALESCE(s.statusname, '') = 'Продано'
                      OR {_LATEST_SOLD}
                  )
            """),
            {"variants": number_variants}
        ).fetchall()
        _raw_sold = {row[0] for row in sold_sizes_rows}
        sold_has_null = '__NULL__' in _raw_sold  # product with no size is sold
        all_sold_sizes = {s for s in _raw_sold if s != '__NULL__'}

        # Which sizes still have available (not sold) stock?
        # A size is "available" if status != 'Продано' AND latest order is NOT active
        available_sizes_rows = db.execute(
            text(f"""
                SELECT DISTINCT COALESCE(p.sizeeu, '__NULL__')
                FROM products p
                LEFT JOIN statuses s ON s.id = p.statusid
                WHERE p.productnumber = ANY(:variants)
                  AND COALESCE(s.statusname, '') != 'Продано'
                  AND NOT {_LATEST_SOLD}
            """),
            {"variants": number_variants}
        ).fetchall()
        _raw_avail = {row[0] for row in available_sizes_rows}
        avail_has_null = '__NULL__' in _raw_avail
        available_sizes = {s for s in _raw_avail if s != '__NULL__'}

        logger.info(f"📊 Product {prod_number}: sold_sizes={all_sold_sizes}, available_sizes={available_sizes}, sold_has_null={sold_has_null}")

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
                    db.execute(text("UPDATE telegram_posts SET tg_status = 'archived', needs_manual_edit = false WHERE id = :id"), {"id": db_id})
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
                        # Check if this size is sold — including fuzzy match for range sizes like '36-37'
                        is_sold = (sz in all_sold_sizes or sz_int in all_sold_sizes or
                                   sold_has_null or  # product without size but sold → treat all as sold
                                   any(sz_int == s.split('-')[0] or sz_int == s.split('-')[-1]
                                       for s in all_sold_sizes if '-' in s))
                        # Check if still available — also fuzzy match ranges
                        is_avail = (sz in available_sizes or sz_int in available_sizes or
                                    any(sz_int == s.split('-')[0] or sz_int == s.split('-')[-1]
                                        for s in available_sizes if '-' in s))
                        if is_sold and not is_avail:
                            sizes_to_remove.append(sz)

                    if not sizes_to_remove:
                        # All sizes in this post still have stock — skip entirely
                        result["skipped"] += 1
                        logger.info(f"⏭ Skipped msg {msg_id} in {chat_title} — all sizes still available: {live_sizes}")
                        continue

                    sizes_remaining = [s for s in live_sizes if s not in sizes_to_remove]

                    if sizes_remaining:
                        # ── There are still unsold sizes — try to EDIT the post ──
                        new_text = live_text
                        for sz in sizes_to_remove:
                            new_text = self._remove_size_line(new_text, sz)

                        if new_text != live_text:
                            try:
                                await self.client.edit_message(chat_entity, int(msg_id), new_text)
                                db.execute(
                                    text("UPDATE telegram_posts SET sizes_in_post = :sizes, is_multi_size = :multi, needs_manual_edit = false WHERE id = :id"),
                                    {"sizes": json.dumps(sizes_remaining), "multi": len(sizes_remaining) > 1, "id": db_id}
                                )
                                result["edited"] += 1
                                logger.info(f"✏️ Edited msg {msg_id} in {chat_title}: removed sizes {sizes_to_remove}, kept {sizes_remaining}")
                                continue  # Edit succeeded — DO NOT delete
                            except Exception as edit_err:
                                # Edit failed (e.g. forwarded message can't be edited)
                                # DO NOT delete — there are still unsold sizes!
                                # Mark for manual editing in UI
                                db.execute(
                                    text("UPDATE telegram_posts SET needs_manual_edit = true WHERE id = :id"),
                                    {"id": db_id}
                                )
                                logger.warning(f"⚠️ Cannot edit msg {msg_id} in {chat_title} (marked for manual edit): {edit_err}")
                                result["skipped"] += 1
                                result.setdefault("needs_manual_edit", []).append({
                                    "chat": chat_title, "msg_id": msg_id,
                                    "sizes_to_remove": sizes_to_remove, "sizes_remaining": sizes_remaining
                                })
                                continue
                        else:
                            # Text didn't change — size lines not found in text
                            # Still DO NOT delete — unsold sizes exist
                            db.execute(
                                text("UPDATE telegram_posts SET needs_manual_edit = true WHERE id = :id"),
                                {"id": db_id}
                            )
                            logger.warning(f"⚠️ Could not match size lines {sizes_to_remove} in msg {msg_id} — marked for manual edit (remaining: {sizes_remaining})")
                            result["skipped"] += 1
                            result.setdefault("needs_manual_edit", []).append({
                                "chat": chat_title, "msg_id": msg_id,
                                "sizes_to_remove": sizes_to_remove, "sizes_remaining": sizes_remaining
                            })
                            continue
                    else:
                        # All sizes in this post are sold — fall through to delete
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
                delete_ok = True  # nothing to delete = already done by prior iteration
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
                        delete_ok = False

                # Update DB status — archive only if TG delete actually succeeded;
                # otherwise keep post 'published' and flag for manual edit so UI shows it
                if delete_ok:
                    db.execute(
                        text("UPDATE telegram_posts SET tg_status = 'archived', needs_manual_edit = false WHERE id = :id"),
                        {"id": db_id}
                    )
                else:
                    db.execute(
                        text("UPDATE telegram_posts SET needs_manual_edit = true WHERE id = :id"),
                        {"id": db_id}
                    )
                    result.setdefault("needs_manual_edit", []).append({
                        "chat": chat_title, "msg_id": msg_id, "reason": "delete_failed"
                    })

            except Exception as e:
                logger.warning(f"⚠️ Error processing post {msg_id} in {chat_title}: {e}")
                result["failed"].append({"chat": chat_title, "msg_id": msg_id, "action": "process", "error": str(e)})

        db.commit()
        logger.info(f"✅ Unpublished product {product_id}: forwarded={result['forwarded']}, deleted={result['deleted']}, edited={result['edited']}, skipped={result['skipped']}")
        return result

    @staticmethod
    def _remove_size_line(text_content: str, size: str) -> str:
        """Remove a specific size from post text.

        Handles all formats:
          1. Bullet lines:   "— 40 (на ніжку 26 см)" → remove entire line
          2. Inline list:    "Розміри: 39, 40, 41" → remove "40" from list
          3. Range in list:  "Розміри: 38-39; 40-41" → remove matching range

        Also cleans up orphaned headers if no sizes remain.
        """
        # Normalize all dash variants to plain hyphen for comparison
        def _normalize(s: str) -> str:
            return s.replace('–', '-').replace('—', '-').replace(',', '.').strip()

        size_norm = _normalize(size)
        size_int = size_norm.split('.')[0].split('-')[0]

        def _size_matches(candidate: str) -> bool:
            """Check if a candidate size string matches the target size."""
            c = _normalize(candidate)
            c_int = c.split('.')[0].split('-')[0]  # first number for ranges
            # Exact match
            if c == size_norm:
                return True
            # Integer match (42 matches 42.0)
            if c_int == size_int and '.' not in size_norm:
                return True
            # Range match: size "40" matches range "40-41"
            if '-' in c:
                parts = c.split('-')
                if len(parts) == 2:
                    if parts[0].strip() == size_int or parts[1].strip() == size_int:
                        return True
            return False

        lines = text_content.split('\n')
        new_lines = []

        for line in lines:
            # Check bullet-line format: "— 40 ...", "- 41 ...", "• 42 ..."
            bullet_match = re.match(r'^[\s]*[—\-•·]\s*(\d{2}(?:[.,]\d{1,2})?(?:\s*[-–—]\s*\d{2}(?:[.,]\d{1,2})?)?)\b', line)
            if bullet_match:
                if _size_matches(bullet_match.group(1)):
                    continue  # Remove this bullet line

            # Check inline format on header line: "Розміри: 39, 40, 41"
            if SIZE_HEADER.search(line):
                # Check if sizes are inline (on the same line as header)
                header_end = SIZE_HEADER.search(line).end()
                after_header = line[header_end:]
                # Find all size tokens (numbers, possibly with ranges)
                size_tokens = re.findall(r'(\d{2}(?:[.,]\d{1,2})?\s*[-–—]\s*\d{2}(?:[.,]\d{1,2})?|\d{2}(?:[.,]\d{1,2})?)', after_header)
                if size_tokens and any(_size_matches(t) for t in size_tokens):
                    # Remove matching sizes from inline list
                    remaining_tokens = [t for t in size_tokens if not _size_matches(t)]
                    if remaining_tokens:
                        # Rebuild the line with remaining sizes
                        # Detect separator: comma, semicolon, or space
                        sep = ', '
                        if ';' in after_header:
                            sep = '; '
                        new_after = sep.join(remaining_tokens)
                        new_line = line[:header_end] + ' ' + new_after
                        new_lines.append(new_line.rstrip())
                    else:
                        # No sizes remain — remove the entire header line
                        pass
                    continue

            new_lines.append(line)

        # ── Reformat: if only ONE size remains, convert to single-size format ──
        # "Розміри:\n— 40 (на ніжку 26 см)" → "Розмір: 40 (на ніжку 26 см)"
        remaining_bullets = BULLET_SIZE_PATTERN.findall('\n'.join(new_lines))
        if len(remaining_bullets) == 1:
            # Find header line and bullet line using flexible matching
            header_re = re.compile(r'(?:[Рр]озмір[иі]?|[Зз]аміри)', re.UNICODE)
            header_idx = None
            bullet_idx = None
            bullet_line_content = ""
            for i, line in enumerate(new_lines):
                if header_re.search(line) and header_idx is None:
                    header_idx = i
                bm = re.match(r'^[\s]*[—\-•·]\s*(.*)', line)
                if bm and BULLET_SIZE_PATTERN.search(line) and bullet_idx is None:
                    bullet_idx = i
                    bullet_line_content = bm.group(1).strip()  # "40 (на ніжку 26 см)"
            if header_idx is not None and bullet_idx is not None and bullet_line_content:
                # Get the emoji/prefix from the header line (e.g. "👣 ")
                header_line = new_lines[header_idx]
                hm = header_re.search(header_line)
                prefix = header_line[:hm.start()]  # everything before "Розмір..."
                # Build new single line: "👣 Розмір: 40 (на ніжку 26 см)"
                new_single_line = f"{prefix}Розмір: {bullet_line_content}"
                # Replace header + bullet with the single line
                rebuilt = []
                for i, line in enumerate(new_lines):
                    if i == header_idx:
                        rebuilt.append(new_single_line)
                    elif i == bullet_idx:
                        continue  # skip old bullet line
                    else:
                        rebuilt.append(line)
                new_lines = rebuilt

        # Clean up orphaned size header if no size lines/tokens remain
        result_text = '\n'.join(new_lines)
        has_header = SIZE_HEADER.search(result_text)
        has_bullets = BULLET_SIZE_PATTERN.search(result_text)
        if has_header and not has_bullets:
            # Check if header line has inline sizes
            has_inline = False
            for line in new_lines:
                if SIZE_HEADER.search(line):
                    after = line[SIZE_HEADER.search(line).end():]
                    if INLINE_SIZE_PATTERN.search(after):
                        has_inline = True
                        break
            if not has_inline:
                # Remove orphaned header
                cleaned = [l for l in new_lines if not SIZE_HEADER.search(l)]
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
        total_forwarded = sum(r.get("forwarded", 0) for r in results)
        total_edited = sum(r.get("edited", 0) for r in results)
        total_skipped = sum(r.get("skipped", 0) for r in results)
        total_failed = sum(len(r.get("failed", [])) for r in results)
        return {
            "products_processed": len(product_ids),
            "total_deleted": total_deleted,
            "total_forwarded": total_forwarded,
            "total_edited": total_edited,
            "total_skipped": total_skipped,
            "total_failed": total_failed,
            "details": results,
        }

    async def verify_archived_posts(self, db: Session, limit: int = 500) -> Dict:
        """Walk through telegram_posts marked 'archived' and verify each really
        no longer exists in Telegram. If a post is still live (B2-bug victim,
        or external archive flag flipped erroneously), flip it back to
        'published' so the UI surfaces it again.

        Read-only against Telegram; only DB writes are status flips back.
        """
        if not self.client:
            return {"error": "Not connected to Telegram"}

        rows = db.execute(
            text("""
                SELECT id, chat_id, message_id, chat_title
                FROM telegram_posts
                WHERE tg_status = 'archived'
                ORDER BY id DESC
                LIMIT :lim
            """),
            {"lim": limit}
        ).fetchall()

        stats = {"checked": len(rows), "restored": 0, "still_gone": 0, "errors": 0}
        if not rows:
            return stats

        # Group by chat to minimize entity resolution
        by_chat: Dict[int, List] = {}
        for r in rows:
            by_chat.setdefault(int(r[1]), []).append(r)

        for chat_id, posts in by_chat.items():
            try:
                chat_entity = await self._resolve_entity(str(chat_id))
            except Exception as e:
                logger.warning(f"verify_archived: cannot resolve chat {chat_id}: {e}")
                stats["errors"] += len(posts)
                continue

            for db_id, _cid, msg_id, chat_title in posts:
                try:
                    tg_msg = await self.client.get_messages(chat_entity, ids=int(msg_id))
                    if tg_msg is None:
                        stats["still_gone"] += 1
                        continue
                    # Live — flip back to published
                    db.execute(
                        text("""UPDATE telegram_posts
                                SET tg_status = 'published', needs_manual_edit = false
                                WHERE id = :id"""),
                        {"id": db_id}
                    )
                    stats["restored"] += 1
                    logger.info(f"verify_archived: restored msg {msg_id} in {chat_title}")
                except Exception as e:
                    stats["errors"] += 1
                    logger.warning(f"verify_archived: error msg {msg_id} in {chat_title}: {e}")

        db.commit()
        logger.info(f"✅ verify_archived done: {stats}")
        return stats

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
            ON CONFLICT (chat_id, message_id) DO UPDATE SET
                -- Поля що можуть змінитись при редагуванні поста у Telegram:
                -- текст, витягнутий номер товару, лінк на товар (якщо номер виправлено),
                -- розміри, прапор multi-size. Решта (chat/thread/date) лишається.
                message_text       = EXCLUDED.message_text,
                product_number_raw = EXCLUDED.product_number_raw,
                product_id         = EXCLUDED.product_id,
                sizes_in_post      = EXCLUDED.sizes_in_post,
                is_multi_size      = EXCLUDED.is_multi_size,
                -- Знімаємо прапор "потребує редагування" якщо текст оновлено
                needs_manual_edit  = false
            WHERE
                -- Тільки якщо щось дійсно змінилось — щоб не оновлювати updated_at
                -- та не triggerati каскадні effects на кожен скан.
                telegram_posts.message_text       IS DISTINCT FROM EXCLUDED.message_text
             OR telegram_posts.product_number_raw IS DISTINCT FROM EXCLUDED.product_number_raw
             OR telegram_posts.product_id         IS DISTINCT FROM EXCLUDED.product_id
             OR telegram_posts.sizes_in_post      IS DISTINCT FROM EXCLUDED.sizes_in_post
             OR telegram_posts.is_multi_size      IS DISTINCT FROM EXCLUDED.is_multi_size
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

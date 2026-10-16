"""Черга постів у канал BrandStore: план у BMS → розклад Telegram → перевірка.

ЧОМУ ЦЕ ІСНУЄ. Раніше BMS віддавав Telegram форвард із `schedule=` і більше
нічого про нього не знав. Перевірка 16.10.2026 на живих даних:

* 20 із 95 запланованих форвардів Telegram мовчки НЕ відправив, хоча оригінал у
  форумі живий і структурно такий самий, як у тих, що вийшли (хвиля 10.09 —
  16 із 18). Помилку бачить лише клієнт власника («висять у Запланованих»), з
  сесії програми чужу чергу навіть не видно;
* розклад Telegram тримає лише 100 повідомлень на канал, і кожне фото альбому
  рахується окремо — тобто ~18 товарів наперед. План «29 товарів на тиждень»
  у таку стелю просто не влазить.

ЯК ТЕПЕР.
1. План живе в BMS (`telegram_scheduled_posts`, стан `planned`) — без стелі.
2. У розклад Telegram ставиться лише найближча частина плану, що влазить у
   бюджет місць (`QUEUE_BUDGET`, стан `queued`). Так пости виходять вчасно,
   навіть коли Mac у цю хвилину спить: відправляє сам Telegram.
3. Сторож (`tick`, раз на 5 хв, лише коли є робота) після часу публікації
   ПЕРЕВІРЯЄ сам канал. Знайшов — `sent`. Не знайшов — BMS пересилає сам
   (у межах кількох годин і денного часу) або переносить на наступний день;
   після `MAX_ATTEMPTS` — `failed`, і це видно в «Публікації → Розклад каналу».
4. Проданий товар знімається з плану й з розкладу Telegram ДО виходу —
   інакше або в канал іде проданий товар, або (оригінал у форумі вже видалено)
   форвард падає. Коли знімалка редагує ростовку, заплановане перевиставляється,
   щоб у канал пішов свіжий текст.

Що НЕ робить: не будить хмару (лише локальна БД і Telegram), не підключається
до Telegram, коли роботи нема (`needs_telegram`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Із 100 місць розкладу Telegram лишаємо запас: їх же бачить і власник, і
# хтось може запланувати щось руками з цього акаунта.
QUEUE_BUDGET = int(os.getenv("TELEGRAM_CHANNEL_QUEUE_BUDGET", "90"))
# Ближче цього до часу виходу в розклад Telegram уже не ставимо — BMS шле сам.
TOPUP_LEAD_SEC = 180
# Скільки чекати після часу виходу, перш ніж шукати пост у каналі.
VERIFY_GRACE_SEC = 180
# Якщо Telegram досі тримає пост у розкладі через стільки після часу виходу —
# він «завис»: прибираємо з розкладу й шлемо самі.
STUCK_AFTER_SEC = 20 * 60
# Досилати «зараз» лише якщо запізнення не більше за це і на дворі день.
LATE_SEND_MAX_SEC = int(os.getenv("TELEGRAM_CHANNEL_LATE_SEND_MAX_SEC", str(6 * 3600)))
SEND_HOURS = (8, 22)  # [з, до) за Києвом
MAX_ATTEMPTS = 3
# Скільки досилань «зараз» за один прохід сторожа: після вимкненого Mac у
# черзі може назбиратися багато прострочених — не вивалюємо їх у канал разом.
FALLBACK_PER_TICK = 3
TICK_SEC = int(os.getenv("TELEGRAM_CHANNEL_QUEUE_TICK_SEC", "300"))
# Якщо план є, а в розкладі Telegram на вигляд вільно — пробувати доставити
# не частіше за це (справжню зайнятість видно лише з Telegram).
TOPUP_RETRY_SEC = 30 * 60
MIN_GAP_SEC = 60          # два пости каналу ближче хвилини — сплеск
SHIFT_STEP_MIN = 2

ACTIVE_STATES = ("planned", "queued", "cancelling")
_LAST_TOPUP_TRY = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Допоміжне
# ─────────────────────────────────────────────────────────────────────────────

def _tp():
    try:
        from services import telegram_publisher
    except ImportError:  # pragma: no cover - запуск як backend.*
        from backend.services import telegram_publisher
    return telegram_publisher


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ids(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    out: List[int] = []
    for raw in value or []:
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _kyiv(value: datetime) -> datetime:
    return _aware(value).astimezone(_tp().KYIV_TZ)


def _is_day(now: datetime) -> bool:
    hour = _kyiv(now).hour
    return SEND_HOURS[0] <= hour < SEND_HOURS[1]


def _clean_pnum(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw.startswith("#") else f"#{raw}" if raw else ""


def _pnum_pattern(pnum: str) -> Optional[re.Pattern]:
    """`#Ф4180` як окремий токен: щоб не зловити `#Ф41801` чи `#4180`."""
    clean = _clean_pnum(pnum)
    if not clean:
        return None
    return re.compile(re.escape(clean) + r"(?![\w-])", re.IGNORECASE)


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


# Товар ще продається, якщо хоч один рядок цього номера «Непродано» (ростовка —
# це кілька рядків з одним номером). Те саме правило, що в undelivered_to_channel.
_AVAILABLE_SQL = """
    EXISTS (
        SELECT 1 FROM products p2
        LEFT JOIN statuses st2 ON st2.id = p2.statusid
        WHERE p2.productnumber = p.productnumber
          AND COALESCE(st2.statusname, '') = 'Непродано'
    )
"""


def _row_dict(row: Any) -> Dict[str, Any]:
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


# ─────────────────────────────────────────────────────────────────────────────
# План (лише БД)
# ─────────────────────────────────────────────────────────────────────────────

def free_slot(db: Session, desired: datetime, *, exclude_id: Optional[int] = None,
              taken: Optional[Iterable[datetime]] = None) -> datetime:
    """Найближчий до `desired` час, вільний від інших постів каналу на хвилину.

    Telegram отримав би кілька пересилань поспіль, а людина побачила б у каналі
    «пачку». Зсуваємо вперед кроком 2 хв — так само, як діалог пакета.
    """
    desired = _aware(desired)
    rows = db.execute(text("""
        SELECT scheduled_at FROM telegram_scheduled_posts
        WHERE state IN ('planned', 'queued')
          AND (CAST(:exclude AS INTEGER) IS NULL OR id <> CAST(:exclude AS INTEGER))
          AND scheduled_at BETWEEN :lo AND :hi
    """), {
        "exclude": exclude_id,
        "lo": desired - timedelta(hours=6),
        "hi": desired + timedelta(days=2),
    }).fetchall()
    busy = [_aware(r[0]) for r in rows] + [_aware(t) for t in (taken or [])]
    when = desired
    for _ in range(2000):
        if all(abs((when - other).total_seconds()) >= MIN_GAP_SEC for other in busy):
            return when
        when += timedelta(minutes=SHIFT_STEP_MIN)
    return when


def next_day_same_time(original: datetime, now: Optional[datetime] = None) -> datetime:
    """Той самий час доби (за Києвом), найближчий у майбутньому ≥ +5 хв."""
    now = now or _now()
    tz = _tp().KYIV_TZ
    local = _kyiv(original)
    candidate = _kyiv(now).replace(hour=local.hour, minute=local.minute, second=0, microsecond=0)
    while candidate <= _kyiv(now) + timedelta(minutes=5):
        candidate += timedelta(days=1)
    return candidate.astimezone(tz)


def _close_siblings(db: Session, product_id: int, reason: str, *,
                    states: Sequence[str], exclude_id: Optional[int] = None) -> None:
    """Закрити інші пункти того самого номера (ростовка = кілька рядків)."""
    db.execute(text("""
        UPDATE telegram_scheduled_posts s
        SET state = CASE WHEN s.state = 'queued' THEN 'cancelling' ELSE 'cancelled' END,
            last_error = :reason, updated_at = now()
        FROM products p, products requested
        WHERE p.id = s.product_id AND requested.id = :pid
          AND p.productnumber = requested.productnumber
          AND s.state = ANY(:states)
          AND (CAST(:exclude AS INTEGER) IS NULL OR s.id <> CAST(:exclude AS INTEGER))
    """), {"pid": product_id, "reason": reason, "states": list(states), "exclude": exclude_id})


def plan_item(db: Session, *, product_id: int, productnumber: str, when: datetime,
              source_ids: Sequence[int], silent: bool = False) -> Dict[str, Any]:
    """Записати пост каналу в план BMS. У Telegram нічого не надсилає.

    Новий пост того самого номера замінює попередній активний: інакше після
    «Опублікувати повторно» в канал пішли б два однакові пости.
    """
    tp = _tp()
    _close_siblings(db, product_id, "Замінено новим постом цього товару",
                    states=("planned", "queued", "failed"))
    at = free_slot(db, when)
    sources = [int(x) for x in source_ids]
    row = db.execute(text("""
        INSERT INTO telegram_scheduled_posts
            (product_id, product_number, chat_id, chat_title, scheduled_at,
             original_scheduled_at, source_chat_id, source_message_id,
             source_message_ids, photo_count, silent, state, updated_at)
        VALUES (:pid, :pnum, :cid, :ctitle, :at, :at, :scid, :smid,
                CAST(:sids AS JSONB), :photos, :silent, 'planned', now())
        RETURNING id
    """), {
        "pid": product_id, "pnum": productnumber, "cid": tp.CHANNEL_CHAT_ID,
        "ctitle": tp.CHANNEL_TITLE, "at": at, "scid": tp.FORUM_CHAT_ID,
        "smid": sources[0] if sources else None, "sids": json.dumps(sources),
        "photos": len(sources) or 1, "silent": bool(silent),
    }).fetchone()
    db.commit()
    return {
        "id": int(row[0]),
        "scheduled_at": at.isoformat(),
        "shifted": abs((at - _aware(when)).total_seconds()) >= 1,
    }


def on_product_unpublished(db: Session, productnumber: str) -> Dict[str, int]:
    """Хук знімалки (лише БД): продано повністю — зняти з плану; ростовку
    відредаговано — перевиставити, щоб у канал пішов свіжий текст."""
    if not productnumber:
        return {"cancelled": 0, "refresh": 0}
    available = db.execute(text(f"""
        SELECT {_AVAILABLE_SQL} FROM products p WHERE p.productnumber = :n LIMIT 1
    """), {"n": productnumber}).scalar()
    if not available:
        res = db.execute(text("""
            UPDATE telegram_scheduled_posts s
            SET state = CASE WHEN s.state = 'queued' THEN 'cancelling' ELSE 'cancelled' END,
                last_error = 'Товар продано — у канал не потрібен', updated_at = now()
            FROM products p
            WHERE p.id = s.product_id AND p.productnumber = :n
              AND s.state IN ('planned', 'queued')
        """), {"n": productnumber})
        db.commit()
        return {"cancelled": res.rowcount or 0, "refresh": 0}
    res = db.execute(text("""
        UPDATE telegram_scheduled_posts s
        SET needs_refresh = true, updated_at = now()
        FROM products p
        WHERE p.id = s.product_id AND p.productnumber = :n AND s.state = 'queued'
    """), {"n": productnumber})
    db.commit()
    return {"cancelled": 0, "refresh": res.rowcount or 0}


def mark_sold_items(db: Session) -> int:
    """Активні пункти, чий товар уже продано (будь-яким шляхом) — зняти."""
    # Без JOIN у FROM: рядок, чий товар видалили (product_id = NULL), теж має
    # знятися — інакше needs_telegram бачив би його вічно й будив Telegram.
    res = db.execute(text("""
        UPDATE telegram_scheduled_posts s
        SET state = CASE WHEN s.state = 'queued' THEN 'cancelling' ELSE 'cancelled' END,
            last_error = 'Товар продано — у канал не потрібен', updated_at = now()
        WHERE s.state IN ('planned', 'queued')
          AND NOT EXISTS (
              SELECT 1 FROM products p
              JOIN products p2 ON p2.productnumber = p.productnumber
              LEFT JOIN statuses st2 ON st2.id = p2.statusid
              WHERE p.id = s.product_id
                AND COALESCE(st2.statusname, '') = 'Непродано'
          )
    """))
    db.commit()
    return res.rowcount or 0


def needs_telegram(db: Session, now: Optional[datetime] = None) -> bool:
    """Чи є робота, заради якої варто підключатися до Telegram. Лише БД."""
    now = now or _now()
    row = db.execute(text(f"""
        SELECT
          COUNT(*) FILTER (WHERE s.state = 'cancelling' OR (s.state = 'queued' AND s.needs_refresh)),
          COUNT(*) FILTER (WHERE s.state IN ('planned', 'queued') AND NOT {_AVAILABLE_SQL}),
          COUNT(*) FILTER (WHERE s.state = 'queued' AND s.scheduled_at <= :verify_before),
          COUNT(*) FILTER (WHERE s.state = 'planned' AND s.scheduled_at <= :lead),
          COUNT(*) FILTER (WHERE s.state = 'planned' AND s.scheduled_at > :lead),
          COALESCE(SUM(s.photo_count) FILTER (WHERE s.state = 'queued'), 0)
        FROM telegram_scheduled_posts s
        LEFT JOIN products p ON p.id = s.product_id
        WHERE s.state IN ('planned', 'queued', 'cancelling')
    """), {
        "verify_before": now - timedelta(seconds=VERIFY_GRACE_SEC),
        "lead": now + timedelta(seconds=TOPUP_LEAD_SEC),
    }).fetchone()
    pending_changes, sold, due, overdue, future_planned, queued_photos = (int(x or 0) for x in row)
    if pending_changes or sold or due or overdue:
        return True
    if future_planned and queued_photos < QUEUE_BUDGET:
        return time.monotonic() - _LAST_TOPUP_TRY >= TOPUP_RETRY_SEC
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────

async def _peers(scanner: Any) -> Tuple[Any, Any]:
    from telethon.tl.types import PeerChannel
    tp = _tp()
    forum = await scanner.client.get_entity(PeerChannel(tp.FORUM_CHAT_ID))
    channel = await scanner.client.get_entity(PeerChannel(tp.CHANNEL_CHAT_ID))
    return forum, channel


async def scheduled_snapshot(scanner: Any, channel: Any) -> Tuple[Set[int], int]:
    """Що зараз стоїть у розкладі каналу (видно лише запланованого ЦИМ акаунтом)."""
    from telethon.tl.functions.messages import GetScheduledHistoryRequest
    history = await scanner.client(GetScheduledHistoryRequest(peer=channel, hash=0))
    messages = list(getattr(history, "messages", None) or [])
    ids = {int(m.id) for m in messages if getattr(m, "id", None) is not None}
    count = int(getattr(history, "count", None) or len(messages))
    return ids, count


async def _unschedule(scanner: Any, channel: Any, ids: Sequence[int]) -> None:
    if not ids:
        return
    from telethon.tl.functions.messages import DeleteScheduledMessagesRequest
    await scanner.client(DeleteScheduledMessagesRequest(peer=channel, id=[int(i) for i in ids]))


def _message_ids(result: Any) -> List[int]:
    items = result if isinstance(result, list) else [result]
    return [int(m.id) for m in items if m is not None and getattr(m, "id", None) is not None]


def _update(db: Session, item_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = []
    params: Dict[str, Any] = {"id": item_id}
    for key, value in fields.items():
        if key in ("tg_scheduled_ids", "source_message_ids"):
            sets.append(f"{key} = CAST(:{key} AS JSONB)")
            params[key] = json.dumps(value) if value is not None else None
        else:
            sets.append(f"{key} = :{key}")
            params[key] = value
    sets.append("updated_at = now()")
    db.execute(text(f"UPDATE telegram_scheduled_posts SET {', '.join(sets)} WHERE id = :id"), params)
    db.commit()


def _record_channel_post(db: Session, item: Dict[str, Any], message_id: int, caption: str) -> None:
    """Пост у каналі — одразу в telegram_posts: інакше до скану товар виглядає
    недоставленим, а знімалка не знає, що його треба знімати і з каналу."""
    tp = _tp()
    sizes_raw = db.execute(text("""
        SELECT sizes_in_post FROM telegram_posts
        WHERE chat_id = :c AND message_id = :m LIMIT 1
    """), {"c": item.get("source_chat_id") or tp.FORUM_CHAT_ID,
           "m": item.get("source_message_id")}).scalar()
    try:
        sizes = json.loads(sizes_raw) if isinstance(sizes_raw, str) else list(sizes_raw or [])
    except ValueError:
        sizes = []
    tp._record_post(
        db, product_id=int(item["product_id"]), pnum=str(item.get("product_number") or ""),
        chat_id=tp.CHANNEL_CHAT_ID, chat_title=tp.CHANNEL_TITLE, chat_type="channel",
        thread_id=None, thread_title=None, message_id=int(message_id),
        caption=caption or "", grouped_id=None, sizes=[str(s) for s in sizes],
    )
    db.commit()


async def _forward(scanner: Any, forum: Any, channel: Any, item: Dict[str, Any],
                   schedule: Optional[datetime]) -> List[int]:
    sources = _ids(item.get("source_message_ids")) or _ids([item.get("source_message_id")])
    result = await scanner.client.forward_messages(
        entity=channel, messages=sources, from_peer=forum,
        schedule=schedule, silent=bool(item.get("silent")),
    )
    return _message_ids(result)


def _is_missing_source(exc: Exception) -> bool:
    raw = f"{type(exc).__name__} {exc}".upper()
    return any(t in raw for t in ("MESSAGE_ID_INVALID", "MESSAGEIDINVALID", "MESSAGE_IDS_EMPTY"))


def _is_schedule_full(exc: Exception) -> bool:
    raw = f"{type(exc).__name__} {exc}".upper()
    return "SCHEDULE_TOO_MUCH" in raw or "SCHEDULETOOMUCH" in raw


async def _fetch_sources(scanner: Any, forum: Any, items: Sequence[Dict[str, Any]]) -> Dict[int, Any]:
    heads = sorted({int(i["source_message_id"]) for i in items if i.get("source_message_id")})
    if not heads:
        return {}
    found = await scanner.client.get_messages(forum, ids=heads)
    found = found if isinstance(found, list) else [found]
    return {int(m.id): m for m in found if m is not None}


async def _channel_window(scanner: Any, channel: Any, since: datetime) -> List[Any]:
    """Повідомлення каналу від `since` (новіші першими), не більше 400."""
    out: List[Any] = []
    async for message in scanner.client.iter_messages(channel, limit=400):
        date = getattr(message, "date", None)
        if date is not None and _aware(date) < since:
            break
        out.append(message)
    return out


def find_delivered(item: Dict[str, Any], window: Sequence[Any], source: Any = None) -> Optional[Any]:
    """Пост товару в каналі, що вийшов не раніше ніж за 2 хв до запланованого.

    Головна ознака — текст рівно як в оригіналі форуму (форвард копіює підпис).
    Якщо оригінал уже видалено — номер товару як окремий токен (`#Ф4180`).
    """
    since = _aware(item["scheduled_at"]) - timedelta(seconds=120)
    source_text = _norm_text(getattr(source, "message", None)) if source is not None else ""
    pattern = _pnum_pattern(str(item.get("product_number") or ""))
    best = None
    for message in window:
        date = getattr(message, "date", None)
        if date is None or _aware(date) < since:
            continue
        body = _norm_text(getattr(message, "message", None))
        if not body:
            continue
        if (source_text and body == source_text) or (pattern and pattern.search(body)):
            if best is None or _aware(message.date) < _aware(best.date):
                best = message
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Кроки сторожа
# ─────────────────────────────────────────────────────────────────────────────

def _active_rows(db: Session, where: str, params: Optional[dict] = None) -> List[Dict[str, Any]]:
    rows = db.execute(text(f"""
        SELECT s.*, p.productnumber AS live_productnumber, {_AVAILABLE_SQL} AS available
        FROM telegram_scheduled_posts s
        LEFT JOIN products p ON p.id = s.product_id
        WHERE {where}
        ORDER BY s.scheduled_at, s.id
    """), params or {}).fetchall()
    return [_row_dict(r) for r in rows]


async def apply_pending(db: Session, scanner: Any, channel: Any = None) -> Dict[str, int]:
    """Прибрати з розкладу Telegram скасоване й перевиставити змінене."""
    rows = _active_rows(db, "s.state = 'cancelling' OR (s.state = 'queued' AND s.needs_refresh)")
    if not rows:
        return {"unscheduled": 0, "refreshed": 0}
    if channel is None:
        _forum, channel = await _peers(scanner)
    stats = {"unscheduled": 0, "refreshed": 0}
    for item in rows:
        try:
            await _unschedule(scanner, channel, _ids(item.get("tg_scheduled_ids")))
        except Exception as exc:  # уже вийшов/видалений — далі розбереться перевірка
            logger.warning("Канал: не вдалося прибрати з розкладу #%s: %s", item["id"], exc)
        if item["state"] == "cancelling":
            _update(db, item["id"], state="cancelled", tg_scheduled_ids=None)
            stats["unscheduled"] += 1
        else:
            _update(db, item["id"], state="planned", tg_scheduled_ids=None, needs_refresh=False)
            stats["refreshed"] += 1
    return stats


async def top_up(db: Session, scanner: Any, *, forum: Any = None, channel: Any = None) -> Dict[str, Any]:
    """Поставити в розклад Telegram найближчі пункти плану, що влазять у бюджет."""
    global _LAST_TOPUP_TRY
    _LAST_TOPUP_TRY = time.monotonic()
    now = _now()
    planned = _active_rows(db, "s.state = 'planned' AND s.scheduled_at > :lead",
                           {"lead": now + timedelta(seconds=TOPUP_LEAD_SEC)})
    result: Dict[str, Any] = {"queued": 0, "left_planned": len(planned), "used": None}
    if not planned:
        return result
    if forum is None or channel is None:
        forum, channel = await _peers(scanner)
    pending, used = await scheduled_snapshot(scanner, channel)
    result["used"] = used
    for item in planned:
        if not item.get("available"):
            continue  # зніме mark_sold_items
        photos = max(1, int(item.get("photo_count") or len(_ids(item.get("source_message_ids"))) or 1))
        if used + photos > QUEUE_BUDGET:
            break
        when = _aware(item["scheduled_at"])
        try:
            ids = await _forward(scanner, forum, channel, item, when)
            if not ids:
                # Telethon не завжди розбирає відповідь на заплановане — тоді
                # нові id беремо з різниці розкладу до і після.
                after, used_after = await scheduled_snapshot(scanner, channel)
                ids = sorted(after - pending)
                pending = after
            else:
                pending |= set(ids)
            _update(db, item["id"], state="queued", tg_scheduled_ids=ids, last_error=None)
            used += photos
            result["queued"] += 1
            result["left_planned"] -= 1
        except Exception as exc:
            if _is_schedule_full(exc):
                result["note"] = "Розклад Telegram заповнений — решта чекає в плані BMS"
                break
            if _tp()._is_rate_limit_error(str(exc)):
                result["note"] = "Telegram тимчасово обмежив запити — продовжу пізніше"
                break
            attempts = int(item.get("attempts") or 0) + 1
            if _is_missing_source(exc):
                _update(db, item["id"], state="failed", attempts=attempts,
                        last_error="Оригінал у форумі видалено — опублікуй товар заново")
            else:
                _update(db, item["id"], attempts=attempts,
                        state="failed" if attempts >= MAX_ATTEMPTS else "planned",
                        last_error=f"Не вдалося поставити в розклад Telegram: {exc}")
            logger.warning("Канал: не вдалося поставити #%s у розклад: %s", item["id"], exc)
    return result


async def _send_now(db: Session, scanner: Any, forum: Any, channel: Any,
                    item: Dict[str, Any], *, via: str) -> Tuple[bool, str]:
    try:
        ids = await _forward(scanner, forum, channel, item, None)
    except Exception as exc:
        if _is_missing_source(exc):
            return False, "Оригінал у форумі видалено — опублікуй товар заново"
        return False, str(exc) or type(exc).__name__
    if not ids:
        return False, "Telegram не повернув повідомлення"
    caption = ""
    try:
        sent = await scanner.client.get_messages(channel, ids=ids)
        sent = sent if isinstance(sent, list) else [sent]
        head = next((m for m in sent if m is not None and getattr(m, "message", None)), None)
        if head is not None:
            caption = head.message or ""
            ids = [int(head.id)] + [i for i in ids if i != int(head.id)]
    except Exception:
        pass
    _update(db, item["id"], state="sent", channel_message_id=ids[0], sent_at=_now(),
            sent_via=via, tg_scheduled_ids=None, last_error=None)
    try:
        _record_channel_post(db, item, ids[0], caption)
    except Exception as exc:
        db.rollback()
        logger.warning("Канал: пост #%s вийшов, але не записаний у telegram_posts: %s", item["id"], exc)
    return True, ""


async def verify_and_recover(db: Session, scanner: Any, *, forum: Any = None,
                             channel: Any = None, now: Optional[datetime] = None) -> Dict[str, int]:
    """Перевірити, що заплановане справді вийшло, і досилати те, що ні."""
    global _LAST_TOPUP_TRY
    now = now or _now()
    due = _active_rows(db, "s.state = 'queued' AND s.scheduled_at <= :before",
                       {"before": now - timedelta(seconds=VERIFY_GRACE_SEC)})
    overdue = _active_rows(db, "s.state = 'planned' AND s.scheduled_at <= :lead",
                           {"lead": now + timedelta(seconds=TOPUP_LEAD_SEC)})
    stats = {"sent": 0, "waiting": 0, "resent": 0, "rescheduled": 0, "failed": 0, "cancelled": 0}
    if not due and not overdue:
        return stats
    if forum is None or channel is None:
        forum, channel = await _peers(scanner)
    pending, _used = await scheduled_snapshot(scanner, channel)
    sources = await _fetch_sources(scanner, forum, due + overdue)

    to_recover: List[Dict[str, Any]] = []
    if due:
        since = min(_aware(i["scheduled_at"]) for i in due) - timedelta(seconds=120)
        window = await _channel_window(scanner, channel, since)
        for item in due:
            still_pending = set(_ids(item.get("tg_scheduled_ids"))) & pending
            if still_pending:
                if (now - _aware(item["scheduled_at"])).total_seconds() < STUCK_AFTER_SEC:
                    stats["waiting"] += 1
                    continue
                try:
                    await _unschedule(scanner, channel, sorted(still_pending))
                except Exception as exc:
                    logger.warning("Канал: не вдалося прибрати завислий #%s: %s", item["id"], exc)
                    stats["waiting"] += 1
                    continue
                item["_reason"] = "Telegram тримав пост у розкладі після часу виходу"
                to_recover.append(item)
                continue
            found = find_delivered(item, window, sources.get(int(item.get("source_message_id") or 0)))
            if found is not None:
                _update(db, item["id"], state="sent", channel_message_id=int(found.id),
                        sent_at=_aware(found.date), sent_via="telegram",
                        tg_scheduled_ids=None, last_error=None)
                try:
                    _record_channel_post(db, item, int(found.id), found.message or "")
                except Exception as exc:
                    db.rollback()
                    logger.warning("Канал: #%s вийшов, але не записаний: %s", item["id"], exc)
                stats["sent"] += 1
                _LAST_TOPUP_TRY = 0.0  # місце в розкладі звільнилось
                continue
            item["_reason"] = "Telegram не відправив заплановане пересилання"
            item["_count_attempt"] = True
            to_recover.append(item)

    for item in overdue:
        item["_reason"] = item.get("last_error") or "Час виходу настав, а пост не стояв у розкладі Telegram"
        to_recover.append(item)

    sends = 0
    for item in to_recover:
        if not item.get("available"):
            _update(db, item["id"], state="cancelled", tg_scheduled_ids=None,
                    last_error="Товар продано — у канал не потрібен")
            stats["cancelled"] += 1
            continue
        attempts = int(item.get("attempts") or 0) + (1 if item.get("_count_attempt") else 0)
        source_alive = int(item.get("source_message_id") or 0) in sources
        if not source_alive:
            _update(db, item["id"], state="failed", attempts=attempts, tg_scheduled_ids=None,
                    last_error="Оригінал у форумі видалено — опублікуй товар заново")
            stats["failed"] += 1
            continue
        if attempts >= MAX_ATTEMPTS:
            _update(db, item["id"], state="failed", attempts=attempts, tg_scheduled_ids=None,
                    last_error=f"{item['_reason']}; спроб: {attempts}")
            stats["failed"] += 1
            continue
        lateness = (now - _aware(item["scheduled_at"])).total_seconds()
        if lateness <= LATE_SEND_MAX_SEC and _is_day(now):
            if sends >= FALLBACK_PER_TICK:
                # Наступний прохід за 5 хв — не вивалюємо все в канал разом.
                _update(db, item["id"], state="planned", attempts=attempts, tg_scheduled_ids=None,
                        last_error=item["_reason"])
                continue
            item["attempts"] = attempts
            ok, error = await _send_now(db, scanner, forum, channel, item, via="bms")
            sends += 1
            if ok:
                _update(db, item["id"], attempts=attempts, last_error=f"{item['_reason']} — BMS доставив сам")
                stats["resent"] += 1
                await asyncio.sleep(_tp().BATCH_POST_GAP_SEC)
                continue
            attempts += 1
            if attempts >= MAX_ATTEMPTS or "видалено" in error:
                _update(db, item["id"], state="failed", attempts=attempts, tg_scheduled_ids=None,
                        last_error=error)
                stats["failed"] += 1
                continue
            item["_reason"] = f"Не вдалося доставити: {error}"
            if _tp()._is_rate_limit_error(error):
                _update(db, item["id"], state="planned", attempts=attempts, tg_scheduled_ids=None,
                        scheduled_at=now + timedelta(minutes=15), last_error=item["_reason"])
                stats["rescheduled"] += 1
                continue
        new_at = free_slot(db, next_day_same_time(item.get("original_scheduled_at") or item["scheduled_at"], now),
                           exclude_id=item["id"])
        _update(db, item["id"], state="planned", attempts=attempts, tg_scheduled_ids=None,
                scheduled_at=new_at,
                last_error=f"{item['_reason']} — перенесено на {_kyiv(new_at):%d.%m %H:%M}")
        stats["rescheduled"] += 1
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Точки входу
# ─────────────────────────────────────────────────────────────────────────────

async def run_with_scanner(db: Session, scanner: Any) -> Dict[str, Any]:
    """Повний прохід на вже відкритому з'єднанні (публікація, ручна дія, сторож)."""
    report: Dict[str, Any] = {"sold": mark_sold_items(db)}
    forum, channel = await _peers(scanner)
    report["pending"] = await apply_pending(db, scanner, channel)
    report["verify"] = await verify_and_recover(db, scanner, forum=forum, channel=channel)
    report["top_up"] = await top_up(db, scanner, forum=forum, channel=channel)
    return report


async def tick(*, force: bool = False, wait_lock: bool = False) -> Dict[str, Any]:
    """Один прохід сторожа. Без роботи до Telegram не підключається."""
    try:
        from models.database import SessionLocal
    except ImportError:  # pragma: no cover
        from backend.models.database import SessionLocal
    tp = _tp()
    db = SessionLocal()
    try:
        if not force and not needs_telegram(db):
            return {"ok": True, "skipped": "idle"}
        if tp._PUBLISH_LOCK.locked() and not wait_lock:
            return {"ok": True, "skipped": "busy"}
        async with tp._PUBLISH_LOCK:
            scanner, err = await tp._connect()
            if err:
                return {"ok": False, "error": err}
            try:
                report = await run_with_scanner(db, scanner)
            finally:
                await scanner.disconnect()
        logger.info("Канал-черга: %s", report)
        return {"ok": True, **report}
    except Exception as exc:
        db.rollback()
        logger.warning("Канал-черга: прохід не вдався: %s", exc)
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()


async def loop() -> None:
    """Фоновий сторож: раз на TICK_SEC, лише коли є робота."""
    await asyncio.sleep(40)
    while True:
        try:
            await tick()
        except Exception as exc:  # pragma: no cover - захист циклу
            logger.warning("Канал-черга: цикл: %s", exc)
        await asyncio.sleep(TICK_SEC)


# ─────────────────────────────────────────────────────────────────────────────
# Для інтерфейсу «Розклад каналу»
# ─────────────────────────────────────────────────────────────────────────────

def list_queue(db: Session, *, history_days: int = 14) -> Dict[str, Any]:
    rows = db.execute(text(f"""
        SELECT s.id, s.product_id, s.product_number, s.scheduled_at, s.original_scheduled_at,
               s.state, s.attempts, s.last_error, s.sent_at, s.sent_via, s.photo_count,
               s.silent, s.channel_message_id, s.created_at,
               b.brandname, p.model, p.price, st.statusname,
               {_AVAILABLE_SQL} AS available
        FROM telegram_scheduled_posts s
        LEFT JOIN products p ON p.id = s.product_id
        LEFT JOIN brands b ON b.id = p.brandid
        LEFT JOIN statuses st ON st.id = p.statusid
        WHERE s.state IN ('planned', 'queued', 'cancelling')
           OR (s.state = 'failed' AND {_AVAILABLE_SQL})
           OR COALESCE(s.sent_at, s.updated_at, s.scheduled_at) >= now() - make_interval(days => :days)
        ORDER BY s.scheduled_at DESC, s.id DESC
        LIMIT 1000
    """), {"days": int(history_days)}).fetchall()
    items = []
    for raw in rows:
        r = _row_dict(raw)
        items.append({
            "id": r["id"], "product_id": r["product_id"],
            "productnumber": str(r["product_number"] or "").lstrip("#"),
            "brand": r["brandname"], "model": r["model"],
            "price": float(r["price"]) if r["price"] is not None else None,
            "product_status": r["statusname"], "available": bool(r["available"]),
            "state": r["state"], "attempts": int(r["attempts"] or 0),
            "last_error": r["last_error"],
            "scheduled_at": _aware(r["scheduled_at"]).isoformat() if r["scheduled_at"] else None,
            "original_scheduled_at": _aware(r["original_scheduled_at"]).isoformat() if r["original_scheduled_at"] else None,
            "sent_at": _aware(r["sent_at"]).isoformat() if r["sent_at"] else None,
            "sent_via": r["sent_via"], "photo_count": r["photo_count"], "silent": bool(r["silent"]),
            "channel_message_id": r["channel_message_id"],
        })
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    upcoming = sorted((i for i in items if i["state"] in ("planned", "queued")),
                      key=lambda i: i["scheduled_at"] or "")
    return {
        "ok": True, "items": items, "counts": counts,
        "queued_photos": sum(int(i["photo_count"] or 0) for i in items if i["state"] == "queued"),
        "queue_budget": QUEUE_BUDGET,
        "next_at": upcoming[0]["scheduled_at"] if upcoming else None,
        "last_at": upcoming[-1]["scheduled_at"] if upcoming else None,
    }


def _get(db: Session, item_id: int) -> Optional[Dict[str, Any]]:
    rows = _active_rows(db, "s.id = :id", {"id": int(item_id)})
    return rows[0] if rows else None


def cancel(db: Session, item_id: int) -> Dict[str, Any]:
    item = _get(db, item_id)
    if not item:
        return {"ok": False, "error": "Пункт розкладу не знайдено"}
    if item["state"] not in ("planned", "queued"):
        return {"ok": False, "error": "Цей пост уже не в плані"}
    _update(db, item_id, state="cancelling" if item["state"] == "queued" else "cancelled",
            last_error="Скасовано вручну")
    return {"ok": True, "needs_telegram": item["state"] == "queued"}


def reschedule(db: Session, item_id: int, raw_when: Any) -> Dict[str, Any]:
    """Новий час для запланованого, а також «спробувати ще раз» для невдалого."""
    item = _get(db, item_id)
    if not item:
        return {"ok": False, "error": "Пункт розкладу не знайдено"}
    if item["state"] in ("sent", "cancelling"):
        return {"ok": False, "error": "Цей пост уже вийшов або знімається"}
    if not item.get("available"):
        return {"ok": False, "error": "Товар уже продано"}
    when, error = _tp()._validate_when(raw_when)
    if error or when is None:
        return {"ok": False, "error": error or "Не вибрано час"}
    at = free_slot(db, when, exclude_id=item_id)
    fields: Dict[str, Any] = {"scheduled_at": at, "original_scheduled_at": at, "last_error": None}
    if item["state"] == "queued":
        fields["needs_refresh"] = True
    else:
        fields.update(state="planned", tg_scheduled_ids=None)
        if item["state"] in ("failed", "cancelled"):
            fields["attempts"] = 0
            # Старі невдалі спроби цього ж товару більше не «висять» у списку.
            _close_siblings(db, int(item["product_id"]), "Замінено новою спробою",
                            states=("failed", "planned", "queued"), exclude_id=item_id)
    _update(db, item_id, **fields)
    return {"ok": True, "scheduled_at": at.isoformat(),
            "shifted": abs((at - when).total_seconds()) >= 1}


async def send_now(db: Session, scanner: Any, item_id: int) -> Dict[str, Any]:
    """Надіслати в канал негайно (з розкладу Telegram прибирається)."""
    item = _get(db, item_id)
    if not item:
        return {"ok": False, "error": "Пункт розкладу не знайдено"}
    if item["state"] in ("sent", "cancelling"):
        return {"ok": False, "error": "Цей пост уже вийшов або знімається"}
    if not item.get("available"):
        return {"ok": False, "error": "Товар уже продано"}
    forum, channel = await _peers(scanner)
    if item["state"] == "queued":
        await _unschedule(scanner, channel, _ids(item.get("tg_scheduled_ids")))
    ok, error = await _send_now(db, scanner, forum, channel, item, via="manual")
    if not ok:
        _update(db, item_id, state="failed", tg_scheduled_ids=None, last_error=error)
        return {"ok": False, "error": error}
    return {"ok": True}

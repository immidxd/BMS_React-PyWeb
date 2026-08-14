"""Контент-план: міст між Obsidian TaskNotes і публікаціями BMS.

Obsidian лишається редактором ПЛАНУ (коли, який канал, яка рубрика), BMS —
виконавцем (які саме товари, статус, посилання на пост). Кожна сторона пише
лише у свої поля, тому двобічна синхронізація не створює конфліктів.

**Чому імпорт, а не читання наживо:** HTTP API TaskNotes працює лише поки
відкритий Obsidian. Якщо BMS читатиме план наживо, то «запланував і закрив
Obsidian» означало б порожній календар. Тому план переноситься у
``content_plan_slots`` і живе в BMS самостійно.

Контракт API TaskNotes 4.12.3 (перевірено на встановленому плагіні):
  * сервер слухає лише ``127.0.0.1``, авторизація — ``Bearer``-токеном;
  * ``GET /api/tasks`` — базовий посторінковий список (``limit`` ≤ 200,
    ``offset``); фільтрувальні query-параметри він свідомо відхиляє з 400 і
    відсилає до ``POST /api/tasks/query``. Тому ми вичитуємо сторінки повністю
    й фільтруємо на боці Python — так міст не залежить від DSL запитів;
  * задача адресується **шляхом нотатки** у vault: ``PUT /api/tasks/<path>``.
"""

import os
import re
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from dotenv import load_dotenv

_ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env'))
load_dotenv(_ENV_PATH, override=False)

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:8080"
_PAGE_LIMIT = 200
_MAX_PAGES = 50
_TIMEOUT = 6

# Канал публікації береться з ``contexts`` задачі — так, як користувач уже
# розмітив свій контент-план у vault.
CHANNEL_CONTEXTS = {
    "telegram": "telegram",
    "телеграм": "telegram",
    "instagram": "instagram",
    "інстаграм": "instagram",
    "ig": "instagram",
    "viber": "viber",
    "вайбер": "viber",
}

# Формат — другим контекстом (`stories`) або зі змісту заголовка.
FORMAT_CONTEXTS = {
    "stories": "stories",
    "story": "stories",
    "сторіз": "stories",
    "reels": "reels",
    "reel": "reels",
    "feed": "feed",
    "carousel": "feed",
    "collage": "collage",
}

DEFAULT_FORMAT = {
    "instagram": "feed",
    "telegram": "post",
    "viber": "collage",
}

# Скільки товарів очікує слот, якщо в заголовку немає явного числа.
DEFAULT_PRODUCT_COUNT = {
    ("telegram", "post"): 5,
    ("viber", "collage"): 6,
    ("instagram", "feed"): 1,
    ("instagram", "stories"): 1,
    ("instagram", "reels"): 1,
}

_COMPLETED_PLAN_STATUSES = {"done", "completed", "published"}


class TaskNotesUnavailable(RuntimeError):
    """Obsidian закритий, API вимкнений або токен не підходить."""


def api_config() -> Tuple[str, str]:
    """URL і токен TaskNotes API з ``.env``."""
    url = (os.getenv("TASKNOTES_API_URL") or DEFAULT_API_URL).rstrip("/")
    token = os.getenv("TASKNOTES_API_TOKEN") or ""
    return url, token


def _headers(token: str) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _unwrap(payload: Any) -> Any:
    """TaskNotes загортає відповідь у ``{success, data}``."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Чисті функції розбору — тестуються без Obsidian і без БД.
# ─────────────────────────────────────────────────────────────────────────────

def detect_channel(contexts: List[str], title: str = "") -> Optional[str]:
    """Канал публікації зі списку контекстів, з підстраховкою по заголовку."""
    for context in contexts or []:
        key = str(context).strip().lower()
        if key in CHANNEL_CONTEXTS:
            return CHANNEL_CONTEXTS[key]
    lowered = (title or "").lower()
    for key, channel in CHANNEL_CONTEXTS.items():
        if key in lowered:
            return channel
    return None


def detect_format(channel: str, contexts: List[str], title: str = "") -> str:
    """Формат поста: явний контекст → натяк у заголовку → типовий для каналу."""
    for context in contexts or []:
        key = str(context).strip().lower()
        if key in FORMAT_CONTEXTS:
            return FORMAT_CONTEXTS[key]
    lowered = (title or "").lower()
    for key, fmt in FORMAT_CONTEXTS.items():
        if key in lowered:
            return fmt
    return DEFAULT_FORMAT.get(channel, "post")


def detect_product_count(title: str, channel: str, post_format: str) -> int:
    """Скільки товарів треба на слот.

    «топ-5 товарів» → 5. Явне число в заголовку має пріоритет над типовим для
    каналу, бо саме воно — намір користувача.
    """
    match = re.search(r"(?:топ|top)\s*[-–—]?\s*(\d{1,2})", title or "", re.IGNORECASE)
    if not match:
        match = re.search(r"(\d{1,2})\s*(?:товар|фото|пост)", title or "", re.IGNORECASE)
    if match:
        count = int(match.group(1))
        if 1 <= count <= 20:
            return count
    return DEFAULT_PRODUCT_COUNT.get((channel, post_format), 1)


def detect_rubric(title: str) -> str:
    """Груба рубрикація — вона керує правилом добору товарів."""
    lowered = (title or "").lower()
    if re.search(r"топ|top", lowered):
        return "top"
    if re.search(r"добірк|подборк|digest", lowered):
        return "digest"
    if re.search(r"нов(инк|ий|е)|new", lowered):
        return "new_arrivals"
    return "general"


def parse_datetime(value: Any) -> Optional[datetime]:
    """ISO-дата TaskNotes: з часом або сама лише дата."""
    if not value:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    normalized = text_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    logger.warning("Контент-план: не розібрано дату %r", value)
    return None


def slot_from_task(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Задача TaskNotes → слот публікації, або ``None`` якщо це не публікація.

    Задачі без каналу (``planning`` тощо) свідомо пропускаються: вкладка
    «Контент-план» показує лише те, що BMS уміє виконати.
    """
    path = task.get("path") or task.get("id")
    if not path:
        return None

    contexts = task.get("contexts") or []
    if isinstance(contexts, str):
        contexts = [contexts]
    title = str(task.get("title") or "").strip()

    channel = detect_channel(contexts, title)
    if not channel:
        return None

    post_format = detect_format(channel, contexts, title)
    scheduled_at = parse_datetime(task.get("scheduled") or task.get("due"))
    plan_status = str(task.get("status") or "planned").strip().lower()

    return {
        "source_id": str(path),
        "title": title or str(path),
        "channel": channel,
        "post_format": post_format,
        "rubric": detect_rubric(title),
        "product_count": detect_product_count(title, channel, post_format),
        "scheduled_at": scheduled_at,
        "plan_status": plan_status,
        "source_modified_at": parse_datetime(task.get("dateModified")),
        "plan_completed": plan_status in _COMPLETED_PLAN_STATUSES,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP-клієнт TaskNotes
# ─────────────────────────────────────────────────────────────────────────────

def check_connection() -> Dict[str, Any]:
    """Чи доступний API — щоб UI міг пояснити «відкрийте Obsidian» замість 500."""
    url, token = api_config()
    if not token:
        return {"connected": False, "reason": "no_token",
                "message": "У .env немає TASKNOTES_API_TOKEN"}
    try:
        response = requests.get(f"{url}/api/health", headers=_headers(token), timeout=_TIMEOUT)
    except requests.RequestException:
        return {"connected": False, "reason": "unreachable",
                "message": "Obsidian закритий або HTTP API TaskNotes вимкнений"}
    if response.status_code in (401, 403):
        return {"connected": False, "reason": "unauthorized",
                "message": "TASKNOTES_API_TOKEN не збігається з токеном у налаштуваннях плагіна"}
    if response.status_code >= 400:
        return {"connected": False, "reason": "error",
                "message": f"TaskNotes відповів {response.status_code}"}
    return {"connected": True, "url": url}


def fetch_tasks() -> List[Dict[str, Any]]:
    """Усі задачі vault через посторінковий ``GET /api/tasks``."""
    url, token = api_config()
    if not token:
        raise TaskNotesUnavailable("У .env немає TASKNOTES_API_TOKEN")

    tasks: List[Dict[str, Any]] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        try:
            response = requests.get(
                f"{url}/api/tasks",
                params={"limit": _PAGE_LIMIT, "offset": offset},
                headers=_headers(token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TaskNotesUnavailable(
                "Obsidian закритий або HTTP API TaskNotes вимкнений"
            ) from exc

        if response.status_code in (401, 403):
            raise TaskNotesUnavailable("TASKNOTES_API_TOKEN не підходить")
        if response.status_code >= 400:
            raise TaskNotesUnavailable(f"TaskNotes відповів {response.status_code}")

        data = _unwrap(response.json()) or {}
        page = data.get("tasks") or []
        tasks.extend(page)

        pagination = data.get("pagination") or {}
        if not pagination.get("hasMore") or not page:
            break
        offset += len(page)

    return tasks


def fetch_slots(days_back: int = 7, days_ahead: int = 30) -> List[Dict[str, Any]]:
    """Слоти публікацій у вікні дат, відсортовані за часом."""
    now = datetime.now()
    window_start = now - timedelta(days=days_back)
    window_end = now + timedelta(days=days_ahead)

    slots = []
    for task in fetch_tasks():
        slot = slot_from_task(task)
        if not slot:
            continue
        scheduled = slot.get("scheduled_at")
        if scheduled is None:
            continue
        naive = scheduled.replace(tzinfo=None) if scheduled.tzinfo else scheduled
        if not (window_start <= naive <= window_end):
            continue
        slots.append(slot)

    slots.sort(key=lambda item: item["scheduled_at"])
    return slots


# ─────────────────────────────────────────────────────────────────────────────
# Вебхуки: Obsidian штовхає зміну сам, не чекаючи кнопки «Синхронізувати».
# ─────────────────────────────────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 над сирим тілом — саме так підписує TaskNotes.

    Плагін рахує підпис від ``JSON.stringify(payload)``, тому звіряти треба
    неперетравлене тіло запиту, а не перезібраний з dict JSON: будь-яка різниця
    у пробілах чи порядку ключів зламала б перевірку.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def extract_task_from_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Дістати задачу з тіла вебхука.

    Обгортка події між версіями плагіна може відрізнятись, тому шукаємо перший
    вкладений об'єкт, схожий на задачу (має ``path``), замість жорсткої
    прив'язки до конкретної форми ``{data: {task: …}}``.
    """
    if isinstance(payload, dict):
        if "path" in payload and ("title" in payload or "status" in payload):
            return payload
        for value in payload.values():
            found = extract_task_from_payload(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = extract_task_from_payload(item)
            if found:
                return found
    return None


def push_slot_result(source_id: str, *, product_numbers: List[str],
                     post_url: Optional[str] = None,
                     mark_done: bool = False,
                     done_status: str = "done") -> bool:
    """Записати результат BMS назад у нотатку Obsidian.

    Пишемо лише у власні поля (``bms_*``) і, за потреби, у ``status`` — щоб
    галочка «📣 Опубліковано» в календарі проставлялась сама. Повертає ``False``
    замість винятку, якщо Obsidian закритий: публікація вже відбулась і зривати
    її через недоступний Obsidian не можна.
    """
    url, token = api_config()
    if not token:
        return False

    payload: Dict[str, Any] = {
        "bms_products": product_numbers,
        "bms_synced_at": datetime.now().isoformat(timespec="seconds"),
    }
    if post_url:
        payload["bms_post_url"] = post_url
    if mark_done:
        payload["status"] = done_status

    try:
        response = requests.put(
            f"{url}/api/tasks/{quote(source_id, safe='')}",
            json=payload,
            headers={**_headers(token), "Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        logger.info("Контент-план: не вдалось оновити нотатку %s — Obsidian закритий", source_id)
        return False

    if response.status_code >= 400:
        logger.warning("Контент-план: TaskNotes відхилив оновлення %s (%s)",
                       source_id, response.status_code)
        return False
    return True

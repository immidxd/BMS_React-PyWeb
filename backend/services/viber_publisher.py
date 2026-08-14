"""Офіційна публікація товарів у Viber Channel через хмарний диспетчер.

Viber Channels Post API не має альбомів. BMS тому створює одну незмінну
JPEG-картку 1080×1080 із 1–5 канонічних фото. Компонування не змінює
оригінали: публікаційна похідна лежить під content-addressed ключем у R2.
Явне віддзеркалення в редакторі проходить окремим канонічним photo-manager.

Секрет Viber навмисно не потрапляє ані у frontend, ані у PostgreSQL, ані в
desktop `.env`. Його зберігає Cloudflare Worker. BMS передає диспетчеру вже
готовий публічний URL, підпис, час та idempotency key. Так «зараз» і
«заплановано» мають один кодовий шлях, а вимкнений Mac не зриває розклад.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageOps
from sqlalchemy import text
from sqlalchemy.orm import Session


CAPTION_LIMIT = 768
COLLAGE_SIZE = 1080
THUMB_SIZE = 400
COLLAGE_MAX_BYTES = 950_000
THUMB_MAX_BYTES = 95_000
MAX_COLLAGE_PHOTOS = 5
BATCH_MAX_PRODUCTS = int(os.getenv("VIBER_BATCH_MAX_PRODUCTS", "10"))
BATCH_GAP_SEC = float(os.getenv("VIBER_BATCH_GAP_SEC", "1.1"))
KYIV_TZ = ZoneInfo("Europe/Kyiv")

DISPATCHER_URL = os.getenv("VIBER_DISPATCHER_URL", "").strip().rstrip("/")
DISPATCHER_KEY = os.getenv("VIBER_DISPATCHER_KEY", "").strip()
CHANNEL_TITLE = os.getenv(
    "VIBER_CHANNEL_TITLE", "Brandstoreua | Взуття з Європи і Америки",
).strip()
ORDER_PHONE = os.getenv("VIBER_ORDER_PHONE", "+380972337387").strip()
DELIVERY_LINE = os.getenv("VIBER_DELIVERY_LINE", "1–2 дні").strip()
CATALOG_URL = os.getenv("VIBER_CATALOG_URL", "").strip()

_BACKGROUNDS = {
    "white": (255, 255, 255),
    "soft": (244, 246, 248),
    "warm": (248, 245, 240),
    "dark": (24, 27, 32),
}
_LAYOUTS = {"auto", "grid", "hero"}
VIBER_COLUMN_SPLIT = 0.63
VIBER_LEFT_SPLIT = 0.505
VIBER_RIGHT_TOP = 0.347
VIBER_RIGHT_MIDDLE = 0.307
FRAME_ZOOM_MIN = 0.5
FRAME_ZOOM_MAX = 3.0


def _tg():
    """Переюз вивіреної товарної/стан-логіки, без Telegram-запитів."""
    try:
        from services import telegram_publisher
    except ImportError:
        from backend.services import telegram_publisher
    return telegram_publisher


def _r2():
    try:
        from services import r2_storage
    except ImportError:
        from backend.services import r2_storage
    return r2_storage


def connection_status() -> dict:
    r2 = _r2()
    missing = []
    if not DISPATCHER_URL:
        missing.append("VIBER_DISPATCHER_URL")
    if not DISPATCHER_KEY:
        missing.append("VIBER_DISPATCHER_KEY")
    if not r2.is_enabled() or not r2.R2_PUBLIC_BASE_URL:
        missing.append("публічний Cloudflare R2")
    return {
        "configured": not missing,
        "dispatcher_configured": bool(DISPATCHER_URL and DISPATCHER_KEY),
        "r2_configured": bool(r2.is_enabled() and r2.R2_PUBLIC_BASE_URL),
        "channel_title": CHANNEL_TITLE,
        "missing": missing,
        "live_publish_available": not missing,
        "schedule_available": not missing,
        "collage": {
            "width": COLLAGE_SIZE,
            "height": COLLAGE_SIZE,
            "format": "JPEG",
            "max_bytes": COLLAGE_MAX_BYTES,
            "max_photos": MAX_COLLAGE_PHOTOS,
        },
    }


def _unique_ints(values: Any, *, maximum: int, limit: int) -> List[int]:
    out: List[int] = []
    if not isinstance(values, list):
        return out
    for raw in values:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value < maximum and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def normalize_collage_spec(payload: dict, image_count: int) -> dict:
    """Один стабільний формат між preview, renderer, batch і Worker payload."""
    selected = _unique_ints(
        payload.get("image_idx"), maximum=image_count, limit=MAX_COLLAGE_PHOTOS,
    )
    if not selected:
        selected = list(range(min(image_count, MAX_COLLAGE_PHOTOS)))
    layout = str(payload.get("layout") or "auto").lower()
    if layout not in _LAYOUTS:
        layout = "auto"
    background = str(payload.get("background") or "white").lower()
    if background not in _BACKGROUNDS:
        background = "white"
    gap = int(_clamp(payload.get("gap"), 0, 32, 4))
    column_split = _clamp(payload.get("column_split"), 0.50, 0.78, VIBER_COLUMN_SPLIT)
    left_split = _clamp(payload.get("left_split"), 0.28, 0.72, VIBER_LEFT_SPLIT)
    right_top = _clamp(payload.get("right_top"), 0.18, 0.55, VIBER_RIGHT_TOP)
    right_middle = _clamp(payload.get("right_middle"), 0.18, 0.55, VIBER_RIGHT_MIDDLE)
    if right_top + right_middle > 0.82:
        factor = 0.82 / (right_top + right_middle)
        right_top *= factor
        right_middle *= factor

    raw_frames = payload.get("frames") if isinstance(payload.get("frames"), list) else []
    by_index: Dict[int, dict] = {}
    for raw in raw_frames:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("image_idx"))
        except (TypeError, ValueError):
            continue
        if idx not in selected:
            continue
        by_index[idx] = {
            "image_idx": idx,
            "zoom": _clamp(raw.get("zoom"), FRAME_ZOOM_MIN, FRAME_ZOOM_MAX, 1.0),
            "x": _clamp(raw.get("x"), -1.0, 1.0, 0.0),
            "y": _clamp(raw.get("y"), -1.0, 1.0, 0.0),
        }
    frames = [by_index.get(idx, {"image_idx": idx, "zoom": 1.0, "x": 0.0, "y": 0.0})
              for idx in selected]
    return {
        "version": 1,
        "width": COLLAGE_SIZE,
        "height": COLLAGE_SIZE,
        "image_idx": selected,
        "layout": layout,
        "background": background,
        "gap": gap,
        "column_split": column_split,
        "left_split": left_split,
        "right_top": right_top,
        "right_middle": right_middle,
        "frames": frames,
    }


def _grid_cells(count: int, margin: int, gap: int) -> List[Tuple[int, int, int, int]]:
    cols = 1 if count == 1 else (2 if count <= 4 else 3)
    rows = math.ceil(count / cols)
    inner = COLLAGE_SIZE - 2 * margin
    cell_w = (inner - gap * (cols - 1)) // cols
    cell_h = (inner - gap * (rows - 1)) // rows
    cells = []
    for index in range(count):
        row, col = divmod(index, cols)
        x = margin + col * (cell_w + gap)
        y = margin + row * (cell_h + gap)
        # Неповний останній ряд із 5 фото центруємо, а не притискаємо вліво.
        if row == rows - 1 and count % cols and index >= count - (count % cols):
            last_count = count % cols
            used = last_count * cell_w + (last_count - 1) * gap
            x = (COLLAGE_SIZE - used) // 2 + col * (cell_w + gap)
        cells.append((x, y, cell_w, cell_h))
    return cells


def _layout_cells(count: int, layout: str, gap: int,
                  tuning: Optional[dict] = None) -> List[Tuple[int, int, int, int]]:
    margin = 18
    inner = COLLAGE_SIZE - 2 * margin
    effective = layout
    if effective == "auto":
        effective = "hero" if count in (3, 5) else "grid"
    if effective == "grid" or count <= 2:
        return _grid_cells(count, margin, gap)
    if count == 3:
        hero_w = int((inner - gap) * 0.62)
        side_w = inner - gap - hero_w
        half_h = (inner - gap) // 2
        return [
            (margin, margin, hero_w, inner),
            (margin + hero_w + gap, margin, side_w, half_h),
            (margin + hero_w + gap, margin + half_h + gap, side_w, inner - half_h - gap),
        ]
    if count == 4:
        hero_w = int((inner - gap) * 0.58)
        side_w = inner - gap - hero_w
        third = (inner - 2 * gap) // 3
        return [
            (margin, margin, hero_w, inner),
            (margin + hero_w + gap, margin, side_w, third),
            (margin + hero_w + gap, margin + third + gap, side_w, third),
            (margin + hero_w + gap, margin + 2 * (third + gap), side_w,
             inner - 2 * (third + gap)),
        ]
    # П’ять фото: фактичний історичний шаблон каналу Brandstoreua у Viber —
    # два великі кадри ліворуч і три компактні праворуч, щільно та без
    # випадкових порожніх зон. Межі колонок/рядів залишаються регульованими.
    values = tuning or {}
    column_split = _clamp(values.get("column_split"), 0.50, 0.78, VIBER_COLUMN_SPLIT)
    left_split = _clamp(values.get("left_split"), 0.28, 0.72, VIBER_LEFT_SPLIT)
    right_top = _clamp(values.get("right_top"), 0.18, 0.55, VIBER_RIGHT_TOP)
    right_middle = _clamp(values.get("right_middle"), 0.18, 0.55, VIBER_RIGHT_MIDDLE)
    if right_top + right_middle > 0.82:
        factor = 0.82 / (right_top + right_middle)
        right_top *= factor
        right_middle *= factor
    usable_w = COLLAGE_SIZE - gap
    left_w = round(usable_w * column_split)
    right_x = left_w + gap
    right_w = COLLAGE_SIZE - right_x
    usable_left_h = COLLAGE_SIZE - gap
    left_top_h = round(usable_left_h * left_split)
    usable_right_h = COLLAGE_SIZE - 2 * gap
    right_top_h = round(usable_right_h * right_top)
    right_middle_h = round(usable_right_h * right_middle)
    right_bottom_y = right_top_h + right_middle_h + 2 * gap
    return [
        (0, 0, left_w, left_top_h),
        (0, left_top_h + gap, left_w, COLLAGE_SIZE - left_top_h - gap),
        (right_x, 0, right_w, right_top_h),
        (right_x, right_top_h + gap, right_w, right_middle_h),
        (right_x, right_bottom_y, right_w, COLLAGE_SIZE - right_bottom_y),
    ]


def _trim_uniform_photo_background(image: Image.Image) -> Image.Image:
    """Прибирає лише впевнено однорідні поля офіційних товарних фото.

    Більшість офіційних фото вже мають білий/майже білий фон і широкі поля.
    Без цього етапу renderer масштабує не товар, а весь білий квадрат — саме
    тому предмети виходили дрібними та випадково розкиданими. Якщо край фото
    неоднорідний (живе фото/інтер’єр), функція нічого не обрізає.
    """
    if image.width < 40 or image.height < 40:
        return image
    px = image.load()
    edge_points: List[Tuple[int, int, int]] = []
    step_x = max(1, image.width // 40)
    step_y = max(1, image.height // 40)
    for x in range(0, image.width, step_x):
        edge_points.extend((px[x, 0], px[x, image.height - 1]))
    for y in range(0, image.height, step_y):
        edge_points.extend((px[0, y], px[image.width - 1, y]))
    channels = [sorted(point[channel] for point in edge_points) for channel in range(3)]
    background = tuple(values[len(values) // 2] for values in channels)
    close = sum(
        1 for point in edge_points
        if max(abs(point[channel] - background[channel]) for channel in range(3)) <= 16
    )
    if close / max(1, len(edge_points)) < 0.82:
        return image

    diff = ImageChops.difference(image, Image.new("RGB", image.size, background))
    mask = ImageChops.lighter(
        ImageChops.lighter(diff.getchannel("R"), diff.getchannel("G")),
        diff.getchannel("B"),
    ).point(lambda value: 255 if value > 18 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    subject_w, subject_h = right - left, bottom - top
    if subject_w < image.width * 0.08 or subject_h < image.height * 0.08:
        return image
    pad_x = max(8, round(subject_w * 0.07))
    pad_y = max(8, round(subject_h * 0.07))
    expanded = (
        max(0, left - pad_x), max(0, top - pad_y),
        min(image.width, right + pad_x), min(image.height, bottom + pad_y),
    )
    # Не робимо зайву повторну інтерполяцію, якщо полів фактично немає.
    if expanded[0] <= image.width * 0.02 and expanded[1] <= image.height * 0.02 \
            and expanded[2] >= image.width * 0.98 and expanded[3] >= image.height * 0.98:
        return image
    return image.crop(expanded)


def _render_tile(raw: bytes, size: Tuple[int, int], frame: dict,
                 background: Tuple[int, int, int]) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image = _trim_uniform_photo_background(image)
    width, height = size
    tile = Image.new("RGB", (width, height), background)
    fit = min(width / max(1, image.width), height / max(1, image.height))
    scale = fit * float(frame.get("zoom") or 1.0)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    free_x = width - resized.width
    free_y = height - resized.height
    # x/y = -1…1 рухають кадр у межах доступного простору або crop-запасу.
    x = round(free_x / 2 + float(frame.get("x") or 0.0) * abs(free_x) / 2)
    y = round(free_y / 2 + float(frame.get("y") or 0.0) * abs(free_y) / 2)
    tile.paste(resized, (x, y))
    return tile


def _jpeg_under_limit(image: Image.Image, max_bytes: int, *, thumb: bool = False) -> bytes:
    qualities = (86, 82, 78, 74, 70, 66, 62) if not thumb else (82, 76, 70, 64, 58)
    for quality in qualities:
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True,
                   subsampling="4:2:0")
        value = buffer.getvalue()
        if len(value) <= max_bytes:
            return value
    raise ValueError(
        f"Не вдалося вкласти JPEG у безпечний ліміт {max_bytes // 1000} КБ"
    )


def render_collage(
    photo_bytes: Sequence[bytes], spec: dict, *, include_thumbnail: bool = True,
) -> Tuple[bytes, bytes]:
    """Детерміновано рендерить картку; thumbnail можна пропустити для UI-прев'ю."""
    if not photo_bytes:
        raise ValueError("Для колажу потрібно хоча б одне фото")
    if len(photo_bytes) > MAX_COLLAGE_PHOTOS:
        raise ValueError(f"У колаж можна додати до {MAX_COLLAGE_PHOTOS} фото")
    normalized = normalize_collage_spec(spec, max(spec.get("image_idx", [0])) + 1)
    frames = normalized["frames"][:len(photo_bytes)]
    cells = _layout_cells(
        len(photo_bytes), normalized["layout"], normalized["gap"], normalized,
    )
    background = _BACKGROUNDS[normalized["background"]]
    canvas = Image.new("RGB", (COLLAGE_SIZE, COLLAGE_SIZE), background)
    for raw, frame, (x, y, width, height) in zip(photo_bytes, frames, cells):
        tile = _render_tile(raw, (width, height), frame, background)
        radius = max(8, min(20, min(width, height) // 18))
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
        canvas.paste(tile, (x, y), mask)
    main = _jpeg_under_limit(canvas, COLLAGE_MAX_BYTES)
    thumb = b""
    if include_thumbnail:
        thumb_image = ImageOps.fit(canvas, (THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
        thumb = _jpeg_under_limit(thumb_image, THUMB_MAX_BYTES, thumb=True)
    return main, thumb


def _read_selected_photos(bms: dict, payload: dict) -> Tuple[List[Any], dict, List[bytes]]:
    tg = _tg()
    photos, _kind = tg._photo_entries(bms)
    spec = normalize_collage_spec(payload, len(photos))
    if not spec["image_idx"]:
        raise ValueError("У товару немає фото для Viber")
    chosen = [photos[index] for index in spec["image_idx"]]
    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes
    values = [read_image_bytes(entry) for entry in chosen]
    if not all(values):
        raise ValueError("Не вдалося прочитати одне або кілька фото товару")
    return chosen, spec, [value for value in values if value is not None]


def _fmt_sizes(bms: dict, sizes: Sequence[dict]) -> str:
    tg = _tg()
    if tg._is_bag(bms):
        dimensions = tg._normalize_dimensions(bms.get("dimensions"))
        return f"📐 Розміри: {dimensions}" if dimensions else ""
    labels = []
    for row in sizes:
        size = str(row.get("size") or "").strip()
        measurement = str(row.get("measurementscm") or "").strip()
        if not size:
            continue
        labels.append(f"{size} (на ніжку {measurement} см)" if measurement else size)
    if not labels:
        return ""
    prefix = "Розмір" if len(labels) == 1 else "Розміри"
    return f"📏 {prefix}: {', '.join(labels)}"


def build_caption(bms: dict, sizes: Sequence[dict], *, features: Optional[Iterable[str]] = None) -> str:
    tg = _tg()
    brand = str(bms.get("brandname") or "").strip()
    model = str(bms.get("model") or "").strip()
    tagline = tg.default_tagline(bms)
    emoji = tg.default_emoji(bms)
    title = " ".join(value for value in (brand, model) if value).strip()
    if tagline:
        title = f"{title} • {tagline}" if title else tagline
    # Viber використовує власну Markdown-підмножину: *bold*, _italic_,
    # ```mono```, ~strike~. Маркери є частиною підпису, який піде в API.
    headline = f"{emoji} {title}".strip()
    leading = [f"*{headline}*"]
    size_line = _fmt_sizes(bms, sizes)
    if size_line:
        leading.append(size_line)
    feature_values = [str(v).strip() for v in (features or tg.default_features(bms)) if str(v).strip()]
    condition = tg._condition_line(bms)
    condition_line = f"{tg._condition_icon(bms)} {condition}" if condition else ""

    protected: List[str] = []
    price = tg._fmt_price(bms.get("price"))
    if price:
        protected.append(f"🛒 Ціна: {price} грн")
    if DELIVERY_LINE:
        protected.append(f"🚚 Доставка: {DELIVERY_LINE}")
    pnum = str(bms.get("productnumber") or "").lstrip("#")
    order_line = f"📲 Пиши ```#{pnum}``` для замовлення"
    if ORDER_PHONE:
        order_line += f" 👉 {ORDER_PHONE}"
    protected.append(order_line)
    if CATALOG_URL:
        protected.append(CATALOG_URL)

    # Дефолт ніколи мовчки не перевищує API-ліміт. Спершу прибираємо останні
    # переваги; CTA з номером і телефоном, ціна та доставка завжди захищені.
    # Технології/переваги за бізнес-шаблоном виділяються одночасно
    # жирним і курсивом. Знімаємо Telegram-маркери з історичних шаблонів.
    def viber_feature(value: str) -> str:
        plain = value.replace("**", "").replace("__", "").replace("~~", "").replace("`", "").strip()
        plain = tg.normalize_technology_abbreviations(plain)
        return f"▪️ *_{plain}_*"

    feature_lines = [viber_feature(value) for value in feature_values[:4]]

    def compose() -> str:
        sections = [*leading]
        if feature_lines:
            sections.append("\n".join(feature_lines))
        if condition_line:
            sections.append(condition_line)
        return "\n\n".join([*sections, *protected]).strip()

    caption = compose()
    while len(caption) > CAPTION_LIMIT and feature_lines:
        feature_lines.pop()
        caption = compose()
    if len(caption) <= CAPTION_LIMIT:
        return tg.normalize_technology_abbreviations(caption)

    suffix = "\n\n".join(protected).strip()
    available = max(0, CAPTION_LIMIT - len(suffix) - 2)
    prefix = "\n\n".join([*leading, *([condition_line] if condition_line else [])]).strip()
    if len(prefix) > available:
        prefix = (prefix[:max(0, available - 1)].rstrip() + "…") if available else ""
    return tg.normalize_technology_abbreviations(
        "\n\n".join(value for value in (prefix, suffix) if value).strip()
    )


def validate_caption(caption: str) -> Optional[str]:
    if not caption.strip():
        return "Підпис Viber порожній"
    if len(caption) > CAPTION_LIMIT:
        return f"Підпис має {len(caption)} символів; ліміт Viber — {CAPTION_LIMIT}"
    return None


def _existing_count(db: Session, pnum: str) -> int:
    return int(db.execute(text("""
        SELECT COUNT(*)
        FROM viber_publications vp
        LEFT JOIN products p ON p.id = vp.product_id
        WHERE COALESCE(p.productnumber, vp.product_number) = :pnum
          AND vp.status = 'published'
    """), {"pnum": pnum}).scalar() or 0)


def _pending_count(db: Session, pnum: str) -> int:
    return int(db.execute(text("""
        SELECT COUNT(*)
        FROM viber_publications vp
        LEFT JOIN products p ON p.id = vp.product_id
        WHERE COALESCE(p.productnumber, vp.product_number) = :pnum
          AND vp.status IN ('queued', 'scheduled', 'processing', 'retrying')
    """), {"pnum": pnum}).scalar() or 0)


def preview_post(db: Session, product_id: int) -> dict:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}
    pnum = str(bms.get("productnumber") or "")
    sizes = tg._available_sizes(db, pnum)
    photos, image_kind = tg._photo_entries(bms)
    caption = build_caption(bms, sizes)
    warnings: List[str] = []
    if not photos:
        warnings.append("У товару немає фото — Viber-картку неможливо створити.")
    if len(photos) > MAX_COLLAGE_PHOTOS:
        warnings.append(f"У колаж увійдуть вибрані {MAX_COLLAGE_PHOTOS} із {len(photos)} фото.")
    if not sizes and not tg._is_bag(bms):
        warnings.append("Немає доступних розмірів у наявності.")
    if tg._condition_requires_confirmation(bms):
        warnings.append(
            f"Стан «{tg._cap(bms.get('conditionname'))}» потребує підтвердження перед публікацією."
        )
    status = connection_status()
    if not status["configured"]:
        warnings.append("Публікація стане доступною після підключення захищеного Viber-диспетчера.")
    return {
        "ok": True,
        "product_id": product_id,
        "productnumber": pnum.lstrip("#"),
        "brand": bms.get("brandname"),
        "model": bms.get("model"),
        "type": bms.get("typename"),
        "condition": tg._condition_line(bms),
        "condition_name": tg._cap(bms.get("conditionname")) or None,
        "condition_confirmation_required": tg._condition_requires_confirmation(bms),
        "caption": caption,
        "caption_len": len(caption),
        "caption_limit": CAPTION_LIMIT,
        "sizes": sizes,
        "image_count": len(photos),
        "image_kind": image_kind,
        "image_urls": [getattr(photo, "url", "") for photo in photos],
        "image_names": [getattr(photo, "filename", "") for photo in photos],
        "default_image_idx": list(range(min(len(photos), MAX_COLLAGE_PHOTOS))),
        "collage": normalize_collage_spec({}, len(photos)),
        "layouts": [
            {"key": "auto", "label": "Розумний"},
            {"key": "hero", "label": "Головне фото"},
            {"key": "grid", "label": "Рівна сітка"},
        ],
        "backgrounds": [
            {"key": "white", "label": "Білий"},
            {"key": "soft", "label": "Світлий"},
            {"key": "warm", "label": "Теплий"},
            {"key": "dark", "label": "Темний"},
        ],
        "channel": {"title": CHANNEL_TITLE},
        "connection": status,
        "already_published": _existing_count(db, pnum),
        "pending_publications": _pending_count(db, pnum),
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "default_publish_at": tg._next_morning(8, 10).isoformat(),
        "warnings": warnings,
    }


def preview_posts_batch(db: Session, product_ids: List[int]) -> dict:
    tg = _tg()
    clean: List[int] = []
    for raw in product_ids[:200]:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in clean:
            clean.append(pid)
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    missing: List[int] = []
    for pid in clean:
        bms = tg._load_product(db, pid)
        if not bms:
            missing.append(pid)
            continue
        pnum = str(bms.get("productnumber") or "")
        key = pnum.lstrip("#").casefold() or f"id:{pid}"
        grouped.setdefault(key, {
            "product_id": pid, "productnumber": pnum, "source_product_ids": [],
        })["source_product_ids"].append(pid)
    if not grouped:
        return {"ok": False, "error": "Серед виділеного не знайдено товарів"}
    if len(grouped) > BATCH_MAX_PRODUCTS:
        return {
            "ok": False,
            "error": f"Один безпечний пакет Viber — до {BATCH_MAX_PRODUCTS} унікальних товарів",
        }
    items = []
    for group in grouped.values():
        preview = preview_post(db, group["product_id"])
        items.append({
            **group,
            "ok": bool(preview.get("ok")),
            "preview": preview if preview.get("ok") else None,
            "error": preview.get("error") if not preview.get("ok") else None,
        })
    return {
        "ok": True,
        "selected_count": len(clean),
        "unique_count": len(items),
        "merged_count": max(0, len(clean) - len(items)),
        "missing_ids": missing,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "items": items,
    }


def render_for_product(
    db: Session, product_id: int, payload: dict, *, include_thumbnail: bool = True,
) -> Tuple[bytes, bytes, dict]:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        raise ValueError("Товар не знайдено")
    _photos, spec, values = _read_selected_photos(bms, payload)
    main, thumb = render_collage(values, spec, include_thumbnail=include_thumbnail)
    return main, thumb, spec


def _validate_schedule(raw: Any) -> Tuple[Optional[datetime], Optional[str]]:
    if raw in (None, "", False):
        return None, None
    try:
        value = str(raw).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KYIV_TZ)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None, "Некоректний час Viber-публікації"
    now = datetime.now(timezone.utc)
    if parsed < now + timedelta(minutes=2):
        return None, "Запланований пост має бути щонайменше через 2 хвилини"
    if parsed > now + timedelta(days=365):
        return None, "Viber-публікацію можна запланувати не далі ніж на 365 днів"
    return parsed, None


def _prepare(db: Session, product_id: int, payload: dict) -> dict:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        raise ValueError("Товар не знайдено")
    pnum = str(bms.get("productnumber") or "")
    caption = str(payload.get("caption") or "").strip()
    problem = validate_caption(caption)
    if problem:
        raise ValueError(problem)
    if (not payload.get("dry_run") and tg._condition_requires_confirmation(bms)
            and payload.get("condition_confirmed") is not True):
        raise ValueError(
            f"Стан «{tg._cap(bms.get('conditionname'))}» потребує явного підтвердження"
        )
    if not payload.get("dry_run") and not payload.get("force"):
        published = _existing_count(db, pnum)
        pending = _pending_count(db, pnum)
        if published:
            raise ValueError("Товар уже опублікований у Viber")
        if pending:
            raise ValueError("Viber-публікація цього товару вже стоїть у черзі або розкладі")
    scheduled_at, schedule_error = _validate_schedule(payload.get("publish_at"))
    if schedule_error:
        raise ValueError(schedule_error)
    main, thumb, spec = render_for_product(db, product_id, payload.get("collage") or payload)
    return {
        "bms": bms,
        "pnum": pnum,
        "caption": caption,
        "scheduled_at": scheduled_at,
        "main": main,
        "thumb": thumb,
        "spec": spec,
    }


def _upload_derivatives(prepared: dict) -> dict:
    r2 = _r2()
    if not r2.is_enabled() or not r2.R2_PUBLIC_BASE_URL:
        raise RuntimeError("Публічний Cloudflare R2 не налаштований")
    digest = hashlib.sha256(prepared["main"] + prepared["caption"].encode("utf-8")).hexdigest()[:24]
    safe_pnum = prepared["pnum"].lstrip("#").replace("/", "-") or "product"
    base = f"social/viber/{safe_pnum}/{digest}"
    main_key = f"{base}.jpeg"
    thumb_key = f"{base}.thumb.jpeg"
    r2.upload_bytes(prepared["main"], main_key, content_type="image/jpeg")
    r2.upload_bytes(prepared["thumb"], thumb_key, content_type="image/jpeg")
    main_url = r2.public_url(main_key)
    thumb_url = r2.public_url(thumb_key)
    if not main_url or not thumb_url:
        raise RuntimeError("R2 не повернув публічний URL Viber-картки")
    return {
        "collage_key": main_key,
        "collage_url": main_url,
        "thumbnail_key": thumb_key,
        "thumbnail_url": thumb_url,
        "digest": digest,
    }


async def _dispatch(request: dict) -> dict:
    status = connection_status()
    if not status["configured"]:
        raise RuntimeError("Viber-диспетчер ще не підключений: " + ", ".join(status["missing"]))
    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(
            f"{DISPATCHER_URL}/v1/jobs",
            headers={"Authorization": f"Bearer {DISPATCHER_KEY}"},
            json=request,
        )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(data.get("error") or f"Viber-диспетчер повернув HTTP {response.status_code}")
    return data


def _cached_result(db: Session, key: str) -> Optional[dict]:
    row = db.execute(text("""
        SELECT product_id, product_number, dispatcher_job_id, message_token,
               status, scheduled_at, published_at, error
        FROM viber_publications WHERE idempotency_key = :key
    """), {"key": key}).mappings().first()
    if not row:
        return None
    value = dict(row)
    return {
        "ok": value["status"] not in ("failed", "error"),
        "cached": True,
        **value,
    }


def _record(db: Session, *, product_id: int, prepared: dict, uploaded: dict,
            dispatch: dict, idempotency_key: str, request_payload: dict) -> None:
    db.execute(text("""
        INSERT INTO viber_publications (
            product_id, product_number, channel_title, dispatcher_job_id,
            message_token, idempotency_key, status, caption,
            collage_key, collage_url, thumbnail_key, thumbnail_url,
            scheduled_at, published_at, payload_json, error, updated_at
        ) VALUES (
            :pid, :pnum, :channel, :job, :token, :idem, :status, :caption,
            :ckey, :curl, :tkey, :turl, :scheduled, :published,
            CAST(:payload AS jsonb), :error, now()
        )
        ON CONFLICT (idempotency_key) DO UPDATE SET
            dispatcher_job_id = EXCLUDED.dispatcher_job_id,
            message_token = EXCLUDED.message_token,
            status = EXCLUDED.status,
            published_at = EXCLUDED.published_at,
            error = EXCLUDED.error,
            updated_at = now()
    """), {
        "pid": product_id,
        "pnum": prepared["pnum"],
        "channel": CHANNEL_TITLE,
        "job": dispatch.get("job_id"),
        "token": str(dispatch.get("message_token") or "") or None,
        "idem": idempotency_key,
        "status": dispatch.get("status") or ("scheduled" if prepared["scheduled_at"] else "queued"),
        "caption": prepared["caption"],
        "ckey": uploaded["collage_key"],
        "curl": uploaded["collage_url"],
        "tkey": uploaded["thumbnail_key"],
        "turl": uploaded["thumbnail_url"],
        "scheduled": prepared["scheduled_at"],
        "published": datetime.now(timezone.utc) if dispatch.get("status") == "published" else None,
        "payload": json.dumps(request_payload, ensure_ascii=False),
        "error": dispatch.get("error"),
    })
    db.commit()


async def create_post(db: Session, product_id: int, payload: dict,
                      *, prepared: Optional[dict] = None) -> dict:
    idempotency_key = str(payload.get("idempotency_key") or uuid.uuid4())[:160]
    cached = _cached_result(db, idempotency_key)
    if cached:
        return cached
    if not payload.get("dry_run"):
        status = connection_status()
        if not status["configured"]:
            return {
                "ok": False,
                "error": "Viber-диспетчер ще не підключений: " + ", ".join(status["missing"]),
            }
    try:
        ready = prepared or _prepare(db, product_id, payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if payload.get("dry_run"):
        return {
            "ok": True,
            "dry_run": True,
            "product_id": product_id,
            "productnumber": ready["pnum"].lstrip("#"),
            "image_bytes": len(ready["main"]),
            "thumbnail_bytes": len(ready["thumb"]),
            "collage": ready["spec"],
        }
    try:
        from starlette.concurrency import run_in_threadpool
        uploaded = await run_in_threadpool(_upload_derivatives, ready)
        request_payload = {
            "idempotency_key": idempotency_key,
            "product_id": product_id,
            "product_number": ready["pnum"].lstrip("#"),
            "channel_title": CHANNEL_TITLE,
            "type": "picture",
            "caption": ready["caption"],
            "media_url": uploaded["collage_url"],
            "thumbnail_url": uploaded["thumbnail_url"],
            "publish_at": ready["scheduled_at"].isoformat() if ready["scheduled_at"] else None,
        }
        dispatched = await _dispatch(request_payload)
        _record(
            db, product_id=product_id, prepared=ready, uploaded=uploaded,
            dispatch=dispatched, idempotency_key=idempotency_key,
            request_payload={**request_payload, "collage": ready["spec"]},
        )
        return {
            "ok": True,
            "product_id": product_id,
            "productnumber": ready["pnum"].lstrip("#"),
            "idempotency_key": idempotency_key,
            "job_id": dispatched.get("job_id"),
            "message_token": dispatched.get("message_token"),
            "status": dispatched.get("status"),
            "scheduled_at": ready["scheduled_at"].isoformat() if ready["scheduled_at"] else None,
            "collage_url": uploaded["collage_url"],
        }
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc), "idempotency_key": idempotency_key}


async def create_posts_batch(
    db: Session, items: Any, batch_id: Any, *, dry_run: bool = False,
) -> dict:
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Пакет Viber порожній"}
    if len(items) > BATCH_MAX_PRODUCTS:
        return {"ok": False, "error": f"Один пакет Viber — до {BATCH_MAX_PRODUCTS} товарів"}
    batch = str(batch_id or "").strip()
    if not batch:
        return {"ok": False, "error": "Пакет не має batch_id"}
    status = connection_status()
    if not dry_run and not status["configured"]:
        return {
            "ok": False,
            "error": "Viber-диспетчер ще не підключений: " + ", ".join(status["missing"]),
        }

    prepared_items: List[Tuple[int, dict, dict]] = []
    product_numbers = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("product_id"):
            return {"ok": False, "error": f"Картка {index + 1} пошкоджена"}
        pid = int(item["product_id"])
        payload = dict(item.get("payload") or item)
        if dry_run:
            payload["dry_run"] = True
        payload["idempotency_key"] = str(
            payload.get("idempotency_key") or f"{batch}:{pid}"
        )[:160]
        try:
            ready = _prepare(db, pid, payload)
        except ValueError as exc:
            return {"ok": False, "error": f"#{pid}: {exc}"}
        number_key = ready["pnum"].lstrip("#").casefold()
        if number_key in product_numbers:
            return {"ok": False, "error": f"Товар {ready['pnum']} повторюється в пакеті"}
        product_numbers.add(number_key)
        prepared_items.append((pid, payload, ready))

    # Пакетна репетиція проходить той самий повний renderer і всі перевірки,
    # але не завантажує JPEG у R2, не створює D1 job і не звертається до Viber.
    if dry_run:
        results = [{
            "product_id": pid,
            "productnumber": ready["pnum"].lstrip("#"),
            "status": "validated",
            "image_bytes": len(ready["main"]),
            "thumbnail_bytes": len(ready["thumb"]),
            "error": None,
        } for pid, _payload, ready in prepared_items]
        return {
            "ok": True,
            "dry_run": True,
            "batch_id": batch,
            "status": "success",
            "counts": {"success": len(results), "error": 0, "total": len(results)},
            "results": results,
        }

    results = []
    for position, (pid, payload, ready) in enumerate(prepared_items):
        result = await create_post(db, pid, payload, prepared=ready)
        results.append({
            "product_id": pid,
            "productnumber": ready["pnum"].lstrip("#"),
            "status": result.get("status") if result.get("ok") else "error",
            "result": result if result.get("ok") else None,
            "error": result.get("error") if not result.get("ok") else None,
        })
        if position < len(prepared_items) - 1:
            await asyncio.sleep(BATCH_GAP_SEC)
    success = sum(1 for row in results if row["error"] is None)
    errors = len(results) - success
    return {
        "ok": True,
        "batch_id": batch,
        "status": "success" if not errors else ("error" if not success else "partial"),
        "counts": {"success": success, "error": errors, "total": len(results)},
        "results": results,
    }


def product_status(db: Session, product_id: int) -> dict:
    rows = db.execute(text("""
        SELECT id, status, channel_title, dispatcher_job_id, message_token,
               scheduled_at, published_at, collage_url, error, created_at
        FROM viber_publications
        WHERE product_id = :pid
           OR product_number = (SELECT productnumber FROM products WHERE id = :pid)
        ORDER BY created_at DESC
    """), {"pid": product_id}).mappings().all()
    return {"product_id": product_id, "publications": [dict(row) for row in rows]}


async def sync_statuses(db: Session, *, product_id: Optional[int] = None) -> dict:
    """Підтягує стани хмарної черги без повторної публікації.

    Channels Post API не надсилає callback на власні пости, тому BMS звіряє
    лише незавершені job із Worker. Токен Viber при цьому ніколи не залишає
    Cloudflare.
    """
    status = connection_status()
    if not status["dispatcher_configured"]:
        return {"ok": False, "error": "Viber-диспетчер ще не підключений"}
    params: Dict[str, Any] = {}
    product_clause = ""
    if product_id is not None:
        params["pid"] = int(product_id)
        product_clause = "AND (product_id = :pid OR product_number = (SELECT productnumber FROM products WHERE id = :pid))"
    rows = db.execute(text(f"""
        SELECT id, dispatcher_job_id
        FROM viber_publications
        WHERE dispatcher_job_id IS NOT NULL
          AND status IN ('queued', 'scheduled', 'processing', 'retrying')
          {product_clause}
        ORDER BY updated_at
        LIMIT 100
    """), params).mappings().all()
    updated = 0
    errors = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for row in rows:
            try:
                response = await client.get(
                    f"{DISPATCHER_URL}/v1/jobs/{row['dispatcher_job_id']}",
                    headers={"Authorization": f"Bearer {DISPATCHER_KEY}"},
                )
                data = response.json()
                if response.status_code >= 400 or not data.get("ok"):
                    raise RuntimeError(data.get("error") or f"HTTP {response.status_code}")
                db.execute(text("""
                    UPDATE viber_publications
                       SET status = :status,
                           message_token = COALESCE(:token, message_token),
                           scheduled_at = COALESCE(CAST(:scheduled AS timestamptz), scheduled_at),
                           published_at = COALESCE(CAST(:published AS timestamptz), published_at),
                           error = :error,
                           updated_at = now()
                     WHERE id = :id
                """), {
                    "id": row["id"],
                    "status": data.get("status") or "queued",
                    "token": str(data.get("message_token") or "") or None,
                    "scheduled": data.get("scheduled_at"),
                    "published": data.get("published_at"),
                    "error": data.get("error"),
                })
                updated += 1
            except Exception as exc:
                errors.append({"job_id": row["dispatcher_job_id"], "error": str(exc)})
    db.commit()
    return {
        "ok": not errors,
        "checked": len(rows),
        "updated": updated,
        "errors": errors,
    }

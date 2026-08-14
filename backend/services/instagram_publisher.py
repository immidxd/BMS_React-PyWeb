"""Instagram renderer, чернетки й керування захищеною Cloudflare-чергою.

BMS готує окремі JPEG/MP4-похідні, але не містить Meta access token і напряму
не викликає Graph API. OAuth, токен, media containers, retries та публікація
ізольовані в Cloudflare Worker; PostgreSQL зберігає лише журнал станів.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
from sqlalchemy import text
from sqlalchemy.orm import Session


CAPTION_LIMIT = 2200
STORY_TEXT_LIMIT = 320
STORY_PRODUCT_NUMBER_RE = re.compile(r"#[\wА-Яа-яІіЇїЄєҐґ-]+", re.UNICODE)
STORY_PRICE_RE = re.compile(r"^(?:[^\w#]+\s*)?ціна\s*:", re.IGNORECASE | re.UNICODE)
STORY_SPECS_RE = re.compile(
    r"^(?:[^\w#]+\s*)?(?:розмір|розміри|заміри)\s*:",
    re.IGNORECASE | re.UNICODE,
)
STORY_CTA_RE = re.compile(
    r"\b(?:пиши|напиши|написати|замов\w*|direct|директ|приватн\w*)",
    re.IGNORECASE | re.UNICODE,
)
HASHTAG_LIMIT = 30
MENTION_LIMIT = 20
MAX_MEDIA = 10
BATCH_MAX_PRODUCTS = int(os.getenv("INSTAGRAM_BATCH_MAX_PRODUCTS", "10"))
JPEG_MAX_BYTES = 7_900_000
REEL_MAX_BYTES = 295_000_000
FRAME_ZOOM_MIN = 0.5
FRAME_ZOOM_MAX = 3.0
KYIV_TZ = ZoneInfo("Europe/Kyiv")
DELIVERY_LINE = os.getenv("INSTAGRAM_DELIVERY_LINE", "1–2 дні").strip()

FEED_PRESETS = {
    "portrait": {"label": "Вертикальний 4:5", "width": 1080, "height": 1350},
    "square": {"label": "Квадрат 1:1", "width": 1080, "height": 1080},
    "landscape": {"label": "Горизонтальний 1.91:1", "width": 1080, "height": 566},
}

STORY_PRESET = {"label": "Stories / Reels 9:16", "width": 1080, "height": 1920}
# Верхній інформаційний блок і товар утворюють одну композицію, делікатно
# зміщену вниз від службової панелі Instagram. Нижня межа лишає великий запас
# до reply/share UI, який відрізняється між телефонами.
STORY_CONTENT_OFFSET_Y = 42
STORY_PRODUCT_BOX = (54, 662, 1026, 1517)
PUBLISH_TYPES = {
    "feed": {"label": "Пост / карусель", "max_media": MAX_MEDIA},
    "story": {"label": "Story", "max_media": 1},
    "reel": {"label": "Reel зі слайдів", "max_media": MAX_MEDIA},
}


def _tg():
    """Перевикористовує перевірену товарну логіку без Telegram-запитів."""
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


def _dispatcher_config() -> Tuple[str, str]:
    return (
        os.getenv("INSTAGRAM_DISPATCHER_URL", "").strip().rstrip("/"),
        os.getenv("INSTAGRAM_DISPATCHER_KEY", "").strip(),
    )


def connection_status() -> dict:
    """Локальна готовність інтеграції без читання або повернення секретів."""
    dispatcher_url, dispatcher_key = _dispatcher_config()
    r2 = _r2()
    configured_parts = {
        "dispatcher_url": bool(dispatcher_url),
        "dispatcher_key": bool(dispatcher_key),
        "public_r2": bool(r2.is_enabled() and r2.R2_PUBLIC_BASE_URL),
    }
    missing = [name for name, present in configured_parts.items() if not present]
    return {
        "configured": not missing,
        "mode": "production" if not missing else "draft_ready",
        "account": "@brandxstoreua",
        "professional_account_verified": True,
        "meta_business_suite_link_verified": True,
        "oauth_method": "instagram_login",
        "facebook_page_required": False,
        "dispatcher_configured": bool(dispatcher_url and dispatcher_key),
        "r2_configured": configured_parts["public_r2"],
        "live_publish_available": not missing,
        "schedule_available": not missing,
        "missing": missing,
        "note": (
            "Редактор і повний renderer доступні. Instagram підключається "
            "напряму через офіційний Instagram Login у Cloudflare Worker."
        ),
        "limits": {
            "caption": CAPTION_LIMIT,
            "hashtags": HASHTAG_LIMIT,
            "mentions": MENTION_LIMIT,
            "carousel_media": MAX_MEDIA,
            "jpeg_bytes": JPEG_MAX_BYTES,
            "reel_bytes": REEL_MAX_BYTES,
        },
        "publish_types": PUBLISH_TYPES,
    }


def _fmt_sizes(bms: dict, sizes: Sequence[dict]) -> str:
    tg = _tg()
    if tg._is_bag(bms):
        dimensions = tg._normalize_dimensions(bms.get("dimensions"))
        return f"📐 Заміри: {dimensions}" if dimensions else ""

    values: List[str] = []
    for row in sizes:
        size = str(row.get("size") or "").strip()
        measurement = str(row.get("measurementscm") or "").strip()
        if not size:
            continue
        values.append(f"{size} (на ніжку {measurement} см)" if measurement else size)
    if not values:
        return ""
    if len(values) == 1:
        return f"📏 Розмір: {values[0]}"
    return "📏 Розміри:\n" + "\n".join(f"— {value}" for value in values)


def build_caption(
    bms: dict,
    sizes: Sequence[dict],
    *,
    features: Optional[Iterable[str]] = None,
) -> str:
    """Типовий plain-text підпис у стилі чинних товарних Instagram-постів."""
    tg = _tg()
    brand = str(bms.get("brandname") or "").strip()
    model = str(bms.get("model") or "").strip()
    title = " ".join(part for part in (brand, model) if part).strip()
    tagline = tg.default_tagline(bms)
    if tagline:
        title = f"{title} • {tagline}" if title else tagline
    headline = f"{tg.default_emoji(bms)} {title}".strip()

    leading = [headline]
    size_line = _fmt_sizes(bms, sizes)
    if size_line:
        leading.append(size_line)

    raw_features = [
        str(value).strip()
        for value in (features or tg.default_features(bms))
        if str(value).strip()
    ]
    feature_lines = [
        f"▪️ {tg.normalize_technology_abbreviations(value)}"
        for value in raw_features[:6]
    ]

    condition = tg._condition_line(bms)
    condition_line = f"{tg._condition_icon(bms)} {condition}" if condition else ""

    protected: List[str] = []
    price = tg._fmt_price(bms.get("price"))
    if price:
        protected.append(f"🛒 Ціна: {price} грн")
    if DELIVERY_LINE:
        protected.append(f"🚚 Доставка: {DELIVERY_LINE}")
    product_number = str(bms.get("productnumber") or "").lstrip("#")
    protected.append(f"📲 Пиши #{product_number} нам в приватні")

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

    suffix = "\n\n".join(protected)
    available = max(0, CAPTION_LIMIT - len(suffix) - 2)
    prefix = "\n\n".join([*leading, *([condition_line] if condition_line else [])])
    if len(prefix) > available:
        prefix = prefix[: max(0, available - 1)].rstrip() + ("…" if available else "")
    return tg.normalize_technology_abbreviations(
        "\n\n".join(value for value in (prefix, suffix) if value).strip()
    )


def build_story_text(bms: dict, sizes: Sequence[dict]) -> str:
    """Короткий текст, який BMS вбудовує безпосередньо в Story JPEG."""
    tg = _tg()
    brand = str(bms.get("brandname") or "").strip()
    model = str(bms.get("model") or "").strip()
    title = " ".join(part for part in (brand, model) if part).strip()
    tagline = tg.default_tagline(bms)

    lines = [title] if title else []
    if tagline:
        lines.append(tagline)
    size_line = _fmt_sizes(bms, sizes)
    for prefix in ("📏 ", "📐 "):
        if size_line.startswith(prefix):
            size_line = size_line[len(prefix):]
            break
    if size_line:
        lines.append(size_line)
    price = tg._fmt_price(bms.get("price"))
    if price:
        lines.append(f"Ціна: {price} грн")
    product_number = str(bms.get("productnumber") or "").lstrip("#")
    lines.append(f"#{product_number}")
    return "\n".join(lines)[:STORY_TEXT_LIMIT].strip()


def validate_caption(caption: str) -> Optional[str]:
    if not caption.strip():
        return "Підпис Instagram порожній"
    if len(caption) > CAPTION_LIMIT:
        return f"Підпис має {len(caption)} символів; ліміт Instagram — {CAPTION_LIMIT}"
    return None


def _unique_ints(values: Any, *, maximum: int, limit: int) -> List[int]:
    result: List[int] = []
    if not isinstance(values, list):
        return result
    for raw in values:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value < maximum and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _clamp(raw: Any, low: float, high: float, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, value))


def normalize_media_spec(payload: dict, image_count: int) -> dict:
    publish_type = str(payload.get("publish_type") or "feed").strip().lower()
    if publish_type not in PUBLISH_TYPES:
        publish_type = "feed"
    max_media = int(PUBLISH_TYPES[publish_type]["max_media"])
    selected = _unique_ints(payload.get("image_idx"), maximum=image_count, limit=max_media)
    if not selected:
        selected = list(range(min(image_count, max_media)))
    feed_preset = str(payload.get("feed_preset") or "square")
    if feed_preset not in FEED_PRESETS:
        feed_preset = "square"
    default_zoom = 0.9 if publish_type == "feed" else 0.6
    raw_frames = payload.get("frames") if isinstance(payload.get("frames"), list) else []
    by_index: Dict[int, dict] = {}
    for raw in raw_frames:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("image_idx"))
        except (TypeError, ValueError):
            continue
        if index not in selected:
            continue
        by_index[index] = {
            "image_idx": index,
            "zoom": _clamp(raw.get("zoom"), FRAME_ZOOM_MIN, FRAME_ZOOM_MAX, default_zoom),
            "x": _clamp(raw.get("x"), -1.0, 1.0, 0.0),
            "y": _clamp(raw.get("y"), -1.0, 1.0, 0.0),
        }
    frames = [
        by_index.get(index, {"image_idx": index, "zoom": default_zoom, "x": 0.0, "y": 0.0})
        for index in selected
    ]
    return {
        "version": 1,
        "publish_type": publish_type,
        "image_idx": selected,
        "feed_preset": feed_preset,
        "background": str(payload.get("background") or "white").lower()
        if str(payload.get("background") or "white").lower() in {"white", "soft", "dark"}
        else "white",
        "frames": frames,
    }


def _background_rgb(value: str) -> Tuple[int, int, int]:
    return {
        "white": (255, 255, 255),
        "soft": (244, 246, 248),
        "dark": (24, 27, 32),
    }.get(value, (255, 255, 255))


def _feed_subject_margins(raw: bytes) -> Tuple[int, int, Optional[Tuple[float, float, float, float]]]:
    """Вимірює вільні поля навколо товару на студійному фото.

    Якщо край неоднорідний, безпечно вважаємо, що значущий вміст може доходити
    до всіх меж. Це краще за помилкове обрізання живого або detail-кадру.
    """
    with Image.open(io.BytesIO(raw)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    if image.width < 40 or image.height < 40:
        return image.width, image.height, (0.0, 0.0, 0.0, 0.0)

    pixels = image.load()
    edge_points: List[Tuple[int, int, int]] = []
    step_x = max(1, image.width // 40)
    step_y = max(1, image.height // 40)
    for x in range(0, image.width, step_x):
        edge_points.extend((pixels[x, 0], pixels[x, image.height - 1]))
    for y in range(0, image.height, step_y):
        edge_points.extend((pixels[0, y], pixels[image.width - 1, y]))
    channels = [sorted(point[channel] for point in edge_points) for channel in range(3)]
    background = tuple(values[len(values) // 2] for values in channels)
    uniformity = sum(
        1 for point in edge_points
        if max(abs(point[channel] - background[channel]) for channel in range(3)) <= 16
    ) / max(1, len(edge_points))
    if uniformity < 0.82:
        return image.width, image.height, (0.0, 0.0, 0.0, 0.0)

    difference = ImageChops.difference(image, Image.new("RGB", image.size, background))
    mask = ImageChops.lighter(
        ImageChops.lighter(difference.getchannel("R"), difference.getchannel("G")),
        difference.getchannel("B"),
    ).point(lambda value: 255 if value > 18 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image.width, image.height, (0.0, 0.0, 0.0, 0.0)
    left, top, right, bottom = bbox
    if right - left < image.width * 0.08 or bottom - top < image.height * 0.08:
        return image.width, image.height, (0.0, 0.0, 0.0, 0.0)
    return image.width, image.height, (
        left / image.width,
        top / image.height,
        (image.width - right) / image.width,
        (image.height - bottom) / image.height,
    )


def _recommended_feed_zoom(raw: bytes, preset: dict) -> Tuple[float, bool]:
    """Лишає 0.90 тільки для фото з вже наявними вільними полями.

    Detail/full-bleed фото, де значущий вміст доходить хоча б до однієї
    межі, має залишатися на 1.00. Це не додає штучних білих полів.
    ``preset`` лишається в сигнатурі, бо default-мапа зберігається окремо
    для кожного Feed-формату.
    """
    _width, _height, margins = _feed_subject_margins(raw)
    if not margins:
        return 0.9, False
    touches_edge = min(margins) <= 0.018
    return (1.0, True) if touches_edge else (0.9, False)


def _feed_zoom_defaults(photos: Sequence[Any]) -> Tuple[Dict[str, List[float]], Dict[str, List[bool]]]:
    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes
    defaults = {key: [] for key in FEED_PRESETS}
    adjusted = {key: [] for key in FEED_PRESETS}
    for photo in photos[:MAX_MEDIA]:
        try:
            raw = read_image_bytes(photo)
        except Exception:
            raw = None
        for key, preset in FEED_PRESETS.items():
            zoom, is_adjusted = _recommended_feed_zoom(raw, preset) if raw else (0.9, False)
            defaults[key].append(zoom)
            adjusted[key].append(is_adjusted)
    return defaults, adjusted


def _render_frame(raw: bytes, width: int, height: int, frame: dict,
                  background: Tuple[int, int, int]) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    base = max(width / max(1, image.width), height / max(1, image.height))
    scale = base * float(frame.get("zoom") or 1.0)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (width, height), background)
    free_x = width - resized.width
    free_y = height - resized.height
    x = round(free_x / 2 + float(frame.get("x") or 0.0) * abs(free_x) / 2)
    y = round(free_y / 2 + float(frame.get("y") or 0.0) * abs(free_y) / 2)
    canvas.paste(resized, (x, y))
    return canvas


def _trim_uniform_story_background(image: Image.Image) -> Image.Image:
    """Прибирає тільки впевнено однорідні поля студійного фото.

    Живі фото з неоднорідним краєм лишаються недоторканими. Операція працює
    лише з публікаційною похідною і не змінює канонічний файл товару.
    """
    if image.width < 40 or image.height < 40:
        return image
    pixels = image.load()
    edge_points: List[Tuple[int, int, int]] = []
    step_x = max(1, image.width // 40)
    step_y = max(1, image.height // 40)
    for x in range(0, image.width, step_x):
        edge_points.extend((pixels[x, 0], pixels[x, image.height - 1]))
    for y in range(0, image.height, step_y):
        edge_points.extend((pixels[0, y], pixels[image.width - 1, y]))
    channels = [sorted(point[channel] for point in edge_points) for channel in range(3)]
    background = tuple(values[len(values) // 2] for values in channels)
    close = sum(
        1 for point in edge_points
        if max(abs(point[channel] - background[channel]) for channel in range(3)) <= 16
    )
    if close / max(1, len(edge_points)) < 0.82:
        return image

    difference = ImageChops.difference(image, Image.new("RGB", image.size, background))
    mask = ImageChops.lighter(
        ImageChops.lighter(difference.getchannel("R"), difference.getchannel("G")),
        difference.getchannel("B"),
    ).point(lambda value: 255 if value > 18 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    subject_w, subject_h = right - left, bottom - top
    if subject_w < image.width * 0.08 or subject_h < image.height * 0.08:
        return image
    pad_x = max(8, round(subject_w * 0.06))
    pad_y = max(8, round(subject_h * 0.06))
    expanded = (
        max(0, left - pad_x), max(0, top - pad_y),
        min(image.width, right + pad_x), min(image.height, bottom + pad_y),
    )
    return image.crop(expanded)


def _render_story_frame(raw: bytes, frame: dict,
                        background: Tuple[int, int, int]) -> Image.Image:
    """Розміщує товар лише в центральній зоні Story без накладання тексту."""
    with Image.open(io.BytesIO(raw)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image = _trim_uniform_story_background(image)
    canvas = Image.new("RGB", (STORY_PRESET["width"], STORY_PRESET["height"]), background)
    left, top, right, bottom = STORY_PRODUCT_BOX
    zone_width, zone_height = right - left, bottom - top
    tile = Image.new("RGB", (zone_width, zone_height), background)
    fit = min(zone_width / max(1, image.width), zone_height / max(1, image.height))
    # 0.60 лишається стандартним значенням UI й означає повний, великий товар
    # у виділеній зоні; інші значення масштабуються відносно нього.
    relative_zoom = float(frame.get("zoom") or 0.6) / 0.6
    scale = fit * 0.93 * relative_zoom
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    free_x = zone_width - resized.width
    free_y = zone_height - resized.height
    x = round(free_x / 2 + float(frame.get("x") or 0.0) * abs(free_x) / 2)
    y = round(free_y / 2 + float(frame.get("y") or 0.0) * abs(free_y) / 2)
    tile.paste(resized, (x, y))
    canvas.paste(tile, (left, top))
    return canvas


def _jpeg_bytes(image: Image.Image) -> bytes:
    for quality in (92, 88, 84, 80, 76, 72, 68):
        output = io.BytesIO()
        image.save(
            output, "JPEG", quality=quality, optimize=True, progressive=True,
            subsampling="4:2:0", icc_profile=None,
        )
        value = output.getvalue()
        if len(value) <= JPEG_MAX_BYTES:
            return value
    raise ValueError("Не вдалося вкласти Instagram JPEG у ліміт 8 МБ")


def _story_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    configured = os.getenv("INSTAGRAM_STORY_FONT_BOLD" if bold else "INSTAGRAM_STORY_FONT", "").strip()
    candidates = [
        configured,
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _story_display_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Нейтральна fashion-типографіка без вигляду системної BMS-картки."""
    configured = os.getenv(
        "INSTAGRAM_STORY_DISPLAY_FONT_BOLD" if bold else "INSTAGRAM_STORY_DISPLAY_FONT", "",
    ).strip()
    candidates: List[Tuple[str, int]] = [
        (configured, 0),
        ("/System/Library/Fonts/Avenir Next.ttc", 2 if bold else 7),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
         else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ]
    for candidate, index in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            return ImageFont.truetype(candidate, size=size, index=index)
        except OSError:
            continue
    return _story_font(size, bold=bold)


def _wrap_story_line(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont,
                     max_width: int) -> List[str]:
    words = value.split()
    if not words:
        return [""]
    result: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            result.append(current)
            current = word
    result.append(current)
    return result


def _story_line_height(draw: ImageDraw.ImageDraw, value: str,
                       font: ImageFont.ImageFont, minimum: int) -> int:
    bbox = draw.textbbox((0, 0), value or " ", font=font)
    return max(minimum, bbox[3] - bbox[1])


def _fit_story_lines(draw: ImageDraw.ImageDraw, value: str, *, max_width: int,
                     start_size: int, min_size: int, max_lines: int,
                     bold: bool = False,
                     font_factory=None) -> Tuple[List[str], ImageFont.ImageFont]:
    """Підбирає кегль і безпечно скорочує лише крайній випадок."""
    factory = font_factory or _story_font
    for size in range(start_size, min_size - 1, -2):
        font = factory(size, bold=bold)
        lines = _wrap_story_line(draw, value, font, max_width)
        if len(lines) <= max_lines and all(draw.textlength(line, font=font) <= max_width for line in lines):
            return lines, font

    font = factory(min_size, bold=bold)
    lines = _wrap_story_line(draw, value, font, max_width)
    if len(lines) <= max_lines:
        return lines, font
    visible = lines[:max_lines]
    final = " ".join(lines[max_lines - 1:])
    while final and draw.textlength(f"{final}…", font=font) > max_width:
        final = final[:-1].rstrip()
    visible[-1] = f"{final}…" if final else "…"
    return visible, font


def _story_detail_summary(lines: Sequence[str]) -> str:
    clean = [str(line or "").strip().lstrip("—–-• ") for line in lines]
    clean = [line for line in clean if line]
    if not clean:
        return ""
    if clean[0].endswith(":") and len(clean) > 1:
        label = clean[0]
        values = clean[1:3]
        compact_values: List[str] = []
        for value in values:
            match = re.match(r"^(.+?)\s*\(на ніжку\s+(.+?)\)$", value, re.IGNORECASE)
            compact_values.append(f"{match.group(1)} / {match.group(2)}" if match else value)
        return f"{label} {' • '.join(compact_values)}"
    return "  •  ".join(clean[:2])


def _render_story_text(image: Image.Image, raw_text: Any) -> Image.Image:
    text_value = str(raw_text or "").strip()
    if not text_value:
        return image
    if len(text_value) > STORY_TEXT_LIMIT:
        raise ValueError(f"Текст Story може містити до {STORY_TEXT_LIMIT} символів")

    layer = image.convert("RGBA")
    draw = ImageDraw.Draw(layer, "RGBA")
    content_x = 78
    content_width = image.width - content_x * 2

    source_lines = [line.strip() for line in text_value.splitlines() if line.strip()]
    title = source_lines[0]
    remaining = source_lines[1:]
    price_line = next((line for line in remaining if STORY_PRICE_RE.search(line)), "")
    cta_line = next((line for line in reversed(remaining) if STORY_CTA_RE.search(line)), "")
    content_lines = [
        line for line in remaining
        if line not in {price_line, cta_line} and not STORY_PRODUCT_NUMBER_RE.search(line)
    ]
    spec_index = next(
        (index for index, line in enumerate(content_lines) if STORY_SPECS_RE.search(line)),
        len(content_lines),
    )
    subtitle_lines = content_lines[:spec_index]
    detail_lines = content_lines[spec_index:]
    number_match = STORY_PRODUCT_NUMBER_RE.search(text_value)
    detail_text = _story_detail_summary(detail_lines)
    subtitle_text = " ".join(subtitle_lines).strip()
    price_value = price_line.split(":", 1)[1].strip() if ":" in price_line else price_line

    # Мінімалістичний editorial-header: без брендових плашок, службових
    # підписів, декоративних фігур і рамок.
    title_lines, title_font = _fit_story_lines(
        draw, title, max_width=content_width, start_size=70, min_size=46,
        max_lines=2, bold=True, font_factory=_story_display_font,
    )
    title_size = int(getattr(title_font, "size", 52))
    title_heights = [
        _story_line_height(draw, line, title_font, title_size + 5)
        for line in title_lines
    ]
    cursor_y = 190 + STORY_CONTENT_OFFSET_Y
    for line, line_height in zip(title_lines, title_heights):
        draw.text((content_x, cursor_y), line, font=title_font, fill=(21, 20, 23, 255))
        cursor_y += line_height + 2

    if subtitle_text:
        subtitle_lines_fit, subtitle_font = _fit_story_lines(
            draw, subtitle_text, max_width=content_width, start_size=38,
            min_size=24, max_lines=2, bold=False, font_factory=_story_display_font,
        )
        subtitle_size = int(getattr(subtitle_font, "size", 28))
        subtitle_y = cursor_y + 8
        for subtitle_line in subtitle_lines_fit:
            draw.text((content_x, subtitle_y), subtitle_line, font=subtitle_font,
                      fill=(93, 89, 96, 255))
            subtitle_y += _story_line_height(
                draw, subtitle_line, subtitle_font, subtitle_size + 5,
            ) + 1
        cursor_y = subtitle_y

    detail_bottom = cursor_y
    if detail_text:
        detail_lines_fit, detail_font = _fit_story_lines(
            draw, detail_text, max_width=content_width, start_size=34,
            min_size=24, max_lines=1, bold=False, font_factory=_story_display_font,
        )
        detail_y = cursor_y + 5
        draw.text((content_x, detail_y), detail_lines_fit[0],
                  font=detail_font, fill=(102, 98, 105, 255))
        detail_size = int(getattr(detail_font, "size", 28))
        detail_bottom = detail_y + _story_line_height(
            draw, detail_lines_fit[0], detail_font, detail_size + 4,
        )

    # Удалий комерційний рядок із попередньої версії: велика акцентна ціна
    # зліва й окремий артикул у легкій контурній капсулі справа. Позиція
    # адаптується до довгого підзаголовка, але не заходить у зону товару.
    commerce_y = min(
        515 + STORY_CONTENT_OFFSET_Y,
        max(440 + STORY_CONTENT_OFFSET_Y, detail_bottom + 14),
    )
    label_font = _story_display_font(21, bold=True)
    price_font = _story_display_font(72, bold=True)
    number_font = _story_display_font(49, bold=True)
    number = number_match.group(0) if number_match else ""
    if price_value:
        draw.text((content_x, commerce_y), "ЦІНА", font=label_font,
                  fill=(111, 105, 115, 255))
        draw.text((content_x, commerce_y + 24), price_value, font=price_font,
                  fill=(78, 35, 88, 255))
    if number:
        number_width = draw.textlength(number, font=number_font)
        pill_width = max(205, number_width + 54)
        pill_left = content_x + content_width - pill_width
        label_width = draw.textlength("АРТИКУЛ", font=label_font)
        label_x = content_x + content_width - label_width
        draw.text((label_x, commerce_y), "АРТИКУЛ", font=label_font,
                  fill=(111, 105, 115, 255))
        pill_top = commerce_y + 30
        pill_bottom = pill_top + 70
        draw.rounded_rectangle(
            (pill_left, pill_top, content_x + content_width, pill_bottom),
            radius=21,
            fill=(249, 246, 250, 255),
            outline=(183, 144, 191, 255),
            width=2,
        )
        number_bbox = draw.textbbox((0, 0), number, font=number_font)
        number_height = number_bbox[3] - number_bbox[1]
        number_x = pill_left + (pill_width - number_width) / 2
        number_y = pill_top + (pill_bottom - pill_top - number_height) / 2 - number_bbox[1]
        draw.text((number_x, number_y), number, font=number_font,
                  fill=(103, 47, 116, 255))
    return layer.convert("RGB")


def _read_photo_bytes(bms: dict, spec: dict) -> List[bytes]:
    tg = _tg()
    photos, _kind = tg._photo_entries(bms)
    if not spec["image_idx"]:
        raise ValueError("У товару немає фото для Instagram")
    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes
    values = [read_image_bytes(photos[index]) for index in spec["image_idx"]]
    if not all(values):
        raise ValueError("Не вдалося прочитати одне або кілька фото товару")
    return [value for value in values if value is not None]


def _render_reel(images: Sequence[Image.Image]) -> bytes:
    if not images:
        raise ValueError("Для Reel потрібно хоча б одне фото")
    with tempfile.TemporaryDirectory(prefix="bms-instagram-reel-") as directory:
        root = Path(directory)
        paths: List[Path] = []
        for index, image in enumerate(images):
            path = root / f"frame-{index:02d}.jpeg"
            image.save(path, "JPEG", quality=90, subsampling="4:2:0")
            paths.append(path)
        duration = 5.0 if len(paths) == 1 else 2.5
        lines: List[str] = []
        for path in paths:
            lines.extend([f"file '{path}'", f"duration {duration:.1f}"])
        lines.append(f"file '{paths[-1]}'")
        manifest = root / "frames.txt"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = root / "reel.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-vf", "fps=30,format=yuv420p", "-c:v", "libx264",
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", str(output),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=180)
        except FileNotFoundError as exc:
            raise ValueError("Для створення Reel на цьому комп’ютері не знайдено FFmpeg") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", b"")
            message = detail.decode("utf-8", errors="replace")[-500:] if detail else str(exc)
            raise ValueError(f"Не вдалося створити Reel: {message}") from exc
        value = output.read_bytes()
        if not value or len(value) > REEL_MAX_BYTES:
            raise ValueError("Reel порожній або перевищує ліміт 300 МБ")
        return value


def render_media_for_product(db: Session, product_id: int, payload: dict) -> dict:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        raise ValueError("Товар не знайдено")
    photos, _kind = tg._photo_entries(bms)
    spec = normalize_media_spec(payload, len(photos))
    values = _read_photo_bytes(bms, spec)
    preset = STORY_PRESET if spec["publish_type"] in {"story", "reel"} else FEED_PRESETS[spec["feed_preset"]]
    explicit_frames = {
        int(frame.get("image_idx"))
        for frame in (payload.get("frames") if isinstance(payload.get("frames"), list) else [])
        if isinstance(frame, dict) and str(frame.get("image_idx", "")).isdigit()
    }
    if spec["publish_type"] == "feed":
        for raw, frame in zip(values, spec["frames"]):
            if int(frame["image_idx"]) not in explicit_frames:
                frame["zoom"] = _recommended_feed_zoom(raw, preset)[0]
    background = _background_rgb(spec["background"])
    if spec["publish_type"] == "story":
        frames = [
            _render_story_frame(raw, frame, background)
            for raw, frame in zip(values, spec["frames"])
        ]
    else:
        frames = [
            _render_frame(raw, preset["width"], preset["height"], frame, background)
            for raw, frame in zip(values, spec["frames"])
        ]
    if spec["publish_type"] == "story":
        frames[0] = _render_story_text(frames[0], payload.get("story_text"))
    if spec["publish_type"] == "reel":
        return {
            "spec": spec,
            "assets": [{
                "type": "VIDEO", "extension": "mp4", "content_type": "video/mp4",
                "bytes": _render_reel(frames), "width": preset["width"],
                "height": preset["height"], "alt_text": "",
            }],
            "cover": _jpeg_bytes(frames[0]),
            "output": preset,
        }
    alt_texts = payload.get("alt_texts") if isinstance(payload.get("alt_texts"), list) else []
    common_alt = str(payload.get("alt_text") or "").strip()
    assets = []
    for index, image in enumerate(frames):
        alt_text = str(alt_texts[index] if index < len(alt_texts) else common_alt).strip()
        if len(alt_text) > 1000:
            raise ValueError("Alt text Instagram може містити до 1000 символів")
        assets.append({
            "type": "IMAGE", "extension": "jpeg", "content_type": "image/jpeg",
            "bytes": _jpeg_bytes(image), "width": preset["width"],
            "height": preset["height"], "alt_text": alt_text,
        })
    return {"spec": spec, "assets": assets, "cover": None, "output": preset}


def _validate_schedule(raw: Any) -> Tuple[Optional[datetime], Optional[str]]:
    if raw in (None, "", False):
        return None, None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KYIV_TZ)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None, "Некоректний час Instagram-публікації"
    now = datetime.now(timezone.utc)
    if parsed < now + timedelta(minutes=2):
        return None, "Запланований Instagram-пост має бути щонайменше через 2 хвилини"
    if parsed > now + timedelta(days=365):
        return None, "Instagram-публікацію можна запланувати не далі ніж на 365 днів"
    return parsed, None


def _next_morning() -> str:
    now = datetime.now(KYIV_TZ)
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if target < now + timedelta(minutes=10):
        target += timedelta(days=1)
    return target.isoformat()


def preview_post(db: Session, product_id: int) -> dict:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}

    product_number = str(bms.get("productnumber") or "")
    sizes = tg._available_sizes(db, product_number)
    photos, image_kind = tg._photo_entries(bms)
    feed_zoom_defaults, feed_edge_adjusted = _feed_zoom_defaults(photos)
    caption = build_caption(bms, sizes)
    story_text = build_story_text(bms, sizes)
    warnings: List[str] = []
    if not photos:
        warnings.append("У товару немає фото — Instagram-пост неможливо підготувати.")
    if len(photos) > MAX_MEDIA:
        warnings.append(f"Карусель вміщує до {MAX_MEDIA} фото; зайві треба прибрати у редакторі.")
    if not sizes and not tg._is_bag(bms):
        warnings.append("Немає доступних розмірів у наявності.")
    if tg._condition_requires_confirmation(bms):
        warnings.append(
            f"Стан «{tg._cap(bms.get('conditionname'))}» потребуватиме окремого "
            "підтвердження перед живою публікацією."
        )
    status = connection_status()
    if not status["configured"]:
        warnings.append("Жива публікація стане доступною після підключення Worker і публічного R2.")

    media_spec = normalize_media_spec({}, len(photos))
    square_defaults = feed_zoom_defaults.get("square", [])
    for frame in media_spec["frames"]:
        image_idx = int(frame["image_idx"])
        if image_idx < len(square_defaults):
            frame["zoom"] = square_defaults[image_idx]

    return {
        "ok": True,
        "mode": status["mode"],
        "product_id": product_id,
        "productnumber": product_number.lstrip("#"),
        "brand": bms.get("brandname"),
        "model": bms.get("model"),
        "type": bms.get("typename"),
        "condition": tg._condition_line(bms),
        "condition_name": tg._cap(bms.get("conditionname")) or None,
        "condition_confirmation_required": tg._condition_requires_confirmation(bms),
        "caption": caption,
        "caption_len": len(caption),
        "caption_limit": CAPTION_LIMIT,
        "story_text": story_text,
        "story_text_limit": STORY_TEXT_LIMIT,
        "sizes": sizes,
        "image_count": len(photos),
        "image_kind": image_kind,
        "image_urls": [getattr(photo, "url", "") for photo in photos],
        "image_names": [getattr(photo, "filename", "") for photo in photos],
        "default_image_idx": list(range(min(len(photos), MAX_MEDIA))),
        "carousel_limit": MAX_MEDIA,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "default_feed_preset": "square",
        "feed_presets": FEED_PRESETS,
        "feed_zoom_defaults": feed_zoom_defaults,
        "feed_edge_adjusted": feed_edge_adjusted,
        "story_preset": STORY_PRESET,
        "publish_types": PUBLISH_TYPES,
        "media_spec": media_spec,
        "default_publish_at": _next_morning(),
        "connection": status,
        "warnings": warnings,
    }


def preview_posts_batch(db: Session, product_ids: List[int]) -> dict:
    tg = _tg()
    clean: List[int] = []
    for raw in product_ids[:200]:
        try:
            product_id = int(raw)
        except (TypeError, ValueError):
            continue
        if product_id > 0 and product_id not in clean:
            clean.append(product_id)

    grouped: "OrderedDict[str, dict]" = OrderedDict()
    missing: List[int] = []
    for product_id in clean:
        bms = tg._load_product(db, product_id)
        if not bms:
            missing.append(product_id)
            continue
        product_number = str(bms.get("productnumber") or "")
        key = product_number.lstrip("#").casefold() or f"id:{product_id}"
        grouped.setdefault(key, {
            "product_id": product_id,
            "productnumber": product_number,
            "source_product_ids": [],
        })["source_product_ids"].append(product_id)

    if not grouped:
        return {"ok": False, "error": "Серед виділеного не знайдено товарів"}
    if len(grouped) > BATCH_MAX_PRODUCTS:
        return {
            "ok": False,
            "error": (
                f"Перший безпечний пакет Instagram — до {BATCH_MAX_PRODUCTS} "
                "унікальних товарів"
            ),
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
        "mode": connection_status()["mode"],
        "selected_count": len(clean),
        "unique_count": len(items),
        "merged_count": max(0, len(clean) - len(items)),
        "missing_ids": missing,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "items": items,
    }


def dry_run(db: Session, product_id: int, payload: dict) -> dict:
    """Повний renderer і валідація без R2, Worker, D1 та Meta Graph API."""
    preview = preview_post(db, product_id)
    if not preview.get("ok"):
        return preview

    caption = str(payload.get("caption") or preview["caption"])
    caption_error = validate_caption(caption)
    if caption_error:
        return {"ok": False, "error": caption_error}

    scheduled_at, schedule_error = _validate_schedule(payload.get("publish_at"))
    if schedule_error:
        return {"ok": False, "error": schedule_error}
    try:
        rendered = render_media_for_product(db, product_id, payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    assets = rendered["assets"]
    spec = rendered["spec"]

    return {
        "ok": True,
        "mode": "dry_run",
        "external_calls": 0,
        "product_id": product_id,
        "productnumber": preview["productnumber"],
        "publish_type": spec["publish_type"],
        "media_count": len(assets),
        "image_idx": spec["image_idx"],
        "feed_preset": spec["feed_preset"],
        "output": rendered["output"],
        "media_bytes": [len(asset["bytes"]) for asset in assets],
        "media_types": [asset["type"] for asset in assets],
        "caption_len": len(caption),
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "would_publish_as": (
            "reel" if spec["publish_type"] == "reel"
            else "story" if spec["publish_type"] == "story"
            else "image" if len(assets) == 1 else "carousel"
        ),
        "note": "Перевірено локально. R2, Cloudflare і Meta не викликалися.",
    }


def dry_run_batch(db: Session, raw_items: List[dict]) -> dict:
    """Перевіряє стабільний пакет чернеток без зовнішніх викликів і записів."""
    if not isinstance(raw_items, list) or not raw_items:
        return {"ok": False, "error": "Не вибрано Instagram-чернетки"}

    product_ids: List[int] = []
    drafts_by_id: dict[int, dict] = {}
    for raw in raw_items[:200]:
        if not isinstance(raw, dict):
            return {"ok": False, "error": "Некоректна Instagram-чернетка"}
        try:
            product_id = int(raw.get("product_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "У чернетці немає коректного product_id"}
        if product_id <= 0:
            return {"ok": False, "error": "У чернетці немає коректного product_id"}
        if product_id not in drafts_by_id:
            product_ids.append(product_id)
            drafts_by_id[product_id] = raw

    batch_preview = preview_posts_batch(db, product_ids)
    if not batch_preview.get("ok"):
        return batch_preview

    results: List[dict] = []
    for group in batch_preview["items"]:
        source_ids = group.get("source_product_ids") or [group["product_id"]]
        selected_id = next(
            (product_id for product_id in source_ids if product_id in drafts_by_id),
            group["product_id"],
        )
        payload = drafts_by_id.get(selected_id, {})
        result = dry_run(db, int(group["product_id"]), payload)
        results.append({
            "product_id": int(group["product_id"]),
            "productnumber": str(group.get("productnumber") or "").lstrip("#"),
            "source_product_ids": source_ids,
            "ok": bool(result.get("ok")),
            "result": result if result.get("ok") else None,
            "error": result.get("error") if not result.get("ok") else None,
        })

    for product_id in batch_preview.get("missing_ids", []):
        results.append({
            "product_id": product_id,
            "productnumber": "",
            "source_product_ids": [product_id],
            "ok": False,
            "result": None,
            "error": "Товар не знайдено",
        })

    success_count = sum(1 for item in results if item["ok"])
    error_count = len(results) - success_count
    return {
        "ok": error_count == 0,
        "mode": "dry_run",
        "external_calls": 0,
        "status": "success" if error_count == 0 else "error",
        "selected_count": batch_preview["selected_count"],
        "unique_count": batch_preview["unique_count"],
        "merged_count": batch_preview["merged_count"],
        "counts": {"success": success_count, "error": error_count},
        "results": results,
        "note": "Усі чернетки перевірено локально. R2, Cloudflare і Meta не викликалися.",
    }


def render_preview_jpeg(db: Session, product_id: int, payload: dict) -> bytes:
    # UI показує перший кадр/обкладинку. Для Reel не запускаємо FFmpeg на
    # кожен рух повзунка — рендеримо той самий 9:16 JPEG точним image-кодом.
    preview_payload = dict(payload)
    selected = payload.get("image_idx") if isinstance(payload.get("image_idx"), list) else []
    preview_payload["image_idx"] = selected[:1]
    if str(payload.get("publish_type") or "feed").lower() == "reel":
        preview_payload["publish_type"] = "story"
        preview_payload["story_text"] = ""
    rendered = render_media_for_product(db, product_id, preview_payload)
    return rendered["assets"][0]["bytes"]


def _prepare(db: Session, product_id: int, payload: dict) -> dict:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        raise ValueError("Товар не знайдено")
    caption = str(payload.get("caption") or build_caption(
        bms, tg._available_sizes(db, str(bms.get("productnumber") or "")),
    )).strip()
    problem = validate_caption(caption)
    if problem:
        raise ValueError(problem)
    if (tg._condition_requires_confirmation(bms)
            and payload.get("condition_confirmed") is not True):
        raise ValueError(
            f"Стан «{tg._cap(bms.get('conditionname'))}» потребує явного підтвердження"
        )
    scheduled_at, schedule_error = _validate_schedule(payload.get("publish_at"))
    if schedule_error:
        raise ValueError(schedule_error)
    rendered = render_media_for_product(db, product_id, payload)
    return {
        "bms": bms,
        "pnum": str(bms.get("productnumber") or ""),
        "caption": caption,
        "scheduled_at": scheduled_at,
        "rendered": rendered,
    }


def _upload_derivatives(prepared: dict) -> dict:
    r2 = _r2()
    if not r2.is_enabled() or not r2.R2_PUBLIC_BASE_URL:
        raise RuntimeError("Публічний Cloudflare R2 не налаштований")
    rendered = prepared["rendered"]
    digest_source = prepared["caption"].encode("utf-8")
    for asset in rendered["assets"]:
        digest_source += asset["bytes"]
    digest = hashlib.sha256(digest_source).hexdigest()[:32]
    safe_number = prepared["pnum"].lstrip("#").replace("/", "-") or "product"
    base = f"social/instagram/{safe_number}/{digest}"
    media = []
    keys = []
    for index, asset in enumerate(rendered["assets"], 1):
        extension = asset["extension"]
        key = f"{base}/{index:02d}.{extension}"
        r2.upload_bytes(asset["bytes"], key, content_type=asset["content_type"])
        url = r2.public_url(key)
        if not url:
            raise RuntimeError("R2 не повернув публічний Instagram media URL")
        media.append({
            "type": asset["type"], "url": url,
            "alt_text": asset.get("alt_text") or "",
        })
        keys.append(key)
    cover_url = None
    cover_key = None
    if rendered.get("cover"):
        cover_key = f"{base}/cover.jpeg"
        r2.upload_bytes(rendered["cover"], cover_key, content_type="image/jpeg")
        cover_url = r2.public_url(cover_key)
    return {
        "digest": digest,
        "media": media,
        "media_keys": keys,
        "cover_key": cover_key,
        "cover_url": cover_url,
    }


async def _dispatcher_request(method: str, path: str, *, payload: Optional[dict] = None) -> dict:
    dispatcher_url, dispatcher_key = _dispatcher_config()
    if not dispatcher_url or not dispatcher_key:
        raise RuntimeError("Instagram-диспетчер ще не підключений")
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.request(
            method, f"{dispatcher_url}{path}",
            headers={"Authorization": f"Bearer {dispatcher_key}"},
            json=payload,
        )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(data.get("error") or f"Instagram Worker повернув HTTP {response.status_code}")
    return data


async def dispatcher_status() -> dict:
    status = connection_status()
    if not status["dispatcher_configured"]:
        return status
    try:
        remote = await _dispatcher_request("GET", "/v1/status")
        oauth_connected = bool(remote.get("accounts"))
        live_available = bool(status["configured"] and oauth_connected and remote.get("live_publish_enabled"))
        return {
            **status,
            "configured": live_available,
            "live_publish_available": live_available,
            "schedule_available": live_available,
            "dispatcher": remote,
            "oauth_connected": oauth_connected,
            "worker_deployed": True,
        }
    except Exception as exc:
        return {**status, "configured": False, "live_publish_available": False,
                "oauth_connected": False, "dispatcher_error": str(exc)}


async def oauth_start() -> dict:
    return await _dispatcher_request("POST", "/v1/oauth/start", payload={})


async def account_check() -> dict:
    """Read-only verification of the encrypted Instagram token and target account."""
    return await _dispatcher_request("GET", "/v1/account-check")


def _cached_result(db: Session, key: str) -> Optional[dict]:
    row = db.execute(text("""
        SELECT product_id, product_number, dispatcher_job_id, instagram_media_id,
               status, scheduled_at, published_at, error
        FROM instagram_publications WHERE idempotency_key = :key
    """), {"key": key}).mappings().first()
    return {
        "ok": row["status"] not in ("failed", "error", "cancelled"),
        "cached": True,
        **dict(row),
    } if row else None


def _record(db: Session, *, product_id: int, prepared: dict, uploaded: dict,
            dispatch: dict, idempotency_key: str, request_payload: dict) -> None:
    db.execute(text("""
        INSERT INTO instagram_publications (
            product_id, product_number, instagram_account_id, instagram_media_id,
            container_id, dispatcher_job_id, idempotency_key, status, media_type,
            caption, media_urls, scheduled_at, published_at, payload_json,
            error, updated_at
        ) VALUES (
            :pid, :pnum, :account, :media_id, NULL, :job, :idem, :status, :type,
            :caption, CAST(:media AS jsonb), :scheduled, :published,
            CAST(:payload AS jsonb), :error, now()
        )
        ON CONFLICT (idempotency_key) DO UPDATE SET
            dispatcher_job_id = EXCLUDED.dispatcher_job_id,
            instagram_media_id = COALESCE(EXCLUDED.instagram_media_id, instagram_publications.instagram_media_id),
            status = EXCLUDED.status,
            published_at = COALESCE(EXCLUDED.published_at, instagram_publications.published_at),
            error = EXCLUDED.error,
            updated_at = now()
    """), {
        "pid": product_id,
        "pnum": prepared["pnum"],
        "account": dispatch.get("account_id"),
        "media_id": dispatch.get("instagram_media_id"),
        "job": dispatch.get("job_id"),
        "idem": idempotency_key,
        "status": dispatch.get("status") or ("scheduled" if prepared["scheduled_at"] else "queued"),
        "type": prepared["rendered"]["spec"]["publish_type"],
        "caption": prepared["caption"],
        "media": json.dumps(uploaded["media"], ensure_ascii=False),
        "scheduled": prepared["scheduled_at"],
        "published": datetime.now(timezone.utc) if dispatch.get("status") == "published" else None,
        "payload": json.dumps({
            **request_payload,
            "permalink": dispatch.get("permalink"),
            "phase": dispatch.get("phase"),
        }, ensure_ascii=False),
        "error": dispatch.get("error"),
    })
    db.commit()


async def create_post(db: Session, product_id: int, payload: dict,
                      *, prepared: Optional[dict] = None) -> dict:
    idempotency_key = str(payload.get("idempotency_key") or uuid.uuid4())[:180]
    cached = _cached_result(db, idempotency_key)
    if cached:
        return cached
    if payload.get("dry_run") is True:
        return dry_run(db, product_id, payload)
    status = await dispatcher_status()
    if not status.get("live_publish_available") or not status.get("oauth_connected"):
        missing = status.get("missing") or []
        detail = status.get("dispatcher_error") or ", ".join(missing) or "OAuth-акаунт не підключено"
        return {"ok": False, "error": f"Instagram ще не готовий до публікації: {detail}"}
    try:
        ready = prepared or _prepare(db, product_id, payload)
        from starlette.concurrency import run_in_threadpool
        uploaded = await run_in_threadpool(_upload_derivatives, ready)
        spec = ready["rendered"]["spec"]
        request_payload = {
            "idempotency_key": idempotency_key,
            "product_id": product_id,
            "product_number": ready["pnum"].lstrip("#"),
            "publish_type": spec["publish_type"].upper(),
            "caption": "" if spec["publish_type"] == "story" else ready["caption"],
            "media": uploaded["media"],
            "cover_url": uploaded.get("cover_url"),
            "publish_at": ready["scheduled_at"].isoformat() if ready["scheduled_at"] else None,
            "collaborators": payload.get("collaborators") or [],
            "user_tags": payload.get("user_tags") or [],
            "product_tags": payload.get("product_tags") or [],
            "location_id": payload.get("location_id"),
            "share_to_feed": payload.get("share_to_feed") is not False,
            "is_ai_generated": payload.get("is_ai_generated") is True,
            "is_paid_partnership": payload.get("is_paid_partnership") is True,
            "branded_content_sponsor_ids": payload.get("branded_content_sponsor_ids") or [],
        }
        dispatched = await _dispatcher_request("POST", "/v1/jobs", payload=request_payload)
        _record(
            db, product_id=product_id, prepared=ready, uploaded=uploaded,
            dispatch=dispatched, idempotency_key=idempotency_key,
            request_payload={**request_payload, "media_spec": spec, "media_keys": uploaded["media_keys"]},
        )
        dispatch_status = str(dispatched.get("status") or "queued").lower()
        if dispatch_status in {"failed", "error", "cancelled"}:
            return {
                "ok": False,
                "error": dispatched.get("error") or "Meta відхилила Instagram-публікацію",
                "product_id": product_id,
                "productnumber": ready["pnum"].lstrip("#"),
                "idempotency_key": idempotency_key,
                "job_id": dispatched.get("job_id"),
                "status": dispatch_status,
                "publish_type": spec["publish_type"],
            }
        return {
            "ok": True, "product_id": product_id,
            "productnumber": ready["pnum"].lstrip("#"),
            "idempotency_key": idempotency_key,
            "job_id": dispatched.get("job_id"),
            "status": dispatched.get("status"),
            "scheduled_at": ready["scheduled_at"].isoformat() if ready["scheduled_at"] else None,
            "publish_type": spec["publish_type"],
        }
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc), "idempotency_key": idempotency_key}


async def create_posts_batch(db: Session, items: Any, batch_id: Any,
                             *, dry_run_only: bool = False) -> dict:
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Пакет Instagram порожній"}
    if len(items) > BATCH_MAX_PRODUCTS:
        return {"ok": False, "error": f"Один пакет Instagram — до {BATCH_MAX_PRODUCTS} товарів"}
    batch = str(batch_id or "").strip()
    if not batch:
        return {"ok": False, "error": "Пакет не має batch_id"}
    if dry_run_only:
        return dry_run_batch(db, [dict(item.get("payload") or item, product_id=item.get("product_id")) for item in items])
    prepared_items: List[Tuple[int, dict, dict]] = []
    numbers = set()
    for position, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("product_id"):
            return {"ok": False, "error": f"Картка {position + 1} пошкоджена"}
        pid = int(item["product_id"])
        payload = dict(item.get("payload") or item)
        payload["idempotency_key"] = str(payload.get("idempotency_key") or f"{batch}:{pid}")[:180]
        try:
            ready = _prepare(db, pid, payload)
        except ValueError as exc:
            return {"ok": False, "error": f"#{pid}: {exc}"}
        number = ready["pnum"].lstrip("#").casefold()
        if number in numbers:
            return {"ok": False, "error": f"Товар {ready['pnum']} повторюється в пакеті"}
        numbers.add(number)
        prepared_items.append((pid, payload, ready))
    results = []
    for position, (pid, payload, ready) in enumerate(prepared_items):
        result = await create_post(db, pid, payload, prepared=ready)
        results.append({
            "product_id": pid, "productnumber": ready["pnum"].lstrip("#"),
            "status": result.get("status") if result.get("ok") else "error",
            "result": result if result.get("ok") else None,
            "error": result.get("error") if not result.get("ok") else None,
        })
        if position < len(prepared_items) - 1:
            await asyncio.sleep(0.25)
    success = sum(1 for row in results if row["error"] is None)
    errors = len(results) - success
    return {
        "ok": True, "batch_id": batch,
        "status": "success" if not errors else ("error" if not success else "partial"),
        "counts": {"success": success, "error": errors, "total": len(results)},
        "results": results,
    }


async def sync_statuses(db: Session, *, product_id: Optional[int] = None) -> dict:
    params: Dict[str, Any] = {}
    clause = ""
    if product_id is not None:
        params["pid"] = int(product_id)
        clause = "AND (product_id = :pid OR product_number = (SELECT productnumber FROM products WHERE id = :pid))"
    rows = db.execute(text(f"""
        SELECT id, dispatcher_job_id FROM instagram_publications
        WHERE dispatcher_job_id IS NOT NULL
          AND (
                status IN ('queued', 'scheduled', 'processing', 'retrying')
                OR (status = 'published' AND COALESCE(payload_json->>'permalink', '') = '')
          )
          {clause}
        ORDER BY updated_at LIMIT 100
    """), params).mappings().all()
    updated = 0
    errors = []
    for row in rows:
        try:
            data = await _dispatcher_request("GET", f"/v1/jobs/{row['dispatcher_job_id']}")
            db.execute(text("""
                UPDATE instagram_publications
                   SET status = :status,
                       instagram_account_id = COALESCE(:account, instagram_account_id),
                       instagram_media_id = COALESCE(:media_id, instagram_media_id),
                       scheduled_at = COALESCE(CAST(:scheduled AS timestamptz), scheduled_at),
                       published_at = COALESCE(CAST(:published AS timestamptz), published_at),
                       error = :error,
                       payload_json = payload_json || CAST(:extra AS jsonb),
                       updated_at = now()
                 WHERE id = :id
            """), {
                "id": row["id"], "status": data.get("status") or "queued",
                "account": data.get("account_id"), "media_id": data.get("instagram_media_id"),
                "scheduled": data.get("scheduled_at"), "published": data.get("published_at"),
                "error": data.get("error"),
                "extra": json.dumps({"permalink": data.get("permalink"), "phase": data.get("phase")}),
            })
            updated += 1
        except Exception as exc:
            errors.append({"job_id": row["dispatcher_job_id"], "error": str(exc)})
    db.commit()
    return {"ok": not errors, "checked": len(rows), "updated": updated, "errors": errors}


async def cancel_publication(db: Session, publication_id: int) -> dict:
    row = db.execute(text("""
        SELECT id, dispatcher_job_id, status
        FROM instagram_publications
        WHERE id = :id
    """), {"id": int(publication_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Instagram-публікацію не знайдено"}
    if not row["dispatcher_job_id"]:
        return {"ok": False, "error": "Публікація не має job у диспетчері"}
    try:
        data = await _dispatcher_request("DELETE", f"/v1/jobs/{row['dispatcher_job_id']}")
        db.execute(text("""
            UPDATE instagram_publications
               SET status = 'cancelled', error = NULL,
                   payload_json = payload_json || CAST(:extra AS jsonb), updated_at = now()
             WHERE id = :id
        """), {
            "id": int(publication_id),
            "extra": json.dumps({"phase": data.get("phase"), "cancelled_at": datetime.now(timezone.utc).isoformat()}),
        })
        db.commit()
        return {"ok": True, "publication_id": int(publication_id), **data}
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc)}


async def reschedule_publication(db: Session, publication_id: int, publish_at: Any) -> dict:
    row = db.execute(text("""
        SELECT id, dispatcher_job_id, status
        FROM instagram_publications
        WHERE id = :id
    """), {"id": int(publication_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Instagram-публікацію не знайдено"}
    if not row["dispatcher_job_id"]:
        return {"ok": False, "error": "Публікація не має job у диспетчері"}
    try:
        scheduled_at, schedule_error = _validate_schedule(publish_at)
        if schedule_error or scheduled_at is None:
            raise ValueError(schedule_error or "Потрібна майбутня дата й час")
        data = await _dispatcher_request(
            "PATCH", f"/v1/jobs/{row['dispatcher_job_id']}",
            payload={"publish_at": scheduled_at.isoformat()},
        )
        db.execute(text("""
            UPDATE instagram_publications
               SET status = 'scheduled', scheduled_at = :scheduled,
                   error = NULL, payload_json = payload_json || CAST(:extra AS jsonb),
                   updated_at = now()
             WHERE id = :id
        """), {
            "id": int(publication_id), "scheduled": scheduled_at,
            "extra": json.dumps({"phase": data.get("phase")}),
        })
        db.commit()
        return {"ok": True, "publication_id": int(publication_id), **data}
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc)}


def product_status(db: Session, product_id: int) -> dict:
    rows = db.execute(text("""
        SELECT id, status, media_type, dispatcher_job_id, instagram_media_id,
               scheduled_at, published_at, media_urls, error, payload_json, created_at
        FROM instagram_publications
        WHERE product_id = :pid
           OR product_number = (SELECT productnumber FROM products WHERE id = :pid)
        ORDER BY created_at DESC
    """), {"pid": product_id}).mappings().all()
    return {"product_id": product_id, "publications": [dict(row) for row in rows]}

"""Підбірка — один банер-сітка з кількох РІЗНИХ товарів.

Відмінність від наявних колажів у тому, що саме потрапляє в комірки. Viber-картка
(`viber_publisher.render_collage`) збирає кілька фото ОДНОГО товару; підбірка бере
по одному фото з кожного товару й розкладає їх рівною пропорційною сіткою.

Тому підбірка ніколи не пише у `viber_publications` / `facebook_publications` і не
змінює статус опублікованості жодного товару: це рекламний банер каналу, а не
публікація конкретної позиції (див. `migrations/2026_08_17_001_create_social_collection_posts.sql`).

Геометрію, обрізання полів і стиснення JPEG свідомо не переписуємо — беремо з
`viber_publisher`, який роками веде реальний канал. Так сітка виглядає рідною
поруч зі звичайними картками, а не «ще одним рендерером».
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageOps
from sqlalchemy import text
from sqlalchemy.orm import Session


MAX_ITEMS = 16
MIN_ITEMS = 2

GRID_LAYOUTS: Dict[str, dict] = {
    "grid9": {"label": "Сітка 3×3 · до 9 товарів", "cols": 3, "capacity": 9},
    "grid16": {"label": "Сітка 4×4 · до 16 товарів", "cols": 4, "capacity": 16},
}

# Viber тягне картку через власний диспетчер і має жорсткий ліміт розміру, тому
# полотно лишається 1080 — те саме, що й у звичайної картки. Facebook приймає
# більше, і для сітки 4×4 зайві пікселі — це різниця між «видно модель» і
# «видно пляму», тож там полотно більше.
PLATFORMS: Dict[str, dict] = {
    "viber": {
        "label": "Viber-канал",
        "size": 1080,
        "max_bytes": 950_000,
        "thumbnail": True,
        "caption_limit": 768,
        "markdown": True,
    },
    "facebook": {
        "label": "Сторінка Facebook",
        "size": 1440,
        "max_bytes": 7_900_000,
        "thumbnail": False,
        "caption_limit": 63_206,
        "markdown": False,
    },
}

BACKGROUNDS = [
    {"key": "white", "label": "Біле"},
    {"key": "soft", "label": "Світле"},
    {"key": "warm", "label": "Тепле"},
    {"key": "dark", "label": "Темне"},
]

FRAME_ZOOM_MIN = 0.5
FRAME_ZOOM_MAX = 3.0
MARGIN_RATIO = 0.0167  # 18px на полотні 1080 — той самий відступ, що й у картки

# Артикул і ціна живуть у власній смузі під фото, а не поверх кадру: на живих
# фото (полиця, руки, інтер'єр) напис поверх зображення нечитабельний, а
# півпрозора плашка під ним перетворює мінімалістичну сітку на строкату.
LABEL_BAND_RATIO = 0.21
LABEL_BAND_MIN = 44
# Нижче цього кегля ціна в стрічці вже не читається — тоді замість двох рядів
# збираємо один компактний.
LABEL_PRICE_MIN_STACKED = 21

# Та сама палітра, що й у Story-картці Instagram: слива для ціни й артикула,
# приглушений сірий для другорядного. Один магазин — один голос, тому кольори
# й типографіку не вигадуємо заново, а переносимо з уже затвердженого макета.
STORY_PLUM = (78, 35, 88)
STORY_PLUM_TEXT = (103, 47, 116)
STORY_PILL_FILL = (249, 246, 250)
STORY_PILL_OUTLINE = (183, 144, 191)
STORY_MUTED = (111, 105, 115)

LABEL_COLORS = {
    "white": {
        "price": STORY_PLUM, "number": STORY_PLUM_TEXT, "muted": STORY_MUTED,
        "pill_fill": STORY_PILL_FILL, "pill_outline": STORY_PILL_OUTLINE,
    },
    "soft": {
        "price": STORY_PLUM, "number": STORY_PLUM_TEXT, "muted": STORY_MUTED,
        "pill_fill": (252, 250, 253), "pill_outline": STORY_PILL_OUTLINE,
    },
    "warm": {
        "price": (74, 38, 74), "number": (104, 54, 104), "muted": (124, 110, 106),
        "pill_fill": (253, 250, 250), "pill_outline": (191, 158, 178),
    },
    # На темному тлі слива стає нечитабельною, тому беремо її світлий відповідник:
    # відчуття кольору лишається, контраст повертається.
    "dark": {
        "price": (240, 232, 246), "number": (226, 210, 235), "muted": (150, 146, 156),
        "pill_fill": (44, 38, 50), "pill_outline": (126, 100, 136),
    },
}


def _viber():
    try:
        from services import viber_publisher
    except ImportError:
        from backend.services import viber_publisher
    return viber_publisher


def _tg():
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


def _read_image_bytes(entry: Any) -> Optional[bytes]:
    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes
    return read_image_bytes(entry)


def platform_config(platform: Any) -> dict:
    key = str(platform or "viber").strip().lower()
    if key not in PLATFORMS:
        raise ValueError(f"Підбірку підтримують лише {', '.join(PLATFORMS)}")
    return {**PLATFORMS[key], "key": key}


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def layout_for_count(count: int) -> str:
    """Найменша сітка, що вміщує вибір: 9 товарів не мають їхати в 4×4."""
    return "grid9" if count <= GRID_LAYOUTS["grid9"]["capacity"] else "grid16"


def _columns(count: int, layout: str) -> int:
    """Скільки колонок реально малювати.

    Обрана розкладка задає стелю (3 або 4), але сітка підлаштовується під
    кількість: 4 товари в сітці 3×3 — це ряд із трьох і самотній кадр під ним,
    тобто діра. Квадратний корінь дає рівний прямокутник для будь-якого числа.
    """
    preferred = int(GRID_LAYOUTS[layout]["cols"])
    return max(1, min(preferred, math.ceil(math.sqrt(max(1, count)))))


def grid_geometry(count: int, layout: str, size: int, gap: int) -> dict:
    """Комірки однакового розміру плюс висота полотна під них.

    Полотно квадратне лише тоді, коли сітка справді квадратна. Для 6 товарів
    (3×2) квадрат дав би широку порожню смугу згори й знизу: комірка все одно
    рахується від ширини, тож зайва висота — це не «повітря», а витрачений
    розмір картинки у стрічці. Тому висота обрізається до вмісту.
    """
    cols = _columns(count, layout)
    rows = math.ceil(count / cols)
    margin = max(8, round(size * MARGIN_RATIO))
    inner = size - 2 * margin
    cell = (inner - gap * (cols - 1)) // cols
    grid_height = rows * cell + gap * (rows - 1)
    height = min(size, grid_height + 2 * margin)
    offset_y = (height - grid_height) // 2
    cells: List[Tuple[int, int, int, int]] = []
    for index in range(count):
        row, col = divmod(index, cols)
        in_row = min(cols, count - row * cols)
        used = in_row * cell + gap * (in_row - 1)
        offset_x = (size - used) // 2
        cells.append((offset_x + col * (cell + gap), offset_y + row * (cell + gap), cell, cell))
    return {"width": size, "height": height, "cols": cols, "rows": rows, "cells": cells}


def normalize_spec(payload: dict, *, item_count: Optional[int] = None) -> dict:
    """Один формат специфікації між прев'ю, рендером, публікацією і БД."""
    config = platform_config(payload.get("platform"))
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    items: List[dict] = []
    seen: set = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            product_id = int(raw.get("product_id"))
        except (TypeError, ValueError):
            continue
        if product_id <= 0 or product_id in seen:
            continue
        seen.add(product_id)
        try:
            image_idx = max(0, int(raw.get("image_idx") or 0))
        except (TypeError, ValueError):
            image_idx = 0
        items.append({
            "product_id": product_id,
            "image_idx": image_idx,
            "zoom": _clamp(raw.get("zoom"), FRAME_ZOOM_MIN, FRAME_ZOOM_MAX, 1.0),
            "x": _clamp(raw.get("x"), -1.0, 1.0, 0.0),
            "y": _clamp(raw.get("y"), -1.0, 1.0, 0.0),
        })
        if len(items) >= MAX_ITEMS:
            break

    layout = str(payload.get("layout") or "").strip().lower()
    if layout not in GRID_LAYOUTS:
        layout = layout_for_count(item_count if item_count is not None else len(items))
    capacity = int(GRID_LAYOUTS[layout]["capacity"])
    if len(items) > capacity:
        items = items[:capacity]

    background = str(payload.get("background") or "white").strip().lower()
    if background not in {row["key"] for row in BACKGROUNDS}:
        background = "white"
    size = int(config["size"])
    gap = int(_clamp(payload.get("gap"), 0, 40, round(size / 135)))
    geometry = grid_geometry(max(1, len(items)), layout, size, gap)
    return {
        "version": 1,
        "platform": config["key"],
        "layout": layout,
        "background": background,
        "labels": payload.get("labels") is not False,
        "gap": gap,
        "width": geometry["width"],
        "height": geometry["height"],
        "cols": geometry["cols"],
        "rows": geometry["rows"],
        "items": items,
    }


def _load_items(db: Session, product_ids: Sequence[int]) -> Tuple[List[dict], List[int]]:
    """Картки товарів для сітки; рядки однієї ростовки зводяться в одну картку."""
    tg = _tg()
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    missing: List[int] = []
    for raw in list(product_ids)[:200]:
        try:
            product_id = int(raw)
        except (TypeError, ValueError):
            continue
        if product_id <= 0:
            continue
        bms = tg._load_product(db, product_id)
        if not bms:
            missing.append(product_id)
            continue
        number = str(bms.get("productnumber") or "")
        key = number.lstrip("#").casefold() or f"id:{product_id}"
        if key in grouped:
            continue
        photos, image_kind = tg._photo_entries(bms)
        sizes = tg._available_sizes(db, number)
        grouped[key] = {
            "product_id": product_id,
            "productnumber": number.lstrip("#"),
            "brand": bms.get("brandname"),
            "model": bms.get("model"),
            "type": bms.get("typename"),
            "price": tg._fmt_price(bms.get("price")),
            "sizes": [str(row.get("size") or "").strip() for row in sizes if row.get("size")],
            "image_kind": image_kind,
            "image_count": len(photos),
            "image_urls": [getattr(photo, "url", "") for photo in photos],
            "image_names": [getattr(photo, "filename", "") for photo in photos],
        }
    return list(grouped.values()), missing


def build_caption(items: Sequence[dict], platform: str) -> str:
    """Чернетка підпису: рядок на товар. Її завжди можна переписати вручну.

    Ліміт Viber — 768 символів, і 16 позицій із брендом, розмірами та ціною в
    нього не влазять. Тому підпис не ріжеться посеред слова: спершу зникають
    розміри, потім назви, і лише в найгіршому разі лишається рядок номерів.
    Так людина завжди отримує цілий, придатний до відправки текст.
    """
    config = platform_config(platform)
    bold = (lambda value: f"*{value}*") if config["markdown"] else (lambda value: value)
    limit = int(config["caption_limit"])
    header = bold("Свіжа підбірка 🔥")
    footer = "📲 Напишіть номер товару — і ми його відкладемо."

    def compose(*, with_title: bool, with_sizes: bool) -> str:
        lines = [header, ""]
        for position, item in enumerate(items, 1):
            parts = [f"{position}. #{item['productnumber']}"]
            title = " ".join(
                value for value in (item.get("brand"), item.get("model")) if value
            ).strip()
            if with_title and title:
                parts.append(title)
            if with_sizes and item.get("sizes"):
                parts.append(", ".join(item["sizes"][:6]))
            if item.get("price"):
                parts.append(f"{item['price']} грн")
            lines.append(" · ".join(parts))
        return "\n".join([*lines, "", footer]).strip()

    for with_title, with_sizes in ((True, True), (True, False), (False, False)):
        caption = compose(with_title=with_title, with_sizes=with_sizes)
        if len(caption) <= limit:
            return caption

    numbers = " · ".join(f"#{item['productnumber']}" for item in items)
    caption = "\n\n".join([header, numbers, footer]).strip()
    return caption if len(caption) <= limit else caption[: limit - 1].rstrip() + "…"


def preview_collection(db: Session, product_ids: Sequence[int], platform: str) -> dict:
    config = platform_config(platform)
    items, missing = _load_items(db, product_ids)
    usable = [item for item in items if item["image_count"] > 0]
    warnings: List[str] = []
    without_photo = [item["productnumber"] for item in items if not item["image_count"]]
    if without_photo:
        warnings.append(
            "Без фото, тому не потраплять у сітку: " + ", ".join(f"#{value}" for value in without_photo)
        )
    if len(usable) > MAX_ITEMS:
        warnings.append(f"У підбірку помістяться перші {MAX_ITEMS} товарів із {len(usable)}.")
        usable = usable[:MAX_ITEMS]
    if len(usable) < MIN_ITEMS:
        return {
            "ok": False,
            "error": f"Для підбірки потрібно щонайменше {MIN_ITEMS} товари з фото",
        }

    layout = layout_for_count(len(usable))
    spec = normalize_spec({
        "platform": config["key"],
        "layout": layout,
        "items": [{"product_id": item["product_id"]} for item in usable],
    })
    return {
        "ok": True,
        "platform": config["key"],
        "platform_label": config["label"],
        "items": usable,
        "missing_ids": missing,
        "spec": spec,
        "layouts": [{"key": key, **value} for key, value in GRID_LAYOUTS.items()],
        "backgrounds": BACKGROUNDS,
        "caption": build_caption(usable, config["key"]),
        "caption_limit": int(config["caption_limit"]),
        "max_items": MAX_ITEMS,
        "min_items": MIN_ITEMS,
        "canvas": {"width": spec["width"], "height": spec["height"]},
        "default_publish_at": _tg()._next_morning(8, 10).isoformat(),
        "warnings": warnings,
    }


def _photo_for_item(db: Session, product_id: int, image_idx: int) -> Tuple[bytes, dict]:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        raise ValueError(f"Товар {product_id} не знайдено")
    photos, _kind = tg._photo_entries(bms)
    if not photos:
        number = str(bms.get("productnumber") or product_id).lstrip("#")
        raise ValueError(f"У товару #{number} немає фото для підбірки")
    entry = photos[image_idx] if 0 <= image_idx < len(photos) else photos[0]
    raw = _read_image_bytes(entry)
    if not raw:
        number = str(bms.get("productnumber") or product_id).lstrip("#")
        raise ValueError(f"Не вдалося прочитати фото товару #{number}")
    return raw, bms


def _rebase_uniform_background(raw: bytes, target: Tuple[int, int, int]) -> bytes:
    """Замінює однорідний студійний фон фото на тло сітки.

    Офіційні фото зняті на білому. Без цього кроку на світлому/теплому/темному
    тлі кожен такий кадр лягає в сітку білим прямокутником — виглядає як
    зламаний рендер. Пікселі, близькі до фону фото, замінюються тлом полотна з
    мʼяким переходом на межі, щоб не з'явився ореол навколо товару.

    Живі фото (інтер'єр, руки, полиці) фон не мають — для них функція нічого не
    робить і повертає ті самі байти.
    """
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if image.width < 40 or image.height < 40:
        return raw

    px = image.load()
    edge_points: List[Tuple[int, int, int]] = []
    step_x = max(1, image.width // 40)
    step_y = max(1, image.height // 40)
    for x in range(0, image.width, step_x):
        edge_points.extend((px[x, 0], px[x, image.height - 1]))
    for y in range(0, image.height, step_y):
        edge_points.extend((px[0, y], px[image.width - 1, y]))
    channels = [sorted(point[channel] for point in edge_points) for channel in range(3)]
    detected = tuple(values[len(values) // 2] for values in channels)
    close = sum(
        1 for point in edge_points
        if max(abs(point[channel] - detected[channel]) for channel in range(3)) <= 16
    )
    if close / max(1, len(edge_points)) < 0.82:
        return raw
    if max(abs(detected[channel] - target[channel]) for channel in range(3)) <= 6:
        return raw

    difference = ImageChops.difference(image, Image.new("RGB", image.size, detected))
    strength = ImageChops.lighter(
        ImageChops.lighter(difference.getchannel("R"), difference.getchannel("G")),
        difference.getchannel("B"),
    )
    # 10…40 — смуга мʼякого переходу: нижче фон повністю замінюється, вище
    # лишається товар, між ними — плавна межа замість «вирізаного» контуру.
    mask = strength.point(lambda value: 0 if value <= 10 else (255 if value >= 40 else round((value - 10) * 255 / 30)))
    flattened = Image.composite(image, Image.new("RGB", image.size, target), mask)
    out = io.BytesIO()
    flattened.save(out, "JPEG", quality=95, subsampling="4:4:4")
    return out.getvalue()


def _label_font(size: int, *, bold: bool = False):
    """Та сама нейтральна типографіка, що й у Story-картці, — один голос."""
    try:
        from services import instagram_publisher
    except ImportError:
        from backend.services import instagram_publisher
    return instagram_publisher._story_display_font(size, bold=bold)


def _fmt_cm(value: Any) -> Optional[str]:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    text = f"{number:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def measurement_label(db: Session, bms: dict) -> Optional[str]:
    """Замір по устілці в сантиметрах — те, за чим реально обирають взуття.

    Ростовка — це кілька рядків products з одним номером, тому в сітці показуємо
    діапазон наявних замірів («23–26 см»), а не замір випадкового рядка. У сумок
    заміру ніжки немає, тож для них рядок порожній.
    """
    tg = _tg()
    if tg._is_bag(bms):
        return None
    number = str(bms.get("productnumber") or "")
    values = []
    for row in tg._available_sizes(db, number):
        formatted = _fmt_cm(row.get("measurementscm"))
        if formatted and formatted not in values:
            values.append(formatted)
    if not values:
        formatted = _fmt_cm(bms.get("measurementscm"))
        return f"{formatted} см" if formatted else None
    if len(values) == 1:
        return f"{values[0]} см"
    ordered = sorted(values, key=lambda item: float(item.replace(",", ".")))
    return f"{ordered[0]}–{ordered[-1]} см"


def _fit_font(draw: ImageDraw.ImageDraw, text: str, start: int, minimum: int,
              max_width: float, *, bold: bool):
    """Найбільший кегль, за якого рядок ще вкладається в комірку."""
    size = max(minimum, start)
    while size > minimum:
        font = _label_font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font, size
        size -= 1
    return _label_font(minimum, bold=bold), minimum


def _draw_pill(draw: ImageDraw.ImageDraw, box: Tuple[float, float, float, float],
               text: str, font, colors: dict) -> None:
    """Артикул у контурній капсулі — той самий елемент, що й у Story-картці."""
    left, top, right, bottom = box
    radius = (bottom - top) / 2
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=radius,
        fill=colors["pill_fill"], outline=colors["pill_outline"],
        width=2 if (bottom - top) >= 30 else 1,
    )
    draw.text(((left + right) / 2, (top + bottom) / 2), text, font=font,
              fill=colors["number"], anchor="mm")


def _draw_cell_label(canvas: Image.Image, box: Tuple[int, int, int, int], *,
                     number: str, measurement: Optional[str], price: Optional[str],
                     background_key: str) -> None:
    """Комерційний блок комірки мовою Story-картки.

    Ієрархія та сама, що вже затверджена в Stories: ціна — герой (велика,
    кольору сливи), артикул — у легкій контурній капсулі, замір — приглушена
    деталь. Рядок однакової ваги, який стояв тут раніше, читався як службовий
    підпис до фото, а не як вітрина.

    Розкладка адаптивна. У просторих сітках (2×2, 3×3) блок стає у два яруси:
    ціна зверху, капсула з артикулом і замір під нею. У щільній 4×4 два яруси
    зменшили б ціну до нечитабельного кегля, тому там збирається один
    компактний ряд — але тими самими кольорами й тією ж капсулою.
    """
    x, y, width, height = box
    draw = ImageDraw.Draw(canvas)
    colors = LABEL_COLORS.get(background_key, LABEL_COLORS["white"])
    price_text = f"{price} грн" if price else ""
    number_text = f"#{number}" if number else ""
    available = max(20, width - 10)
    if not price_text and not number_text and not measurement:
        return

    _price_font, price_size = _fit_font(
        draw, price_text or "0 грн", round(height * 0.42), 13, available, bold=True,
    )
    stacked = (price_size >= LABEL_PRICE_MIN_STACKED and bool(price_text)
               and bool(number_text or measurement))

    if stacked:
        # Блок вирівнюється від ВЕРХУ смуги, а не по її центру: увесь вільний
        # запас має лишитися знизу, інакше підпис зорово прилипає до фото
        # наступного ряду й читається як його заголовок.
        pad_top = max(5, round(height * 0.09))
        price_top = y + pad_top
        draw.text((x + width / 2, price_top), price_text,
                  font=_label_font(price_size, bold=True), fill=colors["price"], anchor="ma")

        detail_size = max(11, round(price_size * 0.46))
        detail_font = _label_font(detail_size)
        pill_height = round(detail_size * 1.85)
        pill_pad = round(detail_size * 0.72)
        number_width = draw.textlength(number_text, font=detail_font) if number_text else 0
        pill_width = number_width + pill_pad * 2 if number_text else 0
        gap = round(detail_size * 0.62) if (number_text and measurement) else 0
        measure_width = draw.textlength(measurement, font=detail_font) if measurement else 0
        total = pill_width + gap + measure_width
        cursor = x + (width - total) / 2
        detail_y = price_top + round(price_size * 1.16) + pill_height / 2
        if number_text:
            _draw_pill(
                draw,
                (cursor, detail_y - pill_height / 2,
                 cursor + pill_width, detail_y + pill_height / 2),
                number_text, detail_font, colors,
            )
            cursor += pill_width + gap
        if measurement:
            draw.text((cursor, detail_y), measurement, font=detail_font,
                      fill=colors["muted"], anchor="lm")
        return

    # Компактний ряд: капсула · замір · ціна — усе в одну лінію.
    base = max(11, round(height * 0.32))
    detail_font = _label_font(base)
    price_font = _label_font(round(base * 1.18), bold=True)
    pill_height = round(base * 1.8)
    pill_pad = round(base * 0.62)
    number_width = draw.textlength(number_text, font=detail_font) if number_text else 0
    pill_width = number_width + pill_pad * 2 if number_text else 0
    measure_width = draw.textlength(measurement, font=detail_font) if measurement else 0
    price_width = draw.textlength(price_text, font=price_font) if price_text else 0
    gap = round(base * 0.5)

    def _total(values):
        present = [value for value in values if value]
        return sum(present) + gap * max(0, len(present) - 1)

    total = _total((pill_width, measure_width, price_width))
    # Замір — перше, чим жертвуємо: артикул і ціна важливіші за деталь.
    if total > available and measurement:
        measurement, measure_width = None, 0
        total = _total((pill_width, price_width))

    cursor = x + (width - total) / 2
    middle = y + max(6, round(height * 0.12)) + pill_height / 2
    if number_text:
        _draw_pill(
            draw,
            (cursor, middle - pill_height / 2, cursor + pill_width, middle + pill_height / 2),
            number_text, detail_font, colors,
        )
        cursor += pill_width + gap
    if measurement:
        draw.text((cursor, middle), measurement, font=detail_font,
                  fill=colors["muted"], anchor="lm")
        cursor += measure_width + gap
    if price_text:
        draw.text((cursor, middle), price_text, font=price_font,
                  fill=colors["price"], anchor="lm")


def render(db: Session, payload: dict) -> dict:
    """Детермінований рендер сітки. Ті самі кроки, що й у Viber-картки."""
    viber = _viber()
    config = platform_config(payload.get("platform"))
    spec = normalize_spec(payload)
    items = spec["items"]
    if len(items) < MIN_ITEMS:
        raise ValueError(f"Для підбірки потрібно щонайменше {MIN_ITEMS} товари")

    background = viber._BACKGROUNDS[spec["background"]]
    canvas = Image.new("RGB", (spec["width"], spec["height"]), background)
    cells = grid_geometry(len(items), spec["layout"], spec["width"], spec["gap"])["cells"]
    numbers: List[str] = []
    product_ids: List[int] = []
    tg = _tg()
    for item, (x, y, width, height) in zip(items, cells):
        raw, bms = _photo_for_item(db, item["product_id"], item["image_idx"])
        number = str(bms.get("productnumber") or "").lstrip("#")
        numbers.append(number)
        product_ids.append(item["product_id"])
        band = 0
        if spec["labels"]:
            band = max(LABEL_BAND_MIN, round(height * LABEL_BAND_RATIO))
        photo_height = max(1, height - band)
        tile = viber._render_tile(
            _rebase_uniform_background(raw, background), (width, photo_height), item, background,
        )
        radius = max(8, min(24, min(width, photo_height) // 18))
        mask = Image.new("L", (width, photo_height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, photo_height), radius=radius, fill=255)
        canvas.paste(tile, (x, y), mask)
        if band:
            _draw_cell_label(
                canvas, (x, y + photo_height, width, band),
                number=number,
                measurement=measurement_label(db, bms),
                price=tg._fmt_price(bms.get("price")),
                background_key=spec["background"],
            )

    main = viber._jpeg_under_limit(canvas, int(config["max_bytes"]))
    thumb = b""
    if config["thumbnail"]:
        # Не `fit`: він обрізав би сітку до квадрата, і в мініатюрі Viber зникли
        # б крайні товари. Зменшуємо цілу сітку зі збереженням пропорцій.
        thumb_image = ImageOps.contain(
            canvas, (viber.THUMB_SIZE, viber.THUMB_SIZE), Image.Resampling.LANCZOS,
        )
        thumb = viber._jpeg_under_limit(thumb_image, viber.THUMB_MAX_BYTES, thumb=True)
    return {
        "main": main,
        "thumbnail": thumb,
        "spec": spec,
        "product_ids": product_ids,
        "product_numbers": numbers,
    }


def upload_derivatives(rendered: dict, caption: str) -> dict:
    """Публікаційна похідна лежить під content-addressed ключем, як і картки."""
    r2 = _r2()
    if not r2.is_enabled() or not r2.R2_PUBLIC_BASE_URL:
        raise RuntimeError("Публічний Cloudflare R2 не налаштований")
    platform = rendered["spec"]["platform"]
    digest = hashlib.sha256(rendered["main"] + caption.encode("utf-8")).hexdigest()[:24]
    base = f"social/{platform}/collections/{digest}"
    image_key = f"{base}.jpeg"
    r2.upload_bytes(rendered["main"], image_key, content_type="image/jpeg")
    image_url = r2.public_url(image_key)
    if not image_url:
        raise RuntimeError("R2 не повернув публічний URL підбірки")
    thumbnail_key = None
    thumbnail_url = None
    if rendered.get("thumbnail"):
        thumbnail_key = f"{base}.thumb.jpeg"
        r2.upload_bytes(rendered["thumbnail"], thumbnail_key, content_type="image/jpeg")
        thumbnail_url = r2.public_url(thumbnail_key)
    return {
        "digest": digest,
        "image_key": image_key,
        "image_url": image_url,
        "thumbnail_key": thumbnail_key,
        "thumbnail_url": thumbnail_url,
    }


def cached_post(db: Session, idempotency_key: str) -> Optional[dict]:
    row = db.execute(text("""
        SELECT id, platform, account_id, dispatcher_job_id, external_post_id,
               status, scheduled_at, published_at, image_url, error
        FROM social_collection_posts WHERE idempotency_key = :key
    """), {"key": idempotency_key}).mappings().first()
    if not row:
        return None
    value = dict(row)
    return {"ok": value["status"] not in ("failed", "error", "cancelled"), "cached": True, **value}


def record_post(db: Session, *, platform: str, idempotency_key: str, caption: str,
                rendered: dict, uploaded: dict, dispatch: dict,
                scheduled_at: Optional[datetime], account_id: Optional[str] = None,
                account_label: Optional[str] = None, request_payload: Optional[dict] = None) -> None:
    db.execute(text("""
        INSERT INTO social_collection_posts (
            platform, account_id, account_label, dispatcher_job_id, external_post_id,
            message_token, idempotency_key, status, caption, layout, item_count,
            image_key, image_url, thumbnail_key, thumbnail_url,
            product_ids, product_numbers, scheduled_at, published_at,
            payload_json, error, updated_at
        ) VALUES (
            :platform, :account, :account_label, :job, :post_id,
            :token, :idem, :status, :caption, :layout, :items,
            :ikey, :iurl, :tkey, :turl,
            CAST(:pids AS jsonb), CAST(:pnums AS jsonb), :scheduled, :published,
            CAST(:payload AS jsonb), :error, now()
        )
        ON CONFLICT (idempotency_key) DO UPDATE SET
            dispatcher_job_id = EXCLUDED.dispatcher_job_id,
            external_post_id = COALESCE(EXCLUDED.external_post_id, social_collection_posts.external_post_id),
            message_token = COALESCE(EXCLUDED.message_token, social_collection_posts.message_token),
            status = EXCLUDED.status,
            published_at = COALESCE(EXCLUDED.published_at, social_collection_posts.published_at),
            error = EXCLUDED.error,
            updated_at = now()
    """), {
        "platform": platform,
        "account": account_id,
        "account_label": account_label,
        "job": dispatch.get("job_id"),
        "post_id": dispatch.get("facebook_post_id") or dispatch.get("post_id"),
        "token": str(dispatch.get("message_token") or "") or None,
        "idem": idempotency_key,
        "status": dispatch.get("status") or ("scheduled" if scheduled_at else "queued"),
        "caption": caption,
        "layout": rendered["spec"]["layout"],
        "items": len(rendered["spec"]["items"]),
        "ikey": uploaded["image_key"],
        "iurl": uploaded["image_url"],
        "tkey": uploaded.get("thumbnail_key"),
        "turl": uploaded.get("thumbnail_url"),
        "pids": json.dumps(rendered["product_ids"]),
        "pnums": json.dumps(rendered["product_numbers"], ensure_ascii=False),
        "scheduled": scheduled_at,
        "published": datetime.now(timezone.utc) if dispatch.get("status") == "published" else None,
        "payload": json.dumps({
            **(request_payload or {}),
            "spec": rendered["spec"],
            "permalink": dispatch.get("permalink"),
        }, ensure_ascii=False),
        "error": dispatch.get("error"),
    })
    db.commit()


def history(db: Session, *, platform: Optional[str] = None, limit: int = 50) -> dict:
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 200))}
    clause = ""
    if platform:
        params["platform"] = platform_config(platform)["key"]
        clause = "WHERE platform = :platform"
    rows = db.execute(text(f"""
        SELECT id, platform, account_label, status, caption, layout, item_count,
               image_url, thumbnail_url, product_numbers, scheduled_at,
               published_at, error, created_at
        FROM social_collection_posts
        {clause}
        ORDER BY created_at DESC
        LIMIT :limit
    """), params).mappings().all()
    return {"ok": True, "posts": [dict(row) for row in rows]}


def pending_jobs(db: Session, platform: str) -> List[dict]:
    rows = db.execute(text("""
        SELECT id, dispatcher_job_id
        FROM social_collection_posts
        WHERE platform = :platform
          AND dispatcher_job_id IS NOT NULL
          AND status IN ('queued', 'scheduled', 'processing', 'retrying')
        ORDER BY updated_at
        LIMIT 100
    """), {"platform": platform_config(platform)["key"]}).mappings().all()
    return [dict(row) for row in rows]


def apply_job_status(db: Session, row_id: int, data: dict) -> None:
    db.execute(text("""
        UPDATE social_collection_posts
           SET status = :status,
               external_post_id = COALESCE(:post_id, external_post_id),
               message_token = COALESCE(:token, message_token),
               scheduled_at = COALESCE(CAST(:scheduled AS timestamptz), scheduled_at),
               published_at = COALESCE(CAST(:published AS timestamptz), published_at),
               error = :error,
               updated_at = now()
         WHERE id = :id
    """), {
        "id": row_id,
        "status": data.get("status") or "queued",
        "post_id": str(data.get("facebook_post_id") or data.get("post_id") or "") or None,
        "token": str(data.get("message_token") or "") or None,
        "scheduled": data.get("scheduled_at"),
        "published": data.get("published_at"),
        "error": data.get("error"),
    })

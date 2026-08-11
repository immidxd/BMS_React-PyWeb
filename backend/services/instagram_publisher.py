"""Безпечна підготовка Instagram-публікацій.

Поточний етап навмисно є preview/dry-run only: модуль не містить Meta access
token, не викликає Graph API і не має функції живої публікації. Так ми можемо
узгодити товарний шаблон, вибір до 10 медіа та пакетний контракт, не ризикуючи
акаунтом ``@brandxstoreua``.

Жива відправка з'явиться окремим етапом через ізольований Cloudflare dispatcher.
Його секрети не повинні потрапляти у frontend, PostgreSQL або Git.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session


CAPTION_LIMIT = 2200
HASHTAG_LIMIT = 30
MENTION_LIMIT = 20
MAX_MEDIA = 10
BATCH_MAX_PRODUCTS = int(os.getenv("INSTAGRAM_BATCH_MAX_PRODUCTS", "10"))
ORDER_PHONE = os.getenv("INSTAGRAM_ORDER_PHONE", "+380972337387").strip()
DELIVERY_LINE = os.getenv("INSTAGRAM_DELIVERY_LINE", "1–2 дні").strip()

FEED_PRESETS = {
    "portrait": {"label": "Вертикальний 4:5", "width": 1080, "height": 1350},
    "square": {"label": "Квадрат 1:1", "width": 1080, "height": 1080},
    "landscape": {"label": "Горизонтальний 1.91:1", "width": 1080, "height": 566},
}


def _tg():
    """Перевикористовує перевірену товарну логіку без Telegram-запитів."""
    try:
        from services import telegram_publisher
    except ImportError:
        from backend.services import telegram_publisher
    return telegram_publisher


def connection_status() -> dict:
    """Стан першого етапу без читання або повернення будь-яких секретів."""
    configured_parts = {
        "dispatcher_url": bool(os.getenv("INSTAGRAM_DISPATCHER_URL", "").strip()),
        "dispatcher_key": bool(os.getenv("INSTAGRAM_DISPATCHER_KEY", "").strip()),
        "instagram_account_id": bool(os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()),
    }
    missing = [name for name, present in configured_parts.items() if not present]
    return {
        "configured": False,
        "mode": "dry_run_only",
        "account": "@brandxstoreua",
        "professional_account_verified": True,
        "meta_business_suite_link_verified": True,
        "live_publish_available": False,
        "schedule_available": False,
        "missing": missing,
        "note": (
            "Instagram працює лише у preview/dry-run. Живий Graph API навмисно "
            "не підключений на цьому етапі."
        ),
        "limits": {
            "caption": CAPTION_LIMIT,
            "hashtags": HASHTAG_LIMIT,
            "mentions": MENTION_LIMIT,
            "carousel_media": MAX_MEDIA,
        },
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
    order_line = f"📲 Пиши #{product_number} в приватні"
    if ORDER_PHONE:
        order_line += f" 👉 {ORDER_PHONE}"
    protected.append(order_line)

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
        return caption

    suffix = "\n\n".join(protected)
    available = max(0, CAPTION_LIMIT - len(suffix) - 2)
    prefix = "\n\n".join([*leading, *([condition_line] if condition_line else [])])
    if len(prefix) > available:
        prefix = prefix[: max(0, available - 1)].rstrip() + ("…" if available else "")
    return "\n\n".join(value for value in (prefix, suffix) if value).strip()


def validate_caption(caption: str) -> Optional[str]:
    if not caption.strip():
        return "Підпис Instagram порожній"
    if len(caption) > CAPTION_LIMIT:
        return f"Підпис має {len(caption)} символів; ліміт Instagram — {CAPTION_LIMIT}"
    return None


def preview_post(db: Session, product_id: int) -> dict:
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}

    product_number = str(bms.get("productnumber") or "")
    sizes = tg._available_sizes(db, product_number)
    photos, image_kind = tg._photo_entries(bms)
    caption = build_caption(bms, sizes)
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
    warnings.append("Це лише preview/dry-run: жоден запит до Meta не виконується.")

    return {
        "ok": True,
        "mode": "dry_run_only",
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
        "sizes": sizes,
        "image_count": len(photos),
        "image_kind": image_kind,
        "image_urls": [getattr(photo, "url", "") for photo in photos],
        "image_names": [getattr(photo, "filename", "") for photo in photos],
        "default_image_idx": list(range(min(len(photos), MAX_MEDIA))),
        "carousel_limit": MAX_MEDIA,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "default_feed_preset": "portrait",
        "feed_presets": FEED_PRESETS,
        "connection": connection_status(),
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
        "mode": "dry_run_only",
        "selected_count": len(clean),
        "unique_count": len(items),
        "merged_count": max(0, len(clean) - len(items)),
        "missing_ids": missing,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "items": items,
    }


def dry_run(db: Session, product_id: int, payload: dict) -> dict:
    """Повна перевірка контракту без R2, Worker, D1 та Meta Graph API."""
    preview = preview_post(db, product_id)
    if not preview.get("ok"):
        return preview

    caption = str(payload.get("caption") or preview["caption"])
    caption_error = validate_caption(caption)
    if caption_error:
        return {"ok": False, "error": caption_error}

    raw_indexes = payload.get("image_idx", preview["default_image_idx"])
    if not isinstance(raw_indexes, list):
        return {"ok": False, "error": "image_idx має бути списком"}
    indexes: List[int] = []
    for raw in raw_indexes:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Некоректний індекс фото"}
        if index < 0 or index >= preview["image_count"]:
            return {"ok": False, "error": "Вибране фото відсутнє у товарі"}
        if index not in indexes:
            indexes.append(index)
    if not indexes:
        return {"ok": False, "error": "Для Instagram треба вибрати хоча б одне фото"}
    if len(indexes) > MAX_MEDIA:
        return {"ok": False, "error": f"У каруселі може бути до {MAX_MEDIA} фото"}

    preset = str(payload.get("feed_preset") or preview["default_feed_preset"])
    if preset not in FEED_PRESETS:
        return {"ok": False, "error": "Невідомий формат Instagram-поста"}

    return {
        "ok": True,
        "mode": "dry_run",
        "external_calls": 0,
        "product_id": product_id,
        "productnumber": preview["productnumber"],
        "media_count": len(indexes),
        "image_idx": indexes,
        "feed_preset": preset,
        "output": FEED_PRESETS[preset],
        "caption_len": len(caption),
        "would_publish_as": "image" if len(indexes) == 1 else "carousel",
        "note": "Перевірено локально. R2, Cloudflare і Meta не викликалися.",
    }

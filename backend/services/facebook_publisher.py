"""Facebook Page: чернетки й керування захищеною Cloudflare-чергою.

Рендерер повністю спільний з Instagram — ті самі JPEG 4:5/1:1/1.91:1, той самий
Story 9:16 з нанесеним текстом, той самий MP4 зі слайдів. Дублювати ~800 рядків
роботи з зображеннями заради іншого майданчика було б не «аналогічно», а просто
дві копії, які потім розʼїдуться. Тому цей модуль — тонкий шар над
``instagram_publisher`` плюс те, що у Facebook справді інше:

* ліміт тексту Сторінки 63 206, а не 2200;
* немає співавторів, alt text і товарних позначок цього API;
* публікація йде через Page access token і Pages API (див. воркер
  ``cloudflare/facebook-dispatcher``), а не через Instagram Login.

BMS так само не містить токена Meta й не викликає Graph API напряму.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session


# Реальний ліміт тексту допису Сторінки. BMS ніколи не підходить до нього
# близько, але валідувати треба за ним, а не за інстаграмівським 2200.
MESSAGE_LIMIT = 63_206
REEL_DESCRIPTION_LIMIT = 2_200
BATCH_MAX_PRODUCTS = int(os.getenv("FACEBOOK_BATCH_MAX_PRODUCTS", "10"))

PUBLISH_TYPES = {
    "feed": {"label": "Пост / альбом", "max_media": 10},
    "story": {"label": "Story", "max_media": 1},
    "reel": {"label": "Reel зі слайдів", "max_media": 10},
}


def _ig():
    """Спільний рендерер. Імпорт відкладений — модуль тягне Pillow і FFmpeg."""
    try:
        from services import instagram_publisher
    except ImportError:
        from backend.services import instagram_publisher
    return instagram_publisher


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


def _dispatcher_config() -> Tuple[str, str]:
    return (
        os.getenv("FACEBOOK_DISPATCHER_URL", "").strip().rstrip("/"),
        os.getenv("FACEBOOK_DISPATCHER_KEY", "").strip(),
    )


def page_label() -> str:
    return os.getenv("FACEBOOK_PAGE_NAME", "Сторінка Facebook").strip() or "Сторінка Facebook"


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
    ig = _ig()
    return {
        "configured": not missing,
        "mode": "production" if not missing else "draft_ready",
        "account": page_label(),
        "oauth_method": "facebook_login",
        "page_required": True,
        "dispatcher_configured": bool(dispatcher_url and dispatcher_key),
        "r2_configured": configured_parts["public_r2"],
        "live_publish_available": not missing,
        "schedule_available": not missing,
        "missing": missing,
        "note": (
            "Редактор і повний renderer доступні. Facebook підключається через "
            "офіційний Facebook Login у Cloudflare Worker; публікує Page access token."
        ),
        "limits": {
            "caption": MESSAGE_LIMIT,
            "reel_description": REEL_DESCRIPTION_LIMIT,
            "album_media": PUBLISH_TYPES["feed"]["max_media"],
            "jpeg_bytes": ig.JPEG_MAX_BYTES,
            "reel_bytes": ig.REEL_MAX_BYTES,
        },
        "publish_types": PUBLISH_TYPES,
        "pages": [],
    }


def build_caption(bms: dict, sizes, *, features=None) -> str:
    """Той самий товарний підпис, що й в Instagram — одна крамниця, один голос."""
    return _ig().build_caption(bms, sizes, features=features)


def build_story_text(bms: dict, sizes) -> str:
    return _ig().build_story_text(bms, sizes)


def validate_caption(caption: str, publish_type: str = "feed") -> Optional[str]:
    if publish_type == "story":
        return None
    if not caption.strip():
        return "Текст допису Facebook порожній"
    limit = REEL_DESCRIPTION_LIMIT if publish_type == "reel" else MESSAGE_LIMIT
    if len(caption) > limit:
        return f"Текст має {len(caption)} символів; ліміт Facebook — {limit}"
    return None


def normalize_media_spec(payload: dict, image_count: int) -> dict:
    """Той самий нормалізатор кадрів, але з лімітами майданчика Facebook."""
    ig = _ig()
    publish_type = str(payload.get("publish_type") or "feed").strip().lower()
    if publish_type not in PUBLISH_TYPES:
        publish_type = "feed"
    limited = dict(payload)
    limited["publish_type"] = publish_type
    spec = ig.normalize_media_spec(limited, image_count)
    max_media = int(PUBLISH_TYPES[publish_type]["max_media"])
    if len(spec["image_idx"]) > max_media:
        spec["image_idx"] = spec["image_idx"][:max_media]
        keep = set(spec["image_idx"])
        spec["frames"] = [frame for frame in spec["frames"] if frame["image_idx"] in keep]
    return spec


def render_media_for_product(db: Session, product_id: int, payload: dict) -> dict:
    return _ig().render_media_for_product(db, product_id, payload)


def render_preview_jpeg(db: Session, product_id: int, payload: dict) -> bytes:
    return _ig().render_preview_jpeg(db, product_id, payload)


def preview_post(db: Session, product_id: int) -> dict:
    """Чернетка Facebook: ті самі дані товару, але тексти й ліміти майданчика."""
    ig = _ig()
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}

    product_number = str(bms.get("productnumber") or "")
    sizes = tg._available_sizes(db, product_number)
    photos, image_kind = tg._photo_entries(bms)
    feed_zoom_defaults, feed_edge_adjusted = ig._feed_zoom_defaults(photos)
    caption = build_caption(bms, sizes)
    story_text = build_story_text(bms, sizes)
    max_album = int(PUBLISH_TYPES["feed"]["max_media"])

    warnings: List[str] = []
    if not photos:
        warnings.append("У товару немає фото — допис Facebook неможливо підготувати.")
    if len(photos) > max_album:
        warnings.append(f"Альбом вміщує до {max_album} фото; зайві треба прибрати у редакторі.")
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
        "caption_limit": MESSAGE_LIMIT,
        "reel_description_limit": REEL_DESCRIPTION_LIMIT,
        "story_text": story_text,
        "story_text_limit": ig.STORY_TEXT_LIMIT,
        "sizes": sizes,
        "image_count": len(photos),
        "image_kind": image_kind,
        "image_urls": [getattr(photo, "url", "") for photo in photos],
        "image_names": [getattr(photo, "filename", "") for photo in photos],
        "default_image_idx": list(range(min(len(photos), max_album))),
        "carousel_limit": max_album,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "default_feed_preset": "square",
        "feed_presets": ig.FEED_PRESETS,
        "feed_zoom_defaults": feed_zoom_defaults,
        "feed_edge_adjusted": feed_edge_adjusted,
        "story_preset": ig.STORY_PRESET,
        "publish_types": PUBLISH_TYPES,
        "media_spec": media_spec,
        "default_publish_at": ig._next_morning(),
        "connection": status,
        "warnings": warnings,
    }


def preview_posts_batch(db: Session, product_ids: List[int]) -> dict:
    """Пакет унікальних товарів: рядки однієї ростовки зводяться в одну картку."""
    tg = _tg()
    clean: List[int] = []
    for raw in product_ids[:200]:
        try:
            product_id = int(raw)
        except (TypeError, ValueError):
            continue
        if product_id > 0 and product_id not in clean:
            clean.append(product_id)

    from collections import OrderedDict
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
                f"Перший безпечний пакет Facebook — до {BATCH_MAX_PRODUCTS} "
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
    ig = _ig()
    preview = preview_post(db, product_id)
    if not preview.get("ok"):
        return preview

    publish_type = str(payload.get("publish_type") or "feed").strip().lower()
    if publish_type not in PUBLISH_TYPES:
        publish_type = "feed"
    caption = str(payload.get("caption") or preview["caption"])
    caption_error = validate_caption(caption, publish_type)
    if caption_error:
        return {"ok": False, "error": caption_error}

    scheduled_at, schedule_error = ig._validate_schedule(payload.get("publish_at"))
    if schedule_error:
        return {"ok": False, "error": schedule_error.replace("Instagram", "Facebook")}
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
            else "photo" if len(assets) == 1 else "album"
        ),
        "note": "Перевірено локально. R2, Cloudflare і Meta не викликалися.",
    }


def dry_run_batch(db: Session, raw_items: List[dict]) -> dict:
    """Перевіряє стабільний пакет чернеток без зовнішніх викликів і записів."""
    if not isinstance(raw_items, list) or not raw_items:
        return {"ok": False, "error": "Не вибрано Facebook-чернетки"}

    product_ids: List[int] = []
    drafts_by_id: Dict[int, dict] = {}
    for raw in raw_items[:200]:
        if not isinstance(raw, dict):
            return {"ok": False, "error": "Некоректна Facebook-чернетка"}
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


def _prepare(db: Session, product_id: int, payload: dict) -> dict:
    ig = _ig()
    tg = _tg()
    bms = tg._load_product(db, product_id)
    if not bms:
        raise ValueError("Товар не знайдено")
    publish_type = str(payload.get("publish_type") or "feed").strip().lower()
    if publish_type not in PUBLISH_TYPES:
        publish_type = "feed"
    caption = str(payload.get("caption") or build_caption(
        bms, tg._available_sizes(db, str(bms.get("productnumber") or "")),
    )).strip()
    problem = validate_caption(caption, publish_type)
    if problem:
        raise ValueError(problem)
    if (tg._condition_requires_confirmation(bms)
            and payload.get("condition_confirmed") is not True):
        raise ValueError(
            f"Стан «{tg._cap(bms.get('conditionname'))}» потребує явного підтвердження"
        )
    scheduled_at, schedule_error = ig._validate_schedule(payload.get("publish_at"))
    if schedule_error:
        raise ValueError(schedule_error.replace("Instagram", "Facebook"))
    rendered = render_media_for_product(db, product_id, payload)
    return {
        "bms": bms,
        "pnum": str(bms.get("productnumber") or ""),
        "caption": caption,
        "scheduled_at": scheduled_at,
        "rendered": rendered,
    }


def _upload_derivatives(prepared: dict) -> dict:
    """Похідні для Facebook лежать окремою гілкою R2.

    Спокуса перевикористати вже залиті інстаграмівські файли є, але Meta тягне
    медіа за URL у момент публікації: спільний ключ означав би, що зміна
    кадрування для одного майданчика мовчки підмінює медіа другого.
    """
    r2 = _r2()
    if not r2.is_enabled() or not r2.R2_PUBLIC_BASE_URL:
        raise RuntimeError("Публічний Cloudflare R2 не налаштований")
    rendered = prepared["rendered"]
    digest_source = prepared["caption"].encode("utf-8")
    for asset in rendered["assets"]:
        digest_source += asset["bytes"]
    digest = hashlib.sha256(digest_source).hexdigest()[:32]
    safe_number = prepared["pnum"].lstrip("#").replace("/", "-") or "product"
    base = f"social/facebook/{safe_number}/{digest}"
    media = []
    keys = []
    for index, asset in enumerate(rendered["assets"], 1):
        extension = asset["extension"]
        key = f"{base}/{index:02d}.{extension}"
        r2.upload_bytes(asset["bytes"], key, content_type=asset["content_type"])
        url = r2.public_url(key)
        if not url:
            raise RuntimeError("R2 не повернув публічний Facebook media URL")
        media.append({"type": asset["type"], "url": url})
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
        raise RuntimeError("Facebook-диспетчер ще не підключений")
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
        raise RuntimeError(data.get("error") or f"Facebook Worker повернув HTTP {response.status_code}")
    return data


async def dispatcher_status() -> dict:
    status = connection_status()
    if not status["dispatcher_configured"]:
        return status
    try:
        remote = await _dispatcher_request("GET", "/v1/status")
        accounts = remote.get("accounts") or []
        oauth_connected = bool(accounts)
        live_available = bool(status["configured"] and oauth_connected and remote.get("live_publish_enabled"))
        pages = [
            {"id": str(row.get("page_id") or ""), "name": str(row.get("page_name") or "").strip()}
            for row in accounts if row.get("page_id")
        ]
        return {
            **status,
            "configured": live_available,
            "account": ", ".join(page["name"] for page in pages) or status["account"],
            "pages": pages,
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
    """Read-only перевірка зашифрованого Page token і самої Сторінки."""
    return await _dispatcher_request("GET", "/v1/account-check")


def _cached_result(db: Session, key: str) -> Optional[dict]:
    row = db.execute(text("""
        SELECT product_id, product_number, dispatcher_job_id, facebook_post_id,
               status, scheduled_at, published_at, error
        FROM facebook_publications WHERE idempotency_key = :key
    """), {"key": key}).mappings().first()
    return {
        "ok": row["status"] not in ("failed", "error", "cancelled"),
        "cached": True,
        **dict(row),
    } if row else None


def _record(db: Session, *, product_id: int, prepared: dict, uploaded: dict,
            dispatch: dict, idempotency_key: str, request_payload: dict) -> None:
    db.execute(text("""
        INSERT INTO facebook_publications (
            product_id, product_number, facebook_page_id, facebook_post_id,
            dispatcher_job_id, idempotency_key, status, media_type,
            caption, media_urls, scheduled_at, published_at, payload_json,
            error, updated_at
        ) VALUES (
            :pid, :pnum, :page, :post_id, :job, :idem, :status, :type,
            :caption, CAST(:media AS jsonb), :scheduled, :published,
            CAST(:payload AS jsonb), :error, now()
        )
        ON CONFLICT (idempotency_key) DO UPDATE SET
            dispatcher_job_id = EXCLUDED.dispatcher_job_id,
            facebook_post_id = COALESCE(EXCLUDED.facebook_post_id, facebook_publications.facebook_post_id),
            status = EXCLUDED.status,
            published_at = COALESCE(EXCLUDED.published_at, facebook_publications.published_at),
            error = EXCLUDED.error,
            updated_at = now()
    """), {
        "pid": product_id,
        "pnum": prepared["pnum"],
        "page": dispatch.get("account_id"),
        "post_id": dispatch.get("facebook_post_id"),
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


def _target_pages(payload: dict, status: dict) -> List[dict]:
    """Куди саме публікуємо. Порожній вибір = всі підключені Сторінки.

    Явно вказані id звіряємо зі списком підключених: мовчки проковтнути
    невідомий id означало б «нічого не опубліковано», і людина дізналася б про
    це лише з порожньої стрічки.
    """
    available = status.get("pages") or []
    requested = payload.get("page_ids")
    if not isinstance(requested, list) or not requested:
        return available
    wanted = [str(value).strip() for value in requested if str(value).strip()]
    by_id = {page["id"]: page for page in available}
    unknown = [value for value in wanted if value not in by_id]
    if unknown:
        raise ValueError(f"Ці Сторінки не підключені: {', '.join(unknown)}")
    return [by_id[value] for value in wanted]


async def create_post(db: Session, product_id: int, payload: dict,
                      *, prepared: Optional[dict] = None) -> dict:
    """Одна чернетка → окремий job на КОЖНУ обрану Сторінку.

    Медіа рендериться й заливається один раз: Meta тягне його за URL, і той
    самий файл однаково придатний обом Сторінкам.
    """
    base_key = str(payload.get("idempotency_key") or uuid.uuid4())[:140]
    if payload.get("dry_run") is True:
        return dry_run(db, product_id, payload)
    status = await dispatcher_status()
    if not status.get("live_publish_available") or not status.get("oauth_connected"):
        missing = status.get("missing") or []
        detail = status.get("dispatcher_error") or ", ".join(missing) or "Сторінку не підключено"
        return {"ok": False, "error": f"Facebook ще не готовий до публікації: {detail}"}
    try:
        pages = _target_pages(payload, status)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not pages:
        return {"ok": False, "error": "Не обрано жодної Сторінки Facebook"}

    try:
        from starlette.concurrency import run_in_threadpool
        # _prepare = повний рендер (Pillow, для Reel ще й FFmpeg). На event loop
        # він морозив би весь бекенд, тому і він, і заливка йдуть у threadpool.
        ready = prepared or await run_in_threadpool(_prepare, db, product_id, payload)
        uploaded = await run_in_threadpool(_upload_derivatives, ready)
        spec = ready["rendered"]["spec"]
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc), "idempotency_key": base_key}

    results: List[dict] = []
    for page in pages:
        idempotency_key = f"{base_key}:{page['id']}"[:180]
        cached = _cached_result(db, idempotency_key)
        if cached:
            results.append({**cached, "page": page})
            continue
        request_payload = {
            "idempotency_key": idempotency_key,
            "account_id": page["id"],
            "product_id": product_id,
            "product_number": ready["pnum"].lstrip("#"),
            "publish_type": spec["publish_type"].upper(),
            "caption": "" if spec["publish_type"] == "story" else ready["caption"],
            "media": uploaded["media"],
            "publish_at": ready["scheduled_at"].isoformat() if ready["scheduled_at"] else None,
        }
        try:
            dispatched = await _dispatcher_request("POST", "/v1/jobs", payload=request_payload)
            _record(
                db, product_id=product_id, prepared=ready, uploaded=uploaded,
                dispatch={**dispatched, "account_id": page["id"]},
                idempotency_key=idempotency_key,
                request_payload={
                    **request_payload, "media_spec": spec,
                    "media_keys": uploaded["media_keys"], "page_name": page["name"],
                },
            )
            dispatch_status = str(dispatched.get("status") or "queued").lower()
            failed = dispatch_status in {"failed", "error", "cancelled"}
            results.append({
                "ok": not failed,
                "page": page,
                "idempotency_key": idempotency_key,
                "job_id": dispatched.get("job_id"),
                "status": dispatch_status,
                "error": dispatched.get("error") if failed else None,
            })
        except Exception as exc:
            db.rollback()
            results.append({
                "ok": False, "page": page, "idempotency_key": idempotency_key,
                "status": "error", "error": str(exc),
            })

    succeeded = [row for row in results if row.get("ok")]
    errors = [row for row in results if not row.get("ok")]
    return {
        "ok": bool(succeeded) and not errors,
        "product_id": product_id,
        "productnumber": ready["pnum"].lstrip("#"),
        "idempotency_key": base_key,
        "publish_type": spec["publish_type"],
        "scheduled_at": ready["scheduled_at"].isoformat() if ready["scheduled_at"] else None,
        "pages": [{"id": row["page"]["id"], "name": row["page"]["name"],
                   "ok": bool(row.get("ok")), "job_id": row.get("job_id"),
                   "status": row.get("status"), "error": row.get("error")}
                  for row in results],
        "status": succeeded[0].get("status") if succeeded else "error",
        "error": "; ".join(
            f"{row['page']['name']}: {row.get('error') or 'помилка'}" for row in errors
        ) or None,
    }


async def create_posts_batch(db: Session, items: Any, batch_id: Any,
                             *, dry_run_only: bool = False) -> dict:
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Пакет Facebook порожній"}
    if len(items) > BATCH_MAX_PRODUCTS:
        return {"ok": False, "error": f"Один пакет Facebook — до {BATCH_MAX_PRODUCTS} товарів"}
    batch = str(batch_id or "").strip()
    if not batch:
        return {"ok": False, "error": "Пакет не має batch_id"}
    if dry_run_only:
        return dry_run_batch(db, [dict(item.get("payload") or item, product_id=item.get("product_id")) for item in items])
    from starlette.concurrency import run_in_threadpool
    prepared_items: List[Tuple[int, dict, dict]] = []
    numbers = set()
    for position, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("product_id"):
            return {"ok": False, "error": f"Картка {position + 1} пошкоджена"}
        pid = int(item["product_id"])
        payload = dict(item.get("payload") or item)
        # Сторінку до ключа додає create_post — тут лишається база на товар.
        payload["idempotency_key"] = str(payload.get("idempotency_key") or f"{batch}:{pid}")[:140]
        try:
            ready = await run_in_threadpool(_prepare, db, pid, payload)
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
            "pages": result.get("pages") or [],
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
        SELECT id, dispatcher_job_id FROM facebook_publications
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
                UPDATE facebook_publications
                   SET status = :status,
                       facebook_page_id = COALESCE(:page, facebook_page_id),
                       facebook_post_id = COALESCE(:post_id, facebook_post_id),
                       scheduled_at = COALESCE(CAST(:scheduled AS timestamptz), scheduled_at),
                       published_at = COALESCE(CAST(:published AS timestamptz), published_at),
                       error = :error,
                       payload_json = payload_json || CAST(:extra AS jsonb),
                       updated_at = now()
                 WHERE id = :id
            """), {
                "id": row["id"], "status": data.get("status") or "queued",
                "page": data.get("account_id"), "post_id": data.get("facebook_post_id"),
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
        FROM facebook_publications
        WHERE id = :id
    """), {"id": int(publication_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Facebook-публікацію не знайдено"}
    if not row["dispatcher_job_id"]:
        return {"ok": False, "error": "Публікація не має job у диспетчері"}
    try:
        data = await _dispatcher_request("DELETE", f"/v1/jobs/{row['dispatcher_job_id']}")
        db.execute(text("""
            UPDATE facebook_publications
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
    ig = _ig()
    row = db.execute(text("""
        SELECT id, dispatcher_job_id, status
        FROM facebook_publications
        WHERE id = :id
    """), {"id": int(publication_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Facebook-публікацію не знайдено"}
    if not row["dispatcher_job_id"]:
        return {"ok": False, "error": "Публікація не має job у диспетчері"}
    try:
        scheduled_at, schedule_error = ig._validate_schedule(publish_at)
        if schedule_error or scheduled_at is None:
            raise ValueError((schedule_error or "Потрібна майбутня дата й час").replace("Instagram", "Facebook"))
        data = await _dispatcher_request(
            "PATCH", f"/v1/jobs/{row['dispatcher_job_id']}",
            payload={"publish_at": scheduled_at.isoformat()},
        )
        db.execute(text("""
            UPDATE facebook_publications
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
        SELECT id, status, media_type, dispatcher_job_id, facebook_post_id,
               scheduled_at, published_at, media_urls, error, payload_json, created_at
        FROM facebook_publications
        WHERE product_id = :pid
           OR product_number = (SELECT productnumber FROM products WHERE id = :pid)
        ORDER BY created_at DESC
    """), {"pid": product_id}).mappings().all()
    return {"product_id": product_id, "publications": [dict(row) for row in rows]}

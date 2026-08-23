"""Відправлення постів майстерні в соцмережі.

Пост майстерні — не публікація товару, тому весь облік тут власний
(`studio_publications`), а товарні таблиці й статуси лишаються недоторканими.

Три речі, які визначили будову цього модуля:

1. **Хмарні диспетчери приймають лише HTTPS-посилання на `.jpg`/`.jpeg`**
   (перевірено в коді воркерів Instagram, Facebook і Viber). Редактор же
   зберігає PNG — він точний і вміє прозорість. Тому перед відправленням
   робиться публікаційна похідна: PNG → JPEG у R2 під content-addressed
   ключем. Майстер-PNG лишається джерелом, JPEG — тим, що бачить мережа.
2. **Воркери вимагають додатного `product_id`** для власного журналу в D1.
   Товару в анонса немає, тож передаємо id самого поста, а в
   `product_number` — «STUDIO-<id>». Так рядок у журналі воркера читається
   однозначно й не вдає із себе товар.
3. **Telegram публікується локально** (Telethon, сесія на цій машині), а не
   через хмару. Тому саме для Telegram відправлення потребує запущеної BMS —
   на відміну від Instagram / Facebook / Viber, де достатньо віддати job.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

try:
    from services import studio
    from services import instagram_publisher as ig
    from services import facebook_publisher as fb
    from services import viber_publisher as vb
    from services import telegram_publisher as tg
    from services import r2_storage
except ImportError:  # запуск із кореня репо
    from backend.services import studio  # type: ignore
    from backend.services import instagram_publisher as ig  # type: ignore
    from backend.services import facebook_publisher as fb  # type: ignore
    from backend.services import viber_publisher as vb  # type: ignore
    from backend.services import telegram_publisher as tg  # type: ignore
    from backend.services import r2_storage  # type: ignore


R2_PUBLISH_PREFIX = "studio/publish"

# Стеля публікаційного JPEG. Instagram і Facebook тягнуть файл самі й на
# кількох мегабайтах не спотикаються, але тримати кадр легким варто: це
# швидша доставка й менший шанс тайм-ауту на боці Meta.
JPEG_MAX_BYTES = 3_500_000

PENDING_STATUSES = ("queued", "scheduled", "processing", "retrying")


class PublishError(RuntimeError):
    """Помилка, яку роут перетворює на людське пояснення, а не 500."""


# ── Публікаційна похідна ────────────────────────────────────────────────────

def _flatten_to_jpeg(raw: bytes, *, max_bytes: int, size: Optional[Tuple[int, int]] = None) -> bytes:
    """PNG із редактора → JPEG для мережі.

    Прозорість підкладається білим, а не лишається чорною: PNG майстерні може
    мати альфу (накладки), і JPEG без підкладки перетворив би її на чорні
    плями саме там, де в макеті було «нічого».
    """
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, (255, 255, 255))
            flat.paste(image, mask=image.split()[-1])
            image = flat
        else:
            image = image.convert("RGB")
        if size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        # Той самий підхід, що й у картках Viber: тиснемо якість, доки кадр не
        # влізе в ліміт, замість того щоб віддати мережі завеликий файл.
        for quality in (92, 88, 84, 80, 74, 68):
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True,
                       subsampling="4:2:0")
            value = buffer.getvalue()
            if len(value) <= max_bytes:
                return value
        # Якість вичерпано — далі зменшуємо саме полотно. Дрібнозернистий кадр
        # (фотофон на весь екран) може не влізти навіть на 68: краще віддати
        # мережі трохи менший кадр, ніж не віддати нічого.
        working = image
        for _ in range(4):
            working = working.resize(
                (max(1, int(working.width * 0.8)), max(1, int(working.height * 0.8))),
                Image.Resampling.LANCZOS,
            )
            buffer = io.BytesIO()
            working.save(buffer, "JPEG", quality=80, optimize=True, progressive=True,
                         subsampling="4:2:0")
            value = buffer.getvalue()
            if len(value) <= max_bytes:
                return value
    raise PublishError(f"Кадр не вкладається у {max_bytes // 1000} КБ")


def publication_derivative(post_id: int, canvas_format: str, raw: bytes,
                           *, with_thumbnail: bool = False) -> dict:
    """JPEG (і за потреби мініатюра) у R2 під ключем від вмісту.

    Content-addressed ключ дає дві речі одразу: незмінений макет не плодить
    копій у хмарі, а виправлений завжди отримує НОВУ адресу — повз будь-який
    кеш CDN і повз кеш ідемпотентності диспетчера.
    """
    if not r2_storage.is_enabled() or not r2_storage.R2_PUBLIC_BASE_URL:
        raise PublishError(
            "Публічний Cloudflare R2 не налаштований — мережі не зможуть "
            "забрати кадр за посиланням"
        )
    jpeg = _flatten_to_jpeg(raw, max_bytes=JPEG_MAX_BYTES)
    digest = hashlib.sha256(jpeg).hexdigest()[:24]
    base = f"{R2_PUBLISH_PREFIX}/{post_id}/{canvas_format}-{digest}"
    image_key = f"{base}.jpeg"
    r2_storage.upload_bytes(jpeg, image_key, content_type="image/jpeg")
    image_url = r2_storage.public_url(image_key)
    if not image_url:
        raise PublishError("R2 не повернув публічне посилання на кадр")

    result = {
        "image_key": image_key, "image_url": image_url,
        "bytes": len(jpeg), "digest": digest, "jpeg": jpeg,
    }
    if with_thumbnail:
        thumb = _flatten_to_jpeg(
            raw, max_bytes=vb.THUMB_MAX_BYTES, size=(vb.THUMB_SIZE, vb.THUMB_SIZE),
        )
        thumb_key = f"{base}-thumb.jpeg"
        r2_storage.upload_bytes(thumb, thumb_key, content_type="image/jpeg")
        result["thumbnail_key"] = thumb_key
        result["thumbnail_url"] = r2_storage.public_url(thumb_key)
    return result


# ── Готовність мереж ────────────────────────────────────────────────────────

async def readiness() -> dict:
    """Чи готова кожна мережа приймати пост — без жодної відправки.

    Питаємо ті самі перевірки, що й товарні публікації: одна крамниця — один
    стан підключення, а не друга думка майстерні про те саме.
    """
    platforms: Dict[str, dict] = {}

    try:
        ig_status = await ig.dispatcher_status()
        platforms["instagram"] = {
            "ready": bool(ig_status.get("live_publish_available") and ig_status.get("oauth_connected")),
            "detail": ig_status.get("dispatcher_error") or ", ".join(ig_status.get("missing") or []) or None,
            "account": ig_status.get("account"),
        }
    except Exception as exc:  # noqa: BLE001
        platforms["instagram"] = {"ready": False, "detail": str(exc)}

    try:
        fb_status = await fb.dispatcher_status()
        pages = fb_status.get("pages") or []
        platforms["facebook"] = {
            "ready": bool(fb_status.get("live_publish_available") and fb_status.get("oauth_connected") and pages),
            "detail": fb_status.get("dispatcher_error") or ", ".join(fb_status.get("missing") or []) or None,
            "pages": [{"id": page.get("id"), "name": page.get("name")} for page in pages],
        }
    except Exception as exc:  # noqa: BLE001
        platforms["facebook"] = {"ready": False, "detail": str(exc)}

    try:
        vb_status = vb.connection_status()
        platforms["viber"] = {
            "ready": bool(vb_status.get("configured")),
            "detail": ", ".join(vb_status.get("missing") or []) or None,
            "channel": getattr(vb, "CHANNEL_TITLE", None),
        }
    except Exception as exc:  # noqa: BLE001
        platforms["viber"] = {"ready": False, "detail": str(exc)}

    # Telegram не має хмарного контуру: публікує сама програма через свою
    # сесію. Тому «готовність» тут — це наявність налаштувань, а не стан
    # віддаленого воркера.
    import os
    tg_configured = all(os.getenv(name) for name in
                        ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE"))
    platforms["telegram"] = {
        "ready": tg_configured,
        "detail": None if tg_configured else "Немає TELEGRAM_API_ID / API_HASH / PHONE у .env",
        "channel": tg.CHANNEL_TITLE,
        "local_only": True,
    }
    return {"platforms": platforms}


# ── Облік ───────────────────────────────────────────────────────────────────

_PUBLICATION_SELECT = """
    SELECT id, post_id, platform, canvas_format, account_id, account_label,
           idempotency_key, dispatcher_job_id, external_post_id, post_url,
           status, caption, image_key, image_url, scheduled_at, published_at,
           error, created_at, updated_at
    FROM studio_publications
"""


def list_publications(db: Session, post_id: int) -> List[dict]:
    rows = db.execute(
        text(_PUBLICATION_SELECT + " WHERE post_id = :post_id ORDER BY created_at DESC"),
        {"post_id": post_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def _record(db: Session, *, post_id: int, platform: str, canvas_format: str,
            idempotency_key: str, caption: str, derivative: dict,
            status: str, scheduled_at: Optional[datetime],
            account_id: Optional[str] = None, account_label: Optional[str] = None,
            job_id: Optional[str] = None, external_post_id: Optional[str] = None,
            published_at: Optional[datetime] = None, error: Optional[str] = None,
            payload: Optional[dict] = None) -> None:
    db.execute(text("""
        INSERT INTO studio_publications(
            post_id, platform, canvas_format, account_id, account_label,
            idempotency_key, dispatcher_job_id, external_post_id, status,
            caption, image_key, image_url, scheduled_at, published_at,
            payload_json, error)
        VALUES (:post_id, :platform, :canvas_format, :account_id, :account_label,
                :key, :job_id, :external_post_id, :status, :caption, :image_key,
                :image_url, :scheduled_at, :published_at, CAST(:payload AS jsonb), :error)
        ON CONFLICT (idempotency_key) DO UPDATE SET
            status = EXCLUDED.status,
            dispatcher_job_id = COALESCE(EXCLUDED.dispatcher_job_id,
                                         studio_publications.dispatcher_job_id),
            external_post_id = COALESCE(EXCLUDED.external_post_id,
                                        studio_publications.external_post_id),
            published_at = COALESCE(EXCLUDED.published_at, studio_publications.published_at),
            error = EXCLUDED.error,
            updated_at = now()
    """), {
        "post_id": post_id, "platform": platform, "canvas_format": canvas_format,
        "account_id": account_id, "account_label": account_label,
        "key": idempotency_key, "job_id": job_id,
        "external_post_id": external_post_id, "status": status, "caption": caption,
        "image_key": derivative.get("image_key"), "image_url": derivative.get("image_url"),
        "scheduled_at": scheduled_at, "published_at": published_at,
        "payload": json.dumps(payload or {}), "error": error,
    })
    db.commit()


def _cached(db: Session, idempotency_key: str) -> Optional[dict]:
    row = db.execute(
        text(_PUBLICATION_SELECT + " WHERE idempotency_key = :key"),
        {"key": idempotency_key},
    ).mappings().first()
    return dict(row) if row else None


# ── Відправлення ────────────────────────────────────────────────────────────

def _publish_type(canvas_format: str) -> str:
    """Формат полотна → тип публікації мережі. Story лишається Story, решта —
    звичайний допис у стрічку."""
    return "STORY" if canvas_format == "story" else "FEED"


def _validate_caption(platform: str, caption: str, canvas_format: str) -> Optional[str]:
    if platform == "instagram":
        return ig.validate_caption(caption)
    if platform == "facebook":
        return fb.validate_caption(caption, "story" if canvas_format == "story" else "feed")
    if platform == "viber":
        return vb.validate_caption(caption)
    if platform == "telegram":
        return tg.validate_caption(caption)
    return None


def _key(post_id: int, platform: str, canvas_format: str, digest: str,
         suffix: str = "") -> str:
    base = f"studio:{post_id}:{platform}:{canvas_format}:{digest}"
    return (f"{base}:{suffix}" if suffix else base)[:180]


async def _publish_instagram(db: Session, post: dict, target: dict, derivative: dict,
                             caption: str, scheduled_at: Optional[datetime]) -> dict:
    canvas_format = target["format"]
    key = _key(post["id"], "instagram", canvas_format, derivative["digest"])
    cached = _cached(db, key)
    if cached and cached["status"] not in ("failed", "cancelled"):
        return {"ok": True, "platform": "instagram", "cached": True, **_public(cached)}

    payload = {
        "idempotency_key": key,
        # Воркер вимагає додатний product_id для свого журналу; товару тут
        # немає, тож підставляємо сам пост і кажемо це прямо в номері.
        "product_id": int(post["id"]),
        "product_number": f"STUDIO-{post['id']}",
        "publish_type": _publish_type(canvas_format),
        # Stories не мають підпису — Instagram його просто ігнорує.
        "caption": "" if canvas_format == "story" else caption,
        "media": [{"type": "IMAGE", "url": derivative["image_url"]}],
        "publish_at": scheduled_at.isoformat() if scheduled_at else None,
    }
    dispatched = await ig._dispatcher_request("POST", "/v1/jobs", payload=payload)
    status = str(dispatched.get("status") or "queued").lower()
    failed = status in {"failed", "error", "cancelled"}
    _record(
        db, post_id=post["id"], platform="instagram", canvas_format=canvas_format,
        idempotency_key=key, caption=payload["caption"], derivative=derivative,
        status="failed" if failed else status, scheduled_at=scheduled_at,
        account_id=dispatched.get("account_id"), account_label="@brandxstoreua",
        job_id=dispatched.get("job_id"),
        error=dispatched.get("error") if failed else None, payload=payload,
    )
    return {
        "ok": not failed, "platform": "instagram", "format": canvas_format,
        "job_id": dispatched.get("job_id"), "status": status,
        "error": dispatched.get("error") if failed else None,
    }


async def _publish_facebook(db: Session, post: dict, target: dict, derivative: dict,
                            caption: str, scheduled_at: Optional[datetime]) -> List[dict]:
    canvas_format = target["format"]
    status = await fb.dispatcher_status()
    try:
        pages = fb._target_pages(target.get("settings") or {}, status)
    except ValueError as exc:
        return [{"ok": False, "platform": "facebook", "format": canvas_format, "error": str(exc)}]
    if not pages:
        return [{"ok": False, "platform": "facebook", "format": canvas_format,
                 "error": "Не обрано жодної Сторінки Facebook"}]

    results: List[dict] = []
    # Кожна Сторінка — окремий job і окремий рядок обліку: у Meta це два різні
    # дописи, з різними лімітами й різними статусами.
    for page in pages:
        key = _key(post["id"], "facebook", canvas_format, derivative["digest"], page["id"])
        cached = _cached(db, key)
        if cached and cached["status"] not in ("failed", "cancelled"):
            results.append({"ok": True, "platform": "facebook", "cached": True,
                            "page": page["name"], **_public(cached)})
            continue
        payload = {
            "idempotency_key": key,
            "account_id": page["id"],
            "product_id": int(post["id"]),
            "product_number": f"STUDIO-{post['id']}",
            "publish_type": _publish_type(canvas_format),
            "caption": "" if canvas_format == "story" else caption,
            "media": [{"type": "IMAGE", "url": derivative["image_url"]}],
            "publish_at": scheduled_at.isoformat() if scheduled_at else None,
        }
        try:
            dispatched = await fb._dispatcher_request("POST", "/v1/jobs", payload=payload)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results.append({"ok": False, "platform": "facebook", "page": page["name"],
                            "format": canvas_format, "error": str(exc)})
            continue
        job_status = str(dispatched.get("status") or "queued").lower()
        failed = job_status in {"failed", "error", "cancelled"}
        _record(
            db, post_id=post["id"], platform="facebook", canvas_format=canvas_format,
            idempotency_key=key, caption=payload["caption"], derivative=derivative,
            status="failed" if failed else job_status, scheduled_at=scheduled_at,
            account_id=page["id"], account_label=page["name"],
            job_id=dispatched.get("job_id"),
            error=dispatched.get("error") if failed else None, payload=payload,
        )
        results.append({
            "ok": not failed, "platform": "facebook", "page": page["name"],
            "format": canvas_format, "job_id": dispatched.get("job_id"),
            "status": job_status, "error": dispatched.get("error") if failed else None,
        })
    return results


async def _publish_viber(db: Session, post: dict, target: dict, derivative: dict,
                         caption: str, scheduled_at: Optional[datetime]) -> dict:
    canvas_format = target["format"]
    key = _key(post["id"], "viber", canvas_format, derivative["digest"])
    cached = _cached(db, key)
    if cached and cached["status"] not in ("failed", "cancelled"):
        return {"ok": True, "platform": "viber", "cached": True, **_public(cached)}

    payload = {
        "idempotency_key": key,
        "product_id": int(post["id"]),
        "product_number": f"STUDIO-{post['id']}",
        "channel_title": vb.CHANNEL_TITLE,
        "type": "picture",
        "caption": caption,
        "media_url": derivative["image_url"],
        "thumbnail_url": derivative.get("thumbnail_url"),
        "publish_at": scheduled_at.isoformat() if scheduled_at else None,
    }
    dispatched = await vb._dispatch(payload)
    status = str(dispatched.get("status") or "queued").lower()
    _record(
        db, post_id=post["id"], platform="viber", canvas_format=canvas_format,
        idempotency_key=key, caption=caption, derivative=derivative,
        status=status, scheduled_at=scheduled_at, account_label=vb.CHANNEL_TITLE,
        job_id=dispatched.get("job_id"), payload=payload,
    )
    return {"ok": True, "platform": "viber", "format": canvas_format,
            "job_id": dispatched.get("job_id"), "status": status}


async def _publish_telegram(db: Session, post: dict, target: dict, derivative: dict,
                            caption: str, scheduled_at: Optional[datetime]) -> dict:
    """Telegram публікує сама програма — хмарного диспетчера тут немає.

    Наслідок, про який треба казати вголос: запланований пост у Telegram
    ставиться у ВЛАСНИЙ розклад Telegram (`schedule=`), тож він піде навіть із
    вимкненою BMS. А от саме натискання «Опублікувати» вимагає запущеної
    програми й вільної сесії.
    """
    canvas_format = target["format"]
    settings = target.get("settings") or {}
    key = _key(post["id"], "telegram", canvas_format, derivative["digest"])
    cached = _cached(db, key)
    if cached and cached["status"] not in ("failed", "cancelled"):
        return {"ok": True, "platform": "telegram", "cached": True, **_public(cached)}

    scanner, error = await tg._connect()
    if not scanner:
        return {"ok": False, "platform": "telegram", "format": canvas_format, "error": error}

    from telethon.tl.types import PeerChannel
    silent = bool(settings.get("silent"))
    caption_html = tg.md_to_html(caption) if caption else ""
    try:
        entity = await scanner.client.get_entity(PeerChannel(tg.CHANNEL_CHAT_ID))
        message = await scanner.client.send_file(
            entity, io.BytesIO(derivative["jpeg"]),
            caption=caption_html or None, parse_mode="html",
            silent=silent, schedule=scheduled_at,
            attributes=None, force_document=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "platform": "telegram", "format": canvas_format, "error": str(exc)}
    finally:
        try:
            await scanner.disconnect()
        except Exception:  # noqa: BLE001
            pass

    message_id = getattr(message, "id", None)
    published_at = None if scheduled_at else datetime.now(timezone.utc)
    _record(
        db, post_id=post["id"], platform="telegram", canvas_format=canvas_format,
        idempotency_key=key, caption=caption, derivative=derivative,
        status="scheduled" if scheduled_at else "published",
        scheduled_at=scheduled_at, published_at=published_at,
        account_label=tg.CHANNEL_TITLE, external_post_id=str(message_id or ""),
        payload={"silent": silent, "channel_chat_id": tg.CHANNEL_CHAT_ID},
    )
    return {
        "ok": True, "platform": "telegram", "format": canvas_format,
        "status": "scheduled" if scheduled_at else "published",
        "message_id": message_id,
    }


def _public(row: dict) -> dict:
    return {
        "format": row.get("canvas_format"), "status": row.get("status"),
        "job_id": row.get("dispatcher_job_id"), "error": row.get("error"),
    }


async def publish_post(db: Session, post_id: int, payload: Optional[dict] = None) -> dict:
    """Відправити пост у всі ввімкнені мережі.

    Порядок навмисний: спершу перевіряємо ВСЕ (кадри, підписи, розклад) і аж
    потім відправляємо хоч кудись. Інакше пост міг би піти в Instagram і
    впертись у помилку на Viber — а забрати назад уже опубліковане не можна.
    """
    payload = payload or {}
    post = studio.get_post(db, post_id)
    targets = [t for t in (post.get("targets") or []) if t.get("enabled", True)]
    if not targets:
        raise PublishError("Не обрано жодної мережі — нікуди публікувати")

    # Правила розкладу спільні з товарними публікаціями (не в минуле, не далі
    # ніж на рік) — беремо ту саму перевірку, лише прибираємо з тексту назву
    # мережі: пост майстерні йде не лише в Instagram.
    scheduled_at, schedule_error = ig._validate_schedule(
        payload.get("publish_at") if "publish_at" in payload else post.get("scheduled_at"))
    if schedule_error:
        raise PublishError(
            schedule_error
            .replace("Instagram-пост", "пост")
            .replace("Instagram-публікацію", "публікацію")
            .replace("Instagram-публікації", "публікації")
        )

    caption = str(post.get("caption") or "").strip()
    renders = post.get("renders") or {}

    # ── Перевірка перед першою відправкою ──────────────────────────────────
    problems: List[str] = []
    for target in targets:
        canvas_format = target["format"]
        entry = renders.get(canvas_format)
        if not isinstance(entry, dict) or not entry.get("key"):
            problems.append(
                f"{target['platform']}: кадр у форматі "
                f"«{studio.CANVAS_FORMATS.get(canvas_format, {}).get('label', canvas_format)}» "
                "ще не зібрано — відкрийте пост і натисніть «Зберегти й зібрати кадр»"
            )
            continue
        caption_problem = _validate_caption(target["platform"], caption, canvas_format)
        if caption_problem:
            problems.append(f"{target['platform']}: {caption_problem}")
    if problems:
        raise PublishError("; ".join(problems))

    dry_run = bool(payload.get("dry_run"))
    results: List[dict] = []

    for target in targets:
        platform = target["platform"]
        canvas_format = target["format"]
        raw = studio.object_bytes(renders[canvas_format]["key"])
        derivative = publication_derivative(
            post_id, canvas_format, raw, with_thumbnail=(platform == "viber"),
        )
        if dry_run:
            results.append({
                "ok": True, "dry_run": True, "platform": platform,
                "format": canvas_format, "image_url": derivative["image_url"],
                "image_bytes": derivative["bytes"],
                "caption_chars": 0 if canvas_format == "story" and platform in ("instagram", "facebook") else len(caption),
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            })
            continue

        try:
            if platform == "instagram":
                results.append(await _publish_instagram(db, post, target, derivative, caption, scheduled_at))
            elif platform == "facebook":
                results.extend(await _publish_facebook(db, post, target, derivative, caption, scheduled_at))
            elif platform == "viber":
                results.append(await _publish_viber(db, post, target, derivative, caption, scheduled_at))
            elif platform == "telegram":
                results.append(await _publish_telegram(db, post, target, derivative, caption, scheduled_at))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("studio: %s не прийняв пост %s", platform, post_id)
            results.append({"ok": False, "platform": platform, "format": canvas_format,
                            "error": str(exc)})

    succeeded = [row for row in results if row.get("ok")]
    failed = [row for row in results if not row.get("ok")]

    if not dry_run and succeeded:
        # Статус поста веде найдалі просунута відправка: якщо хоч щось уже
        # пішло, «чернеткою» він більше не є.
        any_published = any(row.get("status") == "published" for row in succeeded)
        studio.update_post(db, post_id, {
            "status": "published" if any_published else "scheduled",
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        })

    return {
        "ok": bool(succeeded) and not failed,
        "dry_run": dry_run,
        "post_id": post_id,
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "results": results,
        "error": "; ".join(
            f"{row.get('platform')}: {row.get('error') or 'помилка'}" for row in failed
        ) or None,
    }


# ── Звірка станів ───────────────────────────────────────────────────────────

async def sync_statuses(db: Session, *, post_id: Optional[int] = None) -> dict:
    """Підтягнути стани незавершених job із хмарних диспетчерів.

    Telegram сюди не потрапляє: він публікується локально й одразу знає свій
    результат — у нього немає «черги», яку треба перепитувати.
    """
    params: Dict[str, Any] = {}
    clause = ""
    if post_id is not None:
        clause = "AND post_id = :post_id"
        params["post_id"] = post_id
    # Список станів підставляємо явно, а не repr-ом кортежу: одна зміна складу
    # PENDING_STATUSES не має тихо перетворитись на некоректний SQL.
    pending = ", ".join(f"'{value}'" for value in PENDING_STATUSES)
    rows = db.execute(text(f"""
        SELECT id, platform, dispatcher_job_id
        FROM studio_publications
        WHERE dispatcher_job_id IS NOT NULL
          AND platform IN ('instagram', 'facebook', 'viber')
          AND status IN ({pending})
          {clause}
        ORDER BY updated_at
        LIMIT 100
    """), params).mappings().all()

    updated, errors = 0, []
    for row in rows:
        try:
            if row["platform"] == "instagram":
                data = await ig._dispatcher_request("GET", f"/v1/jobs/{row['dispatcher_job_id']}")
            elif row["platform"] == "facebook":
                data = await fb._dispatcher_request("GET", f"/v1/jobs/{row['dispatcher_job_id']}")
            else:
                import httpx
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        f"{vb.DISPATCHER_URL}/v1/jobs/{row['dispatcher_job_id']}",
                        headers={"Authorization": f"Bearer {vb.DISPATCHER_KEY}"},
                    )
                data = response.json()
                if response.status_code >= 400 or not data.get("ok"):
                    raise RuntimeError(data.get("error") or f"HTTP {response.status_code}")
            status = str(data.get("status") or "queued").lower()
            db.execute(text("""
                UPDATE studio_publications
                   SET status = :status,
                       external_post_id = COALESCE(:external, external_post_id),
                       post_url = COALESCE(:url, post_url),
                       published_at = COALESCE(CAST(:published AS timestamptz), published_at),
                       error = :error,
                       updated_at = now()
                 WHERE id = :id
            """), {
                "id": row["id"],
                "status": status if status in
                          (*PENDING_STATUSES, "published", "failed", "cancelled") else "queued",
                "external": data.get("external_post_id") or data.get("media_id")
                            or data.get("post_id") or data.get("message_token"),
                "url": data.get("permalink") or data.get("post_url"),
                "published": data.get("published_at"),
                "error": data.get("error"),
            })
            updated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"job_id": row["dispatcher_job_id"], "error": str(exc)})
    db.commit()
    return {"ok": not errors, "checked": len(rows), "updated": updated, "errors": errors}

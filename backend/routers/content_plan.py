"""Контент-план — слоти публікацій з Obsidian TaskNotes.

Роутер свідомо НЕ публікує сам: він володіє планом і добором товарів, а сама
відправка йде вже наявними ендпоїнтами каналів
(``/api/publications/<channel>/create-posts-batch``). Так логіка публікації
лишається в одному місці, а контент-план не дублює її втретє.

Потік: ``sync`` тягне слоти з Obsidian → ``suggest`` добирає товари →
користувач підтверджує → фронтенд публікує наявним ендпоїнтом →
``mark-published`` фіксує факт і дописує статус назад у нотатку.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import text

try:
    from models.database import get_db
    from services import content_plan as plan_service
    from services.product_images import list_images
    from routers.publications import _sold_units_join
except ImportError:  # запуск як пакет backend.*
    from backend.models.database import get_db
    from backend.services import content_plan as plan_service
    from backend.services.product_images import list_images
    from backend.routers.publications import _sold_units_join

logger = logging.getLogger(__name__)

router = APIRouter()

# Товар не годиться в слот, якщо в цьому ж каналі він уже висить або стоїть у черзі.
#
# ⚠️ Telegram звіряємо за НОМЕРОМ, а не за `tp.product_id = p.id`. Пост
# прив'язаний до одного рядка ростовки, а решта рядків того самого номера — то
# той самий товар у профілі. Перевірка за id пропускала вже опубліковані картки
# (спіймано на #Ф2924: 5 живих постів, яких фільтр за id не бачив).
_ALREADY_IN_CHANNEL = {
    "telegram": """NOT EXISTS (
        SELECT 1 FROM telegram_posts tp
        JOIN products tp_p ON tp_p.id = tp.product_id
        WHERE tp.tg_status = 'published'
          AND TRIM(LEADING '#' FROM BTRIM(tp_p.productnumber)) = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
    )""",
    "viber": """NOT EXISTS (
        SELECT 1 FROM viber_publications vp
        WHERE TRIM(LEADING '#' FROM BTRIM(vp.product_number)) = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
          AND vp.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
    )""",
    "instagram": """NOT EXISTS (
        SELECT 1 FROM instagram_publications ip
        WHERE TRIM(LEADING '#' FROM BTRIM(ip.product_number)) = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
          AND ip.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
    )""",
    "facebook": """NOT EXISTS (
        SELECT 1 FROM facebook_publications fp
        WHERE TRIM(LEADING '#' FROM BTRIM(fp.product_number)) = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
          AND fp.status IN ('queued', 'scheduled', 'processing', 'retrying', 'published')
    )""",
}

# Порядок добору залежить від рубрики слота: «топ» — дорожче спершу,
# решта — свіжіші завози спершу.
#
# ⚠️ Наявність фото тут НЕ перевіряється, хоч і вирішує все: `products.mainimage`
# порожній у всіх 12 081 рядків, а `product_images` — порожня таблиця. Єдине
# джерело правди про фото — файли на диску (+ дзеркало R2), тому кандидатів
# доводиться відсіювати вже в Python через `list_images()`.
_RUBRIC_ORDER = {
    "top": "p.price DESC NULLS LAST, p.dateadded DESC NULLS LAST",
    "new_arrivals": "p.dateadded DESC NULLS LAST",
    "digest": "p.dateadded DESC NULLS LAST",
    "general": "p.dateadded DESC NULLS LAST",
}

# Скільки кандидатів тягнути з БД на один потрібний товар. Перевірка фото
# коштує ~80 мс на номер без кешу, тому пул обмежений: беремо із запасом, але
# зупиняємось, щойно набрали потрібну кількість.
_CANDIDATE_MULTIPLIER = 4
_CANDIDATE_POOL_MIN = 20
_CANDIDATE_POOL_MAX = 60

# Той самий фізичний залишок, що у вкладках «Товари» й «Публікації».
_IN_STOCK = """(
    GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold_filter.sold_count, 0), 0) > 0
    AND (
        s.statusname IS NULL
        OR s.statusname NOT IN ('Продано', 'Подаровано', 'Повернуто')
        OR (
            s.statusname IN ('Продано', 'Подаровано')
            AND COALESCE(sold_filter.sold_count, 0) < COALESCE(NULLIF(p.quantity, 0), 1)
            AND EXISTS (SELECT 1 FROM order_items oi_uns WHERE oi_uns.product_id = p.id)
        )
    )
)"""


def _slot_row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "title": row["title"],
        "channel": row["channel"],
        "post_format": row["post_format"],
        "rubric": row["rubric"],
        "product_count": row["product_count"],
        "scheduled_at": row["scheduled_at"].isoformat() if row["scheduled_at"] else None,
        "plan_status": row["plan_status"],
        "slot_state": row["slot_state"],
        "product_numbers": row["product_numbers"] or [],
        "product_ids": row["product_ids"] or [],
        "suggested_numbers": row["suggested_numbers"] or [],
        "suggested_ids": row["suggested_ids"] or [],
        "post_url": row["post_url"],
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
    }


def _load_slot(db: Session, slot_id: int) -> Dict[str, Any]:
    row = db.execute(
        text("SELECT * FROM content_plan_slots WHERE id = :id"), {"id": slot_id}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Слот контент-плану не знайдено")
    return dict(row)


@router.get("/api/content-plan/status")
def content_plan_status(db: Session = Depends(get_db)):
    """Чи бачить BMS Obsidian і коли востаннє був імпорт."""
    connection = plan_service.check_connection()
    last_import = db.execute(
        text("SELECT MAX(imported_at) AS last_import FROM content_plan_slots")
    ).mappings().first()
    return {
        **connection,
        "last_import": last_import["last_import"].isoformat()
        if last_import and last_import["last_import"] else None,
    }


@router.post("/api/content-plan/sync")
def sync_content_plan(
    days_back: int = Query(7, ge=0, le=90),
    days_ahead: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Забрати слоти з Obsidian у власну таблицю BMS.

    Оновлюємо лише поля плану — товари й стан виконання, які вже підібрані в
    BMS, правка нотатки не затирає.
    """
    try:
        slots = plan_service.fetch_slots(days_back=days_back, days_ahead=days_ahead)
    except plan_service.TaskNotesUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    imported, updated = 0, 0
    for slot in slots:
        if _upsert_slot(db, slot):
            imported += 1
        else:
            updated += 1

    db.commit()
    return {"success": True, "imported": imported, "updated": updated, "total": len(slots)}


def _upsert_slot(db: Session, slot: Dict[str, Any]) -> bool:
    """Записати слот плану, не чіпаючи виконання. ``True`` — слот новий.

    Слот, уже опублікований у BMS, повторний імпорт не відкочує назад у 'new':
    план належить Obsidian, виконання — BMS.
    """
    result = db.execute(text("""
            INSERT INTO content_plan_slots
                (source, source_id, title, channel, post_format, rubric,
                 product_count, scheduled_at, plan_status, source_modified_at,
                 slot_state, imported_at, updated_at)
            VALUES
                ('tasknotes', :source_id, :title, :channel, :post_format, :rubric,
                 :product_count, :scheduled_at, :plan_status, :source_modified_at,
                 'new', now(), now())
            ON CONFLICT (source, source_id) DO UPDATE SET
                title = EXCLUDED.title,
                channel = EXCLUDED.channel,
                post_format = EXCLUDED.post_format,
                rubric = EXCLUDED.rubric,
                product_count = EXCLUDED.product_count,
                scheduled_at = EXCLUDED.scheduled_at,
                plan_status = EXCLUDED.plan_status,
                source_modified_at = EXCLUDED.source_modified_at,
                imported_at = now(),
                updated_at = now()
            RETURNING (xmax = 0) AS inserted
        """), {
            "source_id": slot["source_id"],
            "title": slot["title"],
            "channel": slot["channel"],
            "post_format": slot["post_format"],
            "rubric": slot["rubric"],
            "product_count": slot["product_count"],
            "scheduled_at": slot["scheduled_at"],
            "plan_status": slot["plan_status"],
            "source_modified_at": slot["source_modified_at"],
        }).mappings().first()
    return bool(result and result["inserted"])


@router.post("/api/content-plan/webhook")
async def content_plan_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Приймач вебхуків TaskNotes — зміна в Obsidian доходить за секунду.

    Без нього план оновлювався б лише кнопкою, і між правкою в Obsidian та
    публікацією з BMS існувало б вікно розсинхрону.

    Ендпоїнт ``async``, бо підпис треба звіряти із СИРИМ тілом, а ``request.body()``
    доступне лише в корутині. Робота з БД винесена у threadpool, щоб не блокувати
    event loop — решта роутера лишається синхронною.
    """
    raw_body = await request.body()
    secret = os.getenv("TASKNOTES_WEBHOOK_SECRET") or ""
    signature = request.headers.get("X-TaskNotes-Signature", "")
    if not plan_service.verify_webhook_signature(raw_body, signature, secret):
        raise HTTPException(status_code=401, detail="Невірний підпис вебхука")

    event = request.headers.get("X-TaskNotes-Event", "")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Тіло вебхука не є JSON")

    task = plan_service.extract_task_from_payload(payload)
    if not task:
        return {"success": True, "handled": False, "reason": "no_task_in_payload"}

    return await run_in_threadpool(_apply_webhook_task, db, event, task)


def _apply_webhook_task(db: Session, event: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Синхронна частина обробки вебхука — виконується у threadpool."""
    source_id = str(task.get("path") or "")
    if event.startswith("task.deleted") and source_id:
        # Видалену в Obsidian задачу не стираємо, якщо BMS уже опублікував пост:
        # історія виконання має пережити прибирання плану.
        db.execute(text("""
            DELETE FROM content_plan_slots
            WHERE source_id = :source_id AND slot_state <> 'published'
        """), {"source_id": source_id})
        db.commit()
        return {"success": True, "handled": True, "event": event, "action": "deleted"}

    slot = plan_service.slot_from_task(task)
    if not slot:
        # Канал прибрали з задачі — вона більше не публікація.
        if source_id:
            db.execute(text("""
                DELETE FROM content_plan_slots
                WHERE source_id = :source_id AND slot_state IN ('new', 'suggested')
            """), {"source_id": source_id})
            db.commit()
        return {"success": True, "handled": False, "reason": "not_a_publication_slot"}

    inserted = _upsert_slot(db, slot)
    db.commit()
    return {"success": True, "handled": True, "event": event,
            "action": "created" if inserted else "updated"}


@router.get("/api/content-plan/slots")
def list_slots(
    days_back: int = Query(7, ge=0, le=90),
    days_ahead: int = Query(30, ge=1, le=365),
    channel: str = Query("all", description="all|telegram|instagram|viber|facebook"),
    db: Session = Depends(get_db),
):
    """Слоти для календаря — з БД, тому працює й при закритому Obsidian."""
    channel = (channel or "all").strip().lower()
    if channel not in {"all", "telegram", "instagram", "viber", "facebook"}:
        raise HTTPException(status_code=400, detail="Невідомий майданчик публікації")

    params: Dict[str, Any] = {
        "start": datetime.now() - timedelta(days=days_back),
        "end": datetime.now() + timedelta(days=days_ahead),
    }
    channel_clause = ""
    if channel != "all":
        channel_clause = "AND channel = :channel"
        params["channel"] = channel

    rows = db.execute(text(f"""
        SELECT * FROM content_plan_slots
        WHERE scheduled_at BETWEEN :start AND :end
          {channel_clause}
        ORDER BY scheduled_at
    """), params).mappings().all()

    return {"slots": [_slot_row_to_dict(row) for row in rows]}


@router.post("/api/content-plan/slots/{slot_id}/suggest")
def suggest_products(slot_id: int, db: Session = Depends(get_db)):
    """Підібрати товари під слот — пропозиція, не публікація.

    Беремо лише те, що фізично в наявності і ще не висить/не стоїть у черзі в
    цьому ж каналі.
    """
    slot = _load_slot(db, slot_id)
    channel = slot["channel"]
    needed = slot["product_count"]
    order_by = _RUBRIC_ORDER.get(slot["rubric"] or "general", _RUBRIC_ORDER["general"])

    pool_size = max(_CANDIDATE_POOL_MIN,
                    min(_CANDIDATE_POOL_MAX, needed * _CANDIDATE_MULTIPLIER))
    candidates = db.execute(text(f"""
        SELECT p.id, p.productnumber, p.model, p.price, p.sizeeu
        FROM products p
        LEFT JOIN statuses s ON s.id = p.statusid
        {_sold_units_join("p.id")}
        WHERE {_IN_STOCK}
          AND {_ALREADY_IN_CHANNEL[channel]}
        ORDER BY {order_by}
        LIMIT :limit
    """), {"limit": pool_size}).mappings().all()

    # Два відсіювання поспіль:
    #   1. Ростовка — це ОДИН пост, а не по посту на розмір. Рядки з тим самим
    #      номером згортаємо в перший, інакше «топ-5» дасть 5 рядків, з яких
    #      дві пари — один і той самий товар у різних розмірах.
    #   2. Товар без фото в пост не годиться — краще пропустити, ніж показати
    #      користувачу порожню картку.
    rows = []
    seen_numbers = set()
    for candidate in candidates:
        if len(rows) >= needed:
            break
        number = str(candidate["productnumber"] or "").strip().lstrip("#").lower()
        if not number or number in seen_numbers:
            continue
        photos = list_images(candidate["productnumber"])
        if not photos:
            seen_numbers.add(number)
            continue
        seen_numbers.add(number)
        rows.append({**dict(candidate), "photo_count": len(photos)})

    suggested = [row["productnumber"] for row in rows]
    suggested_ids = [row["id"] for row in rows]
    db.execute(text("""
        UPDATE content_plan_slots
        SET suggested_numbers = CAST(:suggested AS jsonb),
            suggested_ids = CAST(:suggested_ids AS jsonb),
            slot_state = CASE WHEN slot_state = 'new' THEN 'suggested' ELSE slot_state END,
            updated_at = now()
        WHERE id = :id
    """), {"suggested": json.dumps(suggested),
           "suggested_ids": json.dumps(suggested_ids),
           "id": slot_id})
    db.commit()

    return {
        "slot_id": slot_id,
        "channel": channel,
        "requested": slot["product_count"],
        "suggested": [
            {
                "product_id": row["id"],
                "product_number": row["productnumber"],
                "model": row["model"],
                "price": float(row["price"]) if row["price"] is not None else None,
                "size": row["sizeeu"],
                "photo_count": row["photo_count"],
            }
            for row in rows
        ],
        "shortfall": max(0, needed - len(rows)),
        "pool_scanned": len(candidates),
    }


@router.put("/api/content-plan/slots/{slot_id}/products")
def set_slot_products(
    slot_id: int,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """Зафіксувати остаточний склад слота після перегляду користувачем.

    Приймаємо саме ``product_ids``: один ``productnumber`` може належати різним
    товарам, тому номер не є ключем. Номери для нотатки Obsidian дістаємо з БД
    за id, а не з тіла запиту — так вони гарантовано відповідають товарам.
    """
    _load_slot(db, slot_id)
    raw_ids = body.get("product_ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="Очікується список product_ids")

    product_ids: List[int] = []
    for value in raw_ids:
        try:
            product_ids.append(int(value))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Невірний id товару: {value!r}")

    numbers: List[str] = []
    if product_ids:
        rows = db.execute(
            text("SELECT id, productnumber FROM products WHERE id = ANY(:ids)"),
            {"ids": product_ids},
        ).mappings().all()
        found = {row["id"]: row["productnumber"] for row in rows}
        missing = [pid for pid in product_ids if pid not in found]
        if missing:
            raise HTTPException(status_code=404,
                                detail=f"Товари не знайдено: {missing}")
        # Порядок задає користувач, а не БД.
        numbers = [found[pid] for pid in product_ids]

    db.execute(text("""
        UPDATE content_plan_slots
        SET product_numbers = CAST(:numbers AS jsonb),
            product_ids = CAST(:ids AS jsonb),
            slot_state = CASE WHEN :has_items THEN 'confirmed' ELSE 'new' END,
            updated_at = now()
        WHERE id = :id
    """), {
        "numbers": json.dumps(numbers),
        "ids": json.dumps(product_ids),
        "has_items": bool(product_ids),
        "id": slot_id,
    })
    db.commit()
    return {"success": True, "slot_id": slot_id,
            "product_ids": product_ids, "product_numbers": numbers}


@router.post("/api/content-plan/slots/{slot_id}/mark-published")
def mark_slot_published(
    slot_id: int,
    body: Dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
):
    """Зафіксувати факт публікації і відзначити слот в Obsidian.

    Викликається фронтендом ПІСЛЯ успішної відправки наявним ендпоїнтом каналу.
    Недоступний Obsidian не є помилкою: пост уже вийшов, і відкочувати запис
    у BMS через закритий редактор плану не можна — просто повідомляємо.
    """
    slot = _load_slot(db, slot_id)
    post_url = body.get("post_url")
    publication_ref = body.get("publication_ref")
    numbers = body.get("product_numbers") or slot["product_numbers"] or []

    db.execute(text("""
        UPDATE content_plan_slots
        SET slot_state = 'published',
            product_numbers = CAST(:numbers AS jsonb),
            publication_ref = :publication_ref,
            post_url = :post_url,
            published_at = now(),
            updated_at = now()
        WHERE id = :id
    """), {
        "numbers": json.dumps(list(numbers)),
        "publication_ref": str(publication_ref) if publication_ref else None,
        "post_url": str(post_url) if post_url else None,
        "id": slot_id,
    })
    db.commit()

    synced = plan_service.push_slot_result(
        slot["source_id"],
        product_numbers=list(numbers),
        post_url=post_url,
        mark_done=True,
    )
    return {
        "success": True,
        "slot_id": slot_id,
        "obsidian_synced": synced,
        "message": None if synced else "Пост опубліковано, але Obsidian закритий — статус у нотатці оновиться після наступної синхронізації",
    }


@router.post("/api/content-plan/slots/{slot_id}/skip")
def skip_slot(slot_id: int, db: Session = Depends(get_db)):
    """Пропустити слот — щоб він не висів у черзі як прострочений."""
    _load_slot(db, slot_id)
    db.execute(text("""
        UPDATE content_plan_slots
        SET slot_state = 'skipped', updated_at = now()
        WHERE id = :id
    """), {"id": slot_id})
    db.commit()
    return {"success": True, "slot_id": slot_id}

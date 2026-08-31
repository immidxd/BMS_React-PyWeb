"""Відправлення затвердженої чернетки — один шлях для кнопки й для автоматики.

Раніше ця послідовність жила тільки в ендпоінтах затвердження, тож увімкнути
«без затвердження» означало б написати її вдруге. Дві копії одного порядку дій
розходяться першої ж правки, а ціна розходження тут — зайва або втрачена
публікація в живому каналі.

Порядок навмисний і однаковий для підбірок і для Stories:

1. склад перевіряється проти ЖИВОЇ бази (`revalidate_draft`);
2. медіа рендериться й іде в диспетчер;
3. чернетка стає `approved` ЛИШЕ після підтвердження диспетчера.

Якщо крок 2 упав, чернетка лишається на перевірці: повторна спроба безпечна
завдяки ключу ідемпотентності, а чернетка, помилково позначена відправленою,
не лікується взагалі.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PublishRefused(Exception):
    """Відправлення не відбулося з бізнес-причини, а не через збій."""

    def __init__(self, message: str, *, status: int = 409):
        super().__init__(message)
        self.status = status


def _mod(name: str):
    try:
        return __import__(f"services.{name}", fromlist=[name])
    except ImportError:
        return __import__(f"backend.services.{name}", fromlist=[name])


async def publish_collection_draft(
    db: Session, draft_id: int, *, body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Затвердити Top-9 чернетку й віддати банер диспетчеру майданчика."""
    body = body or {}
    scheduler = _mod("auto_collection_scheduler")
    draft = scheduler.load_draft(db, draft_id)
    if not draft:
        raise PublishRefused("Чернетку не знайдено", status=404)
    if draft.get("status") != scheduler.REVIEW_STATUS:
        raise PublishRefused("Цю чернетку вже опрацьовано")

    checked = scheduler.revalidate_draft(db, draft)
    if not checked["ok"]:
        raise PublishRefused(
            "Після перевірки лишилось замало доступних товарів: "
            + " ".join(checked["warnings"])
        )
    if body.get("dry_run"):
        return {"ok": True, "dry_run": True, "draft_id": draft_id, **checked}

    platform = str(draft.get("platform") or "")
    collection = _mod("collection_collage")
    preview = collection.preview_collection(db, checked["product_ids"], platform, ranked=True)
    if not preview.get("ok"):
        raise PublishRefused(preview.get("error", "Не вдалося зібрати банер"), status=400)

    request = {
        **preview["spec"],
        "caption": str(body.get("caption") or preview["caption"]),
        "publish_at": body.get("publish_at"),
        # Ключ прив'язаний до чернетки: повторна спроба поверне вже створене
        # завдання, а не опублікує банер удруге.
        "idempotency_key": f"auto-collection:{draft_id}:{draft.get('selection_key')}",
        **({"page_ids": body["page_ids"]} if body.get("page_ids") else {}),
    }
    publisher = _mod("viber_publisher") if platform == "viber" else _mod("facebook_publisher")
    result = await publisher.create_collection_post(db, request)
    if not result.get("ok"):
        raise PublishRefused(result.get("error", "Підбірку не відправлено"), status=400)

    scheduler.mark_approved(db, draft_id, dispatch=result, note=body.get("note"))
    result["draft_id"] = draft_id
    result["revalidation"] = {
        "warnings": checked["warnings"],
        "dropped": [row.get("productnumber") for row in checked["dropped"]],
        "promoted": [row.get("productnumber") for row in checked["promoted"]],
    }
    return result


async def publish_story_draft(
    db: Session, draft_id: int, *, body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Затвердити Story-чернетку й віддати кадр диспетчеру майданчика."""
    body = body or {}
    scheduler = _mod("story_automation_scheduler")
    draft = scheduler.load_draft(db, draft_id)
    if not draft:
        raise PublishRefused("Чернетку не знайдено", status=404)
    if draft.get("status") != scheduler.REVIEW_STATUS:
        raise PublishRefused("Цю чернетку вже опрацьовано")

    checked = scheduler.revalidate_draft(db, draft)
    if not checked["ok"]:
        raise PublishRefused(" ".join(checked["warnings"]))
    if body.get("dry_run"):
        return {"ok": True, "dry_run": True, "draft_id": draft_id, **checked}

    product = checked["product"]
    platform = str(draft.get("platform") or "")
    # Пакет розноситься в часі САМИМ ДИСПЕТЧЕРОМ: слот кожної Story вже
    # рознесений на кілька хвилин, і цей час передається як `publish_at`.
    # Інакше десять Stories пішли б одним залпом і вперлися б у ліміт
    # застосунку Meta — 18.08 так згоріло 42 завдання з 66.
    # Минулий час не передаємо: диспетчер відхилив би його, а людина, яка
    # затверджує вручну через годину, має отримати відправлення негайно.
    slot = draft.get("scheduled_for")
    paced = None
    if slot is not None:
        moment = slot if slot.tzinfo else slot.replace(tzinfo=timezone.utc)
        if moment > datetime.now(timezone.utc):
            paced = moment.isoformat()
    request = {
        "publish_type": "story",
        "image_idx": [0],
        "story_text": str(body.get("story_text") or draft.get("story_text") or ""),
        "publish_at": body.get("publish_at") or paced,
        "idempotency_key": f"auto-story:{draft_id}:{product['productnumber']}",
        **({"page_ids": body["page_ids"]} if body.get("page_ids") else {}),
    }
    publisher = _mod("instagram_publisher") if platform == "instagram" else _mod("facebook_publisher")
    result = await publisher.create_post(db, int(product["product_id"]), request)
    if not result.get("ok"):
        raise PublishRefused(result.get("error", "Story не відправлено"), status=400)

    scheduler.mark_approved(db, draft_id, dispatch=result, product=product,
                            note=body.get("note"))
    result["draft_id"] = draft_id
    result["revalidation"] = {
        "warnings": checked["warnings"],
        "productnumber": product["productnumber"],
        "replaced_from": checked["replaced_from"],
    }
    return result


# Таблиця чернеток кожного виду. Обидві мають (id, platform, status, scheduled_for),
# тож пошук спільний.
_DRAFT_TABLE = {
    "collection": "auto_collection_drafts",
    "story": "story_automation_drafts",
}

# Наскільки пізно після запланованого часу автоматика ще має право відправити.
# Вузько НАВМИСНО: сенс автопублікації — вийти у свій слот, а не наздоганяти
# тиждень потому. Що припізнилось — лишається людині.
DEFAULT_MAX_DRAFT_AGE_HOURS = 6

# Чернетки, які просто зараз відправляються. Цикл бігає кожні 5 хв, а рендер із
# диспетчером можуть тривати довше — без цього наступний оберт узяв би ту саму
# чернетку (вона ще `awaiting_review`) і пішов би на друге коло.
_in_flight: set = set()


def _due_drafts(db: Session, kind: str, platforms: list, max_age_hours: int) -> list:
    """Найсвіжіша прострочена чернетка КОЖНОГО майданчика, ще не переглянута.

    `DISTINCT ON (platform)` — свідоме обмеження «не більше однієї за оберт на
    майданчик»: якщо назбирався хвіст, автоматика візьме найновішу, а не
    вистрелить пачкою. У Facebook це особливо важливо — там на кожну Сторінку
    створюється ОКРЕМЕ завдання, тож пачка множиться на дві.
    """
    from sqlalchemy import text

    table = _DRAFT_TABLE[kind]
    scheduler = _mod("auto_collection_scheduler" if kind == "collection" else "story_automation_scheduler")
    rows = db.execute(text(f"""
        SELECT DISTINCT ON (platform) id, platform, scheduled_for
        FROM {table}
        WHERE status = :review
          AND platform = ANY(:platforms)
          AND scheduled_for <= now()
          AND scheduled_for >= now() - make_interval(hours => :max_age)
        ORDER BY platform, scheduled_for DESC
    """), {
        "review": scheduler.REVIEW_STATUS,
        "platforms": platforms,
        "max_age": max_age_hours,
    }).mappings().all()
    return [dict(r) for r in rows]


async def publish_due_drafts(db: Session, *, kind: str,
                             max_age_hours: Optional[int] = None) -> Dict[str, Any]:
    """Відправити прострочені чернетки майданчиків, де вимкнено ручну перевірку.

    ⚠️ Раніше сюди передавався список `created` — те, що ЩОЙНО створив локальний
    цикл. Через це автопублікація мовчки померла, коли зʼявився хмарний воркер:
    він вставляє чернетку першим, локальний `generate_due_drafts` через
    `ON CONFLICT (platform, scheduled_for) DO NOTHING` не створює нічого,
    `created` порожній — і крок відправлення виходив, не глянувши в базу.
    Тобто що надійніше працював воркер, то певніше не працювала автоматика
    (підбірка 30.08.2026 висіла на перевірці 5,5 години, поки її не затвердили
    руками). Тепер джерело правди — стан чернетки в базі, а не те, хто її створив.

    Побічно це дало повтор: якщо відправлення впало або BMS перезапустився між
    створенням і відправленням, наступний оберт спробує ще раз, поки чернетка в
    межах вікна. Раніше такий слот втрачався назавжди.

    Помилка одного майданчика не зупиняє другий: часткова невдача — штатний
    результат, а не привід залишити обидва канали без публікації.
    """
    import os

    if max_age_hours is None:
        max_age_hours = int(os.getenv("AUTO_PUBLISH_MAX_DRAFT_AGE_HOURS",
                                      str(DEFAULT_MAX_DRAFT_AGE_HOURS)))
    scheduler = _mod("auto_collection_scheduler" if kind == "collection" else "story_automation_scheduler")
    publish = publish_collection_draft if kind == "collection" else publish_story_draft
    auto = [
        str(row["platform"]) for row in scheduler.config_rows(db)
        if row.get("auto_publish")
    ]
    sent, refused, failed = [], [], []
    if not auto:
        return {"sent": sent, "refused": refused, "failed": failed}

    for draft in _due_drafts(db, kind, auto, max_age_hours):
        platform = str(draft.get("platform") or "")
        draft_id = draft.get("id")
        # Дубль умови із запиту, і це навмисно: «майданчик, де перевірку не
        # вимкнено, НЕ публікується сам» — інваріант, який має триматись навіть
        # якщо пошук колись перепишуть. Ціна помилки тут — публікація в живий
        # канал повз людину.
        if not draft_id or platform not in auto:
            continue
        key = (kind, int(draft_id))
        if key in _in_flight:
            continue
        _in_flight.add(key)
        try:
            result = await publish(db, int(draft_id), body={})
            sent.append({"platform": platform, "draft_id": draft_id,
                         "job_id": result.get("job_id"), "status": result.get("status")})
        except PublishRefused as exc:
            refused.append({"platform": platform, "draft_id": draft_id, "reason": str(exc)})
            logger.info("Auto-publish refused (%s #%s): %s", kind, draft_id, exc)
        except Exception as exc:  # noqa: BLE001 — канал не має падати через один майданчик
            failed.append({"platform": platform, "draft_id": draft_id, "error": str(exc)})
            logger.warning("Auto-publish failed (%s #%s): %s", kind, draft_id, exc)
        finally:
            # Обовʼязково у finally: інакше збій відправлення замкнув би чернетку
            # до перезапуску BMS, і повтор — сенс якого в цьому й полягає — не
            # відбувся б жодного разу.
            _in_flight.discard(key)
    return {"sent": sent, "refused": refused, "failed": failed}

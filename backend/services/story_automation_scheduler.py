"""Розклад регулярних Stories з обов'язковою межею ручної перевірки.

Сервіс може записати чернетку в PostgreSQL. Він не рендерить кадр, не заливає
медіа й не має жодної залежності від диспетчера, тож спрацювання розкладу
структурно не здатне перетворитись на публікацію.

Ритм тут інтервальний, а не тижневий: Stories живуть добою, і «щодня об 11:00»
описується природніше інтервалом у 24 години, ніж днем тижня. Слоти
відраховуються від першого `local_time` після ввімкнення, тож розклад
передбачуваний за стінним годинником, а не «через N годин від якоїсь події».
"""

from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from backend.services import story_automation
except ImportError:
    from services import story_automation

PLATFORMS = story_automation.PLATFORMS
REVIEW_STATUS = "awaiting_review"
MIN_INTERVAL_HOURS = 4
MAX_INTERVAL_HOURS = 168


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_local_time(value: Any) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    try:
        return time.fromisoformat(str(value or "").strip()).replace(second=0, microsecond=0)
    except ValueError as exc:
        raise ValueError("Час має бути у форматі ГГ:ХХ") from exc


def validate_config(payload: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = {**(current or {}), **payload}
    enabled = source.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Стан розкладу має бути увімкнено або вимкнено")
    auto_publish = source.get("auto_publish", False)
    if not isinstance(auto_publish, bool):
        raise ValueError("Режим публікації має бути увімкнено або вимкнено")
    interval_hours = int(source.get("interval_hours", story_automation.DEFAULT_INTERVAL_HOURS))
    if interval_hours < MIN_INTERVAL_HOURS or interval_hours > MAX_INTERVAL_HOURS:
        raise ValueError(f"Періодичність має бути від {MIN_INTERVAL_HOURS} до {MAX_INTERVAL_HOURS} годин")
    local_time = parse_local_time(source.get("local_time", story_automation.DEFAULT_LOCAL_TIME))
    timezone_name = str(source.get("timezone") or story_automation.DEFAULT_TIMEZONE).strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Невідомий часовий пояс") from exc
    cooldown_days = int(source.get("cooldown_days", story_automation.DEFAULT_COOLDOWN_DAYS))
    if cooldown_days < 7 or cooldown_days > 180:
        raise ValueError("Захист від повторів має бути від 7 до 180 днів")
    return {
        "enabled": enabled,
        "auto_publish": auto_publish,
        "interval_hours": interval_hours,
        "local_time": local_time,
        "timezone": timezone_name,
        "cooldown_days": cooldown_days,
        "filters": story_automation.normalize_filters(source.get("filters")),
    }


def series_start(enabled_at: datetime, local_time: time, timezone_name: str) -> datetime:
    """Перший слот: `local_time` того дня, коли ввімкнули, або наступного."""
    tz = ZoneInfo(timezone_name)
    local = enabled_at.astimezone(tz)
    candidate = datetime.combine(local.date(), local_time, tzinfo=tz)
    if candidate < local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _slot_index(start: datetime, interval_hours: int, moment: datetime) -> int:
    elapsed = (moment - start).total_seconds() / 3600.0
    return math.floor(elapsed / interval_hours)


def latest_slot(config: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """Останній слот, що вже настав; None — якщо перший ще попереду."""
    enabled_at = config.get("enabled_at")
    if not enabled_at:
        return None
    if enabled_at.tzinfo is None:
        enabled_at = enabled_at.replace(tzinfo=timezone.utc)
    start = series_start(enabled_at, parse_local_time(config["local_time"]), str(config["timezone"]))
    interval = int(config["interval_hours"])
    index = _slot_index(start, interval, now)
    if index < 0:
        return None
    return start + timedelta(hours=interval * index)


def next_slot(config: Dict[str, Any], now: datetime) -> Optional[datetime]:
    enabled_at = config.get("enabled_at")
    if not enabled_at:
        return None
    if enabled_at.tzinfo is None:
        enabled_at = enabled_at.replace(tzinfo=timezone.utc)
    start = series_start(enabled_at, parse_local_time(config["local_time"]), str(config["timezone"]))
    interval = int(config["interval_hours"])
    index = _slot_index(start, interval, now)
    return start + timedelta(hours=interval * (index + 1 if index >= 0 else 0))


def due_slot(config: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """Слот, який чекає на чернетку. Минуле до ввімкнення не надолужується."""
    if not config.get("enabled"):
        return None
    return latest_slot(config, now)


_CONFIG_SELECT = """
    SELECT platform, enabled, auto_publish, interval_hours, local_time, timezone,
           cooldown_days, filters_json, enabled_at, last_generated_at,
           last_error, last_error_at, updated_at
    FROM story_automation_configs
"""

_DRAFT_SELECT = """
    SELECT id, platform, source, status, scheduled_for, product_id, productnumber,
           story_text, reserves_json, warnings_json, policy_json, audit_json,
           reviewed_at, review_note, created_at, updated_at
    FROM story_automation_drafts
"""


def _serialize_config(row: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    value = dict(row)
    value["local_time"] = parse_local_time(value["local_time"]).strftime("%H:%M")
    value["filters"] = value.pop("filters_json") or {}
    value["next_run_at"] = next_slot(value, now or utc_now()) if value.get("enabled") else None
    value["manual_review_required"] = not bool(value.get("auto_publish"))
    return value


def _serialize_draft(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["reserves"] = item.pop("reserves_json") or []
    item["warnings"] = item.pop("warnings_json") or []
    item["policy"] = item.pop("policy_json") or {}
    item["audit"] = item.pop("audit_json") or {}
    return item


def config_rows(db: Session) -> List[Dict[str, Any]]:
    rows = db.execute(text(_CONFIG_SELECT + """
        ORDER BY CASE platform WHEN 'instagram' THEN 1 ELSE 2 END
    """)).mappings().all()
    return [dict(row) for row in rows]


def draft_rows(db: Session, limit: int = 20) -> List[Dict[str, Any]]:
    rows = db.execute(text(_DRAFT_SELECT + """
        ORDER BY created_at DESC LIMIT :limit
    """), {"limit": max(1, min(int(limit), 100))}).mappings().all()
    return [_serialize_draft(row) for row in rows]


def dashboard(db: Session, *, draft_limit: int = 20) -> Dict[str, Any]:
    now = utc_now()
    configs = []
    for row in config_rows(db):
        value = _serialize_config(row, now)
        value["filters_label"] = story_automation.describe_filters(db, value["filters"])
        configs.append(value)
    pending = int(db.execute(text("""
        SELECT COUNT(*) FROM story_automation_drafts WHERE status = :status
    """), {"status": REVIEW_STATUS}).scalar() or 0)
    return {
        "ok": True,
        "configs": configs,
        "drafts": draft_rows(db, draft_limit),
        "pending_count": pending,
        "safety": {
            "manual_review_default": True,
            "media_uploads": False,
        },
    }


def update_config(db: Session, platform: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError("Автоматичні Stories доступні лише для Instagram і Facebook")
    current = db.execute(text(_CONFIG_SELECT + " WHERE platform=:platform"),
                         {"platform": platform}).mappings().first()
    if not current:
        raise ValueError("Налаштування майданчика не знайдено")
    current_dict = dict(current)
    current_dict["filters"] = current_dict.pop("filters_json") or {}
    clean = validate_config(payload, current_dict)
    # Зміна ритму або добору починає серію заново: інакше «наступний запуск»
    # рахувався б від старого якоря й міг спрацювати одразу після збереження.
    restarted = any(
        clean[key] != current_dict.get(key)
        for key in ("interval_hours", "local_time", "timezone")
    ) or clean["filters"] != current_dict.get("filters")
    newly_enabled = clean["enabled"] and not bool(current_dict.get("enabled"))
    enabled_at = (
        utc_now() if newly_enabled or (clean["enabled"] and restarted)
        else current_dict.get("enabled_at") if clean["enabled"]
        else None
    )
    row = db.execute(text("""
        UPDATE story_automation_configs
           SET enabled=:enabled, auto_publish=:auto_publish,
               interval_hours=:interval_hours,
               local_time=CAST(:local_time AS time), timezone=:timezone,
               cooldown_days=:cooldown_days,
               filters_json=CAST(:filters AS jsonb),
               enabled_at=:enabled_at, last_error=NULL, last_error_at=NULL,
               updated_at=now()
         WHERE platform=:platform
     RETURNING platform, enabled, auto_publish, interval_hours, local_time, timezone,
               cooldown_days, filters_json, enabled_at, last_generated_at,
               last_error, last_error_at, updated_at
    """), {
        "platform": platform,
        "enabled": clean["enabled"],
        "auto_publish": clean["auto_publish"],
        "interval_hours": clean["interval_hours"],
        "local_time": clean["local_time"].strftime("%H:%M"),
        "timezone": clean["timezone"],
        "cooldown_days": clean["cooldown_days"],
        "filters": json.dumps(clean["filters"], ensure_ascii=False),
        "enabled_at": enabled_at,
    }).mappings().one()
    db.commit()
    value = _serialize_config(dict(row))
    value["filters_label"] = story_automation.describe_filters(db, value["filters"])
    return value


def _existing_draft(db: Session, platform: str, scheduled_for: datetime) -> Optional[Dict[str, Any]]:
    row = db.execute(text(_DRAFT_SELECT + """
        WHERE platform=:platform AND scheduled_for=:scheduled_for
    """), {"platform": platform, "scheduled_for": scheduled_for}).mappings().first()
    return _serialize_draft(row) if row else None


def _story_text(db: Session, product_id: int) -> str:
    try:
        from services import instagram_publisher as ig, telegram_publisher as tg
    except ImportError:
        from backend.services import instagram_publisher as ig, telegram_publisher as tg
    bms = tg._load_product(db, int(product_id))
    if not bms:
        return ""
    sizes = tg._available_sizes(db, str(bms.get("productnumber") or ""))
    return ig.build_story_text(bms, sizes)


def create_draft(
    db: Session,
    *,
    platform: str,
    source: str,
    scheduled_for: Optional[datetime] = None,
    config: Optional[Dict[str, Any]] = None,
    schedule_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError("Автоматичні Stories доступні лише для Instagram і Facebook")
    if source not in ("scheduled", "manual"):
        raise ValueError("Невідоме джерело чернетки")
    slot = scheduled_for or utc_now()
    if slot.tzinfo is None:
        slot = slot.replace(tzinfo=timezone.utc)
    existing = _existing_draft(db, platform, slot)
    if existing:
        return {"created": False, "draft": existing}

    fresh = db.execute(text(_CONFIG_SELECT + " WHERE platform=:platform FOR UPDATE"),
                       {"platform": platform}).mappings().first()
    if not fresh:
        raise ValueError("Налаштування майданчика не знайдено")
    config = dict(fresh)
    config["filters"] = config.pop("filters_json") or {}
    if source == "scheduled":
        # Перечитати конфіг під замком: людина могла вимкнути розклад або
        # змінити добір, поки тривав відбір. Її дія має виграти.
        existing = _existing_draft(db, platform, slot)
        if existing:
            db.rollback()
            return {"created": False, "draft": existing}
        if due_slot(config, schedule_now or utc_now()) != slot:
            db.rollback()
            return {"created": False, "draft": None, "reason": "schedule_changed_or_disabled"}

    picked = story_automation.select_for_slot(
        db,
        filters=config["filters"],
        cooldown_days=int(config["cooldown_days"]),
    )
    if not picked["ok"]:
        raise ValueError((picked["warnings"] or ["Під цей добір немає товарів"])[0])

    chosen = picked["selected"]
    policy = {
        "interval_hours": int(config["interval_hours"]),
        "cooldown_days": int(config["cooldown_days"]),
        "filters": config["filters"],
        "requires_available_stock": True,
        "requires_photo": True,
        "revalidate_before_publish": True,
        "auto_publish": bool(config.get("auto_publish")),
    }
    audit = {
        "eligible_pool": picked["eligible_pool"],
        "no_photo_skipped": picked["no_photo_skipped"],
        "generated_at": utc_now().isoformat(),
        "data_source": "live",
        "filters_label": story_automation.describe_filters(db, config["filters"]),
    }
    row = db.execute(text("""
        INSERT INTO story_automation_drafts (
            platform, source, status, scheduled_for, product_id, productnumber,
            story_text, reserves_json, warnings_json, policy_json, audit_json
        ) VALUES (
            :platform, :source, :status, :scheduled_for, :product_id, :productnumber,
            :story_text, CAST(:reserves AS jsonb), CAST(:warnings AS jsonb),
            CAST(:policy AS jsonb), CAST(:audit AS jsonb)
        )
        ON CONFLICT (platform, scheduled_for) DO NOTHING
        RETURNING id
    """), {
        "platform": platform,
        "source": source,
        "status": REVIEW_STATUS,
        "scheduled_for": slot,
        "product_id": int(chosen["product_id"]),
        "productnumber": str(chosen["productnumber"]),
        "story_text": _story_text(db, int(chosen["product_id"])),
        "reserves": json.dumps(picked["reserves"], ensure_ascii=False, default=str),
        "warnings": json.dumps(picked["warnings"], ensure_ascii=False),
        "policy": json.dumps(policy, ensure_ascii=False, default=str),
        "audit": json.dumps(audit, ensure_ascii=False, default=str),
    }).mappings().first()
    db.commit()
    return {"created": bool(row), "draft": _existing_draft(db, platform, slot)}


def generate_due_drafts(db: Session, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or utc_now()
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for config in config_rows(db):
        value = dict(config)
        value["filters"] = value.pop("filters_json") or {}
        slot = due_slot(value, current)
        if slot is None:
            skipped.append({"platform": value["platform"], "reason": "disabled_or_not_due"})
            continue
        try:
            result = create_draft(
                db, platform=value["platform"], source="scheduled",
                scheduled_for=slot, config=value, schedule_now=current,
            )
            if result["created"]:
                created.append(result["draft"])
                db.execute(text("""
                    UPDATE story_automation_configs
                       SET last_generated_at=now(), last_error=NULL,
                           last_error_at=NULL, updated_at=now()
                     WHERE platform=:platform
                """), {"platform": value["platform"]})
                db.commit()
            else:
                skipped.append({
                    "platform": value["platform"],
                    "reason": result.get("reason") or "already_created",
                })
        except Exception as exc:  # один майданчик не має блокувати інший
            db.rollback()
            message = str(exc)
            errors.append({"platform": value["platform"], "error": message})
            db.execute(text("""
                UPDATE story_automation_configs
                   SET last_error=:error, last_error_at=now(), updated_at=now()
                 WHERE platform=:platform
            """), {"platform": value["platform"], "error": message[:2000]})
            db.commit()
    return {"ok": not errors, "created": created, "skipped": skipped, "errors": errors}


def load_draft(db: Session, draft_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute(text(_DRAFT_SELECT + " WHERE id=:id"), {"id": int(draft_id)}).mappings().first()
    return _serialize_draft(row) if row else None


def revalidate_draft(db: Session, draft: Dict[str, Any]) -> Dict[str, Any]:
    """Свіжа перевірка товару безпосередньо перед відправленням.

    Між створенням чернетки і публікацією товар могли продати або показати
    вручну в іншій Story. Перевірка йде проти ЖИВОЇ бази; якщо основний товар
    випав, його місце займає перший придатний із запасу тієї ж чернетки — черга
    не має мовчки зупинятись через одну продану пару.
    """
    policy = draft.get("policy") or {}
    filters = policy.get("filters") or {}
    cooldown_days = int(policy.get("cooldown_days") or story_automation.DEFAULT_COOLDOWN_DAYS)
    fresh = story_automation.candidate_rows(
        db, filters, cooldown_days, pool=5000, exclude_draft_id=draft.get("id"),
    )
    eligible = {story_automation.normalize_number(row["productnumber"]): row for row in fresh}

    def pick(number: Any) -> Optional[Dict[str, Any]]:
        row = eligible.get(story_automation.normalize_number(number))
        return row if row and story_automation._photo_ready(row) else None

    warnings: List[str] = []
    chosen = pick(draft.get("productnumber"))
    replaced_from = None
    if chosen is None:
        warnings.append(f"#{str(draft.get('productnumber')).lstrip('#')} уже недоступний.")
        for reserve in draft.get("reserves") or []:
            chosen = pick(reserve.get("productnumber"))
            if chosen is not None:
                replaced_from = reserve.get("productnumber")
                warnings.append(f"Замість нього з резерву: #{str(replaced_from).lstrip('#')}.")
                break
    if chosen is None and not warnings:
        warnings.append("Товар більше не підходить під добір.")
    if chosen is None:
        warnings.append("Запас теж вичерпано — Story не відправлено.")

    return {
        "ok": chosen is not None,
        "product": chosen,
        "replaced_from": replaced_from,
        "warnings": warnings,
    }


def mark_approved(db: Session, draft_id: int, *, dispatch: Dict[str, Any],
                  product: Optional[Dict[str, Any]] = None,
                  note: Optional[str] = None) -> Dict[str, Any]:
    """Позначити чернетку відправленою. Тільки ПІСЛЯ успіху диспетчера."""
    row = db.execute(text("""
        UPDATE story_automation_drafts
           SET status='approved', reviewed_at=now(), review_note=:note,
               product_id=COALESCE(:product_id, product_id),
               productnumber=COALESCE(:productnumber, productnumber),
               audit_json = COALESCE(audit_json, '{}'::jsonb) || CAST(:dispatch AS jsonb),
               updated_at=now()
         WHERE id=:id AND status=:status
         RETURNING id
    """), {
        "id": int(draft_id),
        "status": REVIEW_STATUS,
        "note": str(note or "").strip()[:1000] or None,
        "product_id": int(product["product_id"]) if product else None,
        "productnumber": str(product["productnumber"]) if product else None,
        "dispatch": json.dumps({"dispatch": dispatch}, ensure_ascii=False, default=str),
    }).mappings().first()
    if not row:
        raise ValueError("Чернетку не знайдено або її вже опрацьовано")
    db.commit()
    return {"ok": True, "id": int(row["id"]), "status": "approved"}


def reject_draft(db: Session, draft_id: int, note: Optional[str] = None) -> Dict[str, Any]:
    row = db.execute(text("""
        UPDATE story_automation_drafts
           SET status='rejected', reviewed_at=now(), review_note=:note, updated_at=now()
         WHERE id=:id AND status='awaiting_review'
         RETURNING id
    """), {"id": int(draft_id), "note": str(note or "").strip()[:1000] or None}).mappings().first()
    if not row:
        raise ValueError("Чернетку не знайдено або її вже опрацьовано")
    db.commit()
    return {"ok": True, "id": int(row["id"]), "status": "rejected"}

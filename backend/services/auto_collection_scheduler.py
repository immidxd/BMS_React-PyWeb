"""Weekly Top-9 draft scheduler with an enforced manual-review boundary.

The service may write a candidate snapshot to PostgreSQL. It cannot render or
upload media and has no dependency on any social dispatcher, so a scheduled
cycle can never turn into an external publication by accident.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from backend.services import auto_collection
except ImportError:
    from services import auto_collection

PLATFORMS = ("viber", "facebook")
PERIODS = (0, 7, 30, 90)
DEFAULT_TIMEZONE = "Europe/Kyiv"
REVIEW_STATUS = "awaiting_review"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_local_time(value: Any) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    raw = str(value or "").strip()
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Час має бути у форматі ГГ:ХХ") from exc
    return parsed.replace(second=0, microsecond=0)


def validate_config(payload: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = {**(current or {}), **payload}
    enabled_value = source.get("enabled", False)
    if not isinstance(enabled_value, bool):
        raise ValueError("Стан розкладу має бути увімкнено або вимкнено")
    enabled = enabled_value
    weekday = int(source.get("weekday", 6))
    if weekday < 0 or weekday > 6:
        raise ValueError("День тижня має бути від понеділка до неділі")
    local_time = parse_local_time(source.get("local_time", "10:00"))
    timezone_name = str(source.get("timezone") or DEFAULT_TIMEZONE).strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Невідомий часовий пояс") from exc
    period_days = int(source.get("period_days", 30))
    if period_days not in PERIODS:
        raise ValueError("Період рейтингу має бути 7, 30, 90 днів або весь чистий період")
    cooldown_days = int(source.get("cooldown_days", 14))
    if cooldown_days < 14 or cooldown_days > 90:
        raise ValueError("Захист від повторів має бути від 14 до 90 днів")
    item_count = int(source.get("item_count", 9))
    if item_count < 2 or item_count > 9:
        raise ValueError("Підбірка має містити від 2 до 9 товарів")
    auto_publish = source.get("auto_publish", False)
    if not isinstance(auto_publish, bool):
        raise ValueError("Режим публікації має бути увімкнено або вимкнено")
    return {
        "enabled": enabled,
        "auto_publish": auto_publish,
        "weekday": weekday,
        "local_time": local_time,
        "timezone": timezone_name,
        "period_days": period_days,
        "cooldown_days": cooldown_days,
        "item_count": item_count,
    }


def latest_weekly_slot(
    now: datetime,
    *,
    weekday: int,
    local_time: time,
    timezone_name: str,
) -> datetime:
    """Most recent configured wall-clock slot, returned as aware UTC."""
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    slot_date = local_now.date() - timedelta(days=(local_now.weekday() - weekday) % 7)
    candidate = datetime.combine(slot_date, local_time, tzinfo=tz)
    if candidate > local_now:
        candidate -= timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def next_weekly_slot(
    now: datetime,
    *,
    weekday: int,
    local_time: time,
    timezone_name: str,
) -> datetime:
    """Next configured wall-clock slot, strictly after ``now``."""
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    slot_date = local_now.date() + timedelta(days=(weekday - local_now.weekday()) % 7)
    candidate = datetime.combine(slot_date, local_time, tzinfo=tz)
    if candidate <= local_now:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def due_slot(config: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """Return one due slot, never backfilling a slot before activation."""
    if not config.get("enabled") or not config.get("enabled_at"):
        return None
    slot = latest_weekly_slot(
        now,
        weekday=int(config["weekday"]),
        local_time=parse_local_time(config["local_time"]),
        timezone_name=str(config["timezone"]),
    )
    enabled_at = config["enabled_at"]
    if enabled_at.tzinfo is None:
        enabled_at = enabled_at.replace(tzinfo=timezone.utc)
    return slot if slot >= enabled_at.astimezone(timezone.utc) else None


def _config_rows(db: Session) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT platform, enabled, auto_publish, weekday, local_time, timezone,
               period_days, cooldown_days, item_count, enabled_at, last_generated_at,
               last_error, last_error_at, updated_at
        FROM auto_collection_configs
        ORDER BY CASE platform WHEN 'viber' THEN 1 ELSE 2 END
    """)).mappings().all()
    return [dict(row) for row in rows]


def config_rows(db: Session) -> List[Dict[str, Any]]:
    """Публічний доступ для автопублікації: їй треба знати, де вимкнено перевірку."""
    return _config_rows(db)


def _serialize_config(row: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    value = dict(row)
    value["local_time"] = parse_local_time(value["local_time"]).strftime("%H:%M")
    current = now or utc_now()
    value["next_run_at"] = (
        next_weekly_slot(
            current,
            weekday=int(value["weekday"]),
            local_time=parse_local_time(value["local_time"]),
            timezone_name=str(value["timezone"]),
        ) if value.get("enabled") else None
    )
    value["manual_review_required"] = not bool(value.get("auto_publish"))
    value["automatic_publishing"] = bool(value.get("auto_publish"))
    return value


_DRAFT_SELECT = """
    SELECT id, platform, source, status, scheduled_for, selection_key,
           product_ids, product_numbers, selected_json, reserves_json,
           warnings_json, policy_json, audit_json, reviewed_at,
           review_note, created_at, updated_at
    FROM auto_collection_drafts
"""


def _serialize_draft(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["selected"] = item.pop("selected_json") or []
    item["reserves"] = item.pop("reserves_json") or []
    item["warnings"] = item.pop("warnings_json") or []
    item["policy"] = item.pop("policy_json") or {}
    item["audit"] = item.pop("audit_json") or {}
    return item


def _draft_rows(db: Session, limit: int = 20) -> List[Dict[str, Any]]:
    rows = db.execute(text(_DRAFT_SELECT + """
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"limit": max(1, min(int(limit), 100))}).mappings().all()
    return [_serialize_draft(row) for row in rows]


def dashboard(db: Session, *, draft_limit: int = 20) -> Dict[str, Any]:
    configs = [_serialize_config(row) for row in _config_rows(db)]
    drafts = _draft_rows(db, draft_limit)
    pending_count = int(db.execute(text("""
        SELECT COUNT(*) FROM auto_collection_drafts WHERE status = :status
    """), {"status": REVIEW_STATUS}).scalar() or 0)
    try:
        from backend.services import auto_collection_cloud_sync
    except ImportError:
        from services import auto_collection_cloud_sync
    return {
        "ok": True,
        "configs": configs,
        "drafts": drafts,
        "pending_count": pending_count,
        "safety": {
            "manual_review_required": True,
            "automatic_publishing": False,
            "media_uploads": False,
        },
        "cloud_sync": auto_collection_cloud_sync.status(),
    }


def update_config(db: Session, platform: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError("Автопідбірки доступні лише для Viber і Facebook")
    current = db.execute(text("""
        SELECT platform, enabled, weekday, local_time, timezone, period_days,
               cooldown_days, item_count, enabled_at
        FROM auto_collection_configs WHERE platform = :platform
    """), {"platform": platform}).mappings().first()
    if not current:
        raise ValueError("Налаштування майданчика не знайдено")
    current_dict = dict(current)
    clean = validate_config(payload, current_dict)
    schedule_changed = any(
        clean[key] != current_dict.get(key)
        for key in ("weekday", "local_time", "timezone")
    )
    newly_enabled = clean["enabled"] and not bool(current_dict.get("enabled"))
    enabled_at = (
        utc_now() if newly_enabled or (clean["enabled"] and schedule_changed)
        else current_dict.get("enabled_at") if clean["enabled"]
        else None
    )
    row = db.execute(text("""
        UPDATE auto_collection_configs
           SET enabled=:enabled, auto_publish=:auto_publish, weekday=:weekday,
               local_time=CAST(:local_time AS time), timezone=:timezone,
               period_days=:period_days, cooldown_days=:cooldown_days,
               item_count=:item_count, enabled_at=:enabled_at,
               last_error=NULL, last_error_at=NULL, updated_at=now()
         WHERE platform=:platform
         RETURNING platform, enabled, auto_publish, weekday, local_time, timezone,
                   period_days, cooldown_days, item_count, enabled_at, last_generated_at,
                   last_error, last_error_at, updated_at
    """), {
        "platform": platform,
        "local_time": clean["local_time"].strftime("%H:%M"),
        "enabled_at": enabled_at,
        **{key: value for key, value in clean.items() if key != "local_time"},
    }).mappings().one()
    db.commit()
    return _serialize_config(dict(row))


def _existing_draft(db: Session, platform: str, scheduled_for: datetime) -> Optional[Dict[str, Any]]:
    row = db.execute(text(_DRAFT_SELECT + """
        WHERE platform=:platform AND scheduled_for=:scheduled_for
    """), {"platform": platform, "scheduled_for": scheduled_for}).mappings().first()
    return _serialize_draft(row) if row else None


def create_draft(
    db: Session,
    *,
    platform: str,
    source: str,
    scheduled_for: Optional[datetime] = None,
    config: Optional[Dict[str, Any]] = None,
    commit_changes: bool = True,
    schedule_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError("Автопідбірки доступні лише для Viber і Facebook")
    if source not in ("scheduled", "manual"):
        raise ValueError("Невідоме джерело чернетки")
    slot = scheduled_for or utc_now()
    if slot.tzinfo is None:
        slot = slot.replace(tzinfo=timezone.utc)
    existing = _existing_draft(db, platform, slot)
    if existing:
        return {"created": False, "draft": existing}

    if source == "scheduled":
        # Re-read and lock the config after the potentially stale periodic scan.
        # A concurrent user disable/schedule edit must win before any draft row
        # is inserted; at most it waits for the already-started selection.
        fresh = db.execute(text("""
            SELECT platform, enabled, weekday, local_time, timezone, period_days,
                   cooldown_days, item_count, enabled_at
            FROM auto_collection_configs WHERE platform=:platform
            FOR UPDATE
        """), {"platform": platform}).mappings().first()
        if not fresh:
            raise ValueError("Налаштування майданчика не знайдено")
        config = dict(fresh)
        existing = _existing_draft(db, platform, slot)
        if existing:
            db.rollback()
            return {"created": False, "draft": existing}
        expected = due_slot(config, schedule_now or utc_now())
        if expected != slot:
            db.rollback()
            return {"created": False, "draft": None, "reason": "schedule_changed_or_disabled"}
    elif config is None:
        row = db.execute(text("""
            SELECT platform, enabled, weekday, local_time, timezone, period_days,
                   cooldown_days, item_count, enabled_at
            FROM auto_collection_configs WHERE platform=:platform
            FOR UPDATE
        """), {"platform": platform}).mappings().first()
        if not row:
            raise ValueError("Налаштування майданчика не знайдено")
        config = dict(row)

    preview = auto_collection.create_preview_draft(
        db,
        platform=platform,
        count=int(config["item_count"]),
        period_days=int(config["period_days"]),
        cooldown_days=int(config["cooldown_days"]),
        scheduled_for=slot,
    )
    if not preview.get("ok"):
        raise ValueError((preview.get("warnings") or ["Недостатньо безпечних товарів"])[0])

    row = db.execute(text("""
        INSERT INTO auto_collection_drafts (
            platform, source, status, scheduled_for, selection_key,
            product_ids, product_numbers, selected_json, reserves_json,
            warnings_json, policy_json, audit_json
        ) VALUES (
            :platform, :source, :status, :scheduled_for, :selection_key,
            CAST(:product_ids AS jsonb), CAST(:product_numbers AS jsonb),
            CAST(:selected AS jsonb), CAST(:reserves AS jsonb),
            CAST(:warnings AS jsonb), CAST(:policy AS jsonb), CAST(:audit AS jsonb)
        )
        ON CONFLICT (platform, scheduled_for) DO NOTHING
        RETURNING id
    """), {
        "platform": platform,
        "source": source,
        "status": REVIEW_STATUS,
        "scheduled_for": slot,
        "selection_key": preview["audit"]["selection_key"],
        "product_ids": json.dumps(preview["product_ids"]),
        "product_numbers": json.dumps(
            [row["productnumber"] for row in preview["selected"]], ensure_ascii=False,
        ),
        "selected": json.dumps(preview["selected"], ensure_ascii=False, default=str),
        "reserves": json.dumps(preview["reserves"], ensure_ascii=False, default=str),
        "warnings": json.dumps(preview["warnings"], ensure_ascii=False),
        "policy": json.dumps(preview["policy"], ensure_ascii=False),
        "audit": json.dumps(preview["audit"], ensure_ascii=False, default=str),
    }).mappings().first()
    if commit_changes:
        db.commit()
    else:
        db.flush()
    draft = _existing_draft(db, platform, slot)
    return {"created": bool(row), "draft": draft}


def generate_due_drafts(db: Session, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or utc_now()
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for config in _config_rows(db):
        slot = due_slot(config, current)
        if slot is None:
            skipped.append({"platform": config["platform"], "reason": "disabled_or_not_due"})
            continue
        try:
            result = create_draft(
                db,
                platform=config["platform"],
                source="scheduled",
                scheduled_for=slot,
                config=config,
                schedule_now=current,
            )
            if result["created"]:
                created.append(result["draft"])
                db.execute(text("""
                    UPDATE auto_collection_configs
                       SET last_generated_at=now(), last_error=NULL,
                           last_error_at=NULL, updated_at=now()
                     WHERE platform=:platform
                """), {"platform": config["platform"]})
                db.commit()
            else:
                skipped.append({
                    "platform": config["platform"],
                    "reason": result.get("reason") or "already_created",
                })
        except Exception as exc:  # a failed platform must not block the other one
            db.rollback()
            message = str(exc)
            errors.append({"platform": config["platform"], "error": message})
            db.execute(text("""
                UPDATE auto_collection_configs
                   SET last_error=:error, last_error_at=now(), updated_at=now()
                 WHERE platform=:platform
            """), {"platform": config["platform"], "error": message[:2000]})
            db.commit()
    return {"ok": not errors, "created": created, "skipped": skipped, "errors": errors}


def load_draft(db: Session, draft_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute(text(_DRAFT_SELECT + " WHERE id=:id"), {"id": int(draft_id)}).mappings().first()
    return _serialize_draft(row) if row else None


def revalidate_draft(db: Session, draft: Dict[str, Any]) -> Dict[str, Any]:
    """Свіжий стан складу безпосередньо перед відправленням.

    Між створенням чернетки і натисканням «Опублікувати» минає час: пару могли
    продати, зняти з вітрини або показати в іншій підбірці. Політика чернетки
    обіцяє `revalidate_before_publish`, і виконується ця обіцянка саме тут —
    проти ЖИВОЇ бази, а не проти знімка, з якого чернетку зібрали. Тому
    хмарна чернетка зі застарілим знімком не небезпечна: склад однаково
    перевіряється заново перед відправленням.

    Випалі позиції заміщуються з резерву тієї ж чернетки — так тижнева
    підбірка лишається повною, замість того щоб їхати діркою.
    """
    policy = draft.get("policy") or {}
    period_days = int(policy.get("period_days") or 30)
    cooldown_days = int(policy.get("cooldown_days") or 14)
    target = int(policy.get("count") or len(draft.get("selected") or []) or 9)

    fresh = auto_collection._candidate_rows(db, period_days, pool=5000)
    eligible = {auto_collection.normalize_number(row["productnumber"]) for row in fresh}
    blocked = {
        auto_collection.normalize_number(value)
        for value in auto_collection._cooldown_numbers(
            db, cooldown_days, draft.get("scheduled_for"),
        )
    }

    def usable(row: Dict[str, Any]) -> bool:
        key = auto_collection.normalize_number(row.get("productnumber"))
        return bool(key) and key in eligible and key not in blocked

    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for row in draft.get("selected") or []:
        (kept if usable(row) else dropped).append(row)

    taken = {auto_collection.normalize_number(row.get("productnumber")) for row in kept}
    promoted: List[Dict[str, Any]] = []
    for row in draft.get("reserves") or []:
        if len(kept) >= target:
            break
        key = auto_collection.normalize_number(row.get("productnumber"))
        if key in taken or not usable(row):
            continue
        kept.append(row)
        promoted.append(row)
        taken.add(key)

    warnings: List[str] = []
    if dropped:
        numbers = ", ".join(f"#{row.get('productnumber')}" for row in dropped)
        warnings.append(f"Уже недоступні, тому прибрані з підбірки: {numbers}.")
    if promoted:
        numbers = ", ".join(f"#{row.get('productnumber')}" for row in promoted)
        warnings.append(f"Замість них із резерву додані: {numbers}.")
    if len(kept) < target:
        warnings.append(f"У підбірці {len(kept)} із {target} позицій: резерв вичерпано.")

    return {
        "ok": len(kept) >= 2,
        "selected": kept,
        "product_ids": [int(row["product_id"]) for row in kept if row.get("product_id")],
        "dropped": dropped,
        "promoted": promoted,
        "warnings": warnings,
    }


def mark_approved(db: Session, draft_id: int, *, dispatch: Dict[str, Any],
                  note: Optional[str] = None) -> Dict[str, Any]:
    """Позначити чернетку відправленою. Викликається ЛИШЕ після успіху диспетчера."""
    row = db.execute(text("""
        UPDATE auto_collection_drafts
           SET status='approved', reviewed_at=now(), review_note=:note,
               audit_json = COALESCE(audit_json, '{}'::jsonb) || CAST(:dispatch AS jsonb),
               updated_at=now()
         WHERE id=:id AND status=:status
         RETURNING id
    """), {
        "id": int(draft_id),
        "status": REVIEW_STATUS,
        "note": str(note or "").strip()[:1000] or None,
        "dispatch": json.dumps({"dispatch": dispatch}, ensure_ascii=False, default=str),
    }).mappings().first()
    if not row:
        raise ValueError("Чернетку не знайдено або її вже опрацьовано")
    db.commit()
    return {"ok": True, "id": int(row["id"]), "status": "approved"}


def reject_draft(db: Session, draft_id: int, note: Optional[str] = None) -> Dict[str, Any]:
    row = db.execute(text("""
        UPDATE auto_collection_drafts
           SET status='rejected', reviewed_at=now(), review_note=:note, updated_at=now()
         WHERE id=:id AND status='awaiting_review'
         RETURNING id
    """), {"id": int(draft_id), "note": str(note or "").strip()[:1000] or None}).mappings().first()
    if not row:
        raise ValueError("Чернетку не знайдено або її вже опрацьовано")
    db.commit()
    return {"ok": True, "id": int(row["id"]), "status": "rejected"}

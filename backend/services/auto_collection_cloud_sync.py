"""Safe BMS <-> Neon mirror for review-only Top-9 drafts.

The cloud tables contain settings, candidate metrics and manual-review
snapshots only.  This module has no imports from any publisher, media renderer,
R2 client or social dispatcher.  Cloud failures are visible but never block
the local BMS workflow.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

try:
    from backend.models.database import SessionLocal
    from backend.services import auto_collection
except ImportError:
    from models.database import SessionLocal
    from services import auto_collection

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNNING = False
_PENDING_REASON: Optional[str] = None
_ENGINE: Optional[Engine] = None
_LAST_SUCCESS_AT: Optional[datetime] = None
_LAST_ERROR: Optional[str] = None
_LAST_ERROR_AT: Optional[datetime] = None

SNAPSHOT_PERIODS = (0, 7, 30, 90)
SNAPSHOT_MAX_AGE_MINUTES = 60
MAX_SNAPSHOT_CANDIDATES = 5000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _catalog_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("BMS_CATALOG_DIR", "~/Desktop/BMS_catalog")))


def _dotenv_value(path: Path, key: str) -> Optional[str]:
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in ("'", '"'):
            value = value[1:-1]
        return value or None
    return None


def cloud_database_url() -> Optional[str]:
    """Resolve the existing catalog Neon URL without ever logging its value."""
    return (
        os.getenv("AUTO_COLLECTION_CLOUD_DATABASE_URL")
        or _dotenv_value(_catalog_dir() / ".env", "CLOUD_DATABASE_URL")
    )


def is_configured() -> bool:
    return bool(cloud_database_url())


def _engine() -> Engine:
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            url = cloud_database_url()
            if not url:
                raise RuntimeError("Хмарна база каталогу не налаштована")
            _ENGINE = create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=300,
                connect_args={"connect_timeout": 12},
            )
        return _ENGINE


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "cloudflare" / "auto-collection-drafts" / "schema.sql"


def ensure_cloud_schema(connection) -> None:
    schema_path = _schema_path()
    if not schema_path.is_file():
        raise RuntimeError("Не знайдено безпечну схему хмарних чернеток")
    # psycopg2/PostgreSQL safely accepts this fixed, repository-owned DDL batch.
    connection.exec_driver_sql(schema_path.read_text(encoding="utf-8"))


def _local_configs(db) -> List[Dict[str, Any]]:
    return [dict(row) for row in db.execute(text("""
        SELECT platform, enabled, weekday, local_time, timezone, period_days,
               cooldown_days, item_count, enabled_at, created_at, updated_at
        FROM auto_collection_configs
        ORDER BY platform
    """)).mappings().all()]


def _local_drafts(db) -> List[Dict[str, Any]]:
    return [dict(row) for row in db.execute(text("""
        SELECT platform, source, status, scheduled_for, selection_key,
               product_ids, product_numbers, selected_json, reserves_json,
               warnings_json, policy_json, audit_json, reviewed_at,
               review_note, created_at, updated_at
        FROM auto_collection_drafts
        WHERE created_at >= now() - interval '180 days'
        ORDER BY created_at
    """)).mappings().all()]


def _recent_posts(db) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT id, platform, status, product_numbers,
               COALESCE(published_at, scheduled_at, created_at) AS occurred_at
        FROM social_collection_posts
        WHERE status NOT IN ('failed','error','cancelled')
          AND COALESCE(published_at, scheduled_at, created_at)
              >= now() - interval '90 days'
    """)).mappings().all()
    result: List[Dict[str, Any]] = []
    for row in rows:
        for number in row["product_numbers"] or []:
            normalized = auto_collection.normalize_number(number)
            if not normalized:
                continue
            result.append({
                "source_key": f"bms:{int(row['id'])}:{normalized}",
                "platform": str(row["platform"] or "unknown"),
                "status": str(row["status"] or "unknown"),
                "productnumber": str(number),
                "occurred_at": row["occurred_at"],
            })
    return result


def _candidate_snapshot(db) -> List[Dict[str, Any]]:
    by_number: Dict[str, Dict[str, Any]] = {}
    for period in SNAPSHOT_PERIODS:
        for row in auto_collection._candidate_rows(
            db, period, pool=MAX_SNAPSHOT_CANDIDATES,
        ):
            number = str(row.get("productnumber") or "").strip()
            if not number:
                continue
            item = by_number.setdefault(number, {
                "productnumber": number,
                "product_id": int(row["product_id"]),
                "brand": row.get("brand"),
                "model": row.get("model"),
                "type": row.get("type"),
                "price": row.get("price"),
                "dateadded": row.get("dateadded"),
                "available": int(row.get("available") or 0),
                "sold_7": 0,
                "sold_30": 0,
                "sold_90": 0,
                "sold_all": 0,
            })
            item[f"sold_{period}" if period else "sold_all"] = int(row.get("sold_count") or 0)
    values = [row for row in by_number.values() if int(row["available"]) > 0]
    if len(values) < 2:
        raise RuntimeError("Локальний знімок має замало безпечних товарів; старий хмарний знімок збережено")
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _push_configs(connection, rows: Iterable[Dict[str, Any]]) -> None:
    connection.execute(text("""
        INSERT INTO auto_collection_configs (
            platform, enabled, weekday, local_time, timezone, period_days,
            cooldown_days, item_count, enabled_at, created_at, updated_at
        ) VALUES (
            :platform, :enabled, :weekday, :local_time, :timezone, :period_days,
            :cooldown_days, :item_count, :enabled_at, :created_at, :updated_at
        )
        ON CONFLICT (platform) DO UPDATE SET
            enabled=EXCLUDED.enabled,
            weekday=EXCLUDED.weekday,
            local_time=EXCLUDED.local_time,
            timezone=EXCLUDED.timezone,
            period_days=EXCLUDED.period_days,
            cooldown_days=EXCLUDED.cooldown_days,
            item_count=EXCLUDED.item_count,
            enabled_at=EXCLUDED.enabled_at,
            updated_at=EXCLUDED.updated_at
    """), list(rows))


def _push_drafts(connection, rows: Iterable[Dict[str, Any]]) -> None:
    payload = []
    for row in rows:
        payload.append({
            **row,
            "product_ids": _json(row.get("product_ids") or []),
            "product_numbers": _json(row.get("product_numbers") or []),
            "selected_json": _json(row.get("selected_json") or []),
            "reserves_json": _json(row.get("reserves_json") or []),
            "warnings_json": _json(row.get("warnings_json") or []),
            "policy_json": _json(row.get("policy_json") or {}),
            "audit_json": _json(row.get("audit_json") or {}),
        })
    if not payload:
        return
    connection.execute(text("""
        INSERT INTO auto_collection_drafts (
            platform, source, status, scheduled_for, selection_key,
            product_ids, product_numbers, selected_json, reserves_json,
            warnings_json, policy_json, audit_json, reviewed_at,
            review_note, created_at, updated_at
        ) VALUES (
            :platform, :source, :status, :scheduled_for, :selection_key,
            CAST(:product_ids AS jsonb), CAST(:product_numbers AS jsonb),
            CAST(:selected_json AS jsonb), CAST(:reserves_json AS jsonb),
            CAST(:warnings_json AS jsonb), CAST(:policy_json AS jsonb),
            CAST(:audit_json AS jsonb), :reviewed_at,
            :review_note, :created_at, :updated_at
        )
        ON CONFLICT (platform, scheduled_for) DO UPDATE SET
            source=EXCLUDED.source,
            status=EXCLUDED.status,
            selection_key=EXCLUDED.selection_key,
            product_ids=EXCLUDED.product_ids,
            product_numbers=EXCLUDED.product_numbers,
            selected_json=EXCLUDED.selected_json,
            reserves_json=EXCLUDED.reserves_json,
            warnings_json=EXCLUDED.warnings_json,
            policy_json=EXCLUDED.policy_json,
            audit_json=EXCLUDED.audit_json,
            reviewed_at=EXCLUDED.reviewed_at,
            review_note=EXCLUDED.review_note,
            updated_at=EXCLUDED.updated_at
        WHERE EXCLUDED.updated_at >= auto_collection_drafts.updated_at
    """), payload)


def _push_recent_posts(connection, rows: Iterable[Dict[str, Any]]) -> None:
    values = list(rows)
    connection.execute(text("DELETE FROM auto_collection_recent_posts WHERE source_key LIKE 'bms:%'"))
    if values:
        connection.execute(text("""
            INSERT INTO auto_collection_recent_posts (
                source_key, platform, status, productnumber, occurred_at, synced_at
            ) VALUES (
                :source_key, :platform, :status, :productnumber, :occurred_at, now()
            )
            ON CONFLICT (source_key) DO UPDATE SET
                platform=EXCLUDED.platform,
                status=EXCLUDED.status,
                productnumber=EXCLUDED.productnumber,
                occurred_at=EXCLUDED.occurred_at,
                synced_at=now()
        """), values)


def _snapshot_is_stale(connection) -> bool:
    return bool(connection.execute(text("""
        SELECT COALESCE(MAX(synced_at), '-infinity'::timestamptz)
               < now() - (:minutes || ' minutes')::interval
        FROM auto_collection_product_snapshot
    """), {"minutes": SNAPSHOT_MAX_AGE_MINUTES}).scalar())


def _replace_snapshot(connection, rows: Iterable[Dict[str, Any]]) -> None:
    values = list(rows)
    connection.execute(text("DELETE FROM auto_collection_product_snapshot"))
    connection.execute(text("""
        INSERT INTO auto_collection_product_snapshot (
            productnumber, product_id, brand, model, type, price, dateadded,
            available, sold_7, sold_30, sold_90, sold_all, synced_at
        ) VALUES (
            :productnumber, :product_id, :brand, :model, :type, :price, :dateadded,
            :available, :sold_7, :sold_30, :sold_90, :sold_all, now()
        )
    """), values)


def _remote_state(connection) -> Dict[str, Any]:
    drafts = [dict(row) for row in connection.execute(text("""
        SELECT platform, source, status, scheduled_for, selection_key,
               product_ids, product_numbers, selected_json, reserves_json,
               warnings_json, policy_json, audit_json, reviewed_at,
               review_note, created_at, updated_at
        FROM auto_collection_drafts
        WHERE created_at >= now() - interval '180 days'
        ORDER BY created_at
    """)).mappings().all()]
    configs = [dict(row) for row in connection.execute(text("""
        SELECT platform, last_generated_at, last_error, last_error_at
        FROM auto_collection_configs ORDER BY platform
    """)).mappings().all()]
    return {"drafts": drafts, "configs": configs}


def _merge_remote_local(state: Dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        for row in state["drafts"]:
            db.execute(text("""
                INSERT INTO auto_collection_drafts (
                    platform, source, status, scheduled_for, selection_key,
                    product_ids, product_numbers, selected_json, reserves_json,
                    warnings_json, policy_json, audit_json, reviewed_at,
                    review_note, created_at, updated_at
                ) VALUES (
                    :platform, :source, :status, :scheduled_for, :selection_key,
                    CAST(:product_ids AS jsonb), CAST(:product_numbers AS jsonb),
                    CAST(:selected_json AS jsonb), CAST(:reserves_json AS jsonb),
                    CAST(:warnings_json AS jsonb), CAST(:policy_json AS jsonb),
                    CAST(:audit_json AS jsonb), :reviewed_at,
                    :review_note, :created_at, :updated_at
                )
                ON CONFLICT (platform, scheduled_for) DO UPDATE SET
                    source=EXCLUDED.source,
                    status=EXCLUDED.status,
                    selection_key=EXCLUDED.selection_key,
                    product_ids=EXCLUDED.product_ids,
                    product_numbers=EXCLUDED.product_numbers,
                    selected_json=EXCLUDED.selected_json,
                    reserves_json=EXCLUDED.reserves_json,
                    warnings_json=EXCLUDED.warnings_json,
                    policy_json=EXCLUDED.policy_json,
                    audit_json=EXCLUDED.audit_json,
                    reviewed_at=EXCLUDED.reviewed_at,
                    review_note=EXCLUDED.review_note,
                    updated_at=EXCLUDED.updated_at
                WHERE EXCLUDED.updated_at > auto_collection_drafts.updated_at
            """), {
                **row,
                "product_ids": _json(row.get("product_ids") or []),
                "product_numbers": _json(row.get("product_numbers") or []),
                "selected_json": _json(row.get("selected_json") or []),
                "reserves_json": _json(row.get("reserves_json") or []),
                "warnings_json": _json(row.get("warnings_json") or []),
                "policy_json": _json(row.get("policy_json") or {}),
                "audit_json": _json(row.get("audit_json") or {}),
            })
        for row in state["configs"]:
            db.execute(text("""
                UPDATE auto_collection_configs
                   SET last_generated_at = CASE
                           WHEN :last_generated_at IS NOT NULL AND (
                               last_generated_at IS NULL OR :last_generated_at > last_generated_at
                           ) THEN :last_generated_at ELSE last_generated_at END,
                       last_error = CASE
                           WHEN :last_generated_at IS NOT NULL AND (
                               last_error_at IS NULL OR :last_generated_at >= last_error_at
                           ) THEN :last_error
                           WHEN :last_error_at IS NOT NULL AND (
                               last_error_at IS NULL OR :last_error_at >= last_error_at
                           ) THEN :last_error ELSE last_error END,
                       last_error_at = CASE
                           WHEN :last_generated_at IS NOT NULL AND (
                               last_error_at IS NULL OR :last_generated_at >= last_error_at
                           ) THEN :last_error_at
                           WHEN :last_error_at IS NOT NULL AND (
                               last_error_at IS NULL OR :last_error_at >= last_error_at
                           ) THEN :last_error_at ELSE last_error_at END
                 WHERE platform=:platform
            """), row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def sync_once(*, force_snapshot: bool = False) -> Dict[str, Any]:
    """Synchronize review-only state; safe to run from a background thread."""
    if os.getenv("AUTO_COLLECTION_CLOUD_SYNC", "1") == "0":
        return {"ok": False, "skipped": "disabled"}
    if not is_configured():
        return {"ok": False, "skipped": "not_configured"}

    local = SessionLocal()
    try:
        configs = _local_configs(local)
        drafts = _local_drafts(local)
        recent_posts = _recent_posts(local)
        local.rollback()
    finally:
        local.close()

    snapshot: Optional[List[Dict[str, Any]]] = None
    engine = _engine()
    with engine.begin() as cloud:
        ensure_cloud_schema(cloud)
        stale = force_snapshot or _snapshot_is_stale(cloud)
    if stale:
        local = SessionLocal()
        try:
            snapshot = _candidate_snapshot(local)
            local.rollback()
        finally:
            local.close()

    with engine.begin() as cloud:
        _push_configs(cloud, configs)
        _push_drafts(cloud, drafts)
        _push_recent_posts(cloud, recent_posts)
        if snapshot is not None:
            _replace_snapshot(cloud, snapshot)
        remote = _remote_state(cloud)
    _merge_remote_local(remote)

    global _LAST_SUCCESS_AT, _LAST_ERROR, _LAST_ERROR_AT
    with _LOCK:
        _LAST_SUCCESS_AT = _utc_now()
        _LAST_ERROR = None
        _LAST_ERROR_AT = None
    return {
        "ok": True,
        "drafts": len(remote["drafts"]),
        "snapshot_products": len(snapshot) if snapshot is not None else None,
    }


def status() -> Dict[str, Any]:
    worker_url = str(os.getenv("AUTO_COLLECTION_DRAFT_WORKER_URL") or "").strip()
    with _LOCK:
        return {
            "configured": is_configured(),
            # This flag becomes true only after a successful Worker deploy and
            # health check stores its URL in the ignored local .env.
            "autonomous": bool(worker_url),
            "running": _RUNNING,
            "pending": bool(_PENDING_REASON),
            "last_success_at": _LAST_SUCCESS_AT,
            "last_error": _LAST_ERROR,
            "last_error_at": _LAST_ERROR_AT,
            "draft_only": True,
        }


def trigger(reason: str = "auto-collection") -> bool:
    """Queue a coalesced sync; repeated UI actions never create parallel jobs."""
    if os.getenv("AUTO_COLLECTION_CLOUD_SYNC", "1") == "0" or not is_configured():
        return False
    global _RUNNING, _PENDING_REASON
    with _LOCK:
        if _RUNNING:
            _PENDING_REASON = reason
            return True
        _RUNNING = True
    threading.Thread(target=_worker, args=(reason,), name="auto-collection-cloud-sync", daemon=True).start()
    return True


def _worker(reason: str) -> None:
    global _RUNNING, _PENDING_REASON, _LAST_ERROR, _LAST_ERROR_AT
    current_reason = reason
    while True:
        try:
            result = sync_once()
            logger.info("Auto-collection cloud sync finished (%s): %s", current_reason, result)
        except Exception as exc:
            message = str(exc)[:2000]
            with _LOCK:
                _LAST_ERROR = message
                _LAST_ERROR_AT = _utc_now()
            logger.warning("Auto-collection cloud sync failed (%s): %s", current_reason, message)
        with _LOCK:
            if _PENDING_REASON:
                current_reason = f"{_PENDING_REASON} / follow-up"
                _PENDING_REASON = None
                continue
            _RUNNING = False
            return

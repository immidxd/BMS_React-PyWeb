from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, text
import hashlib
import logging
import re
from datetime import datetime

from models.database import get_db
from models.models import (
    Client, Gender, ClientAddress, ClientRelation, ClientRelationOrder,
    ClientAlias, ClientFlag,
)
from schemas.reference import (
    Client as ClientSchema, ClientCreate, ClientUpdate, ClientList,
    ClientAddress as ClientAddressSchema, ClientAddressCreate, ClientAddressUpdate,
    ClientRelation as ClientRelationSchema, ClientRelationCreate, ClientRelationUpdate,
    ClientAlias as ClientAliasSchema, ClientAliasCreate,
    ClientFlag as ClientFlagSchema, ClientMergeRequest, ClientFlagDismiss,
)
from utils.identity_normalizer import (
    normalize_phone, normalize_facebook,
    normalize_instagram, normalize_telegram,
)


def _apply_normalized(client_obj):
    """Recompute *_normalized fields from raw values. Idempotent."""
    client_obj.phone_normalized     = normalize_phone(getattr(client_obj, 'phone_number', None))
    client_obj.facebook_normalized  = normalize_facebook(getattr(client_obj, 'facebook', None))
    client_obj.instagram_normalized = normalize_instagram(getattr(client_obj, 'instagram', None))
    client_obj.telegram_normalized  = normalize_telegram(getattr(client_obj, 'telegram', None))


# ── Identity helpers (Step 4) ─────────────────────────────────────────────
_NAME_LIKE_FIELDS = {"first_name", "last_name", "middle_name", "nickname"}


def _norm_alias_key(first: str, last: str, nickname: str) -> str:
    return "|".join([
        (first or "").strip().lower(),
        (last or "").strip().lower(),
        (nickname or "").strip().lower(),
    ])


def _save_alias_from_client(db: Session, client: Client, source: str = "manual_edit_history") -> None:
    """Зберегти ПОТОЧНІ first/last/nickname клієнта як alias (перед редагуванням).
    Idempotent через UNIQUE(client_id, norm_key)."""
    f = (client.first_name or "").strip()
    l = (client.last_name or "").strip()
    n = (client.nickname or "").strip()
    key = _norm_alias_key(f, l, n)
    if key == "||":
        return
    full_raw = (f + (" " + l if l else "") + (f" ({n})" if n else "")).strip()
    db.execute(text("""
        INSERT INTO client_aliases
            (client_id, first_name, last_name, nickname, full_raw,
             norm_key, source, seen_count, first_seen_at, last_seen_at)
        VALUES
            (:cid, :f, :l, :n, :raw, :k, :src, 1, NOW(), NOW())
        ON CONFLICT (client_id, norm_key) DO UPDATE
            SET last_seen_at = NOW(),
                full_raw = COALESCE(EXCLUDED.full_raw, client_aliases.full_raw)
    """), {
        "cid": client.id, "f": f or None, "l": l or None, "n": n or None,
        "raw": full_raw or None, "k": key, "src": source,
    })

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/api/clients", response_model=ClientList, tags=["clients"])
async def get_clients(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    gender_id: Optional[int] = None,
    sort_by: str = Query("last_name", description="id|last_name|first_name|order_count|total_order_amount|confirmed_orders|cancelled_count|rating"),
    sort_dir: str = Query("asc", description="asc|desc"),
    db: Session = Depends(get_db)
):
    """
    Get list of clients with pagination, filtering, and order breakdown counts.
    """
    logger.info(f"Fetching clients: page={page}, per_page={per_page}, search={search}, sort={sort_by} {sort_dir}")

    where_clauses = []
    params: dict = {}

    if search:
        # Шукаємо по: ПІБ / nickname / телефон (raw + normalized) / email /
        #             соцмережі (telegram/instagram/facebook/viber/olx) /
        #             історичні aliases (full_raw + first/last/nickname).
        # ВАЖЛИВО: знімаємо апострофи в обох сторонах — щоб пошук
        # «мяна» знаходив «Мар'яна», а «вяч» знаходив «В'ячеслав».
        #
        # ВНУТРІШНЄ ПРАВИЛО: чистимо у ДВА ПРОХОДИ.
        # 1) chr(39) знімає ASCII-апостроф (U+0027) — його не можна засунути
        #    у regex char-class всередині SQL string literal без зламу парсера.
        # 2) [ʼ’`´] знімає інші типографські варіанти: U+02BC, U+2019,
        #    U+0060 (gravis), U+00B4 (acute).
        def _strip_apx(col: str) -> str:
            return f"regexp_replace(regexp_replace(COALESCE({col},''), chr(39), '', 'g'), '[ʼ’`´]', '', 'g')"

        where_clauses.append(f"""
            (
                {_strip_apx('c.first_name')} ILIKE :search_clean
                OR {_strip_apx('c.last_name')} ILIKE :search_clean
                OR {_strip_apx('c.nickname')}  ILIKE :search_clean
                OR c.phone_number ILIKE :search
                OR c.phone_normalized ILIKE :search_digits
                OR c.email ILIKE :search
                OR c.telegram ILIKE :search
                OR c.instagram ILIKE :search
                OR c.facebook ILIKE :search
                OR c.viber ILIKE :search
                OR c.olx ILIKE :search
                OR EXISTS (
                    SELECT 1 FROM client_aliases ca
                    WHERE ca.client_id = c.id
                      AND ({_strip_apx('ca.full_raw')}    ILIKE :search_clean
                           OR {_strip_apx('ca.first_name')} ILIKE :search_clean
                           OR {_strip_apx('ca.last_name')}  ILIKE :search_clean
                           OR {_strip_apx('ca.nickname')}   ILIKE :search_clean)
                )
            )
        """)
        params["search"] = f"%{search}%"
        # Версія запиту без апострофів для порівняння з очищеними полями
        search_clean = re.sub(r"['ʼ’`´]", "", search)
        params["search_clean"] = f"%{search_clean}%"
        # Для phone_normalized шукаємо по цифрах (нормалізована форма зберігає лише цифри)
        digits_only = re.sub(r"\D+", "", search)
        params["search_digits"] = f"%{digits_only}%" if digits_only else "__NEVER_MATCH__"

    if gender_id:
        where_clauses.append("c.gender_id = :gender_id")
        params["gender_id"] = gender_id

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Count total
    count_sql = f"SELECT COUNT(*) FROM clients c {where_sql}"
    total = db.execute(text(count_sql), params).scalar() or 0

    # Allowed sort columns (prevent SQL injection)
    allowed_sorts = {
        "id": "c.id",
        "last_name": "c.last_name",
        "first_name": "c.first_name",
        "order_count": "c.order_count",
        "total_order_amount": "c.total_order_amount",
        "confirmed_orders": "confirmed_orders",
        "cancelled_count": "cancelled_count",
        "ignored_count": "ignored_count",
        "return_exchange_count": "return_exchange_count",
        "rating": "rating",
    }
    sort_col = allowed_sorts.get(sort_by, "c.last_name")
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    params["limit_val"] = per_page
    params["offset_val"] = (page - 1) * per_page

    main_sql = f"""
        SELECT
            c.*,
            COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '') AS full_name,
            COALESCE(stats.confirmed_orders, 0) AS confirmed_orders,
            COALESCE(stats.cancelled_count, 0) AS cancelled_count,
            COALESCE(stats.ignored_count, 0) AS ignored_count,
            COALESCE(stats.return_exchange_count, 0) AS return_exchange_count,
            COALESCE(stats.has_deferred, false) AS has_deferred,
            COALESCE(flags.has_active_flags, false) AS has_active_flags,
            flags.top_flag_type AS top_flag_type,
            -- Rating formula: base 5.0 + order bonus - cancel/ignore/return penalties + amount bonus, clamped 0-10
            GREATEST(0, LEAST(10,
                5.0
                + LEAST(COALESCE(stats.confirmed_orders, 0) * 0.5, 3.0)
                - LEAST(COALESCE(stats.cancelled_count, 0) * 1.0, 3.0)
                - LEAST(COALESCE(stats.ignored_count, 0) * 0.5, 2.0)
                - LEAST(COALESCE(stats.return_exchange_count, 0) * 0.3, 1.0)
                + LEAST(COALESCE(c.total_order_amount, 0) / 10000.0, 2.0)
            )) AS rating
        FROM clients c
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) FILTER (WHERE o.order_status_id NOT IN (5, 6, 9, 10)) AS confirmed_orders,
                COUNT(*) FILTER (WHERE o.order_status_id = 5) AS cancelled_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 6) AS ignored_count,
                COUNT(*) FILTER (WHERE o.order_status_id IN (9, 10)) AS return_exchange_count,
                BOOL_OR(o.deferred_until IS NOT NULL) AS has_deferred
            FROM orders o
            WHERE o.client_id = c.id
        ) stats ON true
        LEFT JOIN LATERAL (
            SELECT
                TRUE AS has_active_flags,
                (array_agg(flag_type ORDER BY
                    CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                    created_at DESC))[1] AS top_flag_type
            FROM client_flags cf
            WHERE cf.client_id = c.id AND cf.dismissed = FALSE
        ) flags ON true
        {where_sql}
        ORDER BY {sort_col} {direction}, c.id {direction}
        LIMIT :limit_val OFFSET :offset_val
    """

    rows = db.execute(text(main_sql), params).mappings().all()
    client_list = [dict(row) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    logger.info(f"Returning {len(client_list)} clients (total={total})")

    return {
        "items": client_list,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }

@router.get("/api/clients/{client_id}", tags=["clients"])
async def get_client(client_id: int, db: Session = Depends(get_db)):
    """
    Get client by ID with full statistics for the client card.
    Includes order breakdown by status, purchased models count, and recent orders.
    """
    # Основні дані клієнта + повна статистика замовлень
    sql = text("""
        SELECT
            c.*,
            COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '') AS full_name,
            COALESCE(stats.total_orders, 0) AS total_orders,
            COALESCE(stats.confirmed_orders, 0) AS confirmed_orders,
            COALESCE(stats.cancelled_count, 0) AS cancelled_count,
            COALESCE(stats.ignored_count, 0) AS ignored_count,
            COALESCE(stats.return_exchange_count, 0) AS return_exchange_count,
            COALESCE(stats.queue_count, 0) AS queue_count,
            COALESCE(stats.gift_count, 0) AS gift_count,
            COALESCE(stats.clarify_count, 0) AS clarify_count,
            COALESCE(stats.has_deferred, false) AS has_deferred,
            COALESCE(stats.total_amount, 0) AS computed_total_amount,
            COALESCE(stats.avg_amount, 0) AS computed_avg_amount,
            COALESCE(stats.max_amount, 0) AS computed_max_amount,
            COALESCE(stats.first_order, c.first_order_date) AS computed_first_order,
            COALESCE(stats.last_order, c.last_order_date) AS computed_last_order,
            COALESCE(models.purchased_models, 0) AS purchased_models,
            -- Rating formula: base 5.0 + order bonus - penalties + amount bonus, clamped 0-10
            GREATEST(0, LEAST(10,
                5.0
                + LEAST(COALESCE(stats.confirmed_orders, 0) * 0.5, 3.0)
                - LEAST(COALESCE(stats.cancelled_count, 0) * 1.0, 3.0)
                - LEAST(COALESCE(stats.ignored_count, 0) * 0.5, 2.0)
                - LEAST(COALESCE(stats.return_exchange_count, 0) * 0.3, 1.0)
                + LEAST(COALESCE(stats.total_amount, 0) / 10000.0, 2.0)
            )) AS rating
        FROM clients c
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS total_orders,
                COUNT(*) FILTER (WHERE o.order_status_id NOT IN (5, 6, 9, 10)) AS confirmed_orders,
                COUNT(*) FILTER (WHERE o.order_status_id = 5) AS cancelled_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 6) AS ignored_count,
                COUNT(*) FILTER (WHERE o.order_status_id IN (9, 10)) AS return_exchange_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 8) AS queue_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 7) AS gift_count,
                COUNT(*) FILTER (WHERE o.order_status_id = 3) AS clarify_count,
                BOOL_OR(o.deferred_until IS NOT NULL) AS has_deferred,
                SUM(o.total_amount) FILTER (WHERE o.order_status_id NOT IN (5, 6)) AS total_amount,
                AVG(o.total_amount) FILTER (WHERE o.order_status_id NOT IN (5, 6)) AS avg_amount,
                MAX(o.total_amount) FILTER (WHERE o.order_status_id NOT IN (5, 6)) AS max_amount,
                MIN(o.order_date) AS first_order,
                MAX(o.order_date) AS last_order
            FROM orders o
            WHERE o.client_id = c.id
        ) stats ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT oi.product_id) AS purchased_models
            FROM orders o2
            JOIN order_items oi ON oi.order_id = o2.id
            WHERE o2.client_id = c.id
        ) models ON true
        WHERE c.id = :client_id
    """)
    row = db.execute(sql, {"client_id": client_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")

    result = dict(row)

    # Останні замовлення клієнта (до 20)
    orders_sql = text("""
        SELECT
            o.id,
            o.order_date,
            o.total_amount,
            o.tracking_number,
            o.notes,
            o.sales_channel,
            os.status_name AS order_status,
            ps.status_name AS payment_status,
            dm.method_name AS delivery_method,
            COALESCE(items.product_numbers, '') AS product_numbers,
            COALESCE(items.item_count, 0) AS item_count
        FROM orders o
        LEFT JOIN order_statuses os ON os.id = o.order_status_id
        LEFT JOIN payment_statuses ps ON ps.id = o.payment_status_id
        LEFT JOIN delivery_methods dm ON dm.id = o.delivery_method_id
        LEFT JOIN LATERAL (
            SELECT
                STRING_AGG(p.productnumber, ', ' ORDER BY oi.id) AS product_numbers,
                COUNT(*) AS item_count
            FROM order_items oi
            LEFT JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = o.id
        ) items ON true
        WHERE o.client_id = :client_id
        ORDER BY o.order_date DESC, o.id DESC
        LIMIT 20
    """)
    orders_rows = db.execute(orders_sql, {"client_id": client_id}).mappings().all()
    result["recent_orders"] = [dict(r) for r in orders_rows]

    # ── Уподобання: top-N агрегати з історії замовлень ────────────────────
    # Виключаємо відмінені/ігнор замовлення (статуси 5, 6) — вони не показують
    # реальні преференції клієнта. Підраховуємо за кількістю позицій (items),
    # а не унікальних товарів, щоб повтор-замовлення давали більшу вагу.
    prefs_sql = text("""
        WITH client_items AS (
            SELECT oi.id AS item_id, p.id AS product_id, p.brandid, p.typeid,
                   p.colorid, p.sizeeu, p.subtypeid
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN products p ON p.id = oi.product_id
            WHERE o.client_id = :client_id
              AND COALESCE(o.order_status_id, 0) NOT IN (5, 6)
        )
        SELECT
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT b.brandname AS name, COUNT(*) AS cnt
                FROM client_items ci JOIN brands b ON b.id = ci.brandid
                WHERE b.brandname IS NOT NULL
                GROUP BY b.brandname ORDER BY cnt DESC LIMIT 8
            ) t) AS top_brands,
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT tp.typename AS name, COUNT(*) AS cnt
                FROM client_items ci JOIN types tp ON tp.id = ci.typeid
                WHERE tp.typename IS NOT NULL
                GROUP BY tp.typename ORDER BY cnt DESC LIMIT 8
            ) t) AS top_types,
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT cl.colorname AS name, COUNT(*) AS cnt
                FROM client_items ci JOIN colors cl ON cl.id = ci.colorid
                WHERE cl.colorname IS NOT NULL
                GROUP BY cl.colorname ORDER BY cnt DESC LIMIT 8
            ) t) AS top_colors,
            (SELECT json_agg(row_to_json(t)) FROM (
                SELECT sizeeu AS name, COUNT(*) AS cnt
                FROM client_items
                WHERE sizeeu IS NOT NULL AND sizeeu <> ''
                GROUP BY sizeeu ORDER BY cnt DESC LIMIT 8
            ) t) AS top_sizes_eu
    """)
    prefs = db.execute(prefs_sql, {"client_id": client_id}).mappings().first() or {}
    result["top_brands"]    = prefs.get("top_brands") or []
    result["top_types"]     = prefs.get("top_types") or []
    result["top_colors"]    = prefs.get("top_colors") or []
    result["top_sizes_eu"]  = prefs.get("top_sizes_eu") or []

    # Розподіл оплат за всю історію
    pay_sql = text("""
        SELECT
            COUNT(*) FILTER (WHERE LOWER(COALESCE(ps.status_name,'')) LIKE 'оплачено%') AS paid,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(ps.status_name,'')) LIKE 'не оплачено%'
                              OR ps.status_name IS NULL) AS unpaid,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(ps.status_name,'')) LIKE 'частково%') AS partial,
            COUNT(*) AS total
        FROM orders o LEFT JOIN payment_statuses ps ON ps.id = o.payment_status_id
        WHERE o.client_id = :client_id
          AND COALESCE(o.order_status_id, 0) NOT IN (5, 6)
    """)
    pay = db.execute(pay_sql, {"client_id": client_id}).mappings().first() or {}
    result["payment_split"] = {
        "paid": pay.get("paid", 0) or 0,
        "unpaid": pay.get("unpaid", 0) or 0,
        "partial": pay.get("partial", 0) or 0,
        "total": pay.get("total", 0) or 0,
    }

    # ── Адресна книга ────────────────────────────────────────────────────
    addrs = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id
    ).order_by(
        ClientAddress.is_primary.desc(),
        ClientAddress.is_active.desc(),
        ClientAddress.usage_count.desc(),
        ClientAddress.id.desc(),
    ).all()
    result["addresses"] = [_addr_to_dict(a) for a in addrs]

    # ── Звʼязки (родичі/друзі/разом замовляють) ─────────────────────────
    rel_rows = db.execute(text("""
        SELECT cr.id, cr.client_id, cr.related_id, cr.relation_type, cr.label,
               cr.source, cr.confirmed, cr.notes,
               cr.created_at, cr.updated_at,
               c2.first_name, c2.last_name,
               COUNT(DISTINCT cro.order_id) AS joint_orders,
               MAX(o.id) FILTER (WHERE o.id IS NOT NULL) AS last_order_id,
               MAX(o.order_date) AS last_order_date
          FROM client_relations cr
          JOIN clients c2 ON c2.id = cr.related_id
          LEFT JOIN client_relation_orders cro ON cro.relation_id = cr.id
          LEFT JOIN orders o ON o.id = cro.order_id
         WHERE cr.client_id = :cid
         GROUP BY cr.id, c2.first_name, c2.last_name
         ORDER BY MAX(o.order_date) DESC NULLS LAST,
                  COUNT(DISTINCT cro.order_id) DESC,
                  cr.id ASC
    """), {"cid": client_id}).mappings().all()
    result["relations"] = [
        {
            "id": r["id"],
            "client_id": r["client_id"],
            "related_id": r["related_id"],
            "related_full_name": " ".join(filter(None, [r["first_name"], r["last_name"]])).strip() or None,
            "relation_type": r["relation_type"],
            "label": r["label"],
            "source": r["source"],
            "confirmed": bool(r["confirmed"]),
            "notes": r["notes"],
            "joint_orders": int(r["joint_orders"] or 0),
            "last_order_id": r["last_order_id"],
            "last_order_date": str(r["last_order_date"]) if r["last_order_date"] else None,
            "created_at": str(r["created_at"]) if r["created_at"] else None,
            "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
        }
        for r in rel_rows
    ]

    # ── Aliases (Step 4) ─────────────────────────────────────────────────
    alias_rows = db.execute(text("""
        SELECT id, client_id, first_name, last_name, nickname, full_raw,
               source, seen_count, first_seen_at, last_seen_at
          FROM client_aliases
         WHERE client_id = :cid
         ORDER BY seen_count DESC, last_seen_at DESC, id ASC
    """), {"cid": client_id}).mappings().all()
    result["aliases"] = [
        {
            "id": a["id"], "client_id": a["client_id"],
            "first_name": a["first_name"], "last_name": a["last_name"],
            "nickname": a["nickname"], "full_raw": a["full_raw"],
            "source": a["source"], "seen_count": int(a["seen_count"] or 1),
            "first_seen_at": a["first_seen_at"].isoformat() if a["first_seen_at"] else None,
            "last_seen_at":  a["last_seen_at"].isoformat()  if a["last_seen_at"]  else None,
        }
        for a in alias_rows
    ]

    # ── Flags + hydrated peer details ────────────────────────────────────
    flag_rows = db.execute(text("""
        SELECT id, client_id, flag_type, severity, peer_client_ids,
               details, dismissed, dismissed_at, dismissed_by, created_at
          FROM client_flags
         WHERE client_id = :cid AND dismissed = FALSE
         ORDER BY severity = 'error' DESC, severity = 'warn' DESC, created_at DESC
    """), {"cid": client_id}).mappings().all()

    # Зібрати усі унікальні peer_ids → один SELECT
    all_peers = set()
    for f in flag_rows:
        for pid in (f["peer_client_ids"] or []):
            all_peers.add(pid)
    peer_map = {}
    if all_peers:
        rows = db.execute(text("""
            SELECT id, first_name, last_name, nickname
              FROM clients WHERE id = ANY(:ids)
        """), {"ids": list(all_peers)}).mappings().all()
        for r in rows:
            peer_map[r["id"]] = {
                "id": r["id"],
                "full_name": " ".join(filter(None, [r["first_name"], r["last_name"]])).strip() or None,
                "nickname": r["nickname"],
            }

    result["flags"] = [
        {
            "id": f["id"], "client_id": f["client_id"],
            "flag_type": f["flag_type"], "severity": f["severity"],
            "peer_client_ids": list(f["peer_client_ids"] or []),
            "peer_clients": [peer_map[pid] for pid in (f["peer_client_ids"] or []) if pid in peer_map],
            "details": f["details"],
            "dismissed": bool(f["dismissed"]),
            "dismissed_at": f["dismissed_at"].isoformat() if f["dismissed_at"] else None,
            "dismissed_by": f["dismissed_by"],
            "created_at": f["created_at"].isoformat() if f["created_at"] else None,
        }
        for f in flag_rows
    ]
    result["has_active_flags"] = len(result["flags"]) > 0

    return result


# ── Адреси клієнта ────────────────────────────────────────────────────────
def _addr_to_dict(a: ClientAddress) -> dict:
    return {
        "id": a.id, "client_id": a.client_id,
        "label": a.label, "delivery_type": a.delivery_type,
        "recipient_name": a.recipient_name, "recipient_phone": a.recipient_phone,
        "city": a.city, "city_ref": a.city_ref, "region": a.region,
        "warehouse_number": a.warehouse_number, "warehouse_ref": a.warehouse_ref,
        "street": a.street, "building": a.building,
        "apartment": a.apartment, "postal_code": a.postal_code,
        "is_primary": bool(a.is_primary), "is_active": bool(a.is_active),
        "source": a.source, "source_order_id": a.source_order_id,
        "fingerprint": a.fingerprint, "usage_count": a.usage_count or 0,
        "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
        "notes": a.notes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _addr_fingerprint(payload: dict) -> str:
    """Стабільний md5 для дедупу адрес: тип+місто+відділення+вулиця+будинок+квартира."""
    parts = [
        (payload.get("delivery_type") or "").strip().lower(),
        (payload.get("city") or "").strip().lower(),
        (payload.get("warehouse_number") or "").strip(),
        (payload.get("street") or "").strip().lower(),
        (payload.get("building") or "").strip().lower(),
        (payload.get("apartment") or "").strip().lower(),
        (payload.get("postal_code") or "").strip(),
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def _ensure_single_primary(db: Session, client_id: int, except_id: Optional[int] = None):
    """Знімає is_primary з усіх інших адрес клієнта (для гарантії one-and-only-one)."""
    q = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id,
        ClientAddress.is_primary == True,  # noqa: E712
    )
    if except_id is not None:
        q = q.filter(ClientAddress.id != except_id)
    for other in q.all():
        other.is_primary = False


@router.get("/api/clients/{client_id}/addresses", tags=["clients"])
async def list_client_addresses(client_id: int, db: Session = Depends(get_db)):
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(404, "Client not found")
    addrs = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id
    ).order_by(
        ClientAddress.is_primary.desc(),
        ClientAddress.is_active.desc(),
        ClientAddress.usage_count.desc(),
        ClientAddress.id.desc(),
    ).all()
    return [_addr_to_dict(a) for a in addrs]


@router.post("/api/clients/{client_id}/addresses", tags=["clients"])
async def create_client_address(client_id: int, payload: ClientAddressCreate, db: Session = Depends(get_db)):
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(404, "Client not found")
    data = payload.dict()
    fp = _addr_fingerprint(data)
    # Дедуп: якщо вже є з тим самим fp у цього клієнта — повертаємо існуючу
    existing = db.query(ClientAddress).filter(
        ClientAddress.client_id == client_id,
        ClientAddress.fingerprint == fp,
    ).first()
    if existing:
        # Просто оновлюємо «м'які» поля
        for k in ("label", "recipient_name", "recipient_phone", "notes"):
            if data.get(k):
                setattr(existing, k, data[k])
        if data.get("is_primary"):
            _ensure_single_primary(db, client_id, except_id=existing.id)
            existing.is_primary = True
        existing.is_active = bool(data.get("is_active", True))
        db.commit()
        db.refresh(existing)
        return _addr_to_dict(existing)

    if data.get("is_primary"):
        _ensure_single_primary(db, client_id)
    addr = ClientAddress(
        client_id=client_id,
        source="manual",
        fingerprint=fp,
        **data,
    )
    db.add(addr)
    db.commit()
    db.refresh(addr)
    return _addr_to_dict(addr)


@router.put("/api/clients/{client_id}/addresses/{address_id}", tags=["clients"])
async def update_client_address(client_id: int, address_id: int, payload: ClientAddressUpdate, db: Session = Depends(get_db)):
    addr = db.query(ClientAddress).filter(
        ClientAddress.id == address_id,
        ClientAddress.client_id == client_id,
    ).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    data = payload.dict(exclude_unset=True)
    # Якщо примарність вмикають — спершу знімаємо в інших
    if data.get("is_primary") is True:
        _ensure_single_primary(db, client_id, except_id=addr.id)
    for k, v in data.items():
        setattr(addr, k, v)
    # Перерахувати fingerprint, якщо геополя змінилися
    geo_keys = {"delivery_type", "city", "warehouse_number", "street", "building", "apartment", "postal_code"}
    if geo_keys & set(data.keys()):
        addr.fingerprint = _addr_fingerprint({
            "delivery_type": addr.delivery_type, "city": addr.city,
            "warehouse_number": addr.warehouse_number, "street": addr.street,
            "building": addr.building, "apartment": addr.apartment,
            "postal_code": addr.postal_code,
        })
    db.commit()
    db.refresh(addr)
    return _addr_to_dict(addr)


@router.delete("/api/clients/{client_id}/addresses/{address_id}", tags=["clients"])
async def delete_client_address(client_id: int, address_id: int, db: Session = Depends(get_db)):
    addr = db.query(ClientAddress).filter(
        ClientAddress.id == address_id,
        ClientAddress.client_id == client_id,
    ).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    db.delete(addr)
    db.commit()
    return {"ok": True}


@router.post("/api/clients/{client_id}/addresses/{address_id}/set-primary", tags=["clients"])
async def set_primary_address(client_id: int, address_id: int, db: Session = Depends(get_db)):
    addr = db.query(ClientAddress).filter(
        ClientAddress.id == address_id,
        ClientAddress.client_id == client_id,
    ).first()
    if not addr:
        raise HTTPException(404, "Address not found")
    _ensure_single_primary(db, client_id, except_id=addr.id)
    addr.is_primary = True
    addr.is_active = True  # якщо був архівний — повертаємо
    db.commit()
    db.refresh(addr)
    return _addr_to_dict(addr)


@router.post("/api/clients/{client_id}/addresses/import-from-orders", tags=["clients"])
async def import_addresses_from_orders(client_id: int, db: Session = Depends(get_db)):
    """Підтягує всі адреси клієнта з історії його замовлень.
    Дедуплікує по fingerprint. Існуючі адреси не чіпає (тільки збільшує usage_count).
    """
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(404, "Client not found")

    rows = db.execute(text("""
        SELECT a.id AS addr_id, a.address_line1, a.address_line2, a.city, a.state,
               a.postal_code, a.recipient_name,
               COUNT(DISTINCT o.id) AS use_count,
               MAX(o.order_date) AS last_used,
               MAX(o.id) AS last_order_id,
               MAX(o.tracking_number) AS tracking_hint
        FROM orders o
        JOIN addresses a ON a.id = o.address_id
        WHERE o.client_id = :cid
        GROUP BY a.id, a.address_line1, a.address_line2, a.city, a.state,
                 a.postal_code, a.recipient_name
    """), {"cid": client_id}).mappings().all()

    imported = 0
    updated = 0
    skipped = 0

    import re as _re
    for r in rows:
        line1 = (r.get("address_line1") or "").strip()
        line2 = (r.get("address_line2") or "").strip()
        city = (r.get("city") or "").strip()

        # Евристика: якщо в рядку є "Відділення №42" або "Поштомат №..." → НП відділення
        wh_match = _re.search(r"(?:відділ[\w]*|поштомат)\s*[№#]?\s*(\d+)", (line1 + " " + line2), _re.IGNORECASE)
        if wh_match:
            delivery_type = "np_warehouse"
            warehouse_number = wh_match.group(1)
            street = None
            building = None
        elif line1:
            # Спроба вийняти "вул. X, буд. Y, кв. Z"
            delivery_type = "np_courier"
            warehouse_number = None
            street = line1
            building = None
            bm = _re.search(r"(?:буд[\w]*|будинок)\s*[№#]?\s*([\dА-Яа-я/-]+)", line1, _re.IGNORECASE)
            if bm:
                building = bm.group(1)
        else:
            delivery_type = "other"
            warehouse_number = None
            street = None
            building = None

        payload = {
            "delivery_type": delivery_type,
            "city": city or None,
            "warehouse_number": warehouse_number,
            "street": street,
            "building": building,
            "apartment": None,
            "postal_code": (r.get("postal_code") or None),
        }
        fp = _addr_fingerprint(payload)

        existing = db.query(ClientAddress).filter(
            ClientAddress.client_id == client_id,
            ClientAddress.fingerprint == fp,
        ).first()

        last_used = r.get("last_used")
        if isinstance(last_used, str):
            try:
                last_used = datetime.fromisoformat(last_used)
            except Exception:
                last_used = None

        if existing:
            # Оновлюємо лічильник + last_used якщо новіше
            existing.usage_count = max(existing.usage_count or 0, int(r.get("use_count") or 0))
            if last_used and (not existing.last_used_at or last_used > existing.last_used_at):
                existing.last_used_at = last_used
            updated += 1
        else:
            addr = ClientAddress(
                client_id=client_id,
                label=None,
                delivery_type=delivery_type,
                recipient_name=r.get("recipient_name"),
                city=city or None,
                warehouse_number=warehouse_number,
                street=street,
                building=building,
                postal_code=r.get("postal_code"),
                is_primary=False,
                is_active=True,
                source="imported_from_order",
                source_order_id=r.get("last_order_id"),
                fingerprint=fp,
                usage_count=int(r.get("use_count") or 0),
                last_used_at=last_used,
            )
            db.add(addr)
            imported += 1

    # Якщо primary порожній і є саме одна найчастіше використовувана адреса — НЕ ставимо
    # автоматично, просимо користувача підтвердити (smart-suggest).
    db.commit()
    return {"imported": imported, "updated": updated, "skipped": skipped}


# ── Звʼязки між клієнтами ("разом замовляють", родичі, друзі) ────────────
# Регекс — лише консервативний шаблон з реальних даних. UA + RU + Latin.
import re as _re_rel
_TOGETHER_RE = _re_rel.compile(
    r"разом\s+з(?:і|о)?\s+([A-Za-z\u0400-\u04FF\u00C0-\u017F][A-Za-z\u0400-\u04FF\u00C0-\u017F'\-]+(?:\s+[A-Za-z\u0400-\u04FF\u00C0-\u017F][A-Za-z\u0400-\u04FF\u00C0-\u017F'\-]+)?)",
    _re_rel.IGNORECASE,
)


def _extract_together_partner_ids(db: Session, notes: str, exclude_id: int) -> List[int]:
    """Зі строки нотаток повертає список client_id партнерів.
    Strict-guard: матчить тільки коли знайдено РІВНО 1 клієнта в БД.
    Self-references (== exclude_id) ігноруються.
    """
    if not notes:
        return []
    found: List[int] = []
    seen: set = set()
    for m in _TOGETHER_RE.finditer(notes):
        raw = m.group(1).strip().rstrip(",.;")
        if not raw or raw.lower() in seen:
            continue
        seen.add(raw.lower())
        rows = db.execute(text("""
            SELECT id FROM clients
             WHERE (COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')) ILIKE :q
                OR (COALESCE(last_name,'')  || ' ' || COALESCE(first_name,'')) ILIKE :q
             LIMIT 2
        """), {"q": f"%{raw}%"}).fetchall()
        if len(rows) != 1:
            continue
        partner_id = rows[0][0]
        if partner_id == exclude_id or partner_id in found:
            continue
        found.append(partner_id)
    return found


def _upsert_relation_pair(db: Session, a_id: int, b_id: int, order_id: Optional[int], source: str = "order_import") -> None:
    """Ідемпотентний апсерт: створює/знаходить два дзеркальні рядки A→B і B→A
    та (якщо order_id заданий) додає в junction. joint_orders читається з junction.
    """
    if a_id == b_id:
        return
    for x, y in ((a_id, b_id), (b_id, a_id)):
        # Get-or-create relation row
        rid = db.execute(text("""
            INSERT INTO client_relations (client_id, related_id, relation_type, source, confirmed)
            VALUES (:c, :r, 'together', :src, FALSE)
            ON CONFLICT (client_id, related_id) DO UPDATE SET updated_at = NOW()
            RETURNING id
        """), {"c": x, "r": y, "src": source}).scalar()
        if order_id and rid:
            db.execute(text("""
                INSERT INTO client_relation_orders (relation_id, order_id)
                VALUES (:rid, :oid)
                ON CONFLICT DO NOTHING
            """), {"rid": rid, "oid": order_id})


@router.get("/api/clients/{client_id}/relations", tags=["clients"])
async def list_client_relations(client_id: int, db: Session = Depends(get_db)):
    """Список звʼязків клієнта з агрегацією joint_orders + last_order."""
    rows = db.execute(text("""
        SELECT cr.id, cr.client_id, cr.related_id, cr.relation_type, cr.label,
               cr.source, cr.confirmed, cr.notes,
               cr.created_at, cr.updated_at,
               c2.first_name, c2.last_name,
               COUNT(DISTINCT cro.order_id) AS joint_orders,
               MAX(o.id) FILTER (WHERE o.id IS NOT NULL) AS last_order_id,
               MAX(o.order_date) AS last_order_date
          FROM client_relations cr
          JOIN clients c2 ON c2.id = cr.related_id
          LEFT JOIN client_relation_orders cro ON cro.relation_id = cr.id
          LEFT JOIN orders o ON o.id = cro.order_id
         WHERE cr.client_id = :cid
         GROUP BY cr.id, c2.first_name, c2.last_name
         ORDER BY MAX(o.order_date) DESC NULLS LAST,
                  COUNT(DISTINCT cro.order_id) DESC,
                  cr.id ASC
    """), {"cid": client_id}).mappings().all()
    return [
        {
            "id": r["id"],
            "client_id": r["client_id"],
            "related_id": r["related_id"],
            "related_full_name": " ".join(filter(None, [r["first_name"], r["last_name"]])).strip() or None,
            "relation_type": r["relation_type"],
            "label": r["label"],
            "source": r["source"],
            "confirmed": bool(r["confirmed"]),
            "notes": r["notes"],
            "joint_orders": int(r["joint_orders"] or 0),
            "last_order_id": r["last_order_id"],
            "last_order_date": str(r["last_order_date"]) if r["last_order_date"] else None,
            "created_at": str(r["created_at"]) if r["created_at"] else None,
            "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
        }
        for r in rows
    ]


@router.post("/api/clients/{client_id}/relations", tags=["clients"])
async def create_client_relation(client_id: int, payload: ClientRelationCreate, db: Session = Depends(get_db)):
    if client_id == payload.related_id:
        raise HTTPException(status_code=400, detail="Не можна повʼязати клієнта з самим собою")
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(status_code=404, detail="Client not found")
    if not db.query(Client).filter(Client.id == payload.related_id).first():
        raise HTTPException(status_code=404, detail="Related client not found")
    # Дзеркальний апсерт; обидва рядки вважаємо confirmed=true (це manual)
    for x, y in ((client_id, payload.related_id), (payload.related_id, client_id)):
        db.execute(text("""
            INSERT INTO client_relations (client_id, related_id, relation_type, label, source, confirmed, notes)
            VALUES (:c, :r, :t, :l, 'manual', TRUE, :n)
            ON CONFLICT (client_id, related_id) DO UPDATE
              SET relation_type = EXCLUDED.relation_type,
                  label         = COALESCE(EXCLUDED.label, client_relations.label),
                  confirmed     = TRUE,
                  notes         = COALESCE(EXCLUDED.notes, client_relations.notes),
                  updated_at    = NOW()
        """), {"c": x, "r": y, "t": payload.relation_type, "l": payload.label, "n": payload.notes})
    db.commit()
    return {"ok": True}


@router.put("/api/clients/{client_id}/relations/{relation_id}", tags=["clients"])
async def update_client_relation(client_id: int, relation_id: int, payload: ClientRelationUpdate, db: Session = Depends(get_db)):
    rel = db.query(ClientRelation).filter(
        ClientRelation.id == relation_id, ClientRelation.client_id == client_id
    ).first()
    if not rel:
        raise HTTPException(status_code=404, detail="Relation not found")
    upd = payload.dict(exclude_unset=True)
    for k, v in upd.items():
        setattr(rel, k, v)
    rel.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(rel)
    return {"ok": True, "id": rel.id}


@router.delete("/api/clients/{client_id}/relations/{relation_id}", tags=["clients"])
async def delete_client_relation(
    client_id: int, relation_id: int,
    both: bool = Query(True, description="Видалити дзеркальний звʼязок теж"),
    db: Session = Depends(get_db),
):
    rel = db.query(ClientRelation).filter(
        ClientRelation.id == relation_id, ClientRelation.client_id == client_id
    ).first()
    if not rel:
        raise HTTPException(status_code=404, detail="Relation not found")
    related_id = rel.related_id
    db.delete(rel)
    if both:
        mirror = db.query(ClientRelation).filter(
            ClientRelation.client_id == related_id, ClientRelation.related_id == client_id
        ).first()
        if mirror:
            db.delete(mirror)
    db.commit()
    return {"ok": True}


@router.post("/api/clients/{client_id}/relations/import-from-orders", tags=["clients"])
async def import_relations_from_orders(client_id: int, db: Session = Depends(get_db)):
    """Сканує всі замовлення цього клієнта на 'разом з <Name>' і апсертить звʼязки.
    Idempotent: повторний виклик не дублює.
    """
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(status_code=404, detail="Client not found")
    rows = db.execute(text("""
        SELECT id, notes, order_date FROM orders
         WHERE client_id = :cid AND notes ILIKE '%разом з%'
    """), {"cid": client_id}).fetchall()
    created_pairs = 0
    linked_orders = 0
    for r in rows:
        partner_ids = _extract_together_partner_ids(db, r.notes or "", exclude_id=client_id)
        for pid in partner_ids:
            _upsert_relation_pair(db, client_id, pid, r.id, source="order_import")
            created_pairs += 1
            linked_orders += 1
    db.commit()
    return {"ok": True, "matches": linked_orders, "pairs_processed": created_pairs}


@router.post("/api/clients/relations/backfill-all", tags=["clients"])
async def backfill_all_relations(db: Session = Depends(get_db)):
    """Одноразовий backfill по всій історії замовлень. Idempotent."""
    rows = db.execute(text("""
        SELECT id, client_id, notes FROM orders
         WHERE notes ILIKE '%разом з%' AND client_id IS NOT NULL
    """)).fetchall()
    matches = 0
    for r in rows:
        partner_ids = _extract_together_partner_ids(db, r.notes or "", exclude_id=r.client_id)
        for pid in partner_ids:
            _upsert_relation_pair(db, r.client_id, pid, r.id, source="order_import")
            matches += 1
    db.commit()
    return {"ok": True, "orders_scanned": len(rows), "matches": matches}


@router.post("/api/clients", response_model=ClientSchema, tags=["clients"])
async def create_client(client: ClientCreate, db: Session = Depends(get_db)):
    """
    Create a new client
    """
    # Validate gender if provided
    if client.gender_id:
        gender = db.query(Gender).filter(Gender.id == client.gender_id).first()
        if not gender:
            raise HTTPException(status_code=404, detail="Gender not found")
    
    # Create new client + compute normalized signals up-front so the partial
    # UNIQUE indexes catch any duplicate insert attempt as IntegrityError.
    db_client = Client(**client.dict())
    _apply_normalized(db_client)
    db.add(db_client)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        # Try to surface the existing client to the caller
        for col, val in (
            ('phone_normalized', db_client.phone_normalized),
            ('facebook_normalized', db_client.facebook_normalized),
            ('telegram_normalized', db_client.telegram_normalized),
            ('instagram_normalized', db_client.instagram_normalized),
        ):
            if val:
                existing = db.query(Client).filter(getattr(Client, col) == val).first()
                if existing:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Client with same {col.replace('_normalized','')} already exists (id={existing.id})",
                    )
        raise HTTPException(status_code=500, detail=f"Could not create client: {e}")
    db.refresh(db_client)
    
    # Add full_name field
    client_dict = db_client.__dict__.copy()
    client_dict["full_name"] = f"{db_client.first_name} {db_client.last_name}"
    
    return client_dict

@router.put("/api/clients/{client_id}", response_model=ClientSchema, tags=["clients"])
async def update_client(client_id: int, client: ClientUpdate, db: Session = Depends(get_db)):
    """
    Update an existing client.
    Identity-lock (Step 4):
      • Якщо змінюється first/last/middle/nickname або strong-signal (phone/fb/tg/ig),
        то перед апдейтом зберігаємо поточну name-комбінацію як alias
        (історія імен) і ставимо manually_edited_at + manually_edited_fields.
      • Парсер після цього не перезатре локнуті поля.
    """
    db_client = db.query(Client).filter(Client.id == client_id).first()
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Validate gender if provided
    if client.gender_id:
        gender = db.query(Gender).filter(Gender.id == client.gender_id).first()
        if not gender:
            raise HTTPException(status_code=404, detail="Gender not found")

    update_data = client.dict(exclude_unset=True)

    # Визначаємо які поля реально міняються (різниця зі старим значенням)
    changed_fields = set()
    for key, new_value in update_data.items():
        old_value = getattr(db_client, key, None)
        # Нормалізуємо None vs "" як однакове
        a = (old_value or "") if isinstance(old_value, (str, type(None))) else old_value
        b = (new_value or "") if isinstance(new_value, (str, type(None))) else new_value
        if a != b:
            changed_fields.add(key)

    # Якщо чіпається ім'я/нікнейм — зберігаємо поточну комбінацію як alias
    name_touched = bool(changed_fields & _NAME_LIKE_FIELDS)
    if name_touched:
        try:
            _save_alias_from_client(db, db_client, source="manual_edit_history")
        except Exception as _e:
            logger.warning("could not save alias snapshot for client %s: %s", client_id, _e)

    # Apply changes
    for key, value in update_data.items():
        setattr(db_client, key, value)

    # Re-compute normalized signals if any source field was touched.
    if changed_fields & {"phone_number", "facebook", "instagram", "telegram"}:
        _apply_normalized(db_client)

    # Лок-список (CSV) — об'єднуємо зі старим
    if changed_fields:
        existing_locked = set()
        if db_client.manually_edited_fields:
            existing_locked = {x.strip() for x in db_client.manually_edited_fields.split(",") if x.strip()}
        new_locked = sorted(existing_locked | changed_fields)
        db_client.manually_edited_fields = ",".join(new_locked)
        db_client.manually_edited_at = datetime.utcnow()

    db.commit()
    db.refresh(db_client)

    # Якщо ми змінили ім'я — додаємо НОВУ комбінацію теж як alias (щоб
    # парсер бачив і нове, і старе ім'я як шляхи до цього клієнта).
    if name_touched:
        try:
            _save_alias_from_client(db, db_client, source="manual_edit_history")
            db.commit()
        except Exception as _e:
            logger.warning("could not save post-edit alias for client %s: %s", client_id, _e)
            db.rollback()

    client_dict = db_client.__dict__.copy()
    client_dict["full_name"] = f"{db_client.first_name or ''} {db_client.last_name or ''}".strip()

    return client_dict

@router.delete("/api/clients/{client_id}", tags=["clients"])
async def delete_client(client_id: int, db: Session = Depends(get_db)):
    """
    Delete a client
    """
    db_client = db.query(Client).filter(Client.id == client_id).first()
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")

    db.delete(db_client)
    db.commit()
    return {"message": "Client deleted successfully"}


# ── Aliases endpoints (Step 4) ────────────────────────────────────────────
@router.get("/api/clients/{client_id}/aliases", tags=["clients"])
async def list_client_aliases(client_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, client_id, first_name, last_name, nickname, full_raw,
               source, seen_count, first_seen_at, last_seen_at
          FROM client_aliases WHERE client_id = :cid
         ORDER BY seen_count DESC, last_seen_at DESC, id ASC
    """), {"cid": client_id}).mappings().all()
    return [
        {
            "id": r["id"], "client_id": r["client_id"],
            "first_name": r["first_name"], "last_name": r["last_name"],
            "nickname": r["nickname"], "full_raw": r["full_raw"],
            "source": r["source"], "seen_count": int(r["seen_count"] or 1),
            "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            "last_seen_at":  r["last_seen_at"].isoformat()  if r["last_seen_at"]  else None,
        }
        for r in rows
    ]


@router.post("/api/clients/{client_id}/aliases", tags=["clients"])
async def add_client_alias(client_id: int, payload: ClientAliasCreate, db: Session = Depends(get_db)):
    """Ручне додавання alias (наприклад: «також відома як ...»)."""
    if not db.query(Client).filter(Client.id == client_id).first():
        raise HTTPException(status_code=404, detail="Client not found")
    f = (payload.first_name or "").strip()
    l = (payload.last_name or "").strip()
    n = (payload.nickname or "").strip()
    if not (f or l or n):
        raise HTTPException(status_code=400, detail="At least one of first_name/last_name/nickname required")
    key = _norm_alias_key(f, l, n)
    raw = payload.full_raw or (f + (" " + l if l else "") + (f" ({n})" if n else "")).strip()
    db.execute(text("""
        INSERT INTO client_aliases
            (client_id, first_name, last_name, nickname, full_raw,
             norm_key, source, seen_count, first_seen_at, last_seen_at)
        VALUES (:cid, :f, :l, :n, :raw, :k, 'manual_edit_history', 1, NOW(), NOW())
        ON CONFLICT (client_id, norm_key) DO UPDATE
            SET last_seen_at = NOW(),
                full_raw = COALESCE(EXCLUDED.full_raw, client_aliases.full_raw)
    """), {"cid": client_id, "f": f or None, "l": l or None, "n": n or None,
           "raw": raw or None, "k": key})
    db.commit()
    return {"ok": True}


@router.delete("/api/clients/{client_id}/aliases/{alias_id}", tags=["clients"])
async def delete_client_alias(client_id: int, alias_id: int, db: Session = Depends(get_db)):
    res = db.execute(text(
        "DELETE FROM client_aliases WHERE id = :aid AND client_id = :cid"
    ), {"aid": alias_id, "cid": client_id})
    db.commit()
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"ok": True}


# ── Flags endpoints (Step 4) ──────────────────────────────────────────────
@router.get("/api/clients/{client_id}/flags", tags=["clients"])
async def list_client_flags(client_id: int, include_dismissed: bool = False, db: Session = Depends(get_db)):
    where = "client_id = :cid" + ("" if include_dismissed else " AND dismissed = FALSE")
    rows = db.execute(text(f"""
        SELECT id, client_id, flag_type, severity, peer_client_ids,
               details, dismissed, dismissed_at, dismissed_by, created_at
          FROM client_flags WHERE {where}
         ORDER BY dismissed ASC, created_at DESC
    """), {"cid": client_id}).mappings().all()
    return [
        {
            "id": r["id"], "client_id": r["client_id"],
            "flag_type": r["flag_type"], "severity": r["severity"],
            "peer_client_ids": list(r["peer_client_ids"] or []),
            "details": r["details"],
            "dismissed": bool(r["dismissed"]),
            "dismissed_at": r["dismissed_at"].isoformat() if r["dismissed_at"] else None,
            "dismissed_by": r["dismissed_by"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@router.post("/api/clients/{client_id}/flags/{flag_id}/dismiss", tags=["clients"])
async def dismiss_client_flag(client_id: int, flag_id: int,
                              payload: ClientFlagDismiss = None,
                              db: Session = Depends(get_db)):
    """«Це різні люди» / «Я перевірив, все ок» — гасить flag."""
    note = (payload.note if payload else None) or "manual_dismiss"
    res = db.execute(text("""
        UPDATE client_flags
           SET dismissed = TRUE, dismissed_at = NOW(), dismissed_by = :who
         WHERE id = :fid AND client_id = :cid
    """), {"fid": flag_id, "cid": client_id, "who": note[:100]})
    db.commit()
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"ok": True}


# ── Merge clients (Step 4) ────────────────────────────────────────────────
@router.post("/api/clients/{source_id}/merge", tags=["clients"])
async def merge_clients(source_id: int, payload: ClientMergeRequest, db: Session = Depends(get_db)):
    """Об'єднати source_id у target_id.
    target залишається; source: orders/addresses/relations переносяться, потім
    source видаляється; alias-історія source копіюється до target. Створюється
    flag 'merged_into' для аудиту.
    """
    target_id = payload.target_id
    if source_id == target_id:
        raise HTTPException(status_code=400, detail="source and target must differ")
    source = db.query(Client).filter(Client.id == source_id).first()
    target = db.query(Client).filter(Client.id == target_id).first()
    if not source or not target:
        raise HTTPException(status_code=404, detail="Client not found")

    moved = {}

    # 1) Перенести orders → target
    res = db.execute(text("UPDATE orders SET client_id = :t WHERE client_id = :s"),
                     {"t": target_id, "s": source_id})
    moved["orders"] = res.rowcount

    # 2) Перенести addresses (унікальність по fingerprint всередині target)
    addr_rows = db.execute(text("""
        SELECT id, fingerprint FROM client_addresses WHERE client_id = :s
    """), {"s": source_id}).fetchall()
    moved_addr = 0
    for arow in addr_rows:
        # Якщо у target вже є з таким fingerprint — видалити дубль із source
        exists = db.execute(text("""
            SELECT 1 FROM client_addresses
             WHERE client_id = :t AND fingerprint = :fp LIMIT 1
        """), {"t": target_id, "fp": arow.fingerprint}).first()
        if exists:
            db.execute(text("DELETE FROM client_addresses WHERE id = :id"), {"id": arow.id})
        else:
            db.execute(text("UPDATE client_addresses SET client_id = :t WHERE id = :id"),
                       {"t": target_id, "id": arow.id})
            moved_addr += 1
    moved["addresses"] = moved_addr

    # 3) Перенести relations (з обох боків) — уникаємо self-loops і дублів пар
    rel_a = db.execute(text("""
        UPDATE client_relations
           SET client_id = :t
         WHERE client_id = :s AND related_id <> :t
           AND NOT EXISTS (
               SELECT 1 FROM client_relations cr2
                WHERE cr2.client_id = :t AND cr2.related_id = client_relations.related_id
           )
    """), {"t": target_id, "s": source_id})
    rel_b = db.execute(text("""
        UPDATE client_relations
           SET related_id = :t
         WHERE related_id = :s AND client_id <> :t
           AND NOT EXISTS (
               SELECT 1 FROM client_relations cr2
                WHERE cr2.client_id = client_relations.client_id AND cr2.related_id = :t
           )
    """), {"t": target_id, "s": source_id})
    # Решту (дублі/self) — просто видалити
    db.execute(text("DELETE FROM client_relations WHERE client_id = :s OR related_id = :s"),
               {"s": source_id})
    moved["relations"] = (rel_a.rowcount or 0) + (rel_b.rowcount or 0)

    # 4) Перенести aliases (idempotent через unique norm_key)
    alias_rows = db.execute(text("""
        SELECT first_name, last_name, nickname, full_raw, norm_key, seen_count
          FROM client_aliases WHERE client_id = :s
    """), {"s": source_id}).fetchall()
    for ar in alias_rows:
        db.execute(text("""
            INSERT INTO client_aliases
                (client_id, first_name, last_name, nickname, full_raw,
                 norm_key, source, seen_count, first_seen_at, last_seen_at)
            VALUES (:cid, :f, :l, :n, :raw, :k, 'merge', :sc, NOW(), NOW())
            ON CONFLICT (client_id, norm_key) DO UPDATE
                SET seen_count = client_aliases.seen_count + EXCLUDED.seen_count,
                    last_seen_at = NOW(),
                    full_raw = COALESCE(EXCLUDED.full_raw, client_aliases.full_raw)
        """), {"cid": target_id, "f": ar.first_name, "l": ar.last_name,
               "n": ar.nickname, "raw": ar.full_raw, "k": ar.norm_key,
               "sc": ar.seen_count})
    moved["aliases"] = len(alias_rows)

    # 5) Загасити usually-spurious flags для пари (вони вже вирішені)
    db.execute(text("""
        UPDATE client_flags SET dismissed = TRUE, dismissed_at = NOW(),
               dismissed_by = 'merge'
         WHERE client_id = :t
           AND flag_type IN ('possible_duplicate', 'phone_mismatch_with_alias',
                             'ambiguous_name_at_parse')
           AND :s = ANY(peer_client_ids)
    """), {"t": target_id, "s": source_id})

    # 6) Створити аудит-flag merged_into на target
    db.execute(text("""
        INSERT INTO client_flags
            (client_id, flag_type, severity, peer_client_ids, details, dismissed, created_at)
        VALUES (:cid, 'merged_into', 'info', :peers, :det, TRUE, NOW())
        ON CONFLICT DO NOTHING
    """), {"cid": target_id, "peers": [source_id],
           "det": f"Merged client #{source_id} into #{target_id}"})

    # 7) Видалити source клієнта
    db.delete(source)
    db.commit()
    return {"ok": True, "moved": moved}


# ── Mass-merge: groups of likely duplicates (Step 5) ──────────────────────
@router.get("/api/clients/duplicate-groups", tags=["clients"])
async def list_duplicate_groups(
    by: str = Query("auto", description="auto|name|phone|facebook|instagram|telegram"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Returns groups of clients that appear to be duplicates.

    'auto' = union of all signal-based + name-based groupings.
    Each group includes per-client metadata (orders, signals, score) so the
    frontend can show a one-click bulk-merge UI.
    """
    sql_parts = []
    if by in ("auto", "phone"):
        sql_parts.append(("phone", "phone_normalized"))
    if by in ("auto", "facebook"):
        sql_parts.append(("facebook", "facebook_normalized"))
    if by in ("auto", "instagram"):
        sql_parts.append(("instagram", "instagram_normalized"))
    if by in ("auto", "telegram"):
        sql_parts.append(("telegram", "telegram_normalized"))

    groups = []
    seen_clusters = set()  # avoid duplicate clusters (same set of ids reported twice)

    for signal_label, col in sql_parts:
        rows = db.execute(text(f"""
            SELECT {col} AS key, array_agg(id ORDER BY id) AS ids
            FROM clients
            WHERE {col} IS NOT NULL
            GROUP BY {col}
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT :lim
        """), {"lim": limit}).fetchall()
        for r in rows:
            ids = list(r[1])
            cluster_key = tuple(sorted(ids))
            if cluster_key in seen_clusters:
                continue
            seen_clusters.add(cluster_key)
            groups.append({"signal": signal_label, "key": r[0], "client_ids": ids})

    if by in ("auto", "name"):
        rows = db.execute(text("""
            SELECT ca.norm_key AS key, array_agg(DISTINCT ca.client_id) AS ids
            FROM client_aliases ca
            WHERE ca.norm_key IS NOT NULL AND ca.norm_key <> '||'
            GROUP BY ca.norm_key
            HAVING COUNT(DISTINCT ca.client_id) > 1
            ORDER BY COUNT(DISTINCT ca.client_id) DESC
            LIMIT :lim
        """), {"lim": limit}).fetchall()
        for r in rows:
            ids = list(r[1])
            cluster_key = tuple(sorted(ids))
            if cluster_key in seen_clusters:
                continue
            seen_clusters.add(cluster_key)
            groups.append({"signal": "name", "key": r[0], "client_ids": ids})

    # Hydrate client info for each group
    all_ids = sorted({i for g in groups for i in g["client_ids"]})
    if not all_ids:
        return {"groups": [], "total_clusters": 0, "total_clients": 0}

    info_rows = db.execute(text("""
        SELECT c.id,
               COALESCE(NULLIF(TRIM(COALESCE(c.first_name,'') || ' ' || COALESCE(c.last_name,'')),''), c.nickname, '—') AS full_name,
               c.phone_number, c.facebook, c.telegram, c.instagram, c.email,
               c.manually_edited_at IS NOT NULL AS is_locked,
               (SELECT COUNT(*) FROM orders o WHERE o.client_id = c.id) AS orders_count,
               (SELECT COALESCE(SUM(o.total_amount),0) FROM orders o WHERE o.client_id = c.id) AS total_amount,
               c.created_at
        FROM clients c WHERE c.id = ANY(:ids)
    """), {"ids": all_ids}).mappings().all()
    by_id = {r["id"]: dict(r) for r in info_rows}

    def _score(c):
        sig = sum(1 for k in ("phone_number","facebook","telegram","instagram","email") if c.get(k))
        return (c.get("orders_count") or 0) * 100 + sig * 10 + (1 if c.get("is_locked") else 0)

    out_groups = []
    for g in groups:
        clients = [by_id[i] for i in g["client_ids"] if i in by_id]
        if len(clients) < 2:
            continue
        clients.sort(key=_score, reverse=True)
        suggested = clients[0]["id"]
        out_groups.append({
            "signal": g["signal"],
            "key": str(g["key"])[:120],
            "suggested_master_id": suggested,
            "clients": [
                {
                    "id": c["id"],
                    "full_name": c["full_name"],
                    "phone": c.get("phone_number"),
                    "facebook": c.get("facebook"),
                    "telegram": c.get("telegram"),
                    "instagram": c.get("instagram"),
                    "email": c.get("email"),
                    "is_locked": c.get("is_locked"),
                    "orders_count": c.get("orders_count"),
                    "total_amount": float(c.get("total_amount") or 0),
                    "is_suggested_master": c["id"] == suggested,
                } for c in clients
            ],
        })

    return {
        "groups": out_groups,
        "total_clusters": len(out_groups),
        "total_clients": sum(len(g["clients"]) for g in out_groups),
    }


@router.post("/api/clients/merge-bulk", tags=["clients"])
async def merge_clients_bulk(payload: dict, db: Session = Depends(get_db)):
    """Bulk merge: payload = { groups: [{ master_id, source_ids: [int,...] }, ...] }.

    For each group, calls merge_clients(source → master) sequentially.
    Returns per-group status.
    """
    groups = (payload or {}).get("groups") or []
    if not isinstance(groups, list) or not groups:
        raise HTTPException(status_code=400, detail="payload.groups must be non-empty list")

    results = []
    total_merged = 0
    for g in groups:
        master_id = g.get("master_id")
        source_ids = g.get("source_ids") or []
        if not isinstance(master_id, int) or not isinstance(source_ids, list):
            results.append({"master_id": master_id, "ok": False, "error": "bad payload"})
            continue
        merged_here = 0
        errors = []
        for sid in source_ids:
            if not isinstance(sid, int) or sid == master_id:
                continue
            try:
                # Re-use existing single-merge function via direct call
                req = ClientMergeRequest(target_id=master_id)
                await merge_clients(sid, req, db)
                merged_here += 1
                total_merged += 1
            except HTTPException as he:
                errors.append({"source_id": sid, "error": he.detail})
            except Exception as e:
                errors.append({"source_id": sid, "error": str(e)})
        results.append({"master_id": master_id, "merged": merged_here, "errors": errors})

    return {"ok": True, "total_merged": total_merged, "groups": results}

"""Добір товару для регулярних Stories: читає, але нічого не публікує.

Відмінність від Top-9 у критерії. Підбірка бере найпопулярніше за переглядами
й продажами; Stories беруть те, що підпадає під заданий людиною фільтр
(«жіночі босоніжки», «усе HOKA»), і крутять цей пул по колу.

Порядок ротації навмисний: спершу те, чого у Stories ще не показували жодного
разу — новіші завози попереду, — а вже потім те, що показували найдавніше. Так
пул обходиться цілком, без випадковості, яку неможливо відтворити при розборі
«а чому вийшло саме це».

Модуль не імпортує жодного публікатора, R2 чи диспетчера.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from backend.utils.order_status_logic import (
        PAID_STATUS_ID, STATUS_CONFIRMED, STATUS_GIFT, STATUS_RETURNED,
    )
except ImportError:
    from utils.order_status_logic import (
        PAID_STATUS_ID, STATUS_CONFIRMED, STATUS_GIFT, STATUS_RETURNED,
    )

DEFAULT_INTERVAL_HOURS = 24
DEFAULT_COOLDOWN_DAYS = 30
DEFAULT_LOCAL_TIME = "11:00"
DEFAULT_TIMEZONE = "Europe/Kyiv"
PLATFORMS = ("instagram", "facebook")
# Скільки кандидатів тягнемо з БД під одну Story. Фото не індексовані в базі,
# тож придатність знімків перевіряється вже в Python партіями — запас потрібен
# на випадок, коли в голови черги фото не виявиться.
CANDIDATE_POOL = 200
RESERVE_COUNT = 5

# Підмножина фільтрів «Товарів», яка має сенс для добору Stories. Свідомо без
# `search`, `only_problematic` тощо: це критерій вітрини, а не пошуку по базі.
LIST_FILTERS = {
    "brandids": "p.brandid = ANY(:brandids)",
    "typeids": "p.typeid = ANY(:typeids)",
    "subtypeids": "p.subtypeid = ANY(:subtypeids)",
    "genderids": "p.genderid = ANY(:genderids)",
    "colorids": "p.colorid = ANY(:colorids)",
    "styleids": "p.styleid = ANY(:styleids)",
    "conditionids": "p.conditionid = ANY(:conditionids)",
}
RANGE_FILTERS = {
    "min_price": "COALESCE(p.price,0) >= :min_price",
    "max_price": "COALESCE(p.price,0) <= :max_price",
    "min_sizeeu": "NULLIF(regexp_replace(COALESCE(p.sizeeu,''),'[^0-9.]','','g'),'')::numeric >= :min_sizeeu",
    "max_sizeeu": "NULLIF(regexp_replace(COALESCE(p.sizeeu,''),'[^0-9.]','','g'),'')::numeric <= :max_sizeeu",
}


def normalize_filters(raw: Any) -> Dict[str, Any]:
    """Лишити тільки відомі критерії й привести їх до безпечних типів.

    Фільтри приходять із браузера і йдуть у SQL, тому сюди не має пролізти
    нічого, крім переліченого вище: невідомий ключ мовчки відкидається.
    """
    source = raw if isinstance(raw, dict) else {}
    clean: Dict[str, Any] = {}
    for key in LIST_FILTERS:
        values = source.get(key)
        if isinstance(values, list):
            ids = [int(value) for value in values if str(value).strip().lstrip("-").isdigit()]
            if ids:
                clean[key] = sorted(set(ids))
    for key in RANGE_FILTERS:
        value = source.get(key)
        if value is None or value == "":
            continue
        try:
            clean[key] = float(value)
        except (TypeError, ValueError):
            continue
    seasons = source.get("seasons")
    if isinstance(seasons, list):
        names = [str(value).strip() for value in seasons if str(value or "").strip()]
        if names:
            clean["seasons"] = sorted(set(names))
    return clean


def describe_filters(db: Session, filters: Dict[str, Any]) -> str:
    """Людський опис добору для журналу й підказки в інтерфейсі."""
    parts: List[str] = []

    def names(table: str, ids: Sequence[int], column: str) -> List[str]:
        if not ids:
            return []
        rows = db.execute(
            text(f"SELECT {column} FROM {table} WHERE id = ANY(:ids) ORDER BY {column}"),
            {"ids": list(ids)},
        ).scalars().all()
        return [str(value) for value in rows if value]

    for key, table, column, label in (
        ("brandids", "brands", "brandname", "бренд"),
        ("typeids", "types", "typename", "тип"),
        ("subtypeids", "subtypes", "subtypename", "підтип"),
        ("genderids", "genders", "gendername", "стать"),
        ("colorids", "colors", "colorname", "колір"),
        ("styleids", "styles", "stylename", "стиль"),
        ("conditionids", "conditions", "conditionname", "стан"),
    ):
        values = names(table, filters.get(key) or [], column)
        if values:
            parts.append(f"{label}: {', '.join(values[:4])}" + (" …" if len(values) > 4 else ""))
    if filters.get("seasons"):
        parts.append("сезон: " + ", ".join(filters["seasons"][:4]))
    price_low, price_high = filters.get("min_price"), filters.get("max_price")
    if price_low or price_high:
        parts.append(f"ціна: {price_low or 0:.0f}–{price_high or 0:.0f} грн")
    size_low, size_high = filters.get("min_sizeeu"), filters.get("max_sizeeu")
    if size_low or size_high:
        parts.append(f"розмір: {size_low or 0:g}–{size_high or 0:g}")
    return " · ".join(parts) if parts else "усі доступні товари"


def candidate_rows(
    db: Session,
    filters: Dict[str, Any],
    cooldown_days: int,
    *,
    pool: int = CANDIDATE_POOL,
) -> List[Dict[str, Any]]:
    """Придатні товари в порядку ротації. Тільки читання."""
    conditions: List[str] = []
    params: Dict[str, Any] = {
        "cooldown_days": int(cooldown_days),
        "pool": max(1, min(int(pool), 2000)),
    }
    for key, clause in {**LIST_FILTERS, **RANGE_FILTERS}.items():
        if key in filters:
            conditions.append(clause)
            params[key] = filters[key]
    if filters.get("seasons"):
        conditions.append(
            "string_to_array(regexp_replace(COALESCE(p.season, ''), "
            "'\\s*,\\s*', ',', 'g'), ',') && :seasons_arr"
        )
        params["seasons_arr"] = filters["seasons"]
    filter_sql = ("AND " + " AND ".join(conditions)) if conditions else ""

    rows = db.execute(text(f"""
        WITH sold AS (
            SELECT oi.product_id,
                   COALESCE(SUM(oi.quantity) FILTER (
                       WHERE o.order_status_id={STATUS_GIFT}
                          OR (o.order_status_id={STATUS_CONFIRMED}
                              AND o.payment_status_id={PAID_STATUS_ID})
                   ),0)::int AS sold_count
            FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id IN ({STATUS_CONFIRMED}, {STATUS_GIFT}, {STATUS_RETURNED})
            GROUP BY oi.product_id
        ), shown AS (
            -- Коли цей номер востаннє був у Stories, на будь-якому майданчику.
            -- Обидві мережі рахуються разом: покупець бачить одну крамницю.
            SELECT productnumber, MAX(occurred_at) AS last_story_at
            FROM (
                SELECT product_number AS productnumber,
                       COALESCE(published_at, scheduled_at, created_at) AS occurred_at
                FROM instagram_publications
                WHERE media_type='story' AND status NOT IN ('failed','error','cancelled')
                UNION ALL
                SELECT product_number AS productnumber,
                       COALESCE(published_at, scheduled_at, created_at) AS occurred_at
                FROM facebook_publications
                WHERE media_type='story' AND status NOT IN ('failed','error','cancelled')
                UNION ALL
                -- Затверджені чернетки теж займають товар: інакше два слоти
                -- поспіль могли б обрати те саме, поки диспетчер ще не відзвітував.
                SELECT productnumber,
                       scheduled_for AS occurred_at
                FROM story_automation_drafts
                WHERE status IN ('awaiting_review','approved')
            ) events
            WHERE productnumber IS NOT NULL
            GROUP BY productnumber
        )
        SELECT p.id AS product_id, p.productnumber, p.price::float AS price,
               p.dateadded, p.sizeeu, p.season,
               b.brandname AS brand, t.typename AS type, g.gendername AS gender,
               p.model, p.official_photos_from,
               shown.last_story_at
        FROM products p
        LEFT JOIN brands b ON b.id=p.brandid
        LEFT JOIN types t ON t.id=p.typeid
        LEFT JOIN genders g ON g.id=p.genderid
        LEFT JOIN statuses s ON s.id=p.statusid
        LEFT JOIN sold ON sold.product_id=p.id
        LEFT JOIN shown ON shown.productnumber=p.productnumber
        WHERE GREATEST(COALESCE(p.quantity,0)-COALESCE(sold.sold_count,0),0) > 0
          AND (s.statusname IS NULL OR s.statusname NOT IN ('Продано','Подаровано','Повернуто'))
          AND COALESCE(p.is_lost,FALSE)=FALSE
          AND p.productnumber IS NOT NULL AND BTRIM(p.productnumber) <> ''
          AND p.productnumber <> '???'
          AND p.productnumber NOT LIKE '???\\_%'
          AND p.productnumber NOT LIKE '\\_\\_tmp\\_rename\\_%'
          AND COALESCE(p.price,0) > 0
          AND (
              shown.last_story_at IS NULL
              OR shown.last_story_at <= now() - (:cooldown_days || ' days')::interval
          )
          {filter_sql}
        ORDER BY shown.last_story_at NULLS FIRST, p.dateadded DESC NULLS LAST, p.id
        LIMIT :pool
    """), params).mappings().all()
    return [dict(row) for row in rows]


def _photo_ready(bms: Dict[str, Any]) -> bool:
    try:
        from services import telegram_publisher as tg
    except ImportError:
        from backend.services import telegram_publisher as tg
    photos, _kind = tg._photo_entries(bms)
    return bool(photos)


def select_for_slot(
    db: Session,
    *,
    filters: Dict[str, Any],
    cooldown_days: int,
    reserve_count: int = RESERVE_COUNT,
) -> Dict[str, Any]:
    """Головний товар для Story плюс запас. Нічого не пише й не публікує."""
    candidates = candidate_rows(db, filters, cooldown_days)
    chosen: List[Dict[str, Any]] = []
    no_photo = 0
    # Фото лежать на диску, не в базі, тож перевіряємо їх партіями й спиняємось
    # щойно набрали головний товар із запасом — а не проходимо весь пул.
    for row in candidates:
        if _photo_ready(row):
            chosen.append(row)
            if len(chosen) > reserve_count:
                break
        else:
            no_photo += 1

    warnings: List[str] = []
    if no_photo:
        warnings.append(f"Без придатного фото пропущено {no_photo} товарів.")
    if not chosen:
        warnings.append("Під цей добір немає жодного товару з фото.")
    elif len(chosen) == 1:
        warnings.append("Запасних товарів немає: пул добору майже вичерпано.")

    return {
        "ok": bool(chosen),
        "selected": chosen[0] if chosen else None,
        "reserves": chosen[1:],
        "eligible_pool": len(candidates),
        "no_photo_skipped": no_photo,
        "warnings": warnings,
    }

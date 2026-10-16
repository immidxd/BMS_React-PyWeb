"""Добір товару для регулярних Stories: читає, але нічого не публікує.

Відмінність від Top-9 у критерії. Підбірка бере найпопулярніше за переглядами
й продажами; Stories беруть те, що підпадає під заданий людиною фільтр
(«жіночі босоніжки», «усе HOKA»), і крутять цей пул по колу.

Порядок ротації навмисний: спершу те, чого у Stories ще не показували жодного
разу — новіші завози попереду, — а вже потім те, що показували найдавніше. Так
пул обходиться цілком, без випадковості, яку неможливо відтворити при розборі
«а чому вийшло саме це».

Ротація В КОЖНОЇ МЕРЕЖІ СВОЯ (рішення власника 16.10.2026, пріоритет — Facebook).
До цього пауза `cooldown_days` була спільною: товар зі Story в Instagram 30 днів
не йшов і у Facebook. Коли пул студійних фото (317 товарів) вичерпався, Instagram,
який добирав першим, забирав кожен товар, що звільнявся, і Facebook із 14.10
стояв без жодної Story. Тепер:
* кожна мережа обходить УВЕСЬ пул за свої 30 днів — показати можна все й там, і там;
* Facebook добирає першим і першим отримує новинки;
* Instagram не бере товар, який Facebook показав за останню добу
  (`AFTER_PRIORITY_GAP_HOURS`) — щоб та сама пара не йшла в обидві мережі разом,
  — і з однаково «свіжих» бере те, що Facebook показав найдавніше.
Ліміти Meta це не зачіпає: обсяг той самий, що працював на початку жовтня
(≈10 Stories на день у кожній мережі), а темп тримає `auto_publish` — не
більше однієї Story на мережу за 5-хв оберт.

Модуль не імпортує жодного публікатора, R2 чи диспетчера.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 24
DEFAULT_COOLDOWN_DAYS = 30
DEFAULT_LOCAL_TIME = "11:00"
DEFAULT_TIMEZONE = "Europe/Kyiv"
PLATFORMS = ("instagram", "facebook")
# Хто добирає першим і чиї покази стримують іншого (див. докстрінг модуля).
PRIORITY_PLATFORM = "facebook"
AFTER_PRIORITY_GAP_HOURS = 24
_PUBLICATIONS_TABLE = {"instagram": "instagram_publications", "facebook": "facebook_publications"}
# Скільки кандидатів тягнемо з БД під одну Story. Запас потрібен на випадок,
# коли в голови черги знімок таки не підійде при живій перевірці.
#
# ⚠️ Вимога студійного фото стоїть у САМОМУ запиті, а не після нього. Поки вона
# була лише перевіркою в Python, ця межа різала добір раніше, ніж він доходив
# до товарів із фото: 07.09.2026 перші 200 кандидатів були поспіль без знімків,
# а перший придатний стояв 202-м — обидва майданчики стали намертво з
# «Без придатного фото пропущено 200 товарів».
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


def normalize_number(value: Any) -> str:
    return str(value or "").strip().lstrip("#").casefold()


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


def _story_events_sql(platform: str) -> str:
    """Покази Story цієї мережі + її чернетки, що вже тримають товар."""
    table = _PUBLICATIONS_TABLE[platform]
    return f"""
        SELECT product_number AS productnumber,
               COALESCE(published_at, scheduled_at, created_at) AS occurred_at
        FROM {table}
        WHERE media_type='story' AND status NOT IN ('failed','error','cancelled')
        UNION ALL
        -- Затверджені чернетки теж займають товар: інакше два слоти
        -- поспіль могли б обрати те саме, поки диспетчер ще не відзвітував.
        SELECT productnumber, scheduled_for AS occurred_at
        FROM story_automation_drafts
        WHERE platform = '{platform}'
          AND status IN ('awaiting_review','approved')
          AND id <> :exclude_draft_id
    """


def candidate_rows(
    db: Session,
    filters: Dict[str, Any],
    cooldown_days: int,
    *,
    platform: Optional[str] = None,
    pool: int = CANDIDATE_POOL,
    exclude_draft_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Придатні товари в порядку ротації мережі `platform`. Тільки читання.

    Без `platform` — стара спільна ротація (показ у будь-якій мережі стримує
    обидві); робочі виклики завжди передають мережу.

    `exclude_draft_id` потрібен під час перевірки перед відправленням: чернетка
    вже тримає свій товар зайнятим, і без цього вона визнала б його непридатним
    сама через себе.
    """
    if platform is not None and platform not in PLATFORMS:
        raise ValueError(f"Невідома мережа: {platform}")
    conditions: List[str] = []
    params: Dict[str, Any] = {
        "cooldown_days": int(cooldown_days),
        "pool": max(1, min(int(pool), 5000)),
        "exclude_draft_id": int(exclude_draft_id) if exclude_draft_id else -1,
        "priority_gap_hours": AFTER_PRIORITY_GAP_HOURS,
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
    # Студійне фото — умова допуску, а не побажання. Фото живуть у файловій
    # системі, не в БД, тож множину номерів (кешовану) вносимо ПАРАМЕТРОМ — той
    # самий прийом, що й у фільтрі «лише з фото» списку товарів. Донор
    # (`official_photos_from`) рахується нарівні з власним номером: публікатор
    # бере знімок звідти так само.
    try:
        from services.product_images import get_official_photo_pnum_set
    except ImportError:
        from backend.services.product_images import get_official_photo_pnum_set
    try:
        official_pnums = sorted(get_official_photo_pnum_set())
    except Exception:  # noqa: BLE001 — без певності про фото не публікуємо взагалі
        logger.warning("Stories: множина студійних фото недоступна — добір порожній")
        official_pnums = []
    if official_pnums:
        # COLLATE "und-x-icu" обов'язковий: база в локалі C, де lower() не
        # опускає кирилицю, і без ICU збіглися б лише суто цифрові номери.
        conditions.append("""(
            lower(btrim(ltrim(btrim(p.productnumber), '#') COLLATE "und-x-icu")) = ANY(:official_pnums)
            OR lower(btrim(ltrim(btrim(COALESCE(p.official_photos_from, '')), '#') COLLATE "und-x-icu")) = ANY(:official_pnums)
        )""")
        params["official_pnums"] = official_pnums
    else:
        # Порожня множина — чесний нуль. Мовчазна публікація «як є» гірша за
        # пропущений слот: знімок із дому не забрати з чужої стрічки назад.
        conditions.append("FALSE")

    if platform is None:
        own_events = " UNION ALL ".join(_story_events_sql(p) for p in PLATFORMS)
        other_events = "SELECT NULL::varchar AS productnumber, NULL::timestamptz AS occurred_at WHERE FALSE"
        order_sql = "shown.last_story_at NULLS FIRST, p.dateadded DESC NULLS LAST, p.id"
    else:
        other = next(p for p in PLATFORMS if p != platform)
        own_events = _story_events_sql(platform)
        other_events = _story_events_sql(other)
        if platform == PRIORITY_PLATFORM:
            # Пріоритетна мережа не зважає на покази іншої взагалі.
            order_sql = "shown.last_story_at NULLS FIRST, p.dateadded DESC NULLS LAST, p.id"
        else:
            # Не дублювати свіжий показ пріоритетної мережі; з рівних — те, що
            # вона показала найдавніше (новинки вона показує першою).
            conditions.append(
                "(other.last_story_at IS NULL "
                "OR other.last_story_at <= now() - make_interval(hours => :priority_gap_hours))"
            )
            order_sql = (
                "shown.last_story_at NULLS FIRST, other.last_story_at ASC NULLS LAST, "
                "p.dateadded DESC NULLS LAST, p.id"
            )
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
            -- Коли цей номер востаннє був у Stories ЦІЄЇ мережі.
            SELECT productnumber, MAX(occurred_at) AS last_story_at
            FROM ({own_events}) events
            WHERE productnumber IS NOT NULL
            GROUP BY productnumber
        ), other AS (
            -- Те саме для другої мережі — лише для черговості й паузи.
            SELECT productnumber, MAX(occurred_at) AS last_story_at
            FROM ({other_events}) events
            WHERE productnumber IS NOT NULL
            GROUP BY productnumber
        )
        SELECT p.id AS product_id, p.productnumber, p.price::float AS price,
               p.dateadded, p.sizeeu, p.season,
               b.brandname AS brand, t.typename AS type, g.gendername AS gender,
               p.model, p.official_photos_from,
               shown.last_story_at, other.last_story_at AS other_last_story_at
        FROM products p
        LEFT JOIN brands b ON b.id=p.brandid
        LEFT JOIN types t ON t.id=p.typeid
        LEFT JOIN genders g ON g.id=p.genderid
        LEFT JOIN statuses s ON s.id=p.statusid
        LEFT JOIN sold ON sold.product_id=p.id
        LEFT JOIN shown ON shown.productnumber=p.productnumber
        LEFT JOIN other ON other.productnumber=p.productnumber
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
        ORDER BY {order_sql}
        LIMIT :pool
    """), params).mappings().all()
    return [dict(row) for row in rows]


def _official_photo_ready(bms: Dict[str, Any]) -> bool:
    """Чи піде в кадр студійний знімок — і лише він.

    Друга лінія після умови в запиті: множина номерів кешується на кілька
    хвилин і може відстати від теки. Тут дивимось на живі файли й на той самий
    `kind`, яким публікатор обирає знімок, — тож «придатний» тут означає рівно
    те, що поїде в стрічку. Реальні фото («як є») лишаються для картки, Telegram
    і замірів; у відкриту вітрину вони не йдуть.
    """
    try:
        from services import telegram_publisher as tg
    except ImportError:
        from backend.services import telegram_publisher as tg
    _photos, kind = tg._photo_entries(bms)
    return kind == "official"


def _exhausted_message(db: Session, filters: Dict[str, Any], cooldown_days: int,
                       platform: Optional[str]) -> str:
    """Чому порожньо — по-людськи: фото є, але все вже показано за паузу.

    Раніше тут завжди писалось «немає жодного товару зі студійним фото», хоча
    16.10.2026 таких товарів було 317 — просто всі на паузі після показу.
    """
    everyone = candidate_rows(db, filters, 0, platform=platform, pool=5000)
    if not everyone:
        return "Під цей добір немає жодного товару зі студійним фото."
    import datetime as _dt
    from zoneinfo import ZoneInfo
    expiries = [row["last_story_at"] + _dt.timedelta(days=int(cooldown_days))
                for row in everyone if row.get("last_story_at")]
    where = {"instagram": " в Instagram", "facebook": " у Facebook"}.get(platform or "", "")
    head = (f"Усі {len(everyone)} товарів зі студійним фото вже були в Stories{where} "
            f"за останні {int(cooldown_days)} днів")
    if not expiries:
        if platform and platform != PRIORITY_PLATFORM:
            return head + " або щойно вийшли у Facebook — Instagram візьме їх за добу."
        return head + "."
    nearest = min(expiries).astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    return head + f". Найближчий звільниться {nearest:%d.%m о %H:%M}."


def select_for_slot(
    db: Session,
    *,
    filters: Dict[str, Any],
    cooldown_days: int,
    platform: Optional[str] = None,
    reserve_count: int = RESERVE_COUNT,
) -> Dict[str, Any]:
    """Головний товар для Story плюс запас. Нічого не пише й не публікує."""
    candidates = candidate_rows(db, filters, cooldown_days, platform=platform)
    chosen: List[Dict[str, Any]] = []
    no_photo = 0
    # Запит уже відсіяв усе без студійного фото, тож тут відсівається лише те,
    # що встигло змінитись після побудови кешу. Спиняємось щойно набрали
    # головний товар із запасом — а не проходимо весь пул.
    for row in candidates:
        if _official_photo_ready(row):
            chosen.append(row)
            if len(chosen) > reserve_count:
                break
        else:
            no_photo += 1

    warnings: List[str] = []
    if no_photo:
        warnings.append(f"Без студійного фото пропущено {no_photo} товарів.")
    if not chosen:
        warnings.append(
            _exhausted_message(db, filters, cooldown_days, platform) if not candidates
            else "Під цей добір немає жодного товару зі студійним фото."
        )
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

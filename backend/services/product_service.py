from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text, func, desc, asc
from sqlalchemy.sql.expression import or_, and_
from typing import Optional, List, Dict, Any, Tuple, Set
from datetime import datetime
import logging
import re
import math

from models import models
from schemas import product as schemas

try:
    from backend.utils.order_status_logic import real_order_sql
    from backend.utils.productnumber_normalizer import effective_product_number
except ImportError:
    from utils.order_status_logic import real_order_sql
    from utils.productnumber_normalizer import effective_product_number

try:
    from backend.services.product_taxonomy_normalization import (
        canonicalize_subtype_name,
        canonicalize_type_name,
        merge_season_values,
        normalize_taxonomy_pair,
        packaging_from_taxonomy_name,
        style_from_taxonomy_name,
        style_from_taxonomy_overrides_existing,
    )
except ImportError:
    from services.product_taxonomy_normalization import (
        canonicalize_subtype_name,
        canonicalize_type_name,
        merge_season_values,
        normalize_taxonomy_pair,
        packaging_from_taxonomy_name,
        style_from_taxonomy_name,
        style_from_taxonomy_overrides_existing,
    )

try:
    from backend.services.product_style_normalization import (
        canonicalize_style_name,
        subtype_from_style_name,
    )
except ImportError:
    from services.product_style_normalization import (
        canonicalize_style_name,
        subtype_from_style_name,
    )

logger = logging.getLogger(__name__)

def get_product(db: Session, product_id: int) -> Optional[models.Product]:
    """Get a single product by ID with all related data"""
    try:
        product = db.query(models.Product).options(
            joinedload(models.Product.type),
            joinedload(models.Product.subtype),
            joinedload(models.Product.brand),
            joinedload(models.Product.gender),
            joinedload(models.Product.color),
            joinedload(models.Product.owner_country),
            joinedload(models.Product.manufacturer_country),
            joinedload(models.Product.status),
            joinedload(models.Product.condition),
            joinedload(models.Product.delivery)
        ).filter(models.Product.id == product_id).first()
        logger.debug(f"Retrieved product: {product}")
        return product
    except Exception as e:
        logger.error(f"Error getting product {product_id}: {str(e)}")
        raise

def get_product_by_number(db: Session, product_number: str) -> Optional[models.Product]:
    """Get a product by its product number"""
    try:
        product = db.query(models.Product).options(
            joinedload(models.Product.type),
            joinedload(models.Product.subtype),
            joinedload(models.Product.brand),
            joinedload(models.Product.gender),
            joinedload(models.Product.color),
            joinedload(models.Product.owner_country),
            joinedload(models.Product.manufacturer_country),
            joinedload(models.Product.status),
            joinedload(models.Product.condition),
            joinedload(models.Product.delivery)
        ).filter(models.Product.productnumber == product_number).first()
        logger.debug(f"Retrieved product by number: {product}")
        return product
    except Exception as e:
        logger.error(f"Error getting product by number {product_number}: {str(e)}")
        raise

def _fmt_size(v: float) -> str:
    """Format a numeric size value: 41.0→'41', 37.5→'37.5', 36.6→'36.6'."""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


_RANGE_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$')


def _expand_sizes_for_filters(raw_sizes: List[str]) -> List[str]:
    """Expand range sizes into individual sizes for the filter panel.

    '41-42' → adds 41, 42  (integer step when both ends are integers)
    '36.6-37.5' → adds 36.6, 37, 37.5  (0.5 step, keep exact endpoints)
    '39-43' → adds 39, 40, 41, 42, 43
    Non-range values (e.g. '41', 'XL') are kept as-is.
    """
    individual: Set[str] = set()
    for s in raw_sizes:
        m = _RANGE_RE.match(s.strip())
        if not m:
            individual.add(s.strip())
            continue
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            individual.add(s.strip())
            continue
        # Always include exact endpoints
        individual.add(_fmt_size(lo))
        individual.add(_fmt_size(hi))
        # Step: 1 when both ends are integers, else 0.5
        both_int = (lo == int(lo) and hi == int(hi))
        step = 1.0 if both_int else 0.5
        cur = lo + step
        while cur < hi - 0.001:
            individual.add(_fmt_size(cur))
            cur += step

    # Sort: numeric values first (by value), then text values alphabetically
    nums = []
    texts = []
    for s in individual:
        try:
            nums.append((float(s), s))
        except ValueError:
            texts.append(s)
    nums.sort(key=lambda x: x[0])
    texts.sort()
    return [s for _, s in nums] + texts


# Спільний FROM+JOIN блок для товарних запитів — повний список (get_products) і
# фасети (get_available_sizes). Тримаємо в ОДНОМУ місці, щоб WHERE-умови, які
# посилаються на аліаси (s.statusname, sold.sold_count, dup.dup_brands, b.brandname
# у пошуку тощо), завжди мали відповідні JOIN-и в обох запитах.
_PRODUCT_FROM_SQL = """
        FROM products p
        LEFT JOIN types t ON p.typeid = t.id
        LEFT JOIN brands b ON p.brandid = b.id
        LEFT JOIN statuses s ON p.statusid = s.id
        LEFT JOIN colors c ON p.colorid = c.id
        LEFT JOIN conditions cond ON p.conditionid = cond.id
        LEFT JOIN conditions cur_cond ON p.current_conditionid = cur_cond.id
        LEFT JOIN genders g ON p.genderid = g.id
        LEFT JOIN subtypes st ON p.subtypeid = st.id
        LEFT JOIN styles sty ON p.styleid = sty.id
        LEFT JOIN sole_types sol ON p.soletypeid = sol.id
        LEFT JOIN toe_shapes tsh ON p.toeshapeid = tsh.id
        LEFT JOIN fastening_types fst ON p.fasteningtypeid = fst.id
        LEFT JOIN linings lin ON p.liningid = lin.id
        LEFT JOIN heel_types ht ON p.heeltypeid = ht.id
        LEFT JOIN lace_types lt ON p.lacetypeid = lt.id
        LEFT JOIN packaging_types pk ON p.packagingid = pk.id
        LEFT JOIN technologies tech ON p.technologyid = tech.id
        LEFT JOIN colors scol ON p.sole_colorid = scol.id
        LEFT JOIN (
            -- «Продано» = Подарунок(7) АБО (Підтверджено(1) І Оплачено).
            -- Повернення(9) повертає сток у наявність ЛИШЕ коли той самий клієнт має
            -- окремий оплачений продаж того ж товару (справжнє sold-then-returned). Якщо
            -- повернену пару перепродали іншому — статус9 і так не входить у «продано»,
            -- тож віднімати його = подвійний облік (фантомна наявність на проданому).
            -- effective_returns = SUM по клієнту LEAST(оплачених_продажів, повернень).
            SELECT pc.product_id,
                   GREATEST(SUM(pc.paid_sold) - SUM(LEAST(pc.paid_sold, pc.returns)), 0) AS sold_count
            FROM (
                SELECT oi.product_id, o.client_id,
                       COUNT(*) FILTER (WHERE o.order_status_id = 7
                                          OR (o.order_status_id = 1 AND o.payment_status_id = 1)) AS paid_sold,
                       COUNT(*) FILTER (WHERE o.order_status_id = 9) AS returns
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.product_id IS NOT NULL
                  AND o.order_status_id IN (1, 7, 9)
                GROUP BY oi.product_id, o.client_id
            ) pc
            GROUP BY pc.product_id
        ) sold ON sold.product_id = p.id
        LEFT JOIN (
            SELECT oi.product_id, COUNT(*) AS reserved_count
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id = 1
              AND o.payment_status_id IS DISTINCT FROM 1
            GROUP BY oi.product_id
        ) reserved ON reserved.product_id = p.id
        LEFT JOIN (
            SELECT productnumber, COUNT(DISTINCT COALESCE(brandid, 0)) AS dup_brands
            FROM products
            GROUP BY productnumber
            HAVING COUNT(DISTINCT COALESCE(brandid, 0)) > 1
        ) dup ON dup.productnumber = p.productnumber
        LEFT JOIN deliveries d ON p.deliveryid = d.id
        LEFT JOIN suppliers sup ON d.supplier_id = sup.id
        LEFT JOIN (
            SELECT oi.product_id, MAX(o.order_date) AS last_sale_date
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND (o.order_status_id = 7
                   OR (o.order_status_id = 1 AND o.payment_status_id = 1))
            GROUP BY oi.product_id
        ) last_sale ON last_sale.product_id = p.id
        LEFT JOIN (
            SELECT new_product_id, COUNT(*) AS pending_count
            FROM merge_candidates
            WHERE status = 'pending'
            GROUP BY new_product_id
        ) mc ON mc.new_product_id = p.id
        LEFT JOIN (
            SELECT DISTINCT TRIM(LEADING '#' FROM p2.productnumber) AS pnum
            FROM telegram_posts tp2
            JOIN products p2 ON p2.id = tp2.product_id
            WHERE tp2.tg_status = 'published' AND p2.productnumber IS NOT NULL
        ) tgpub ON tgpub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            -- Лише фактично надіслані Viber-пости. Черга/розклад ще не є
            -- «опубліковано». Нормалізований номер покриває всю ростовку.
            SELECT DISTINCT TRIM(LEADING '#' FROM BTRIM(
                       COALESCE(NULLIF(BTRIM(p2.productnumber), ''), vp.product_number)
                   )) AS pnum
            FROM viber_publications vp
            LEFT JOIN products p2 ON p2.id = vp.product_id
            WHERE vp.status = 'published'
              AND COALESCE(NULLIF(BTRIM(p2.productnumber), ''), NULLIF(BTRIM(vp.product_number), '')) IS NOT NULL
        ) viberpub ON viberpub.pnum = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
        LEFT JOIN (
            -- Instagram: лише опубліковане (черга/розклад ще не «опубліковано»).
            SELECT DISTINCT TRIM(LEADING '#' FROM BTRIM(
                       COALESCE(NULLIF(BTRIM(p2.productnumber), ''), ip.product_number)
                   )) AS pnum
            FROM instagram_publications ip
            LEFT JOIN products p2 ON p2.id = ip.product_id
            WHERE ip.status = 'published'
              AND COALESCE(NULLIF(BTRIM(p2.productnumber), ''), NULLIF(BTRIM(ip.product_number), '')) IS NOT NULL
        ) igpub ON igpub.pnum = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
        LEFT JOIN (
            -- Facebook: те саме правило, окрема таблиця.
            SELECT DISTINCT TRIM(LEADING '#' FROM BTRIM(
                       COALESCE(NULLIF(BTRIM(p2.productnumber), ''), fp.product_number)
                   )) AS pnum
            FROM facebook_publications fp
            LEFT JOIN products p2 ON p2.id = fp.product_id
            WHERE fp.status = 'published'
              AND COALESCE(NULLIF(BTRIM(p2.productnumber), ''), NULLIF(BTRIM(fp.product_number), '')) IS NOT NULL
        ) fbpub ON fbpub.pnum = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
        LEFT JOIN (
            -- Найкращий OLX-стан за номером: 'active' = повністю ПУБЛІЧНЕ,
            -- 'limited' = створене, але з обмеженою видимістю (потрібен пакет).
            SELECT TRIM(LEADING '#' FROM p2.productnumber) AS pnum,
                   CASE MAX(CASE oa.status WHEN 'active' THEN 3 WHEN 'limited' THEN 2 ELSE 1 END)
                        WHEN 3 THEN 'active' WHEN 2 THEN 'limited' ELSE 'other' END AS olx_status
            FROM olx_adverts oa
            JOIN products p2 ON p2.id = oa.product_id
            WHERE oa.status IN ('active', 'limited') AND p2.productnumber IS NOT NULL
            GROUP BY TRIM(LEADING '#' FROM p2.productnumber)
        ) olxpub ON olxpub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            -- Номери (без #), що Є на Prom: лістинг у дзеркалі (будь-який статус, крім
            -- deleted — тобто й ЧЕРНЕТКА) АБО в черзі експорту (pending). Узгоджено з чіпом «Prom».
            SELECT TRIM(LEADING '#' FROM p2.productnumber) AS pnum
            FROM prom_products pp
            JOIN products p2 ON p2.id = pp.product_id
            WHERE COALESCE(pp.status, '') <> 'deleted' AND p2.productnumber IS NOT NULL
            UNION
            SELECT TRIM(LEADING '#' FROM p3.productnumber) AS pnum
            FROM prom_draft_queue q
            JOIN products p3 ON q.sku = TRIM(LEADING '#' FROM p3.productnumber)
                             OR q.sku LIKE TRIM(LEADING '#' FROM p3.productnumber) || '-%'
        ) prompub ON prompub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            SELECT productnumber AS pnum,
                   CASE MAX(CASE
                       WHEN status = 'confirmed'
                            AND (NULLIF(BTRIM(shafa_url), '') IS NOT NULL
                                 OR NULLIF(BTRIM(shafa_listing_id), '') IS NOT NULL) THEN 4
                       WHEN status = 'manual_existing'
                            AND (NULLIF(BTRIM(shafa_url), '') IS NOT NULL
                                 OR NULLIF(BTRIM(shafa_listing_id), '') IS NOT NULL) THEN 3
                       WHEN status IN ('confirmed','manual_existing','bridge_ready') THEN 2
                       WHEN status = 'waiting_prom' THEN 1
                       ELSE 0 END)
                       WHEN 4 THEN 'confirmed'
                       WHEN 3 THEN 'manual_existing'
                       WHEN 2 THEN 'bridge_ready'
                       WHEN 1 THEN 'waiting_prom'
                   END AS shafa_status
            FROM shafa_publications
            WHERE status IN ('waiting_prom','bridge_ready','confirmed','manual_existing')
            GROUP BY productnumber
        ) shafapub ON shafapub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            -- Номери (без #), опубліковані в ПУБЛІЧНОМУ інтернет-каталозі (Telegram
            -- Mini App): catalog_listings.is_published, ключ=productnumber → вся
            -- ростовка. Узгоджено з чіпом «У каталозі» в картці товару.
            SELECT DISTINCT TRIM(LEADING '#' FROM cl.productnumber) AS pnum
            FROM catalog_listings cl
            WHERE cl.is_published
        ) catpub ON catpub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        """


def _build_product_where(filters: Optional["schemas.ProductFilter"]) -> tuple:
    """Будує список WHERE-умов + параметри для товарних запитів.

    Винесено зі get_products, щоб ту саму фільтрацію переюзати у фасетах
    (get_available_sizes) — інакше логіка фільтрів розповзлась би по двох
    місцях. Див. feedback_filter_params_chain.
    """
    where_conditions: list = []
    params: dict = {}

    if filters:
        if filters.search:
            _LAT2CYR = str.maketrans({
                'A':'А','B':'В','C':'С','E':'Е','H':'Н','I':'І','K':'К',
                'M':'М','O':'О','P':'Р','T':'Т','X':'Х','Y':'У',
                'a':'а','b':'в','c':'с','e':'е','h':'н','i':'і','k':'к',
                'm':'м','o':'о','p':'р','t':'т','x':'х','y':'у',
            })
            _bases = {filters.search, filters.search.translate(_LAT2CYR)}
            _variants = set()
            for _b in _bases:
                _variants.update({_b, _b.lower(), _b.upper(), _b.capitalize()})
            search_patterns = [f"%{v}%" for v in _variants]
            where_conditions.append("""
                (p.productnumber  ILIKE ANY(:search_patterns) OR
                 p.clonednumbers  ILIKE ANY(:search_patterns) OR
                 p.model          ILIKE ANY(:search_patterns) OR
                 p.description    ILIKE ANY(:search_patterns) OR
                 p.marking        ILIKE ANY(:search_patterns) OR
                 p.gtin           ILIKE ANY(:search_patterns) OR
                 p.collection     ILIKE ANY(:search_patterns) OR
                 p.extranote      ILIKE ANY(:search_patterns) OR
                 b.brandname      ILIKE ANY(:search_patterns) OR
                 t.typename       ILIKE ANY(:search_patterns) OR
                 c.colorname      ILIKE ANY(:search_patterns) OR
                 g.gendername     ILIKE ANY(:search_patterns) OR
                 cond.conditionname ILIKE ANY(:search_patterns) OR
                 s.statusname     ILIKE ANY(:search_patterns) OR
                 p.brandid IN (
                     SELECT ba.brand_id FROM brand_aliases ba
                     JOIN unnest(CAST(:search_tokens AS text[])) AS tok ON (
                          unaccent(lower(ba.alias_name COLLATE "und-x-icu")) = unaccent(lower(tok COLLATE "und-x-icu"))
                       OR (length(tok) >= 4 AND unaccent(lower(ba.alias_name COLLATE "und-x-icu")) LIKE '%' || unaccent(lower(tok COLLATE "und-x-icu")) || '%')
                       OR (length(ba.alias_name) >= 4 AND unaccent(lower(tok COLLATE "und-x-icu")) LIKE '%' || unaccent(lower(ba.alias_name COLLATE "und-x-icu")) || '%')
                       OR (length(tok) >= 4 AND levenshtein(unaccent(lower(ba.alias_name COLLATE "und-x-icu")), unaccent(lower(tok COLLATE "und-x-icu"))) <= 1)
                     )
                 ))
            """)
            params['search_patterns'] = search_patterns
            # Кирилична транслітерація бренду («найк»→Nike, «адідас»→Adidas, одрук «адидс»):
            # резолв токенів через СПІЛЬНУ brand_aliases (та сама таблиця, що в каталозі та
            # BMS-парсері). Лише ДОДАЄ збіги до ILIKE-блоку вище — наявна поведінка не
            # змінюється. levenshtein≤1 — толерантність до одруків (fuzzystrmatch у БД).
            params['search_tokens'] = [t for t in filters.search.split() if len(t) >= 2]

        # Single ID filters
        if filters.typeid and not filters.typeids:
            where_conditions.append("p.typeid = :typeid")
            params['typeid'] = filters.typeid

        if filters.subtypeid and not filters.subtypeids:
            where_conditions.append("p.subtypeid = :subtypeid")
            params['subtypeid'] = filters.subtypeid

        if filters.brandid and not filters.brandids:
            # Один учасник альянсу брендів означає весь альянс. Для бренду без
            # concern_id поведінка лишається точною по одному ID.
            where_conditions.append("""
                p.brandid IN (
                    SELECT member.id
                    FROM brands selected
                    JOIN brands member
                      ON member.id = selected.id
                      OR (
                          selected.concern_id IS NOT NULL
                          AND member.concern_id = selected.concern_id
                      )
                    WHERE selected.id = :brandid
                )
            """)
            params['brandid'] = filters.brandid

        if filters.genderid and not filters.genderids:
            where_conditions.append("p.genderid = :genderid")
            params['genderid'] = filters.genderid

        if filters.colorid and not filters.colorids:
            where_conditions.append("p.colorid = :colorid")
            params['colorid'] = filters.colorid

        if filters.statusid and not filters.statusids:
            where_conditions.append("p.statusid = :statusid")
            params['statusid'] = filters.statusid

        if filters.conditionid and not filters.conditionids:
            where_conditions.append(
                "COALESCE(p.current_conditionid, p.conditionid) = :conditionid"
            )
            params['conditionid'] = filters.conditionid

        if getattr(filters, "styleid", None) and not getattr(filters, "styleids", None):
            where_conditions.append("p.styleid = :styleid")
            params['styleid'] = filters.styleid
        if getattr(filters, "styleids", None):
            where_conditions.append("p.styleid = ANY(:styleids)")
            params['styleids'] = filters.styleids
        if getattr(filters, "current_conditionid", None) and not getattr(filters, "current_conditionids", None):
            where_conditions.append("p.current_conditionid = :current_conditionid")
            params['current_conditionid'] = filters.current_conditionid
        if getattr(filters, "current_conditionids", None):
            where_conditions.append("p.current_conditionid = ANY(:current_conditionids)")
            params['current_conditionids'] = filters.current_conditionids
        if getattr(filters, "seasons", None):
            where_conditions.append(
                "string_to_array(regexp_replace(COALESCE(p.season, ''), "
                "'\\s*,\\s*', ',', 'g'), ',') && :seasons_arr"
            )
            params['seasons_arr'] = [s.strip() for s in filters.seasons if s and s.strip()]
        if getattr(filters, "widths", None):
            where_conditions.append("p.width = ANY(:widths)")
            params['widths'] = filters.widths

        # Multi-ID filters (arrays)
        _type_or = []
        if filters.typeids:
            _type_or.append("p.typeid = ANY(:typeids)")
            params['typeids'] = filters.typeids
        if filters.subtypeids:
            _type_or.append("p.subtypeid = ANY(:subtypeids)")
            params['subtypeids'] = filters.subtypeids
        if _type_or:
            where_conditions.append("(" + " OR ".join(_type_or) + ")")

        if filters.brandids:
            where_conditions.append("""
                p.brandid IN (
                    SELECT DISTINCT member.id
                    FROM brands selected
                    JOIN brands member
                      ON member.id = selected.id
                      OR (
                          selected.concern_id IS NOT NULL
                          AND member.concern_id = selected.concern_id
                      )
                    WHERE selected.id = ANY(:brandids)
                )
            """)
            params['brandids'] = filters.brandids

        if filters.genderids:
            where_conditions.append("p.genderid = ANY(:genderids)")
            params['genderids'] = filters.genderids

        if filters.colorids:
            where_conditions.append("p.colorid = ANY(:colorids)")
            params['colorids'] = filters.colorids

        if hasattr(filters, 'color_group_ids') and filters.color_group_ids:
            where_conditions.append("""
                EXISTS (
                    SELECT 1 FROM color_group_members cgm
                    WHERE cgm.color_id = p.colorid
                    AND cgm.group_id = ANY(:color_group_ids)
                )
            """)
            params['color_group_ids'] = filters.color_group_ids

        if filters.statusids:
            where_conditions.append("p.statusid = ANY(:statusids)")
            params['statusids'] = filters.statusids

        # Публікації: «де опубліковано» — реюз tgpub/olxpub join-ів (вони в base_sql,
        # отже доступні і в count_sql, що обгортає base_sql).
        #   published_on      — ПОЗИТИВНІ (показати ті, що там опубліковані), OR між обраними;
        #   published_on_not  — НЕГАТИВНІ  (виключити ті, що там опубліковані), AND між обраними.
        # Позитив і негатив незалежні: можна «показати з Telegram, але БЕЗ OLX».
        # Один майданчик = один булевий вираз ↓; негатив = NOT COALESCE(вираз, FALSE),
        # щоб NULL (не опубліковано, напр. shafa_status IS NULL) лишався у вибірці.
        _PUB_EXPR = {
            'telegram': "tgpub.pnum IS NOT NULL",
            'viber':    "viberpub.pnum IS NOT NULL",
            'olx':      "olxpub.pnum IS NOT NULL",
            'prom':     "prompub.pnum IS NOT NULL",
            # Shafa-фільтр охоплює весь чесний pipeline: жовті очікувані стани
            # та чорні підтверджені. published_shafa у відповіді все одно true
            # лише за реального URL/ID.
            'shafa':    "shafapub.shafa_status IN ('waiting_prom','bridge_ready','confirmed','manual_existing')",
            'catalog':  "catpub.pnum IS NOT NULL",
            'instagram': "igpub.pnum IS NOT NULL",
            'facebook':  "fbpub.pnum IS NOT NULL",
        }
        if getattr(filters, 'published_on', None):
            _pub = [_PUB_EXPR[k] for k in filters.published_on if k in _PUB_EXPR]
            if _pub:
                where_conditions.append("(" + " OR ".join(_pub) + ")")
        if getattr(filters, 'published_on_not', None):
            for k in filters.published_on_not:
                if k in _PUB_EXPR:
                    where_conditions.append(f"NOT COALESCE(({_PUB_EXPR[k]}), FALSE)")

        if filters.conditionids:
            where_conditions.append(
                "COALESCE(p.current_conditionid, p.conditionid) = ANY(:conditionids)"
            )
            params['conditionids'] = filters.conditionids

        # Price range
        if filters.min_price is not None:
            where_conditions.append("p.price >= :min_price")
            params['min_price'] = filters.min_price

        if filters.max_price is not None:
            where_conditions.append("p.price <= :max_price")
            params['max_price'] = filters.max_price

        # Measurements CM range filter
        if getattr(filters, 'min_measurementscm', None) is not None:
            where_conditions.append("p.measurementscm_max >= :min_cm")
            params['min_cm'] = filters.min_measurementscm
        if getattr(filters, 'max_measurementscm', None) is not None:
            where_conditions.append("p.measurementscm_min <= :max_cm")
            params['max_cm'] = filters.max_measurementscm

        # Size EU range filter (min/max)
        if filters.min_sizeeu is not None or filters.max_sizeeu is not None:
            lo = filters.min_sizeeu if filters.min_sizeeu is not None else 0
            hi = filters.max_sizeeu if filters.max_sizeeu is not None else 999
            params['sz_lo'] = lo
            params['sz_hi'] = hi
            where_conditions.append("""(
                (p.sizeeu ~ '^[0-9]+([.,][0-9]+)?$'
                 AND CAST(replace(p.sizeeu, ',', '.') AS numeric) BETWEEN :sz_lo AND :sz_hi)
                OR
                (p.sizeeu ~ '^[0-9]+[.,]?[0-9]*-[0-9]+[.,]?[0-9]*$'
                 AND CAST(split_part(p.sizeeu, '-', 1) AS numeric) <= :sz_hi
                 AND CAST(split_part(p.sizeeu, '-', 2) AS numeric) >= :sz_lo)
            )""")

        # Size EU filter (multi-select of WHOLE sizes) — BMS bucket semantics.
        # обраний цілий N ↔ розмір товару x, де N-0.5 < x < N+1
        # (N, N.3, N.5 → лише N; N.6/N.7 → і N, і N+1). Діапазони ("39-43") — overlap.
        if filters.sizeeu:
            size_clauses = []
            for i, s in enumerate(filters.sizeeu):
                try:
                    n = float(s)
                except (ValueError, TypeError):
                    continue
                p_lo = f"sz_b_lo_{i}"
                p_hi = f"sz_b_hi_{i}"
                p_n = f"sz_b_n_{i}"
                params[p_lo] = n - 0.5
                params[p_hi] = n + 1
                params[p_n] = n
                size_clauses.append(f"""(
                    (p.sizeeu ~ '^[0-9]+([.,][0-9]+)?$'
                     AND CAST(replace(p.sizeeu, ',', '.') AS numeric) > :{p_lo}
                     AND CAST(replace(p.sizeeu, ',', '.') AS numeric) < :{p_hi})
                    OR
                    (p.sizeeu ~ '^[0-9]+[.,]?[0-9]*-[0-9]+[.,]?[0-9]*$'
                     AND CAST(replace(split_part(p.sizeeu, '-', 1), ',', '.') AS numeric) <= :{p_n}
                     AND CAST(replace(split_part(p.sizeeu, '-', 2), ',', '.') AS numeric) >= :{p_n})
                )""")
            if size_clauses:
                where_conditions.append("(" + " OR ".join(size_clauses) + ")")

        if filters.size_letter:
            where_conditions.append("p.size_letter = ANY(:size_letter)")
            params['size_letter'] = filters.size_letter

        if filters.with_stock_only:
            where_conditions.append("p.quantity > 0")

        if filters.only_unsold:
            # Застарілий знімок «Продано» спростовують лише РЕАЛЬНІ замовлення:
            # анонімна мітка «В черзі» — це рядок Sheets, а не замовлення, і
            # раніше вона одна повертала товар у «Тільки непродані», хоча UI
            # (order_count рахує з тим самим real_order_sql) показував «Продано».
            where_conditions.append(f"""(
                GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold.sold_count, 0), 0) > 0
                AND (
                    s.statusname IS NULL
                    OR s.statusname NOT IN ('Продано', 'Подаровано', 'Повернуто')
                    OR (
                        s.statusname IN ('Продано', 'Подаровано')
                        AND COALESCE(sold.sold_count, 0) < COALESCE(NULLIF(p.quantity, 0), 1)
                        AND EXISTS (
                            SELECT 1 FROM order_items oi_uns
                            JOIN orders o_uns ON o_uns.id = oi_uns.order_id
                            WHERE oi_uns.product_id = p.id AND {real_order_sql('o_uns')}
                        )
                    )
                )
            )""")

        if filters.only_problematic:
            where_conditions.append("""(
                p.productnumber IS NULL
                OR p.productnumber = '???'
                OR p.productnumber LIKE '???\\_%'
                OR p.productnumber LIKE '__tmp_rename\\_%'
                OR p.typeid IS NULL
                OR p.price IS NULL OR p.price = 0
                OR COALESCE(sold.sold_count, 0) > COALESCE(p.quantity, 0)
                OR COALESCE(dup.dup_brands, 0) > 1
            )""")

        if filters.only_rostovka:
            where_conditions.append("""(
                p.quantity > 1
                OR LOWER(COALESCE(p.extranote, '')) LIKE '%ростовка%'
                OR p.productnumber ~ '^.+\\([0-9]+\\)$'
                OR EXISTS (
                    SELECT 1 FROM products p_sib
                    WHERE p_sib.productnumber = p.productnumber || '(1)'
                )
            )""")

        if filters.shipment_id:
            where_conditions.append("p.deliveryid = :shipment_id")
            params["shipment_id"] = filters.shipment_id

    return where_conditions, params


def get_available_sizes(
    db: Session,
    filters: Optional["schemas.ProductFilter"] = None,
) -> List[str]:
    """Distinct EU-розміри, наявні в поточному відфільтрованому наборі (фасет).

    Сам розмірний фільтр (sizeeu/min/max) ВИКЛЮЧАЄТЬСЯ з обчислення — щоб сітка
    показувала всі досяжні за іншими фільтрами розміри (стандартний faceted
    search): вибрані лишаються вибірними, можна додавати/прибирати без скидання.
    Фронт сам згортає у цілі та ховає <14.
    """
    from sqlalchemy import text
    import copy as _copy

    f = _copy.copy(filters) if filters is not None else None
    if f is not None:
        f.sizeeu = None
        f.min_sizeeu = None
        f.max_sizeeu = None

    where_conditions, params = _build_product_where(f)
    sql = "SELECT DISTINCT p.sizeeu " + _PRODUCT_FROM_SQL
    if where_conditions:
        sql += " WHERE " + " AND ".join(where_conditions)
    rows = db.execute(text(sql), params).fetchall()
    return [r[0] for r in rows if r[0]]


def get_available_color_groups(
    db: Session,
    filters: Optional["schemas.ProductFilter"] = None,
) -> List[Dict[str, int]]:
    """Кольорові групи (id + count товарів), наявні в поточному відфільтрованому
    наборі (фасет). Сам кольоровий фільтр (colorids/color_group_ids) ВИКЛЮЧАЄТЬСЯ
    — щоб чіпи показували всі досяжні за іншими фільтрами кольори і лишались
    вибірними. Дзеркало get_available_sizes для кольору.
    """
    from sqlalchemy import text
    import copy as _copy

    f = _copy.copy(filters) if filters is not None else None
    if f is not None:
        f.colorids = None
        f.color_group_ids = None

    where_conditions, params = _build_product_where(f)
    sql = (
        "SELECT cgm.group_id AS id, COUNT(DISTINCT p.id) AS cnt "
        + _PRODUCT_FROM_SQL
        + " JOIN color_group_members cgm ON cgm.color_id = p.colorid "
    )
    if where_conditions:
        sql += " WHERE " + " AND ".join(where_conditions)
    sql += " GROUP BY cgm.group_id"
    rows = db.execute(text(sql), params).fetchall()
    return [{"id": r[0], "count": r[1]} for r in rows]


def get_products(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    filters: Optional[schemas.ProductFilter] = None,
    sort_by: str = "id",
    sort_dir: str = "desc",
) -> Dict[str, Any]:
    """Get a list of products with pagination and filtering with related data."""
    try:
        logger.debug(f"Getting products with filters: {filters}")
        # Hot reload trigger
        
        # Use direct SQL query to get all product data with related names
        from sqlalchemy import text
        
        base_sql = f"""
        SELECT p.*,
               t.typename as type_name,
               b.brandname as brand_name,
               s.statusname as status_name,
               c.colorname as color_name,
               cond.conditionname as condition_name,
               cur_cond.conditionname as current_condition_name,
               g.gendername as gender_name,
               st.subtypename as subtype_name,
               sty.stylename as style_name,
               sup.company_name as supplier_name,
               sol.soletypename as sole_type_name,
               tsh.toeshapename as toe_shape_name,
               fst.fasteningtypename as fastening_type_name,
               lin.liningname as lining_name,
               ht.heeltypename as heel_type_name,
               lt.lacetypename as lace_type_name,
               pk.packagingname as packaging_name,
               tech.technologyname as technology_name,
               scol.colorname as sole_color_name,
               COALESCE(sold.sold_count, 0) AS sold_count,
               GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold.sold_count, 0), 0) AS available_qty,
               -- Скільки взагалі замовлень на товар (будь-який статус). Потрібно щоб
               -- відрізнити «застарілий знімок Продано» (замовлення є, але всі в не-
               -- продажному стані Обмін/Відміна → sold_count<qty) від легітимного
               -- неформального продажу (замовлень нема зовсім → знімку довіряємо).
               (SELECT COUNT(*)
                FROM order_items oi_oc
                JOIN orders o_oc ON o_oc.id = oi_oc.order_id
                WHERE oi_oc.product_id = p.id AND {real_order_sql("o_oc")}) AS order_count,
               -- «Заброньовано»: є активна бронь (Підтверджено без Оплачено) і товар
               -- ще не повністю проданий. Оверлей над «Непродано» — НЕ змінює sold/available.
               COALESCE(reserved.reserved_count, 0) AS reserved_count,
               (COALESCE(reserved.reserved_count, 0) > 0
                    AND COALESCE(sold.sold_count, 0) < COALESCE(NULLIF(p.quantity, 0), 1)) AS is_reserved,
               COALESCE(dup.dup_brands, 0) AS pnum_dup_brands,
               COALESCE(mc.pending_count, 0) AS pending_candidates_count,
               -- Ростовка: quantity>1 АБО extranote містить "ростовка" АБО (n)-суфікс
               (
                   p.quantity > 1
                   OR LOWER(COALESCE(p.extranote, '')) LIKE '%ростовка%'
                   OR p.productnumber ~ '^.+\\([0-9]+\\)$'
                   OR EXISTS (
                       SELECT 1 FROM products p2
                       WHERE p2.productnumber = p.productnumber || '(1)'
                   )
               ) AS is_rostovka,
               -- Опубліковано в Telegram. Пост лінкується до ОДНОГО товару-рядка
               -- (publications matcher обирає один best product на пост), але один
               -- пост покриває ВСЮ ростовку (всі розміри спільного номера). Тож
               -- маркер вмикаємо для всіх рядків номера, якщо хоч один sibling
               -- має published-пост → tgpub (за нормалізованим productnumber).
               (tgpub.pnum IS NOT NULL) AS published_tg,
               -- Viber: тільки status=published; черга й розклад не є живим постом.
               (viberpub.pnum IS NOT NULL) AS published_viber,
               -- Опубліковано на OLX (active/limited оголошення) — той самий
               -- принцип «за номером» (одне оголошення покриває всю ростовку).
               -- Зелена OLX-іконка ЛИШЕ для повністю публічного ('active');
               -- 'limited' показуємо окремо (olx_status) як «потрібен пакет».
               (olxpub.olx_status = 'active') AS published_olx,
               olxpub.olx_status,
               -- Опубліковано на Prom (товар on_display) — той самий принцип «за номером».
               (prompub.pnum IS NOT NULL) AS published_prom,
               -- Shafa: published=true лише з доказом фактичного оголошення (URL/ID).
               (shafapub.shafa_status IN ('confirmed','manual_existing')) AS published_shafa,
               shafapub.shafa_status,
               -- У публічному інтернет-каталозі (catalog_listings) — «за номером».
               (catpub.pnum IS NOT NULL) AS published_catalog,
               (igpub.pnum IS NOT NULL) AS published_instagram,
               (fbpub.pnum IS NOT NULL) AS published_facebook
        FROM products p
        LEFT JOIN types t ON p.typeid = t.id
        LEFT JOIN brands b ON p.brandid = b.id  
        LEFT JOIN statuses s ON p.statusid = s.id
        LEFT JOIN colors c ON p.colorid = c.id
        LEFT JOIN conditions cond ON p.conditionid = cond.id
        LEFT JOIN conditions cur_cond ON p.current_conditionid = cur_cond.id
        LEFT JOIN genders g ON p.genderid = g.id
        LEFT JOIN subtypes st ON p.subtypeid = st.id
        LEFT JOIN styles sty ON p.styleid = sty.id
        LEFT JOIN sole_types sol ON p.soletypeid = sol.id
        LEFT JOIN toe_shapes tsh ON p.toeshapeid = tsh.id
        LEFT JOIN fastening_types fst ON p.fasteningtypeid = fst.id
        LEFT JOIN linings lin ON p.liningid = lin.id
        LEFT JOIN heel_types ht ON p.heeltypeid = ht.id
        LEFT JOIN lace_types lt ON p.lacetypeid = lt.id
        LEFT JOIN packaging_types pk ON p.packagingid = pk.id
        LEFT JOIN technologies tech ON p.technologyid = tech.id
        LEFT JOIN colors scol ON p.sole_colorid = scol.id
        LEFT JOIN (
            -- «Продано» = Подарунок(7) АБО (Підтверджено(1) І Оплачено).
            -- Повернення(9) кредитує сток назад ЛИШЕ за окремого оплаченого продажу
            -- того ж клієнта на той самий товар; перепродаж іншому не подвоюється.
            -- payment_status_id=1 = «Оплачено». Див. utils/order_status_logic.
            SELECT pc.product_id,
                   GREATEST(SUM(pc.paid_sold) - SUM(LEAST(pc.paid_sold, pc.returns)), 0) AS sold_count
            FROM (
                SELECT oi.product_id, o.client_id,
                       COUNT(*) FILTER (WHERE o.order_status_id = 7
                                          OR (o.order_status_id = 1 AND o.payment_status_id = 1)) AS paid_sold,
                       COUNT(*) FILTER (WHERE o.order_status_id = 9) AS returns
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.product_id IS NOT NULL
                  AND o.order_status_id IN (1, 7, 9)
                GROUP BY oi.product_id, o.client_id
            ) pc
            GROUP BY pc.product_id
        ) sold ON sold.product_id = p.id
        LEFT JOIN (
            -- «Заброньовано» = Підтверджено(1) але НЕ Оплачено (payment_status_id != 1):
            -- активна бронь, що ще НЕ спожила сток (на відміну від sold). NULL-оплата
            -- теж = не оплачено. Див. utils/order_status_logic.PAID_STATUS_ID.
            SELECT oi.product_id, COUNT(*) AS reserved_count
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id = 1
              AND o.payment_status_id IS DISTINCT FROM 1
            GROUP BY oi.product_id
        ) reserved ON reserved.product_id = p.id
        LEFT JOIN (
            SELECT productnumber, COUNT(DISTINCT COALESCE(brandid, 0)) AS dup_brands
            FROM products
            GROUP BY productnumber
            HAVING COUNT(DISTINCT COALESCE(brandid, 0)) > 1
        ) dup ON dup.productnumber = p.productnumber
        LEFT JOIN deliveries d ON p.deliveryid = d.id
        LEFT JOIN suppliers sup ON d.supplier_id = sup.id
        LEFT JOIN (
            SELECT oi.product_id, MAX(o.order_date) AS last_sale_date
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              -- «Продано» = Подарунок(7) АБО (Підтверджено(1) І Оплачено). Саме
              -- «Підтверджено» без оплати ще НЕ продано (бізнес-правило).
              -- payment_status_id=1 = «Оплачено». Див. utils/order_status_logic.
              AND (o.order_status_id = 7
                   OR (o.order_status_id = 1 AND o.payment_status_id = 1))
            GROUP BY oi.product_id
        ) last_sale ON last_sale.product_id = p.id
        LEFT JOIN (
            SELECT new_product_id, COUNT(*) AS pending_count
            FROM merge_candidates
            WHERE status = 'pending'
            GROUP BY new_product_id
        ) mc ON mc.new_product_id = p.id
        LEFT JOIN (
            -- Номери (без #), що мають ≥1 published-пост у TG. Один пост → ВСЯ
            -- ростовка номера вважається опублікованою (всі розміри в одному пості).
            SELECT DISTINCT TRIM(LEADING '#' FROM p2.productnumber) AS pnum
            FROM telegram_posts tp2
            JOIN products p2 ON p2.id = tp2.product_id
            WHERE tp2.tg_status = 'published' AND p2.productnumber IS NOT NULL
        ) tgpub ON tgpub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            SELECT DISTINCT TRIM(LEADING '#' FROM BTRIM(
                       COALESCE(NULLIF(BTRIM(p2.productnumber), ''), vp.product_number)
                   )) AS pnum
            FROM viber_publications vp
            LEFT JOIN products p2 ON p2.id = vp.product_id
            WHERE vp.status = 'published'
              AND COALESCE(NULLIF(BTRIM(p2.productnumber), ''), NULLIF(BTRIM(vp.product_number), '')) IS NOT NULL
        ) viberpub ON viberpub.pnum = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
        LEFT JOIN (
            -- Instagram: лише опубліковане (черга/розклад ще не «опубліковано»).
            SELECT DISTINCT TRIM(LEADING '#' FROM BTRIM(
                       COALESCE(NULLIF(BTRIM(p2.productnumber), ''), ip.product_number)
                   )) AS pnum
            FROM instagram_publications ip
            LEFT JOIN products p2 ON p2.id = ip.product_id
            WHERE ip.status = 'published'
              AND COALESCE(NULLIF(BTRIM(p2.productnumber), ''), NULLIF(BTRIM(ip.product_number), '')) IS NOT NULL
        ) igpub ON igpub.pnum = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
        LEFT JOIN (
            -- Facebook: те саме правило, окрема таблиця.
            SELECT DISTINCT TRIM(LEADING '#' FROM BTRIM(
                       COALESCE(NULLIF(BTRIM(p2.productnumber), ''), fp.product_number)
                   )) AS pnum
            FROM facebook_publications fp
            LEFT JOIN products p2 ON p2.id = fp.product_id
            WHERE fp.status = 'published'
              AND COALESCE(NULLIF(BTRIM(p2.productnumber), ''), NULLIF(BTRIM(fp.product_number), '')) IS NOT NULL
        ) fbpub ON fbpub.pnum = TRIM(LEADING '#' FROM BTRIM(p.productnumber))
        LEFT JOIN (
            -- Найкращий OLX-стан за номером: active (публічне) / limited (обмежене).
            SELECT TRIM(LEADING '#' FROM p2.productnumber) AS pnum,
                   CASE MAX(CASE oa.status WHEN 'active' THEN 3 WHEN 'limited' THEN 2 ELSE 1 END)
                        WHEN 3 THEN 'active' WHEN 2 THEN 'limited' ELSE 'other' END AS olx_status
            FROM olx_adverts oa
            JOIN products p2 ON p2.id = oa.product_id
            WHERE oa.status IN ('active', 'limited') AND p2.productnumber IS NOT NULL
            GROUP BY TRIM(LEADING '#' FROM p2.productnumber)
        ) olxpub ON olxpub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            -- Номери (без #), що Є на Prom: лістинг у дзеркалі (будь-який статус, крім
            -- deleted — тобто й ЧЕРНЕТКА) АБО в черзі експорту (pending). Узгоджено з чіпом «Prom».
            SELECT TRIM(LEADING '#' FROM p2.productnumber) AS pnum
            FROM prom_products pp
            JOIN products p2 ON p2.id = pp.product_id
            WHERE COALESCE(pp.status, '') <> 'deleted' AND p2.productnumber IS NOT NULL
            UNION
            SELECT TRIM(LEADING '#' FROM p3.productnumber) AS pnum
            FROM prom_draft_queue q
            JOIN products p3 ON q.sku = TRIM(LEADING '#' FROM p3.productnumber)
                             OR q.sku LIKE TRIM(LEADING '#' FROM p3.productnumber) || '-%'
        ) prompub ON prompub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            SELECT productnumber AS pnum,
                   CASE MAX(CASE
                       WHEN status = 'confirmed'
                            AND (NULLIF(BTRIM(shafa_url), '') IS NOT NULL
                                 OR NULLIF(BTRIM(shafa_listing_id), '') IS NOT NULL) THEN 4
                       WHEN status = 'manual_existing'
                            AND (NULLIF(BTRIM(shafa_url), '') IS NOT NULL
                                 OR NULLIF(BTRIM(shafa_listing_id), '') IS NOT NULL) THEN 3
                       WHEN status IN ('confirmed','manual_existing','bridge_ready') THEN 2
                       WHEN status = 'waiting_prom' THEN 1
                       ELSE 0 END)
                       WHEN 4 THEN 'confirmed'
                       WHEN 3 THEN 'manual_existing'
                       WHEN 2 THEN 'bridge_ready'
                       WHEN 1 THEN 'waiting_prom'
                   END AS shafa_status
            FROM shafa_publications
            WHERE status IN ('waiting_prom','bridge_ready','confirmed','manual_existing')
            GROUP BY productnumber
        ) shafapub ON shafapub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        LEFT JOIN (
            -- Номери (без #), опубліковані в ПУБЛІЧНОМУ інтернет-каталозі (Telegram
            -- Mini App): catalog_listings.is_published, ключ=productnumber → вся
            -- ростовка. Узгоджено з чіпом «У каталозі» в картці товару.
            SELECT DISTINCT TRIM(LEADING '#' FROM cl.productnumber) AS pnum
            FROM catalog_listings cl
            WHERE cl.is_published
        ) catpub ON catpub.pnum = TRIM(LEADING '#' FROM p.productnumber)
        """
        
        where_conditions, params = _build_product_where(filters)
        # Build WHERE clause
        if where_conditions:
            base_sql += " WHERE " + " AND ".join(where_conditions)
        
        # Count total
        count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS subquery"
        total_result = db.execute(text(count_sql), params)
        total = total_result.scalar()
        
        # Add ORDER BY — compound sort modes + simple column fallback
        sort_map = {
            # "Найновіші" = нещодавно ДОДАНІ в базу (p.created_at) — напр. дописав
            # товар у старий завіз. updated_at не годиться: парсинг бампить його
            # для всіх рядків. Тай-брейк p.id для стабільної пагінації.
            "created_at":     "p.created_at DESC NULLS LAST, p.id DESC",
            "created_at_asc": "p.created_at ASC NULLS LAST, p.id ASC",
            # "За датою завозу" = реальна дата завозу = COALESCE(d.deliverydate, p.dateadded).
            # d.deliverydate з аркуша журналу (назва = дата завозу); якщо товар
            # ще не прив'язаний до delivery — fallback p.dateadded, інакше через
            # NULLS LAST він провалився б у кінець.
            # ВИНЯТОК: загублені (is_lost, Воркспейс/Старі) НЕ мають реального завозу,
            # а dateadded=date.today() → інакше вони фейково вискакували б нагору.
            # Тож для них дату завозу лишаємо NULL → NULLS LAST → донизу.
            "delivery_date":     "COALESCE(d.deliverydate, CASE WHEN p.is_lost THEN NULL ELSE p.dateadded END) DESC NULLS LAST, p.id DESC",
            "delivery_date_asc": "COALESCE(d.deliverydate, CASE WHEN p.is_lost THEN NULL ELSE p.dateadded END) ASC NULLS LAST, p.id ASC",
            "last_sold":      "last_sale.last_sale_date DESC NULLS LAST, p.id DESC",
            "price_desc":     "p.price DESC NULLS LAST",
            "price_asc":      "p.price ASC NULLS LAST",
        }
        sort_key = sort_by if sort_by in sort_map else f"{sort_by}_{sort_dir}"
        if sort_key in sort_map:
            base_sql += f" ORDER BY {sort_map[sort_key]}"
        else:
            # Fallback for simple column sorts
            allowed_simple = {"id", "dateadded", "price", "created_at", "updated_at"}
            if sort_by in allowed_simple:
                order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
                base_sql += f" ORDER BY p.{sort_by} {order_dir}"
            else:
                base_sql += " ORDER BY p.created_at DESC"
        
        # Add LIMIT and OFFSET
        base_sql += " LIMIT :limit OFFSET :offset"
        params['limit'] = limit
        params['offset'] = skip
        
        # Execute query
        rows = db.execute(text(base_sql), params).fetchall()

        # Множина номерів з фото — будуємо ОДИН раз на сторінку (не на рядок).
        try:
            from services.product_images import get_photo_pnum_set, product_has_photo
        except ImportError:
            from backend.services.product_images import get_photo_pnum_set, product_has_photo
        try:
            _photo_set = get_photo_pnum_set()
        except Exception as _pe:
            logger.warning(f"get_products: photo-set unavailable: {_pe}")
            _photo_set = frozenset()

        # Convert rows to dictionaries using _mapping (safe regardless of column order)
        items = []
        for row in rows:
            m = row._mapping
            product_dict = {
                'id': m.get('id'),
                'productnumber': m.get('productnumber'),
                # Ключовий номер для показу: реальний, або перший клон-номер поки
                # реального нема (див. effective_product_number). productnumber
                # лишається сирим — UI розрізняє «клон-похідний» через display_number.
                'display_number': effective_product_number(
                    m.get('productnumber'), m.get('clonednumbers')),
                'clonednumbers': m.get('clonednumbers'),
                'official_photos_from': m.get('official_photos_from'),
                'model': m.get('model'),
                'collection': m.get('collection'),
                'marking': m.get('marking'),
                'gtin': m.get('gtin'),
                'year': m.get('year'),
                'description': m.get('description'),
                'extranote': m.get('extranote'),
                'price': m.get('price'),
                'oldprice': m.get('oldprice'),
                'dateadded': str(m.get('dateadded')) if m.get('dateadded') else None,
                'sizeeu': m.get('sizeeu'),
                'size_letter': m.get('size_letter'),
                'sizeua': m.get('sizeua'),
                'sizeusa': m.get('sizeusa'),
                'sizeuk': m.get('sizeuk'),
                'sizejp': m.get('sizejp'),
                'sizecn': m.get('sizecn'),
                'measurementscm': m.get('measurementscm'),
                'quantity': m.get('quantity'),
                'mainimage': m.get('mainimage'),
                'is_visible': m.get('is_visible'),
                'typeid': m.get('typeid'),
                'subtypeid': m.get('subtypeid'),
                'brandid': m.get('brandid'),
                'genderid': m.get('genderid'),
                'colorid': m.get('colorid'),
                'ownercountryid': m.get('ownercountryid'),
                'manufacturercountryid': m.get('manufacturercountryid'),
                'statusid': m.get('statusid'),
                'conditionid': m.get('conditionid'),
                'current_conditionid': m.get('current_conditionid'),
                'styleid': m.get('styleid'),
                'season': m.get('season'),
                'dimensions': m.get('dimensions'),
                'geometric_shape': m.get('geometric_shape'),
                'width': m.get('width'),
                'importid': m.get('importid'),
                'deliveryid': m.get('deliveryid'),
                'created_at': str(m.get('created_at')) if m.get('created_at') else None,
                'updated_at': str(m.get('updated_at')) if m.get('updated_at') else None,
                'type_name': m.get('type_name'),
                'brand_name': m.get('brand_name'),
                'status_name': m.get('status_name'),
                'color_name': m.get('color_name'),
                'condition_name': m.get('condition_name'),
                'current_condition_name': m.get('current_condition_name'),
                'gender_name': m.get('gender_name'),
                'supplier_name': m.get('supplier_name'),
                'subtype_name': m.get('subtype_name'),
                'style_name': m.get('style_name'),
                'measurementscm_min': m.get('measurementscm_min'),
                'measurementscm_max': m.get('measurementscm_max'),
                'soletypeid': m.get('soletypeid'),
                'toeshapeid': m.get('toeshapeid'),
                'fasteningtypeid': m.get('fasteningtypeid'),
                'liningid': m.get('liningid'),
                'heeltypeid': m.get('heeltypeid'),
                'lacetypeid': m.get('lacetypeid'),
                'packagingid': m.get('packagingid'),
                'technologyid': m.get('technologyid'),
                'sole_colorid': m.get('sole_colorid'),
                'sole_type_name': m.get('sole_type_name'),
                'toe_shape_name': m.get('toe_shape_name'),
                'fastening_type_name': m.get('fastening_type_name'),
                'lining_name': m.get('lining_name'),
                'heel_type_name': m.get('heel_type_name'),
                'lace_type_name': m.get('lace_type_name'),
                'packaging_name': m.get('packaging_name'),
                'technology_name': m.get('technology_name'),
                'sole_color_name': m.get('sole_color_name'),
                'sold_count': m.get('sold_count', 0),
                'available_qty': m.get('available_qty'),
                'order_count': int(m.get('order_count') or 0),
                'reserved_count': int(m.get('reserved_count') or 0),
                'is_reserved': bool(m.get('is_reserved', False)),
                'pnum_dup_brands': m.get('pnum_dup_brands', 0),
                'pending_candidates_count': int(m.get('pending_candidates_count') or 0),
                'is_rostovka': bool(m.get('is_rostovka', False)),
                'published_tg': bool(m.get('published_tg', False)),
                'published_viber': bool(m.get('published_viber', False)),
                'published_instagram': bool(m.get('published_instagram', False)),
                'published_facebook': bool(m.get('published_facebook', False)),
                'published_olx': bool(m.get('published_olx', False)),
                'olx_status': m.get('olx_status'),
                'published_prom': bool(m.get('published_prom', False)),
                'published_shafa': bool(m.get('published_shafa', False)),
                'shafa_status': m.get('shafa_status'),
                'published_catalog': bool(m.get('published_catalog', False)),
                # Позичені студійні фото теж «є фото». Донор (official_photos_from)
                # — це той самий черевик під іншим номером у наступному завозі;
                # картка показує його кадри, тож і маркер у списку, і прев'ю на
                # ховері мусять про них знати. Без цього рядок з донором виглядав
                # як «Без фото», хоча в картці фото було (напр. #Ф4350 ← Ф4336).
                'has_photo': (
                    product_has_photo(m.get('productnumber'), _photo_set)
                    or product_has_photo(m.get('official_photos_from'), _photo_set)
                ),
            }
            items.append(product_dict)
        
        result = {
            "items": items,
            "total": total,
            "page": skip // limit + 1 if limit > 0 else 1,
            "size": limit,
            "pages": (total + limit - 1) // limit if limit > 0 else 1
        }
        logger.debug(f"Retrieved products: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting products: {str(e)}")
        raise

def get_next_product_number(db: Session, prefix: str = "Ф") -> str:
    """Згенерувати наступний вільний номер товару для літерного префікса (політика max+1).

    Реал-тайм снапшот УСІХ productnumber + clonednumbers → результат гарантовано не
    збігається ні з чим (вкл. ростовка-клони й «дірки» від видалених). prefix='' →
    цифрова серія без літери. Ростовка-суфікс ('-N') ігнорується для лічби бази.
    Повертає канонічну #-форму (напр. '#Ф4114'). Політика max+1 узгоджена з користувачем
    (2026-06-19): ніколи не перевикористовувати старі номери → нуль колізій з історією.
    """
    try:
        from utils.productnumber_normalizer import normalize as _norm, _HOMOGLYPH_TO_CYR
    except ImportError:
        from backend.utils.productnumber_normalizer import normalize as _norm, _HOMOGLYPH_TO_CYR
    pref = (prefix or "").strip().lstrip("#").upper().translate(_HOMOGLYPH_TO_CYR)
    pat = re.compile(rf"^{re.escape(pref)}(\d+)(?:-\d+)?$")
    rows = db.execute(text("SELECT productnumber, clonednumbers FROM products")).fetchall()
    max_n = 0
    for pn, clones in rows:
        toks = [pn] + (re.split(r"[;,/\s]+", clones) if clones else [])
        for tok in toks:
            if not tok:
                continue
            t = tok.strip().lstrip("#").upper().translate(_HOMOGLYPH_TO_CYR)
            m = pat.match(t)
            if m:
                n = int(m.group(1))
                if n > max_n:
                    max_n = n
    candidate = f"{pref}{max_n + 1}"
    return _norm(candidate) or f"#{candidate}"


def check_number_conflict(db: Session, number: str, exclude_id: Optional[int] = None,
                          rostovka_ref: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Чи зайнятий `number` ІНШИМ товаром (productnumber або clonednumbers)?
    Повертає {id, productnumber} конфліктного товару або None.

    ⚠️ РОСТОВКА ≠ дубль: якщо передано `rostovka_ref` (бренд/модель/розмір товару, який
    переіменовують) і збіжний товар = ТОЙ САМИЙ бренд+модель, але ІНШИЙ розмір — це
    легітимна ростовка (один номер на кілька розмірів), НЕ конфлікт. Той самий розмір під
    одним номером = справжній дубль → конфлікт. Homoglyph-fold (Lat→Cyr), регістронезалежно."""
    try:
        from utils.productnumber_normalizer import _HOMOGLYPH_TO_CYR
    except ImportError:
        from backend.utils.productnumber_normalizer import _HOMOGLYPH_TO_CYR

    def _canon(s: str) -> str:
        return (s or "").strip().lstrip("#").rstrip(";").strip().upper().translate(_HOMOGLYPH_TO_CYR)

    def _s(x) -> str:
        return str(x).strip().lower() if x is not None else ""

    target = _canon(number)
    if not target:
        return None
    rows = db.execute(
        text("SELECT id, productnumber, clonednumbers, brandid, model, sizeeu, size_letter "
             "FROM products WHERE id != :ex"),
        {"ex": exclude_id if exclude_id is not None else -1}).fetchall()
    for pid, pn, clones, brandid, model, sizeeu, size_letter in rows:
        toks = [pn] + (re.split(r"[;,/\s]+", clones) if clones else [])
        if not any(tok and _canon(tok) == target for tok in toks):
            continue
        # Збіг номера. Перевіряємо, чи це ростовка-близнюк (не дубль).
        if rostovka_ref is not None:
            same_brand = brandid == rostovka_ref.get("brandid")          # None==None ок
            model_ok = _s(model) == _s(rostovka_ref.get("model"))         # обидва порожні = ок
            diff_size = (_s(sizeeu) != _s(rostovka_ref.get("sizeeu"))) or \
                        (_s(size_letter) != _s(rostovka_ref.get("size_letter")))
            if same_brand and model_ok and diff_size:
                continue  # легітимна ростовка — не конфлікт
        return {"id": int(pid), "productnumber": pn}
    return None


def create_product(db: Session, product: schemas.ProductCreate) -> models.Product:
    """Create a new product"""
    try:
        db_product = models.Product(**product.dict())
        db.add(db_product)
        db.commit()
        db.refresh(db_product)
        logger.debug(f"Created product: {db_product}")
        return db_product
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating product: {str(e)}")
        raise

# Phase 2a: fields that, when edited in the app, get locked against parser
# overwrite (the parser restores the user's value after a reparse). Keep in sync
# with the frontend inline-edit set and PRODUCT_LOCK_FIELDS in sheets_parser.
LOCKABLE_PRODUCT_FIELDS = {
    "price", "oldprice", "model", "marking", "description", "extranote", "season",
    # Model-level scalar fields (same across a rostovka) — full sheet sync.
    "year", "width", "clonednumbers", "collection", "geometric_shape",
    # Per-item scalar fields (unique per size-row) — see PER_ITEM_FIELDS below.
    "sizeeu", "size_letter", "measurementscm", "dimensions", "gtin",
    # Shoe-lookup FKs (model-level; edited by NAME, written back as name). See
    # LOOKUP_NAME_FIELDS below + SHOE_FK_WRITEBACK in sheets_parser.
    "heeltypeid", "lacetypeid", "packagingid", "technologyid", "sole_colorid",
    # «Інше» shoe-lookups (model-level; edited by NAME) — same pattern.
    "soletypeid", "toeshapeid", "fasteningtypeid", "liningid",
    # Main color (model-level FK → colors; edited by NAME like sole color).
    "colorid",
    # Класифікація (model-level; edited via dropdown by id). Write-back as name.
    "typeid", "subtypeid", "styleid", "brandid", "genderid",
    # Condition (Стан/поточний стан) — PER-ITEM (not propagated to rostovka siblings).
    "current_conditionid",
    # Країна-виробник — model-level FK → countries; edited by NAME (Виробник).
    "manufacturercountryid",
}

# Inline-edit name field (from ProductUpdate) → (FK id column, lookup table, name column).
# update_product resolves the typed name → FK id (get-or-create, case-insensitive).
LOOKUP_NAME_FIELDS = {
    "heel_type_name":  ("heeltypeid",   "heel_types",      "heeltypename"),
    "lace_type_name":  ("lacetypeid",   "lace_types",      "lacetypename"),
    "packaging_name":  ("packagingid",  "packaging_types", "packagingname"),
    "technology_name": ("technologyid", "technologies",    "technologyname"),
    "sole_color_name": ("sole_colorid", "colors",          "colorname"),
    "sole_type_name":      ("soletypeid",     "sole_types",      "soletypename"),
    "toe_shape_name":      ("toeshapeid",     "toe_shapes",      "toeshapename"),
    "fastening_type_name": ("fasteningtypeid", "fastening_types", "fasteningtypename"),
    "lining_name":         ("liningid",       "linings",         "liningname"),
    "color_name":      ("colorid",      "colors",          "colorname"),
    "current_condition_name": ("current_conditionid", "conditions", "conditionname"),
    "manufacturer_country_name": ("manufacturercountryid", "countries", "countryname"),
    # Класифікація (1.4): вільний ввід за назвою. subtype_name НЕ тут — йому
    # потрібен typeid-контекст (обробляється окремо в update_product).
    "brand_name": ("brandid", "brands", "brandname"),
    "type_name":  ("typeid",  "types",  "typename"),
    "style_name": ("styleid", "styles", "stylename"),
}

# Описові довідники, де канонічна форма — З МАЛОЇ літери (у БД 100% lowercase).
# Якщо нова назва приходить капіталізованою через автокапіталізацію WKWebView
# («Шнурівка», «Плоска», «Круглий») — нормалізуємо першу літеру до lower перед
# INSERT, щоб не плодити різнорегістрові дублікати того самого значення.
# Користувацька стать/тип/бренд НЕ чіпаємо (вони бувають з великої: Жіноча/Nike).
# ⚠️ technologies НЕ включаємо — це власні назви (SOFTFOAM+, BOOST, Gore-Tex, GEL),
# лоуеркейс зіпсував би їх ('sOFTFOAM+'). Так само materials лишаємо як ввів користувач.
LOWERCASE_LOOKUP_TABLES = {
    "sole_types", "toe_shapes", "fastening_types", "lace_types", "heel_types",
    "linings", "colors",
}


def _resolve_lookup_id_by_name(db: Session, table: str, name_col: str, value: str) -> Optional[int]:
    """Case-insensitive get-or-create in a single-FK lookup table. Returns id.

    ⚠️ SQL LOWER()/ILIKE do NOT case-fold Cyrillic in this DB (C collation), so the
    case-insensitive step is done in Python (str.lower() handles Cyrillic). See
    feedback_ilike_exact_match. Lookups are tiny, so the full scan is cheap.
    """
    val = (value or "").strip()
    if not val:
        return None
    if table == "types":
        val = canonicalize_type_name(val) or ""
        if not val:
            return None
    if table == "styles":
        val = canonicalize_style_name(val) or ""
        if not val:
            return None
    # Стать — рівно 3 канонічні значення. Канонізуємо ввід («Жіночі»→«Жіноча»),
    # щоб редагування в картці не плодило дубль-стать. Незнане → None (не створюємо).
    if table == "genders":
        try:
            from utils.gender_normalizer import normalize_gender
        except ImportError:
            from backend.utils.gender_normalizer import normalize_gender
        val = normalize_gender(val)
        if not val:
            return None
    # 1) exact match (fast path; covers the common "kept the pre-filled value" case)
    row = db.execute(
        text(f"SELECT id FROM {table} WHERE TRIM({name_col}) = :v LIMIT 1"),
        {"v": val},
    ).fetchone()
    if row:
        return int(row[0])
    # 2) Cyrillic-correct case-insensitive fold in Python
    folded = val.lower()
    for rid, nm in db.execute(text(f"SELECT id, {name_col} FROM {table}")).fetchall():
        if (nm or "").strip().lower() == folded:
            return int(rid)
    # 3) create new — для описових таблиць (sole_types/toe_shapes/...) канон у БД
    #    — з малої літери, тож нормалізуємо першу літеру вниз перед INSERT, щоб не
    #    плодити дублікатів типу «Шнурівка» поряд із «шнурівка».
    insert_val = val
    if table in LOWERCASE_LOOKUP_TABLES and insert_val[:1].isupper():
        insert_val = insert_val[0].lower() + insert_val[1:]
    new_id = db.execute(
        text(f"INSERT INTO {table} ({name_col}) VALUES (:v) "
             f"ON CONFLICT ({name_col}) DO UPDATE SET {name_col} = EXCLUDED.{name_col} "
             f"RETURNING id"),
        {"v": insert_val},
    ).fetchone()
    return int(new_id[0]) if new_id else None


def _resolve_subtype_id_by_name(db: Session, value: str, typeid: Optional[int]) -> Optional[int]:
    """subtype_name → subtypeid з typeid-контекстом. subtypename УНІКАЛЬНИЙ глобально,
    тож наявна назва переюзається (її typeid не чіпаємо — вона вже комусь належить);
    НОВА створюється з typeid поточного товару, щоб не плодити «сирітських» підтипів."""
    val = canonicalize_subtype_name(value) or ""
    if not val:
        return None
    folded = val.lower()
    for rid, nm in db.execute(text("SELECT id, subtypename FROM subtypes")).fetchall():
        if (nm or "").strip().lower() == folded:
            return int(rid)
    new = db.execute(
        text("INSERT INTO subtypes (subtypename, typeid) VALUES (:v, :t) "
             "ON CONFLICT (subtypename) DO UPDATE SET subtypename = EXCLUDED.subtypename "
             "RETURNING id"),
        {"v": val, "t": typeid},
    ).fetchone()
    return int(new[0]) if new else None


def resolve_lookup_name(db: Session, fk_field: str, fk_id: Optional[int]) -> Optional[str]:
    """FK id → canonical name string (for sheet write-back). None if id is None."""
    if fk_id is None:
        return None
    tbl_col = {
        "heeltypeid":          ("heel_types",      "heeltypename"),
        "lacetypeid":          ("lace_types",      "lacetypename"),
        "packagingid":         ("packaging_types", "packagingname"),
        "technologyid":        ("technologies",    "technologyname"),
        "sole_colorid":        ("colors",          "colorname"),
        "soletypeid":          ("sole_types",      "soletypename"),
        "toeshapeid":          ("toe_shapes",      "toeshapename"),
        "fasteningtypeid":     ("fastening_types", "fasteningtypename"),
        "liningid":            ("linings",         "liningname"),
        "colorid":             ("colors",          "colorname"),
        "current_conditionid": ("conditions",      "conditionname"),
        # Класифікація (для write-back id→name у відповідну колонку журналу).
        "typeid":              ("types",           "typename"),
        "subtypeid":           ("subtypes",        "subtypename"),
        "styleid":             ("styles",          "stylename"),
        "brandid":             ("brands",          "brandname"),
        "genderid":            ("genders",         "gendername"),
        # Країна-виробник. Без цього рядка writeback писав у журнал сирий FK-id
        # («3» замість «Китай»), парсер читав його назад як назву країни й плодив
        # країни-привиди з іменами-числами.
        "manufacturercountryid": ("countries",      "countryname"),
    }.get(fk_field)
    if not tbl_col:
        return None
    row = db.execute(
        text(f"SELECT {tbl_col[1]} FROM {tbl_col[0]} WHERE id = :id"),
        {"id": fk_id},
    ).fetchone()
    return row[0] if row else None


# FK fields edited by name (used by the router to map id→name for write-back).
SHOE_FK_NAME_FIELDS = {"heeltypeid", "lacetypeid", "packagingid", "technologyid", "sole_colorid",
                       "soletypeid", "toeshapeid", "fasteningtypeid", "liningid",
                       "colorid", "current_conditionid",
                       # Класифікація — write-back as canonical name.
                       "typeid", "subtypeid", "styleid", "brandid", "genderid",
                       # Країна-виробник — теж FK, теж пишеться назвою.
                       "manufacturercountryid"}
# Propagation policy across rostovka siblings (same productnumber):
#   PER_ITEM_FIELDS — unique per pair/size, NEVER propagated (e.g. condition/розмір).
#   PRICE_FIELDS    — propagated only to same-condition siblings without their own locked price.
#   (anything else lockable) — per-model: propagated to all siblings.
PER_ITEM_FIELDS = {"current_conditionid", "conditionid",
                   "sizeeu", "size_letter", "measurementscm", "dimensions", "gtin"}
PRICE_FIELDS = {"price", "oldprice"}


def _merge_lock(prod, fields: set) -> None:
    """Add `fields` to a product's manually_edited_fields and bump timestamp."""
    existing = set()
    if prod.manually_edited_fields:
        existing = {x.strip() for x in prod.manually_edited_fields.split(",") if x.strip()}
    prod.manually_edited_fields = ",".join(sorted(existing | fields))
    prod.manually_edited_at = datetime.utcnow()


# Measurements inline-edit ──────────────────────────────────────────────────
# name → (min_col, max_col). Рядок-діапазон ("26"/"25-27") → min/max (per-item:
# заміри унікальні на розмір ростовки, тож НЕ пропагуються; write-back per-item).
MEASUREMENT_EDIT_FIELDS = {
    "length":         ("measurements_length_min",         "measurements_length_max"),
    "pog":            ("measurements_pog_min",            "measurements_pog_max"),
    "pob":            ("measurements_pob_min",            "measurements_pob_max"),
    "pot":            ("measurements_pot_min",            "measurements_pot_max"),
    "sleeve":         ("measurements_sleeve_min",         "measurements_sleeve_max"),
    "height":         ("measurements_height_min",         "measurements_height_max"),
    "sole_thickness": ("measurements_sole_thickness_min", "measurements_sole_thickness_max"),
    "heel":           ("measurements_heel_min",           "measurements_heel_max"),
}


def _fmt_measure_num(v) -> str:
    """26.0 → '26'; 26.5 → '26.5'; None → ''."""
    if v is None:
        return ""
    try:
        fv = float(v)
        return str(int(fv)) if fv == int(fv) else str(fv)
    except (TypeError, ValueError):
        return ""


def _fmt_measure_range(mn, mx) -> str:
    """(min,max) → рядок для write-back: '26' (min==max) / '25-27' / ''."""
    if mn is None and mx is None:
        return ""
    if mn is not None and mx is not None and float(mn) == float(mx):
        return _fmt_measure_num(mn)
    if mn is not None and mx is not None:
        return f"{_fmt_measure_num(mn)}-{_fmt_measure_num(mx)}"
    return _fmt_measure_num(mn if mn is not None else mx)


def _apply_measurements_edit(db_product, edits: Dict[str, str]) -> Dict[str, str]:
    """Парсить рядок-діапазон кожного заміру → *_min/*_max на товарі. Повертає
    {meas_<name>: canonical_range_str} для реально оброблених (для write-back)."""
    try:
        from scripts.sheets_parser import _parse_measurement_range
    except ImportError:
        from backend.scripts.sheets_parser import _parse_measurement_range
    out: Dict[str, str] = {}
    for name, rng in edits.items():
        cols = MEASUREMENT_EDIT_FIELDS.get(name)
        if not cols:
            continue
        mn, mx = _parse_measurement_range(rng or "")
        setattr(db_product, cols[0], mn)
        setattr(db_product, cols[1], mx)
        out[f"meas_{name}"] = _fmt_measure_range(mn, mx)
    return out


# Materials inline-edit ─────────────────────────────────────────────────────
# Канонічні позиції матеріалів (узгоджено з MATERIAL_POSITIONS у sheets_parser).
# ⚠️ При додаванні позиції в парсер — додати і СЮДИ: інакше _apply_materials_edit
# мовчки відкидає редагування цієї позиції в картці («значення зникає», так було
# з midsole: у парсері з 2026-06-10, тут забули).
MATERIAL_POSITIONS = {"upper", "middle", "insole", "sole", "midsole", "membrane"}


def _get_or_create_material_id(db: Session, name: str) -> Optional[int]:
    """Канонічна назва матеріалу (lowercase) → material_id. Get-or-create:
    матеріали — відкрита лексика (на відміну від бренду/типу), тож невідоме
    значення створюємо (category='other'), а не глушимо. Порожнє → None."""
    val = (name or "").strip().lower()
    if not val:
        return None
    row = db.execute(
        text("SELECT id FROM materials WHERE materialname = :n LIMIT 1"), {"n": val}
    ).fetchone()
    if row:
        return int(row[0])
    new = db.execute(
        text("INSERT INTO materials (materialname, category) VALUES (:n, 'other') "
             "ON CONFLICT (materialname) DO UPDATE SET materialname = EXCLUDED.materialname "
             "RETURNING id"),
        {"n": val},
    ).fetchone()
    return int(new[0]) if new else None


def _apply_materials_edit(db: Session, product_id: int, by_position: Dict[str, str]) -> Dict[str, str]:
    """Full-replace матеріалів для КОЖНОЇ наданої позиції. Повертає
    {position: canonical_csv} лише для реально оброблених позицій (для лока +
    write-back). Невідома позиція ігнорується. "" → очищає позицію."""
    applied: Dict[str, str] = {}
    for position, csv in by_position.items():
        pos = (position or "").strip()
        if pos not in MATERIAL_POSITIONS:
            continue
        names = [n.strip().lower() for n in (csv or "").split(",") if n.strip()]
        # Wipe + re-insert у порядку (як парсерний _apply_product_materials).
        db.execute(
            text("DELETE FROM product_materials WHERE product_id = :pid AND position = :pos"),
            {"pid": product_id, "pos": pos},
        )
        ord_idx = 0
        canon: list[str] = []
        for nm in names:
            mid = _get_or_create_material_id(db, nm)
            if mid is None:
                continue
            db.execute(
                text("INSERT INTO product_materials (product_id, position, material_id, ord) "
                     "VALUES (:pid, :pos, :mid, :ord) "
                     "ON CONFLICT (product_id, position, material_id) DO UPDATE SET ord = EXCLUDED.ord"),
                {"pid": product_id, "pos": pos, "mid": mid, "ord": ord_idx},
            )
            ord_idx += 1
            canon.append(nm)
        applied[pos] = ", ".join(canon)
    return applied


def get_delivery_name(db: Session, delivery_id: Optional[int]) -> Optional[str]:
    """Journal sheet title a product belongs to (deliveries.deliveryname)."""
    if not delivery_id:
        return None
    row = db.execute(text("SELECT deliveryname FROM deliveries WHERE id = :id"),
                     {"id": delivery_id}).fetchone()
    return row[0] if row else None


def update_product(db: Session, product_id: int, product: schemas.ProductUpdate) -> Optional[models.Product]:
    """Update an existing product.

    Edited LOCKABLE_PRODUCT_FIELDS are recorded in manually_edited_fields so the
    parser won't overwrite them on reparse, and propagated to all sibling rows of
    the same productnumber (per-model semantics for a rostovka).
    """
    try:
        db_product = db.query(models.Product).filter(models.Product.id == product_id).first()
        if db_product:
            update_data = product.dict(exclude_unset=True)

            # Materials (per-position CSV) — обробляються окремо (не ORM-колонка).
            # Витягуємо ДО загального setattr-циклу. Legacy-поле `materials` (структурне)
            # інлайн-UI не шле — popаємо, щоб не присвоїти relationship напряму.
            materials_by_position = update_data.pop("materials_by_position", None)
            update_data.pop("materials", None)
            measurements_edit = update_data.pop("measurements_edit", None)

            # These exact labels were reviewed as subtypes, not styles.  A
            # direct edit must obey the same cross-column rule as both parsers,
            # including replacement of a previously populated subtype.
            if "style_name" in update_data:
                moved_subtype = subtype_from_style_name(update_data["style_name"])
                if moved_subtype:
                    update_data["subtype_name"] = moved_subtype
                    update_data["style_name"] = ""

            # Resolve Type/Subtype together. This moves misplaced season words
            # into ``season``, splits reviewed combined labels and prevents a
            # subtype that merely repeats the type from being written back.
            if "type_name" in update_data or "subtype_name" in update_data:
                type_was_supplied = "type_name" in update_data
                subtype_was_supplied = "subtype_name" in update_data
                current_type_name = resolve_lookup_name(
                    db, "typeid", db_product.typeid
                )
                current_subtype_name = resolve_lookup_name(
                    db, "subtypeid", db_product.subtypeid
                )
                requested_type = update_data.get("type_name", current_type_name)
                requested_subtype = update_data.get("subtype_name", current_subtype_name)
                moved_style = next(
                    (
                        value
                        for value in (
                            style_from_taxonomy_name(requested_type),
                            style_from_taxonomy_name(requested_subtype),
                        )
                        if value
                    ),
                    None,
                )
                moved_packaging = next(
                    (
                        value
                        for value in (
                            packaging_from_taxonomy_name(requested_type),
                            packaging_from_taxonomy_name(requested_subtype),
                        )
                        if value
                    ),
                    None,
                )
                current_style = resolve_lookup_name(db, "styleid", db_product.styleid)
                requested_style = update_data.get("style_name", current_style)
                force_style_relocation = any(
                    style_from_taxonomy_overrides_existing(value)
                    for value in (requested_type, requested_subtype)
                )
                current_packaging = resolve_lookup_name(
                    db, "packagingid", db_product.packagingid
                )
                requested_packaging = update_data.get(
                    "packaging_name", current_packaging
                )
                style_conflict = bool(
                    moved_style
                    and requested_style
                    and canonicalize_style_name(requested_style) != moved_style
                    and not force_style_relocation
                )
                packaging_conflict = bool(
                    moved_packaging
                    and requested_packaging
                    and str(requested_packaging).strip().casefold()
                    != moved_packaging.casefold()
                )
                if moved_style and (force_style_relocation or not style_conflict):
                    update_data["style_name"] = moved_style
                if moved_packaging and not packaging_conflict:
                    update_data["packaging_name"] = moved_packaging
                normalized_type, normalized_subtype, moved_seasons = (
                    normalize_taxonomy_pair(
                        requested_type,
                        requested_subtype,
                    )
                )
                # Never clear the only copy when the destination already holds
                # a different value; retain the taxonomy source for review.
                if style_conflict or packaging_conflict:
                    if (
                        style_from_taxonomy_name(requested_type)
                        or packaging_from_taxonomy_name(requested_type)
                    ):
                        normalized_type = requested_type
                    if (
                        style_from_taxonomy_name(requested_subtype)
                        or packaging_from_taxonomy_name(requested_subtype)
                    ):
                        normalized_subtype = requested_subtype
                if type_was_supplied or normalized_type != current_type_name:
                    update_data["type_name"] = normalized_type or ""
                if subtype_was_supplied or normalized_subtype != current_subtype_name:
                    update_data["subtype_name"] = normalized_subtype or ""
                if moved_seasons:
                    update_data["season"] = merge_season_values(
                        update_data.get("season", db_product.season),
                        *moved_seasons,
                    )

            # Inline-edit by NAME: translate typed lookup names → FK id columns
            # (get-or-create, case-insensitive). "" / null clears the FK.
            for name_key, (fk_field, table, name_col) in LOOKUP_NAME_FIELDS.items():
                if name_key in update_data:
                    raw = update_data.pop(name_key)
                    update_data[fk_field] = _resolve_lookup_id_by_name(db, table, name_col, raw)

            # Підтип — ПІСЛЯ циклу вище: type_name уже зрезолвлено в typeid, тож
            # новий підтип створюється з коректним typeid-контекстом.
            if "subtype_name" in update_data:
                raw = update_data.pop("subtype_name")
                target_typeid = update_data.get("typeid", db_product.typeid)
                update_data["subtypeid"] = _resolve_subtype_id_by_name(db, raw, target_typeid)

            # Markdown: on a price DECREASE, preserve the previous price into
            # "Стара ціна" if it is empty (per user rule). Existing oldprice and
            # price increases are left untouched.
            if update_data.get("price") is not None:
                try:
                    new_price = float(update_data["price"])
                    cur_price = float(db_product.price or 0)
                    cur_oldprice = float(db_product.oldprice or 0)
                    if new_price < cur_price and not cur_oldprice:
                        update_data["oldprice"] = cur_price
                except (TypeError, ValueError):
                    pass

            for key, value in update_data.items():
                setattr(db_product, key, value)

            newly_locked = {k for k in update_data.keys() if k in LOCKABLE_PRODUCT_FIELDS}
            if newly_locked:
                _merge_lock(db_product, newly_locked)

                # Per-field propagation policy across rostovka siblings (same number):
                #   • PER_ITEM (стан) — NEVER propagate (unique per pair).
                #   • price/oldprice — only to siblings with the SAME current_conditionid
                #     AND no own manually-locked price (a diverged pair keeps its price).
                #   • everything else (model/marking/description/season/...) — per-model
                #     (propagate to all siblings) so the rostovka stays consistent.
                siblings = db.query(models.Product).filter(
                    models.Product.productnumber == db_product.productnumber,
                    models.Product.id != db_product.id,
                ).all()
                for sib in siblings:
                    sib_locked = set()
                    if sib.manually_edited_fields:
                        sib_locked = {x.strip() for x in sib.manually_edited_fields.split(",") if x.strip()}
                    same_condition = sib.current_conditionid == db_product.current_conditionid
                    propagate_price = same_condition and ("price" not in sib_locked)

                    applied = set()
                    for f in newly_locked:
                        if f in PER_ITEM_FIELDS:
                            continue  # стан — унікальний на пару
                        if f in PRICE_FIELDS and not propagate_price:
                            continue  # diverged pair keeps its own price
                        setattr(sib, f, getattr(db_product, f))
                        applied.add(f)
                    if applied:
                        _merge_lock(sib, applied)

            # official_photos_from — МОДЕЛЬ-РІВНЕВЕ поле: студійні фото однакові для
            # всієї ростовки. Прив'язку (чи її очищення), зроблену на одному записі,
            # поширюємо на ВСІ записи того ж productnumber, щоб користувач не мусив
            # повторювати її для кожного розміру. НЕ в LOCKABLE_PRODUCT_FIELDS — це
            # app-internal поле (нема колонки в журналі, парсер його не чіпає), тому
            # пропагуємо явно тут, окремо від lock/write-back механіки.
            if "official_photos_from" in update_data:
                opf_val = update_data["official_photos_from"]
                opf_siblings = db.query(models.Product).filter(
                    models.Product.productnumber == db_product.productnumber,
                    models.Product.id != db_product.id,
                ).all()
                for sib in opf_siblings:
                    sib.official_photos_from = opf_val

            # Заміри (per-item) — парс рядка-діапазону → *_min/*_max на товарі.
            # НЕ пропагуються на сиблінгів (унікальні на розмір). Захист від
            # парсера — NULL-only guard у _apply_new_fields_and_materials; write-back
            # тримає аркуш синхронним (per-item guard: лише коли номер = 1 рядок).
            measurement_writeback: Dict[str, str] = {}
            if measurements_edit:
                measurement_writeback = _apply_measurements_edit(db_product, measurements_edit)

            # Materials (per-position CSV) — full-replace + лок кожної редагованої
            # позиції (`material_<pos>`). Model-level: дублюємо на всіх «братів»
            # ростовки. Лок прибирає перезапис парсером (skip-guard у sheets_parser).
            material_writeback: Dict[str, str] = {}
            if materials_by_position:
                material_writeback = _apply_materials_edit(db, db_product.id, materials_by_position)
                pos_locks = {f"material_{p}" for p in material_writeback.keys()}
                if pos_locks:
                    _merge_lock(db_product, pos_locks)
                    mat_sibs = db.query(models.Product).filter(
                        models.Product.productnumber == db_product.productnumber,
                        models.Product.id != db_product.id,
                    ).all()
                    for sib in mat_sibs:
                        _apply_materials_edit(db, sib.id, materials_by_position)
                        _merge_lock(sib, pos_locks)

            db.commit()
            db.refresh(db_product)
            # Transient (non-column) hint for the router: which lockable fields
            # were actually written this call → those get written back to sheet.
            # Includes auto-derived oldprice from the markdown rule.
            db_product._writeback_fields = newly_locked
            db_product._material_writeback = material_writeback   # {position: csv}
            db_product._measurement_writeback = measurement_writeback   # {meas_<name>: range}
            logger.debug(f"Updated product: {db_product}")
        return db_product
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating product {product_id}: {str(e)}")
        raise

def delete_product(db: Session, product_id: int) -> Optional[models.Product]:
    """Delete a product"""
    try:
        db_product = db.query(models.Product).filter(models.Product.id == product_id).first()
        if db_product:
            db.delete(db_product)
            db.commit()
            logger.debug(f"Deleted product: {db_product}")
        return db_product
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting product {product_id}: {str(e)}")
        raise

def get_product_filters(db: Session) -> Dict[str, Any]:
    """Get all available filters for products using raw SQL (tables may not have ORM models)."""
    try:
        def fetch_pairs(sql: str):
            return db.execute(text(sql)).fetchall()

        # Exclude '?'-only placeholder entries (user marks unknown with ?, ??, ???)
        types = fetch_pairs("""
            SELECT t.id, t.typename
            FROM types t
            WHERE t.typename IS NOT NULL
              AND btrim(t.typename) !~ '^[?]+$'
              AND EXISTS (SELECT 1 FROM products p WHERE p.typeid = t.id)
            ORDER BY t.typename
        """)
        # ``subtypes.typeid`` is a legacy single-parent hint, while real BMS
        # data legitimately reuses some subtype labels across several types.
        # Return every currently observed type association as well, so the UI
        # does not hide a valid subtype just because its old parent points at a
        # different type.
        subtypes_rows = db.execute(text("""
            SELECT s.id, s.subtypename, s.typeid,
                   ARRAY_REMOVE(
                       ARRAY_AGG(DISTINCT p.typeid ORDER BY p.typeid),
                       NULL
                   ) AS typeids
            FROM subtypes s
            JOIN products p ON p.subtypeid = s.id
            WHERE s.subtypename IS NOT NULL
              AND btrim(s.subtypename) !~ '^[?]+$'
            GROUP BY s.id, s.subtypename, s.typeid
            ORDER BY s.subtypename
        """)).fetchall()
        # У товарному фільтрі показуємо лише бренди, які реально мають товари.
        # Осиротілі/службові записи лишаються доступними на сторінці «Бренди»
        # для аудиту, але більше не засмічують щоденний пошук товарів.
        brands = fetch_pairs("""
            SELECT b.id, b.brandname
            FROM brands b
            WHERE b.brandname IS NOT NULL
              AND btrim(b.brandname) !~ '^[?]+$'
              AND EXISTS (SELECT 1 FROM products p WHERE p.brandid = b.id)
            ORDER BY b.brandname
        """)
        genders = fetch_pairs(
            "SELECT id, INITCAP(gendername) as gendername FROM genders "
            "WHERE id != 0 ORDER BY gendername"
        )
        colors = fetch_pairs("SELECT id, colorname FROM colors ORDER BY colorname")
        # Кольорові групи + відтінки (для нового UI фільтра)
        color_groups_rows = db.execute(text("""
            SELECT cg.id, cg.name, cg.hex_code, cg.display_order,
                   COUNT(DISTINCT p.id) as product_count
            FROM color_groups cg
            LEFT JOIN color_group_members cgm ON cgm.group_id = cg.id
            LEFT JOIN products p ON p.colorid = cgm.color_id
            GROUP BY cg.id, cg.name, cg.hex_code, cg.display_order
            ORDER BY cg.display_order
        """)).fetchall()
        statuses = fetch_pairs("SELECT id, statusname FROM statuses ORDER BY statusname")
        # Фільтр має містити тільки стани, які реально використовуються як
        # поточні. Раніше весь довідник безумовно потрапляв у UI, тому навіть
        # осиротілий текст із помилково зміщеної колонки Sheets ставав опцією.
        conditions = fetch_pairs("""
            SELECT c.id, c.conditionname
            FROM conditions c
            WHERE EXISTS (
                SELECT 1 FROM products p WHERE p.current_conditionid = c.id
            )
            ORDER BY c.conditionname
        """)
        styles = fetch_pairs("""
            SELECT s.id, s.stylename
            FROM styles s
            WHERE EXISTS (SELECT 1 FROM products p WHERE p.styleid = s.id)
            ORDER BY s.stylename
        """)
        # Унікальні значення для нових текстових полів (для випадаючих фільтрів)
        seasons_rows = db.execute(text("SELECT DISTINCT TRIM(season) FROM products WHERE season IS NOT NULL AND season != ''")).fetchall()
        widths_rows = db.execute(text("SELECT DISTINCT TRIM(width) FROM products WHERE width IS NOT NULL AND width != '' ORDER BY 1")).fetchall()

        price_min_max = db.execute(text("SELECT COALESCE(min(price),0) AS min_price, COALESCE(max(price),0) AS max_price FROM products")).mappings().first()
        min_price = float(price_min_max["min_price"]) if price_min_max else 0
        max_price = float(price_min_max["max_price"]) if price_min_max else 0

        countries = fetch_pairs("SELECT id, countryname FROM countries ORDER BY countryname")

        shipments_rows = db.execute(text(
            """SELECT d.id, d.deliveryname, d.deliverydate, COUNT(p.id) AS product_count
               FROM deliveries d
               JOIN products p ON p.deliveryid = d.id
               GROUP BY d.id, d.deliveryname, d.deliverydate
               HAVING COUNT(p.id) > 0
               ORDER BY d.deliverydate DESC NULLS LAST, d.id DESC"""
        )).fetchall()

        # Size ranges per system — expand range values into individual sizes
        def fetch_sizes(col: str, numeric_only: bool = False):
            if numeric_only:
                # EU sizes: exclude letter sizes (S/M/L/XL…) and garbage; keep numeric + dash-ranges
                rows = db.execute(text(f"""
                    SELECT DISTINCT {col} FROM products
                    WHERE {col} IS NOT NULL AND {col} != ''
                      AND {col} ~ '^[0-9]'
                    ORDER BY {col}
                """)).fetchall()
            else:
                rows = db.execute(text(f"SELECT DISTINCT {col} FROM products WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}")).fetchall()
            raw = [r[0] for r in rows]
            return _expand_sizes_for_filters(raw)

        result = {
            "types": [{"id": t[0], "name": t[1]} for t in types],
            "subtypes": [
                {
                    "id": s[0],
                    "name": s[1],
                    "typeid": s[2],
                    "typeids": list(s[3] or []),
                }
                for s in subtypes_rows
            ],
            "brands": [{"id": b[0], "name": b[1]} for b in brands],
            "genders": [{"id": g[0], "name": g[1]} for g in genders],
            "colors": [{"id": c[0], "name": c[1]} for c in colors],
            "color_groups": [
                {"id": cg[0], "name": cg[1], "hex": cg[2], "order": cg[3], "count": cg[4]}
                for cg in color_groups_rows
            ],
            "statuses": [{"id": s[0], "name": s[1]} for s in statuses],
            "conditions": [{"id": c[0], "name": c[1]} for c in conditions],
            "styles": [{"id": s[0], "name": s[1]} for s in styles],
            # Розгортаємо сезон-multi-value у плоский список унікальних значень
            "seasons": sorted(list({
                v.strip()
                for row in seasons_rows
                for v in (row[0] or "").split(",")
                if v.strip()
            })),
            "widths": [w[0] for w in widths_rows],
            "countries": [{"id": c[0], "name": c[1]} for c in countries],
            "shipments": [{"id": s[0], "name": s[1], "date": str(s[2]) if s[2] else None, "count": s[3]} for s in shipments_rows],
            "price_range": {"min_price": min_price, "max_price": max_price},
            "size_ranges": {
                "eu": fetch_sizes("sizeeu", numeric_only=True),
                "ua": fetch_sizes("sizeua"),
                "usa": fetch_sizes("sizeusa"),
                "uk": fetch_sizes("sizeuk"),
                "jp": fetch_sizes("sizejp"),
                "cn": fetch_sizes("sizecn"),
            },
            "size_letters": [r[0] for r in db.execute(text(
                # Сортування за «розміром»: XS < S < M < L < XL < XXL < XXXL < ...
                # CASE мусить бути у SELECT-листі, бо DISTINCT.
                "SELECT size_letter FROM ("
                "  SELECT DISTINCT size_letter, "
                "    CASE size_letter "
                "      WHEN 'XS' THEN 0 WHEN 'S' THEN 1 WHEN 'M' THEN 2 WHEN 'L' THEN 3 "
                "      ELSE LENGTH(size_letter) + 3 END AS ord "
                "  FROM products "
                "  WHERE size_letter IS NOT NULL AND size_letter != '' "
                ") s ORDER BY ord"
            )).fetchall()],
        }
        # Довідники-характеристики для автодоповнення у формі додавання (назви).
        def _names(table: str, col: str):
            try:
                return [r[0] for r in db.execute(text(
                    f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL AND btrim({col}) <> '' ORDER BY {col}"
                )).fetchall()]
            except Exception:
                return []
        result["lookups"] = {
            "sole_types":      _names("sole_types", "soletypename"),
            "toe_shapes":      _names("toe_shapes", "toeshapename"),
            "fastening_types": _names("fastening_types", "fasteningtypename"),
            "lace_types":      _names("lace_types", "lacetypename"),
            "heel_types":      _names("heel_types", "heeltypename"),
            "linings":         _names("linings", "liningname"),
            "technologies":    _names("technologies", "technologyname"),
            "packagings":      _names("packaging_types", "packagingname"),
            "materials":       _names("materials", "materialname"),
        }
        logger.debug("Retrieved filters via raw SQL")
        return result
    except Exception as e:
        logger.error(f"Error getting filters: {str(e)}")
        raise

def get_product_with_relations(db: Session, product_id: int) -> Optional[Dict[str, Any]]:
    """
    Отримати товар з усіма зв'язаними даними
    """
    try:
        # SQL запит з JOIN для отримання пов'язаних даних
        query = text(f"""
            SELECT p.*,
                   t.typename as type_name,
                   st.subtypename as subtype_name,
                   sty.stylename as style_name,
                   b.brandname as brand_name,
                   g.gendername as gender_name,
                   c.colorname as color_name,
                   oc.countryname as owner_country_name,
                   mc.countryname as manufacturer_country_name,
                   s.statusname as status_name,
                   cond.conditionname as condition_name,
                   cur_cond.conditionname as current_condition_name,
                   i.importname as import_name,
                   d.deliveryname as delivery_name,
                   sol.soletypename as sole_type_name,
                   tsh.toeshapename as toe_shape_name,
                   fst.fasteningtypename as fastening_type_name,
                   lin.liningname as lining_name,
                   ht.heeltypename as heel_type_name,
                   lt.lacetypename as lace_type_name,
                   pk.packagingname as packaging_name,
                   tech.technologyname as technology_name,
                   scol.colorname as sole_color_name,
                   COALESCE(sold.sold_count, 0) AS sold_count,
                   GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold.sold_count, 0), 0) AS available_qty,
                   -- див. коментар у get_products: відрізняє застарілий знімок «Продано»
                   -- від легітимного неформального продажу без замовлень.
                   (SELECT COUNT(*)
                    FROM order_items oi_oc
                    JOIN orders o_oc ON o_oc.id = oi_oc.order_id
                    WHERE oi_oc.product_id = p.id AND {real_order_sql("o_oc")}) AS order_count,
                   COALESCE(reserved.reserved_count, 0) AS reserved_count,
                   (COALESCE(reserved.reserved_count, 0) > 0
                        AND COALESCE(sold.sold_count, 0) < COALESCE(NULLIF(p.quantity, 0), 1)) AS is_reserved,
                   mat_agg.materials_json
            FROM products p
            LEFT JOIN types t ON p.typeid = t.id
            LEFT JOIN subtypes st ON p.subtypeid = st.id
            LEFT JOIN styles sty ON p.styleid = sty.id
            LEFT JOIN brands b ON p.brandid = b.id
            LEFT JOIN genders g ON p.genderid = g.id
            LEFT JOIN colors c ON p.colorid = c.id
            LEFT JOIN countries oc ON p.ownercountryid = oc.id
            LEFT JOIN countries mc ON p.manufacturercountryid = mc.id
            LEFT JOIN statuses s ON p.statusid = s.id
            LEFT JOIN conditions cond ON p.conditionid = cond.id
            LEFT JOIN conditions cur_cond ON p.current_conditionid = cur_cond.id
            LEFT JOIN imports i ON p.importid = i.id
            LEFT JOIN deliveries d ON p.deliveryid = d.id
            LEFT JOIN sole_types sol ON p.soletypeid = sol.id
            LEFT JOIN toe_shapes tsh ON p.toeshapeid = tsh.id
            LEFT JOIN fastening_types fst ON p.fasteningtypeid = fst.id
            LEFT JOIN linings lin ON p.liningid = lin.id
            LEFT JOIN heel_types ht ON p.heeltypeid = ht.id
            LEFT JOIN lace_types lt ON p.lacetypeid = lt.id
            LEFT JOIN packaging_types pk ON p.packagingid = pk.id
            LEFT JOIN technologies tech ON p.technologyid = tech.id
            LEFT JOIN colors scol ON p.sole_colorid = scol.id
            LEFT JOIN (
                -- «Продано» = Подарунок(7) АБО (Підтверджено(1) І Оплачено).
                -- Повернення(9) повертає сток ЛИШЕ за окремого оплаченого продажу того ж
                -- клієнта; перепродаж іншому — статус9 і так не в «продано», тож не
                -- віднімаємо. effective_returns = SUM по клієнту LEAST(оплат, повернень).
                SELECT pc.product_id,
                       GREATEST(SUM(pc.paid_sold) - SUM(LEAST(pc.paid_sold, pc.returns)), 0) AS sold_count
                FROM (
                    SELECT oi.product_id, o.client_id,
                           COUNT(*) FILTER (WHERE o.order_status_id = 7
                                              OR (o.order_status_id = 1 AND o.payment_status_id = 1)) AS paid_sold,
                           COUNT(*) FILTER (WHERE o.order_status_id = 9) AS returns
                    FROM order_items oi
                    JOIN orders o ON o.id = oi.order_id
                    WHERE oi.product_id IS NOT NULL
                      AND o.order_status_id IN (1, 7, 9)
                    GROUP BY oi.product_id, o.client_id
                ) pc
                GROUP BY pc.product_id
            ) sold ON sold.product_id = p.id
            LEFT JOIN (
                -- «Заброньовано» = Підтверджено(1) без Оплачено (payment != 1). Бронь.
                SELECT oi.product_id, COUNT(*) AS reserved_count
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.product_id IS NOT NULL
                  AND o.order_status_id = 1
                  AND o.payment_status_id IS DISTINCT FROM 1
                GROUP BY oi.product_id
            ) reserved ON reserved.product_id = p.id
            LEFT JOIN LATERAL (
                SELECT json_agg(
                    json_build_object(
                        'position', pm.position,
                        'material_id', pm.material_id,
                        'materialname', m.materialname,
                        'category', m.category,
                        'ord', pm.ord
                    ) ORDER BY pm.ord
                ) AS materials_json
                FROM product_materials pm
                JOIN materials m ON m.id = pm.material_id
                WHERE pm.product_id = p.id
            ) mat_agg ON true
            WHERE p.id = :id
        """)
        
        # Виконання запиту
        result = db.execute(query, {"id": product_id}).mappings().first()
        
        if not result:
            logger.warning(f"Product with ID {product_id} not found with relations")
            return None

        data = dict(result)
        # Ключовий номер для показу (реальний або перший клон-номер) — див.
        # effective_product_number. Дзеркалить логіку списку get_products.
        data['display_number'] = effective_product_number(
            data.get('productnumber'), data.get('clonednumbers'))
        # Parse materials JSON (PostgreSQL returns it as a string or None)
        import json as _json
        raw_mat = data.pop('materials_json', None)
        if isinstance(raw_mat, str):
            data['materials'] = _json.loads(raw_mat) or []
        elif isinstance(raw_mat, list):
            data['materials'] = raw_mat
        else:
            data['materials'] = []
        return data
    except Exception as e:
        logger.error(f"Error fetching product ID {product_id} with relations: {str(e)}")
        raise

def update_product_visibility(db: Session, product_id: int, is_visible: bool) -> bool:
    """
    Оновити видимість товару
    """
    try:
        db_product = db.query(models.Product).filter(models.Product.id == product_id).first()
        
        if not db_product:
            logger.warning(f"Product with ID {product_id} not found for visibility update")
            return False
        
        db_product.is_visible = is_visible
        db.commit()
        logger.info(f"Updated visibility for product ID {product_id}: {is_visible}")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating visibility for product ID {product_id}: {str(e)}")
        raise

def _enqueue_bulk_writeback(db: Session, product_ids: List[int], fields: Set[str]) -> None:
    """Поставити в чергу запис у журнал для масово змінених полів.

    FK-поля резолвимо в назви тут, поки сесія жива: воркер працює поза нею.
    """
    try:
        try:
            from backend.services import journal_sync
        except ImportError:
            from services import journal_sync
        rows = db.execute(text("""
            SELECT p.id, p.productnumber, d.deliveryname
            FROM products p LEFT JOIN deliveries d ON d.id = p.deliveryid
            WHERE p.id = ANY(:ids)
        """), {"ids": list(product_ids)}).fetchall()
        for r in rows:
            values = {}
            for f in fields:
                v = db.execute(text(f"SELECT {f} FROM products WHERE id = :i"),
                               {"i": r.id}).scalar()
                if f in SHOE_FK_NAME_FIELDS:
                    v = resolve_lookup_name(db, f, v)
                values[f] = v
            journal_sync.enqueue_many(db, r.id, r.productnumber, r.deliveryname, values)
        db.commit()
        journal_sync.kick()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.error(f"bulk write-back enqueue failed: {e}")


def bulk_update_products(db: Session, product_ids: List[int], update_data: Dict[str, Any]) -> int:
    """
    Масове оновлення товарів
    """
    try:
        # Фільтруємо тільки валідні поля для оновлення
        valid_fields = [c.name for c in models.Product.__table__.columns]
        filtered_data = {k: v for k, v in update_data.items() if k in valid_fields}
        
        if not filtered_data:
            logger.warning("No valid fields to update")
            return 0
        
        # Виконуємо оновлення
        result = db.query(models.Product).filter(models.Product.id.in_(product_ids)).update(
            filtered_data, 
            synchronize_session=False
        )
        db.flush()

        # Масова правка — така сама правка користувача, як і в картці: її треба
        # ЗАЛОЧИТИ (інакше наступний парсинг мовчки поверне старе значення з
        # аркуша) і донести до журналу. Раніше тут був голий UPDATE: дані
        # лишались тільки в БД, у таблиці журналу порожньо, а лок відсутній —
        # тобто правка ще й жила до першого парсингу.
        lockable = {f for f in filtered_data if f in LOCKABLE_PRODUCT_FIELDS}
        if lockable:
            for prod in db.query(models.Product).filter(models.Product.id.in_(product_ids)).all():
                _merge_lock(prod, set(lockable))
            db.flush()

        db.commit()

        if lockable:
            _enqueue_bulk_writeback(db, product_ids, lockable)

        logger.info(f"Bulk updated {result} products")
        return result
    except Exception as e:
        db.rollback()
        logger.error(f"Error during bulk update: {str(e)}")
        raise


def _ensure_status_id(db: Session, name: str) -> int:
    """Get or create a product status by name, return its id."""
    row = db.execute(
        text("SELECT id FROM statuses WHERE statusname = :n LIMIT 1"), {"n": name}
    ).fetchone()
    if row:
        return row[0]
    row = db.execute(
        text("INSERT INTO statuses (statusname) VALUES (:n) RETURNING id"), {"n": name}
    ).fetchone()
    db.flush()
    return row[0]


def sync_product_statuses(db: Session) -> Dict[str, int]:
    """
    Синхронізує статуси товарів після парсингу.

    Логіка:
      - "Продано"    якщо є хоча б одне підтверджене замовлення (order_status 1)
                     і загальна кількість (підтверджено+подарунок) >= quantity
      - "Подаровано" якщо є ТІЛЬКИ подарункові замовлення (order_status 7)
                     і gift_count >= quantity
      - "Непродано"  в усіх інших випадках

    Повертає: {'prodano': N, 'neprodano': N, 'podarovano': N}
    """
    try:
        prodano_id    = _ensure_status_id(db, "Продано")
        neprodano_id  = _ensure_status_id(db, "Непродано")
        podarovano_id = _ensure_status_id(db, "Подаровано")

        # Крок 1: оновити товари що мають продажі або подарунки
        r1 = db.execute(text("""
            WITH
            id_confirmed AS (
                SELECT oi.product_id, COUNT(*) AS cnt
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.product_id IS NOT NULL
                  AND o.order_status_id = 1
                GROUP BY oi.product_id
            ),
            id_gifted AS (
                SELECT oi.product_id, COUNT(*) AS cnt
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.product_id IS NOT NULL
                  AND o.order_status_id = 7
                GROUP BY oi.product_id
            ),
            notes_sold AS (
                SELECT p.id, COUNT(oi.id) AS cnt
                FROM products p
                JOIN order_items oi
                    ON oi.product_id IS NULL
                   AND oi.notes IS NOT NULL
                   AND oi.notes != ''
                   AND oi.notes NOT LIKE '%% %%'
                   AND '#' || LTRIM(oi.notes, '#') = p.productnumber
                WHERE (
                    SELECT COUNT(*) FROM products p2
                    WHERE p2.productnumber = p.productnumber
                ) = 1
                GROUP BY p.id
            ),
            sold_counts AS (
                SELECT
                    p.id,
                    COALESCE(c.cnt, 0) + COALESCE(n.cnt, 0) AS confirmed_count,
                    COALESCE(g.cnt, 0)                       AS gift_count,
                    COALESCE(c.cnt, 0) + COALESCE(n.cnt, 0)
                        + COALESCE(g.cnt, 0)                 AS total_count,
                    COALESCE(p.quantity, 1)                   AS qty
                FROM products p
                LEFT JOIN id_confirmed c ON c.product_id = p.id
                LEFT JOIN id_gifted    g ON g.product_id = p.id
                LEFT JOIN notes_sold   n ON n.id         = p.id
                WHERE COALESCE(c.cnt, 0) + COALESCE(g.cnt, 0) + COALESCE(n.cnt, 0) > 0
            )
            UPDATE products p
            SET statusid = CASE
                WHEN sc.confirmed_count > 0 AND sc.total_count >= sc.qty THEN :prodano_id
                WHEN sc.confirmed_count = 0 AND sc.gift_count  >= sc.qty THEN :podarovano_id
                ELSE :neprodano_id
            END
            FROM sold_counts sc
            WHERE p.id = sc.id
        """), {"prodano_id": prodano_id, "neprodano_id": neprodano_id,
               "podarovano_id": podarovano_id})
        total_updated = r1.rowcount

        # Крок 2: скинути хибний "Продано"/"Подаровано" де фактично sold_count < quantity
        r2 = db.execute(text("""
            UPDATE products p
            SET statusid = :neprodano_id
            WHERE p.statusid IN (:prodano_id, :podarovano_id)
              AND p.id NOT IN (
                  SELECT sc.id FROM (
                      SELECT
                          p2.id,
                          COALESCE(
                              (SELECT GREATEST(SUM(pc.paid_sold) - SUM(LEAST(pc.paid_sold, pc.returns)), 0)
                               FROM (
                                   -- «Продано» = Подарунок(7) АБО (Підтв.(1) І Оплачено); Повернення(9)
                                   -- кредитує лише за окремого оплаченого продажу того ж клієнта.
                                   SELECT o.client_id,
                                          COUNT(*) FILTER (WHERE o.order_status_id = 7
                                                             OR (o.order_status_id = 1 AND o.payment_status_id = 1)) AS paid_sold,
                                          COUNT(*) FILTER (WHERE o.order_status_id = 9) AS returns
                                   FROM order_items oi
                                   JOIN orders o ON o.id = oi.order_id
                                   WHERE oi.product_id = p2.id
                                     AND o.order_status_id IN (1, 7, 9)
                                   GROUP BY o.client_id
                               ) pc), 0
                          ) AS sold_count,
                          COALESCE(p2.quantity, 1) AS qty
                      FROM products p2
                      WHERE p2.statusid IN (:prodano_id, :podarovano_id)
                  ) sc
                  WHERE sc.sold_count >= sc.qty AND sc.qty > 0
              )
        """), {"prodano_id": prodano_id, "neprodano_id": neprodano_id,
               "podarovano_id": podarovano_id})
        total_updated += r2.rowcount
        logger.info(f"sync_product_statuses: fixed {r2.rowcount} false statuses → 'Непродано'")

        # Фінальний розподіл
        counts = db.execute(text("""
            SELECT s.statusname, COUNT(p.id) as cnt
            FROM products p JOIN statuses s ON p.statusid = s.id
            GROUP BY s.statusname
        """)).fetchall()
        prodano_count    = next((r.cnt for r in counts if r.statusname == 'Продано'),    0)
        neprodano_count  = next((r.cnt for r in counts if r.statusname == 'Непродано'),  0)
        podarovano_count = next((r.cnt for r in counts if r.statusname == 'Подаровано'), 0)

        db.commit()
        logger.info(
            f"sync_product_statuses: Продано={prodano_count}, "
            f"Непродано={neprodano_count}, Подаровано={podarovano_count}"
        )
        return {
            "prodano": prodano_count,
            "neprodano": neprodano_count,
            "podarovano": podarovano_count,
        }

    except Exception as e:
        db.rollback()
        logger.error(f"sync_product_statuses error: {e}")
        raise

"""Оркестратор офіційного глобального мосту Prom.ua -> Shafa.ua.

У Shafa немає публічного seller API. BMS не викликає приватні endpoint-и й не
видає локальний запис за підтверджене віддалене оголошення. Явні стани:
waiting_prom -> bridge_ready -> confirmed; manual_existing — ручний зворотний
зв'язок для товару, який уже був на Shafa.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from services import prom_service
except ImportError:  # pragma: no cover
    from backend.services import prom_service

logger = logging.getLogger(__name__)


SHAFA_MAX_LISTINGS = 10_000
SHAFA_TARIFFS_HELP = "https://shafa.ua/page/tarifi-ta-umovi-koristuvannya-shafoyu"
OFFICIAL_BRIDGE_HELP = (
    "https://shafa.ua/uk/page/umovi-eksportu-ogoloshen-z-platformi-promua-na-shafaua"
)
PROM_BRIDGE_HELP = (
    "https://support.prom.ua/hc/uk/articles/10101798372893-"
    "%D0%95%D0%BA%D1%81%D0%BF%D0%BE%D1%80%D1%82-%D1%82%D0%BE%D0%B2%D0%B0%D1%80%D1%96%D0%B2-%D0%BD%D0%B0-Shafa-ua"
)
TRACKED_STATUSES = ("waiting_prom", "bridge_ready", "confirmed", "manual_existing")
PUBLISHED_STATUSES = ("confirmed", "manual_existing")
# Чинні тарифи Shafa, опубліковані 30.06.2026. Старий поділ Base/Business
# більше не використовується: ставка залежить від ціни та групи категорій.
# Primary source: https://shafa.ua/page/tarifi-ta-umovi-koristuvannya-shafoyu
SHAFA_TARIFF_EFFECTIVE_DATE = "2026-06-30"
SHAFA_TARIFF_CORE = "fashion_home"
SHAFA_TARIFF_OTHER = "other"
_SHAFA_RATES = {
    SHAFA_TARIFF_CORE: (0.20, 0.17, 0.13),
    SHAFA_TARIFF_OTHER: (0.15, 0.12, 0.09),
}
_SHAFA_TARIFF_LABELS = {
    SHAFA_TARIFF_CORE: "Одяг, взуття, аксесуари, краса або дім",
    SHAFA_TARIFF_OTHER: "Інші категорії",
}
# Типи з BMS, які однозначно належать до «інших» категорій Shafa. Для
# невідомого типу беремо дорожчу fashion/home сітку — це захищає маржу.
_SHAFA_OTHER_TYPES = {"іграшка", "ролики", "пенал", "компрес"}
SHAFA_FEE_CAP = 500.0
SHAFA_INITIAL_EXPORT_WINDOW = timedelta(days=2)

_PROM_CANDIDATES_CTE = """
WITH raw_candidates AS (
    SELECT TRIM(LEADING '#' FROM p.productnumber) AS productnumber,
           MIN(p.id) AS anchor_product_id,
           BOOL_OR(pp.status = 'on_display' AND pp.presence = 'available') AS bridge_ready
    FROM prom_products pp
    JOIN products p ON p.id = pp.product_id
    WHERE COALESCE(pp.status, '') <> 'deleted'
      AND NULLIF(TRIM(LEADING '#' FROM p.productnumber), '') IS NOT NULL
    GROUP BY TRIM(LEADING '#' FROM p.productnumber)

    UNION ALL

    SELECT TRIM(LEADING '#' FROM p.productnumber) AS productnumber,
           MIN(p.id) AS anchor_product_id,
           FALSE AS bridge_ready
    FROM prom_draft_queue q
    JOIN products p
      ON q.sku = TRIM(LEADING '#' FROM p.productnumber)
      OR q.sku LIKE TRIM(LEADING '#' FROM p.productnumber) || '-%'
    WHERE NULLIF(TRIM(LEADING '#' FROM p.productnumber), '') IS NOT NULL
    GROUP BY TRIM(LEADING '#' FROM p.productnumber)
), candidates AS (
    SELECT productnumber, MIN(anchor_product_id) AS anchor_product_id,
           BOOL_OR(bridge_ready) AS bridge_ready
    FROM raw_candidates
    GROUP BY productnumber
)
"""


def _number(value: Any) -> str:
    return str(value or "").strip().lstrip("#")


def _config(db: Session) -> dict:
    row = db.execute(text(
        "SELECT bridge_enabled, bridge_confirmed_at, "
        "price_strategy, auto_publish, updated_at "
        "FROM shafa_config WHERE id=1"
    )).mappings().first()
    return dict(row) if row else {
        "bridge_enabled": False, "bridge_confirmed_at": None,
        "price_strategy": "unified_safe", "auto_publish": True, "updated_at": None,
    }


def save_bridge_config(db: Session, enabled: Optional[bool] = None) -> dict:
    db.execute(text("""
        INSERT INTO shafa_config
            (id, bridge_enabled, bridge_confirmed_at, price_strategy,
             auto_publish, updated_at)
        VALUES (1, COALESCE(:enabled, FALSE),
                CASE WHEN :enabled IS TRUE THEN now() ELSE NULL END,
                'unified_safe', TRUE, now())
        ON CONFLICT (id) DO UPDATE SET
            bridge_enabled=COALESCE(:enabled, shafa_config.bridge_enabled),
            bridge_confirmed_at=CASE
                WHEN :enabled IS TRUE THEN COALESCE(shafa_config.bridge_confirmed_at, now())
                WHEN :enabled IS FALSE THEN NULL
                ELSE shafa_config.bridge_confirmed_at END,
            price_strategy='unified_safe',
            auto_publish=TRUE,
            updated_at=now()
    """), {"enabled": enabled})
    db.commit()
    reconciliation = None
    if enabled is True:
        reconciliation = reconcile_expected_from_prom(db)
    result = get_status(db)
    if reconciliation is not None:
        result["reconciliation"] = reconciliation
    return result


def reconcile_expected_from_prom(db: Session) -> dict:
    """Віддзеркалити офіційну Prom→Shafa чергу в локальних чесних станах.

    Це не перевірка Shafa: seller API немає. `waiting_prom` означає, що Prom ще
    обробляє товар/його доступність, `bridge_ready` — що живий доступний товар
    уже відповідає задокументованим умовам глобального експорту. Підтверджені,
    ручні, заблоковані та явно зняті записи ніколи не перезаписуються.
    """
    if not _config(db).get("bridge_enabled"):
        return {
            "ok": True, "bridge_enabled": False, "candidates": 0,
            "added": 0, "upgraded": 0,
        }

    candidates = int(db.execute(text(
        _PROM_CANDIDATES_CTE + "SELECT COUNT(*) FROM candidates"
    )).scalar() or 0)

    inserted = db.execute(text(_PROM_CANDIDATES_CTE + """
        INSERT INTO shafa_publications
            (productnumber, anchor_product_id, source, status, updated_at)
        SELECT c.productnumber, c.anchor_product_id, 'prom_bridge',
               CASE WHEN c.bridge_ready THEN 'bridge_ready' ELSE 'waiting_prom' END,
               now()
        FROM candidates c
        WHERE NOT EXISTS (
            SELECT 1 FROM shafa_publications sp
            WHERE sp.productnumber = c.productnumber
        )
        RETURNING status
    """)).scalars().all()

    upgraded = db.execute(text(_PROM_CANDIDATES_CTE + """
        UPDATE shafa_publications sp
        SET status='bridge_ready',
            anchor_product_id=COALESCE(sp.anchor_product_id, c.anchor_product_id),
            source='prom_bridge', last_error=NULL, updated_at=now()
        FROM candidates c
        WHERE sp.productnumber=c.productnumber
          AND sp.status='waiting_prom'
          AND c.bridge_ready
        RETURNING sp.productnumber
    """)).scalars().all()
    db.commit()

    ready_added = sum(1 for status in inserted if status == "bridge_ready")
    waiting_added = sum(1 for status in inserted if status == "waiting_prom")
    return {
        "ok": True, "bridge_enabled": True, "candidates": candidates,
        "added": len(inserted), "waiting_added": waiting_added,
        "bridge_ready_added": ready_added, "upgraded": len(upgraded),
    }


def shafa_tariff_group(typename: Optional[str]) -> str:
    """Мапить тип BMS на одну з двох чинних категорійних сіток Shafa."""
    value = str(typename or "").strip().casefold()
    return SHAFA_TARIFF_OTHER if value in _SHAFA_OTHER_TYPES else SHAFA_TARIFF_CORE


def _tariff_group(value: Optional[str]) -> str:
    return value if value in _SHAFA_RATES else SHAFA_TARIFF_CORE


def shafa_commission_rate(price: float, tariff_group: str = SHAFA_TARIFF_CORE) -> float:
    """Чинна ставка для фактичної ціни та категорійної групи Shafa."""
    price = max(float(price or 0), 0.0)
    low, middle, high = _SHAFA_RATES[_tariff_group(tariff_group)]
    if price <= 500:
        return low
    if price <= 1000:
        return middle
    return high


def shafa_fee(price: float, tariff_group: str = SHAFA_TARIFF_CORE) -> float:
    price = max(float(price or 0), 0.0)
    if price <= 0:
        return 0.0
    raw_fee = price * shafa_commission_rate(price, tariff_group)
    minimum = 10.0 if price <= 50 else (20.0 if price <= 100 else 30.0)
    return round(min(max(raw_fee, minimum), SHAFA_FEE_CAP), 2)


def _psychological_ceil(value: float) -> int:
    """Найменша ціна не нижче value з фіналом …50 або …90."""
    value = max(float(value or 0), 0.0)
    hundred = int(value // 100) * 100
    for candidate in (hundred + 50, hundred + 90, hundred + 150, hundred + 190):
        if candidate >= value - 1e-9:
            return int(candidate)
    return int(math.ceil(value / 100.0) * 100 + 50)


def _minimum_shafa_price(target_net: float, tariff_group: str) -> int:
    """Мінімальна психологічна ціна, що лишає target_net після комісії Shafa."""
    target_net = max(float(target_net or 0), 0.0)
    tariff_group = _tariff_group(tariff_group)
    candidate = _psychological_ceil(target_net)
    for _ in range(100_000):
        if candidate - shafa_fee(candidate, tariff_group) >= target_net - 0.01:
            return candidate
        candidate = _psychological_ceil(candidate + 1)
    raise RuntimeError("Не вдалося підібрати безпечну ціну Shafa")


def price_economics(base_price: float, typename: str,
                    current_prom_price: Optional[float] = None, kids: bool = False,
                    tariff_group: Optional[str] = None) -> dict:
    """Єдина безпечна ціна мосту.

    Міст не підтримує окрему ціну Shafa, тому effective_price захищає планову
    націнку одночасно на Prom і Shafa. Вища вимога перемагає.
    """
    base = max(float(base_price or 0), 0.0)
    tariff_group = _tariff_group(tariff_group or shafa_tariff_group(typename))
    markup = prom_service._TYPE_MARKUP.get(str(typename or "").strip().lower(), 1.33)
    target_net = round(base * markup, 2)
    prom_rate = prom_service._PROM_FEE_KIDS if kids else prom_service._PROM_FEE_ADULT
    # Prom уже має власну перевірену формулу та психологічне округлення. Shafa
    # не повинна повторно рахувати Prom суворішим ceil і без потреби змінювати
    # 2490→2550. Для нового товару беремо штатну Prom-ціну; наявну вищу ціну
    # також ніколи не знижуємо.
    prom_safe = int(prom_service._prom_price(base, typename, kids=kids)) if base else 0
    shafa_safe = _minimum_shafa_price(target_net, tariff_group) if base else 0
    existing_prom = max(float(current_prom_price or 0), 0.0)
    effective = max(prom_safe, shafa_safe, existing_prom)
    s_rate = shafa_commission_rate(effective, tariff_group) if effective else 0
    s_fee = shafa_fee(effective, tariff_group) if effective else 0
    s_net = round(effective - s_fee, 2)
    p_fee = round(effective * prom_rate + prom_service._PROM_POSTPAY_FEE, 2) if effective else 0
    p_net = round(effective - p_fee, 2)
    margin = round(s_net - base, 2)
    return {
        "strategy": "unified_safe",
        "tariff_group": tariff_group,
        "tariff_group_label": _SHAFA_TARIFF_LABELS[tariff_group],
        "tariff_effective_date": SHAFA_TARIFF_EFFECTIVE_DATE,
        "shafa_fee_cap": SHAFA_FEE_CAP,
        "base_price": round(base, 2),
        "markup_multiplier": markup,
        "target_markup_pct": round((markup - 1.0) * 100, 1),
        "target_net": target_net,
        "prom_safe_price": prom_safe,
        "shafa_safe_price": shafa_safe,
        "effective_price": effective,
        "current_prom_price": float(current_prom_price) if current_prom_price is not None else None,
        "price_will_change": current_prom_price is not None and abs(float(current_prom_price) - effective) >= 0.01,
        "shafa_commission_pct": round(s_rate * 100, 1),
        "shafa_fee": s_fee,
        "shafa_net": s_net,
        "shafa_margin": margin,
        "shafa_margin_pct": round((margin / base) * 100, 1) if base else 0,
        "prom_commission_pct": round(prom_rate * 100, 1),
        "prom_postpay_fee": prom_service._PROM_POSTPAY_FEE,
        "prom_fee": p_fee,
        "prom_net": p_net,
        "extra_net_vs_prom": round(s_net - p_net, 2),
        # Prom зберігає власну штатну формулу (вона може округлити на кілька
        # гривень нижче математичної цілі), а Shafa захищає повну target_net.
        "margin_safe": bool(
            base and effective >= prom_safe
            and p_net >= base * 1.10 - 0.01
            and s_net >= target_net - 0.01
        ),
        "independent_shafa_price_supported": False,
    }


def _product_pricing(db: Session, meta: dict, cfg: dict, prom: dict) -> dict:
    try:
        bms = prom_service._bms_product_for_export(db, int(meta["product_id"])) or meta
        kids = prom_service._is_kids(bms)
    except Exception:
        kids = False
    return price_economics(
        meta.get("price"), meta.get("typename") or "",
        kids=kids, current_prom_price=prom.get("price"),
    )


def _tracked_count(db: Session) -> int:
    return int(db.execute(text(
        "SELECT COUNT(*) FROM shafa_publications WHERE status = ANY(:statuses)"
    ), {"statuses": list(TRACKED_STATUSES)}).scalar() or 0)


def _status_breakdown(db: Session) -> dict:
    """Реальна розкладка станів — щоб UI показував правду, а не «всі очікують».

    ``confirmed`` рахуємо лише з доказом (URL/ID): це відповіді, які BMS
    публічно перевірила або продавець прив'язав вручну.
    """
    rows = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'waiting_prom')                          AS waiting_prom,
            COUNT(*) FILTER (WHERE status = 'bridge_ready')                          AS bridge_ready,
            COUNT(*) FILTER (WHERE status IN ('confirmed','manual_existing')
                             AND (NULLIF(BTRIM(shafa_url),'') IS NOT NULL
                                  OR NULLIF(BTRIM(shafa_listing_id),'') IS NOT NULL)) AS confirmed,
            COUNT(*) FILTER (WHERE shafa_presence = 'available')                     AS available_on_shafa,
            MAX(shafa_checked_at)                                                    AS last_checked_at
        FROM shafa_publications
    """)).mappings().first() or {}
    return {
        "waiting_prom": int(rows.get("waiting_prom") or 0),
        "bridge_ready": int(rows.get("bridge_ready") or 0),
        "confirmed": int(rows.get("confirmed") or 0),
        "available_on_shafa": int(rows.get("available_on_shafa") or 0),
        "last_checked_at": (
            rows.get("last_checked_at").isoformat()
            if hasattr(rows.get("last_checked_at"), "isoformat") else None
        ),
    }


def get_status(db: Session) -> dict:
    cfg = _config(db)
    tracked = _tracked_count(db)
    breakdown = _status_breakdown(db)
    seller_username = db.execute(text(
        "SELECT seller_username FROM shafa_config WHERE id=1")).scalar()
    warnings = [
        "Офіційний міст глобальний: доступні товари Prom автоматично створюються на Shafa; окремо тиснути не треба.",
        "BMS звіряє фактичну появу лише за публічними оголошеннями Shafa (seller API немає), тому «підтверджено» = знайдено реальне оголошення.",
        "Синхронізація одностороння: BMS оновлює Prom, Prom оновлює Shafa; продажі Shafa не повертаються в BMS автоматично.",
    ]
    if tracked >= SHAFA_MAX_LISTINGS:
        warnings.append("Досягнуто локальну оцінку ліміту 10 000 оголошень Shafa.")
    elif tracked >= int(SHAFA_MAX_LISTINGS * 0.9):
        warnings.append(f"Локально відстежується {tracked} із 10 000 оголошень Shafa.")
    return {
        "ok": True,
        **cfg,
        "seller_username": seller_username or None,
        "tracked": tracked,
        **breakdown,
        "max_listings": SHAFA_MAX_LISTINGS,
        "remaining_local_estimate": max(SHAFA_MAX_LISTINGS - tracked, 0),
        "warnings": warnings,
        "official_help_url": OFFICIAL_BRIDGE_HELP,
        "prom_help_url": PROM_BRIDGE_HELP,
    }


def _product_meta(db: Session, product_id: int) -> Optional[dict]:
    anchor = db.execute(text("""
        SELECT p.id, p.productnumber, t.typename, p.price, p.gtin
        FROM products p LEFT JOIN types t ON t.id=p.typeid
        WHERE p.id=:id
    """), {"id": int(product_id)}).mappings().first()
    if not anchor:
        return None
    number = _number(anchor["productnumber"])
    variants = db.execute(text("""
        SELECT p.id, p.sizeeu, p.gtin, p.price
        FROM products p
        WHERE TRIM(LEADING '#' FROM p.productnumber)=:number
        ORDER BY p.id
    """), {"number": number}).mappings().all()
    gtins, invalid_gtins = [], []
    for variant in variants:
        raw = str(variant.get("gtin") or "").strip()
        if not raw:
            continue
        valid = prom_service.normalize_gtin(raw)
        (gtins if valid else invalid_gtins).append(valid or raw)
    try:
        available = sum(prom_service._available_by_size(db, anchor["productnumber"]).values())
    except Exception:
        available = 0
    try:
        image_count = len(prom_service._product_image_urls(anchor["productnumber"]))
    except Exception:
        image_count = 0
    return {
        "product_id": int(anchor["id"]),
        "productnumber": number,
        "typename": anchor.get("typename"),
        "price": float(anchor.get("price") or 0),
        "variant_count": len(variants),
        "gtins": gtins,
        "invalid_gtins": invalid_gtins,
        "available_qty": int(available),
        "image_count": image_count,
    }


def _publication(db: Session, number: str) -> Optional[dict]:
    row = db.execute(text("""
        SELECT productnumber, anchor_product_id, source, status, shafa_listing_id,
               shafa_url, last_error, confirmed_at, created_at, updated_at,
               shafa_presence, shafa_checked_at
        FROM shafa_publications WHERE productnumber=:number
    """), {"number": number}).mappings().first()
    return dict(row) if row else None


def _upsert(db: Session, meta: dict, status: str, source: str = "prom_bridge",
            url: Optional[str] = None, listing_id: Optional[str] = None,
            last_error: Optional[str] = None) -> None:
    db.execute(text("""
        INSERT INTO shafa_publications
            (productnumber, anchor_product_id, source, status, shafa_url,
             shafa_listing_id, last_error, confirmed_at, updated_at)
        VALUES (:number, :pid, :source, :status, :url, :listing_id, :error,
                CASE WHEN :status IN ('confirmed','manual_existing') THEN now() ELSE NULL END,
                now())
        ON CONFLICT (productnumber) DO UPDATE SET
            anchor_product_id=EXCLUDED.anchor_product_id,
            source=EXCLUDED.source,
            status=EXCLUDED.status,
            shafa_url=COALESCE(EXCLUDED.shafa_url, shafa_publications.shafa_url),
            shafa_listing_id=COALESCE(EXCLUDED.shafa_listing_id, shafa_publications.shafa_listing_id),
            last_error=EXCLUDED.last_error,
            confirmed_at=CASE
                WHEN EXCLUDED.status IN ('confirmed','manual_existing')
                    THEN COALESCE(shafa_publications.confirmed_at, now())
                ELSE shafa_publications.confirmed_at
            END,
            updated_at=now()
    """), {
        "number": meta["productnumber"], "pid": meta["product_id"],
        "source": source, "status": status, "url": url,
        "listing_id": listing_id, "error": last_error,
    })


def _listing_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    tail = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    return digits or None


def _verify_now(db: Session, product_id: int) -> None:
    """Публічно перечитати відоме оголошення Shafa зараз. Мережева помилка НЕ
    повинна валити основну дію (підтвердження/прив'язку)."""
    try:
        try:
            from services import shafa_reader
        except ImportError:
            from backend.services import shafa_reader
        shafa_reader.verify_product(db, int(product_id))
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("Shafa verify_now(%s) failed: %s", product_id, exc)


def _valid_shafa_url(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and (
            parsed.hostname == "shafa.ua" or str(parsed.hostname or "").endswith(".shafa.ua")
        )
    except Exception:
        return False


def product_status(db: Session, product_id: int) -> dict:
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    cfg = _config(db)
    prom = prom_service.prom_product_status(db, int(product_id))
    pub = _publication(db, meta["productnumber"])

    # Звичайна публікація через чіп Prom теж автоматично входить у Shafa-pipeline.
    # До bridge_ready переходимо лише після живого Prom-лістингу з наявністю:
    # pending/draft не є підтвердженням, що міст уже має що забирати.
    prom_bridge_ready = (
        prom.get("on_prom") and prom.get("status") == "on_display"
        and prom.get("presence") == "available"
    )
    desired_state = "bridge_ready" if prom_bridge_ready else "waiting_prom"
    should_register = (
        cfg.get("bridge_enabled") and prom.get("on_prom") and (
            pub is None
            or (pub.get("status") == "waiting_prom" and prom_bridge_ready)
        )
    )
    if should_register:
        _upsert(db, meta, desired_state)
        db.commit()
        pub = _publication(db, meta["productnumber"])

    raw_state = (pub or {}).get("status") or "not_requested"
    has_remote_evidence = bool((pub or {}).get("shafa_url") or (pub or {}).get("shafa_listing_id"))
    # Захист на випадок, якщо старий запис ще не пройшов cleanup-міграцію:
    # підтвердження без URL/ID не може потрапляти в API як фактична публікація.
    state = raw_state
    if raw_state in PUBLISHED_STATUSES and not has_remote_evidence:
        state = "removed" if (pub or {}).get("source") == "manual" else "bridge_ready"
    expected_since = (pub or {}).get("updated_at") or (pub or {}).get("created_at")
    confirmation_overdue = False
    if state == "bridge_ready" and expected_since:
        try:
            normalized_since = expected_since
            if normalized_since.tzinfo is None:
                normalized_since = normalized_since.replace(tzinfo=timezone.utc)
            confirmation_overdue = datetime.now(timezone.utc) - normalized_since > SHAFA_INITIAL_EXPORT_WINDOW
        except (AttributeError, TypeError):
            confirmation_overdue = False
    pricing = _product_pricing(db, meta, cfg, prom)
    warnings: List[str] = []
    if not cfg.get("bridge_enabled"):
        warnings.append("Увімкни «Експорт товарів на Shafa.ua» у Маркеті Prom і підтвердь це в BMS.")
    if meta["available_qty"] <= 0:
        warnings.append("Shafa експортує лише товари, які є в наявності.")
    if meta["image_count"] <= 0 and not prom.get("on_prom"):
        warnings.append("Немає фото: створення джерела на Prom буде відхилено.")
    if meta["invalid_gtins"]:
        warnings.append("Є невалідний GTIN (контрольна цифра GS1); він не буде переданий у Prom.")
    elif not meta["gtins"]:
        warnings.append("GTIN не заповнено. Якщо товар має EAN, додай його в картці BMS.")
    if state == "bridge_ready":
        warnings.append(
            "Товар автоматично очікується на Shafa: Prom підтвердив статус «Опубліковано» "
            "та наявність. Це ще не доказ фактичного оголошення Shafa."
        )
    if confirmation_overdue:
        warnings.append(
            "Минуло понад 2 доби від готовності товару для мосту. Перевір дозволену категорію "
            "та модерацію в кабінеті Shafa; якщо оголошення є — прив’яжи його URL."
        )
    if (pub or {}).get("last_error"):
        warnings.append(f"Остання спроба: {(pub or {}).get('last_error')}")
    if state == "manual_existing" and prom.get("on_prom") and cfg.get("bridge_enabled"):
        warnings.append("Глобальний міст може створити дублікат ручного оголошення; автоматичне злиття не задокументоване.")
    warnings.append(
        f"Shafa стягує {pricing['shafa_commission_pct']:g}% для категорії «{pricing['tariff_group_label']}» "
        f"(максимум {SHAFA_FEE_CAP:g} грн); замовлення обробляються в Shafa."
    )

    verified = state in PUBLISHED_STATUSES and has_remote_evidence
    expected_presence = "available" if meta["available_qty"] > 0 else "not_available"
    if prom.get("on_prom") and prom.get("presence") and prom.get("presence") != expected_presence:
        warnings.append(
            f"Залишок ще не збігається: BMS очікує {expected_presence}, "
            f"а дзеркало Prom показує {prom.get('presence')}. Запусти синхронізацію наявності Prom."
        )
    if raw_state in PUBLISHED_STATUSES and not has_remote_evidence:
        warnings.append("Старе локальне підтвердження не має URL/ID Shafa, тому воно не вважається доказом публікації.")
    if pricing.get("price_will_change"):
        warnings.append(
            f"One-click оновить ціну Prom з "
            f"{pricing.get('current_prom_price'):g} до {pricing.get('effective_price')} грн; "
            "це вища зі штатної Prom-ціни та мінімуму Shafa, і саме її міст передасть у Shafa."
        )

    return {
        "ok": True,
        **meta,
        "bridge_enabled": bool(cfg.get("bridge_enabled")),
        "on_prom": bool(prom.get("on_prom")),
        "prom_status": prom.get("status"),
        "prom_presence": prom.get("presence"),
        "prom_price": prom.get("price"),
        "prom_last_synced_at": prom.get("last_synced_at"),
        "pricing": pricing,
        "state": state,
        "tracked": state in TRACKED_STATUSES,
        "on_shafa": verified,
        "verified": verified,
        "expected_since": expected_since.isoformat() if hasattr(expected_since, "isoformat") else None,
        "confirmation_overdue": confirmation_overdue,
        "source": (pub or {}).get("source"),
        "shafa_url": (pub or {}).get("shafa_url"),
        # Реальний стан із боку Shafa (публічний statusTitle), а не лише
        # припущення з дзеркала Prom. None — ще не перевіряли.
        "shafa_presence": (pub or {}).get("shafa_presence"),
        "shafa_checked_at": (
            (pub or {}).get("shafa_checked_at").isoformat()
            if hasattr((pub or {}).get("shafa_checked_at"), "isoformat") else None
        ),
        "last_error": (pub or {}).get("last_error"),
        "warnings": warnings,
        "max_listings": SHAFA_MAX_LISTINGS,
        "tracked_total": _tracked_count(db),
        "official_help_url": OFFICIAL_BRIDGE_HELP,
        "tariffs_help_url": SHAFA_TARIFFS_HELP,
        "prom_help_url": PROM_BRIDGE_HELP,
    }


def publish_product(db: Session, product_id: int, force: bool = False) -> dict:
    """Один клік: безпечна ціна -> живий Prom -> глобальний міст Shafa."""
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    cfg = _config(db)
    if not cfg.get("bridge_enabled"):
        return {"ok": False, "error": "Спочатку реально підключи експорт Shafa в Маркеті Prom"}
    if meta["available_qty"] <= 0:
        return {"ok": False, "error": "Shafa експортує лише товари в наявності"}
    current = _publication(db, meta["productnumber"])
    if current and current.get("status") == "manual_existing" and not force:
        return {"ok": False, "duplicate_risk": True,
                "error": "Товар уже прив'язано до ручного оголошення Shafa; автоміст може створити дублікат"}

    prom_before = prom_service.prom_product_status(db, int(product_id))
    pricing = _product_pricing(db, meta, cfg, prom_before)
    if not pricing.get("margin_safe") or not pricing.get("effective_price"):
        return {"ok": False, "error": "Не вдалося розрахувати ціну із захищеною маржею"}

    preserve_confirmed = bool(
        current and current.get("status") == "confirmed"
        and (current.get("shafa_url") or current.get("shafa_listing_id"))
    )
    if not preserve_confirmed:
        _upsert(db, meta, "waiting_prom", last_error=None)
        db.commit()
    prom_result = prom_service.ensure_product_live(
        db, int(product_id), float(pricing["effective_price"]),
    )
    if not prom_result.get("ok"):
        error = str(prom_result.get("error") or "Prom не прийняв товар")
        _upsert(db, meta, "confirmed" if preserve_confirmed else "waiting_prom", last_error=error)
        db.commit()
        return {**prom_result, "ok": False, "pricing": pricing}

    next_state = (
        "confirmed" if preserve_confirmed
        else ("waiting_prom" if prom_result.get("queued") else "bridge_ready")
    )
    _upsert(db, meta, next_state, last_error=None)
    db.commit()
    result = product_status(db, product_id)
    result.update({
        "queued": bool(prom_result.get("queued")),
        "updated_existing": bool(prom_result.get("updated_existing")),
        "import_id": prom_result.get("import_id"),
        "skus": prom_result.get("skus") or ([prom_result.get("sku")] if prom_result.get("sku") else []),
        "visible_skus": prom_result.get("visible_skus") or [],
        "pricing": pricing,
        "note": (
            f"Ціну {pricing['effective_price']} грн і наявність підтверджено на Prom; "
            "глобальний міст оновить Shafa автоматично."
            if not prom_result.get("queued") else
            f"Prom прийняв товар за {pricing['effective_price']} грн. BMS автоматично дочекається "
            "офіційного підтвердження й передасть його глобальному мосту Shafa."
        ),
    })
    return result


def finalize_product(db: Session, product_id: int, skus: List[str]) -> dict:
    """Після SUCCESS імпорту точково підтягнути Prom і завершити one-click flow."""
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    sync = prom_service.sync_products_by_skus(db, skus)
    result = product_status(db, product_id)
    result["prom_sync"] = sync
    if result.get("prom_status") == "on_display" and result.get("prom_presence") == "available":
        result["note"] = (
            "Prom офіційно підтвердив живий товар і наявність. Глобальний міст Shafa "
            "створить або оновить оголошення автоматично."
        )
    else:
        result["note"] = (
            "Імпорт Prom завершено, але живий товар ще не з'явився у дзеркалі. "
            "Фоновий контроль продовжить синхронізацію."
        )
    return result


def finalize_products_batch(db: Session, product_ids: List[int], skus: List[str]) -> dict:
    """Пакетний аналог finalize_product після одного Prom import_file."""
    sync = prom_service.sync_products_by_skus(db, skus)
    seen, ready, waiting = set(), 0, 0
    for raw_pid in product_ids or []:
        meta = _product_meta(db, int(raw_pid))
        if not meta or meta["productnumber"] in seen:
            continue
        seen.add(meta["productnumber"])
        status = product_status(db, int(raw_pid))
        if status.get("state") == "bridge_ready":
            ready += 1
        elif status.get("state") == "waiting_prom":
            waiting += 1
    return {
        "ok": True, "prom_sync": sync, "ready_for_bridge": ready,
        "waiting_prom": waiting,
        "note": (
            f"Prom підтвердив пакет: {ready} товар(ів) готові для автоматичного мосту Shafa."
            + (f" Ще синхронізуються: {waiting}." if waiting else "")
        ),
    }


def prepare_product(db: Session, product_id: int, force: bool = False) -> dict:
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    if not _config(db).get("bridge_enabled"):
        return {"ok": False, "error": "Міст Prom→Shafa ще не підтверджено в BMS"}
    if meta["available_qty"] <= 0:
        return {"ok": False, "error": "Shafa експортує лише товари в наявності"}
    current = _publication(db, meta["productnumber"])
    if current and current.get("status") == "manual_existing" and not force:
        return {
            "ok": False, "duplicate_risk": True,
            "error": "Товар уже позначено як наявний на Shafa. Автоміст може створити дублікат.",
        }
    prom = prom_service.prom_product_status(db, int(product_id))
    _upsert(db, meta, "bridge_ready" if prom.get("on_prom") else "waiting_prom")
    db.commit()
    result = product_status(db, product_id)
    result["needs_prom"] = not bool(prom.get("on_prom"))
    result["note"] = (
        "Товар є на Prom і доданий до локального контролю BMS. Глобальний міст Prom→Shafa працює сам; перевір появу в Shafa."
        if prom.get("on_prom") else
        "Намір Shafa збережено. Наступний крок — перевірити й опублікувати товар на Prom."
    )
    return result


def confirm_product(db: Session, product_id: int, url: Optional[str] = None) -> dict:
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    url = (url or "").strip() or None
    if not url:
        return {"ok": False, "error": "Встав посилання на фактичне оголошення Shafa — без нього BMS не може підтвердити публікацію"}
    if not _valid_shafa_url(url):
        return {"ok": False, "error": "Посилання має вести на shafa.ua"}
    _upsert(db, meta, "confirmed", url=url, listing_id=_listing_id(url))
    db.commit()
    # Одразу публічно перечитуємо оголошення: вивчаємо seller_username і
    # синхронізуємо фактичну наявність Shafa — без чекання фонового циклу.
    _verify_now(db, product_id)
    return product_status(db, product_id)


def link_existing(db: Session, product_id: int, url: Optional[str] = None) -> dict:
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    url = (url or "").strip() or None
    if not url:
        return {"ok": False, "error": "Встав посилання на наявне оголошення Shafa"}
    if not _valid_shafa_url(url):
        return {"ok": False, "error": "Посилання має вести на shafa.ua"}
    _upsert(db, meta, "manual_existing", source="manual", url=url, listing_id=_listing_id(url))
    db.commit()
    _verify_now(db, product_id)
    return product_status(db, product_id)


def untrack_product(db: Session, product_id: int) -> dict:
    meta = _product_meta(db, product_id)
    if not meta:
        return {"ok": False, "error": "Товар не знайдено"}
    _upsert(db, meta, "removed")
    db.commit()
    result = product_status(db, product_id)
    result["note"] = "Знято лише локальну позначку BMS; оголошення Shafa не змінено."
    return result


def prepare_products_batch(db: Session, product_ids: List[int]) -> dict:
    cfg = _config(db)
    if not cfg.get("bridge_enabled"):
        return {"ok": False, "error": "Міст Prom→Shafa ще не підтверджено в BMS"}
    ids = [int(x) for x in (product_ids or []) if x]
    if not ids:
        return {"ok": False, "error": "Не вибрано товарів"}
    if len(ids) > 500:
        return {"ok": False, "error": "За один пакет можна вибрати максимум 500 рядків"}

    seen, missing_prom, ready, already, skipped = set(), [], 0, 0, []
    existing_updates: Dict[int, float] = {}
    price_overrides: Dict[int, float] = {}
    pending_skus: List[str] = []
    pending_visible_skus: List[str] = []
    pending_existing = 0
    for pid in ids:
        meta = _product_meta(db, pid)
        if not meta:
            skipped.append({"product_id": pid, "reason": "не знайдено"})
            continue
        number = meta["productnumber"]
        if number in seen:  # ростовка: один номер -> один набір лістингів
            continue
        seen.add(number)
        current = _publication(db, number)
        if current and current.get("status") in ("confirmed", "manual_existing"):
            already += 1
            continue
        if meta["available_qty"] <= 0:
            skipped.append({"product_id": pid, "reason": f"{number}: немає в наявності"})
            continue
        prom_status = prom_service.prom_product_status(db, pid)
        pricing = _product_pricing(db, meta, cfg, prom_status)
        if not pricing.get("margin_safe"):
            skipped.append({"product_id": pid, "reason": f"{number}: не вдалося захистити маржу"})
            continue
        price_overrides[pid] = float(pricing["effective_price"])
        if prom_status.get("status") == "pending":
            _upsert(db, meta, "waiting_prom")
            try:
                pending_rows = prom_service._export_rows(db, pid)
                pending_skus.extend(r["_sku"] for r in pending_rows)
                pending_visible_skus.extend(
                    r["_sku"] for r in pending_rows if int(r.get("_qty") or 0) > 0
                )
            except Exception:
                pass
            pending_existing += 1
        elif prom_status.get("on_prom"):
            existing_updates[pid] = float(pricing["effective_price"])
        else:
            _upsert(db, meta, "waiting_prom")
            missing_prom.append(pid)
    db.commit()

    if existing_updates:
        update_result = prom_service.update_products_for_bridge(db, existing_updates)
        if not update_result.get("ok"):
            err = update_result.get("error") or "Prom не оновив безпечну ціну"
            for pid in existing_updates:
                meta = _product_meta(db, pid)
                if meta:
                    _upsert(db, meta, "waiting_prom", last_error=str(err))
            db.commit()
            return {"ok": False, "error": str(err), "skipped": skipped}
        for pid in existing_updates:
            meta = _product_meta(db, pid)
            if meta:
                _upsert(db, meta, "bridge_ready")
                ready += 1
        db.commit()

    prom_result: Dict[str, Any] = {}
    if missing_prom:
        prom_result = prom_service.export_products_batch(
            db, missing_prom, price_overrides={pid: price_overrides[pid] for pid in missing_prom},
        )
        if not prom_result.get("ok"):
            err = prom_result.get("error") or "Не вдалося створити джерело на Prom"
            for pid in missing_prom:
                meta = _product_meta(db, pid)
                if meta:
                    _upsert(db, meta, "waiting_prom", last_error=err)
            db.commit()
            return {
                **prom_result, "ok": False, "ready_for_bridge": ready,
                "waiting_prom": len(missing_prom), "already": already,
                "skipped": skipped + (prom_result.get("skipped") or []),
            }
        prom_skipped = prom_result.get("skipped") or []
        skipped.extend(prom_skipped)
        skipped_ids = {int(x.get("product_id")) for x in prom_skipped if x.get("product_id")}
        for item in prom_skipped:
            pid = item.get("product_id")
            meta = _product_meta(db, int(pid)) if pid else None
            if meta:
                _upsert(db, meta, "waiting_prom", last_error=str(item.get("reason") or "Prom пропустив товар"))
        if prom_skipped:
            db.commit()
        waiting = len([pid for pid in missing_prom if pid not in skipped_ids]) + pending_existing
    else:
        waiting = pending_existing

    tracked = _tracked_count(db)
    limit_warning = None
    if tracked >= SHAFA_MAX_LISTINGS:
        limit_warning = "Локальна оцінка досягла ліміту Shafa 10 000; перевір фактичну кількість у кабінеті."
    note = (
        f"Shafa: {ready} товар(ів) оновлено на Prom із захищеною маржею; "
        f"{waiting} — BMS контролює до офіційного підтвердження Prom; "
        f"{already} вже підтверджено."
    )
    if skipped:
        note += f" Пропущено: {len(skipped)}."
    return {
        "ok": True,
        "queued": bool(waiting),
        "import_id": prom_result.get("import_id"),
        "skus": list(dict.fromkeys((prom_result.get("skus") or []) + pending_skus)),
        "visible_skus": list(dict.fromkeys((prom_result.get("visible_skus") or []) + pending_visible_skus)),
        "published": ready + waiting,
        "ready_for_bridge": ready,
        "waiting_prom": waiting,
        "already": already,
        "skipped": skipped,
        "limit_warning": limit_warning,
        "note": note,
    }

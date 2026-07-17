"""monoБазар (monobank marketplace) — ціноутворення + статус (READ + write-блокер).

⚠️  СТВОРЕННЯ ОГОЛОШЕНЬ ЗАБЛОКОВАНЕ (станом на 2026-07) — АЛЕ ЧИТАННЯ ПРАЦЮЄ.
────────────────────────────────────────────────────────────────────────────
monoБазар — маркетплейс вживаних речей усередині застосунку monobank
(Маркет → «Базар»). Реверс-інжинірингом публічної вітрини продавця знайдено
ПУБЛІЧНИЙ REST-шлюз без авторизації (`monobazar_reader.py`,
resale-public-api-gateway.monobazar.com.ua) — читає активні оголошення,
кількість, профіль. Це дає верифікацію/моніторинг ВЖЕ ЗАРАЗ, без ФОП і без
партнерського доступу.

Але СТВОРЕННЯ оголошень лишається заблокованим: у JS-бандлах вітрини немає
жодного write-ендпоінта — «Нове оголошення» існує лише в мобільному
застосунку (приватне API, не досліджувалось — інша категорія ризику, ніж
публічний веб-код). Щоб постити офіційно, потрібно:
  • ФОП-рахунок monobank (доступ для ФОП відкривається пізніше, дата не оголошена);
  • запрошення/онбординг від банку в партнерську програму;
  • договір із МУРКОД (оператор);
  • приватну API-документацію (видається партнерам).

Модель витрат monoБазар (з публічних джерел, 2026):
  • комісія продавця: 0.1% до 2026-01-08, далі МІНІМУМ ~1.9% від суми продажу;
  • плати за публікацію (пакета) немає — на відміну від [[olx_pricing]];
  • доставку оплачує покупець.
Точний відсоток для ФОП уточнюється договором — тримаємо його конфігурованим.
"""

from __future__ import annotations

import math
import os
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from services import prom_service, monobazar_reader
except ImportError:  # pragma: no cover
    from backend.services import prom_service, monobazar_reader

# Комісія продавця monoБазар (частка). Дефолт — оголошений мінімум 1.9%.
# Уточнюється партнерським договором для ФОП — тому env-конфігуровано.
MONOBAZAR_COMMISSION = float(os.getenv("MONOBAZAR_COMMISSION", "0.019"))
# Опційний фіксований збір (напр. за еквайринг), якщо з'явиться в договорі.
MONOBAZAR_FLAT_FEE = float(os.getenv("MONOBAZAR_FLAT_FEE", "0"))


def get_status(db: Optional[Session] = None) -> dict:
    """Стан інтеграції для UI: читання ПРАЦЮЄ (verified live), запис — заблоковано."""
    base = {
        "ok": True,
        "available": False,          # створення оголошень
        "reading_available": True,   # верифікація/моніторинг через публічний READ API
        "reason": "Партнерський доступ до створення оголошень ще не відкрито",
        "blockers": [
            "Публічного write-API створення оголошень немає (лише мобільний застосунок)",
            "Потрібен ФОП-рахунок monobank (доступ для ФОП відкривається пізніше)",
            "Потрібне запрошення/онбординг банку в партнерську програму",
            "Потрібен договір із МУРКОД і приватна API-документація",
        ],
        "commission_pct": round(MONOBAZAR_COMMISSION * 100, 2),
        "pricing_ready": True,
        "posting_ready": False,
        "seller_username": None,
        "tracked": 0, "confident": 0, "ambiguous": 0, "unmatched": 0,
        "store_synced_at": None,
    }
    if db is None:
        return base
    username = monobazar_reader.get_seller_username(db)
    row = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE match_confidence = 'confident') AS confident,
            COUNT(*) FILTER (WHERE match_confidence = 'ambiguous') AS ambiguous,
            COUNT(*) FILTER (WHERE match_confidence = 'none')      AS unmatched,
            COUNT(*)                                                AS total
        FROM monobazar_listings
    """)).mappings().first() or {}
    cfg = db.execute(text(
        "SELECT store_synced_at FROM monobazar_config WHERE id=1")).first()
    base.update({
        "seller_username": username,
        "tracked": int(row.get("total") or 0),
        "confident": int(row.get("confident") or 0),
        "ambiguous": int(row.get("ambiguous") or 0),
        "unmatched": int(row.get("unmatched") or 0),
        "store_synced_at": (cfg[0].isoformat() if (cfg and cfg[0]) else None),
    })
    return base


def _round_up_10(value: float) -> int:
    return int(math.ceil(max(float(value or 0), 0.0) / 10.0) * 10)


def price_economics(base_price: float, typename: str,
                    commission: Optional[float] = None,
                    flat_fee: Optional[float] = None,
                    current_price: Optional[float] = None) -> dict:
    """Безпечна ціна monoБазар: чистими після комісії ≥ base × націнка.

    Комісійна модель (без плати за публікацію): грос-ап ціни на комісію.
    """
    base = max(float(base_price or 0), 0.0)
    markup = prom_service._TYPE_MARKUP.get(str(typename or "").strip().lower(), 1.33)
    c = commission if commission is not None else MONOBAZAR_COMMISSION
    flat = flat_fee if flat_fee is not None else MONOBAZAR_FLAT_FEE
    target_net = round(base * markup, 2)
    if base <= 0:
        effective = int(current_price or 0)
    else:
        # price − price*c − flat ≥ target_net  →  price ≥ (target_net + flat)/(1 − c)
        raw = (target_net + flat) / max(1.0 - c, 1e-6)
        effective = _round_up_10(raw)
        # ніколи не знижуємо наявну вищу ціну
        effective = int(max(effective, float(current_price or 0)))
    comm = round(effective * c + flat, 2)
    net = round(effective - comm, 2)
    margin = round(net - base, 2)
    return {
        "strategy": "monobazar_commission",
        "base_price": round(base, 2),
        "markup_multiplier": markup,
        "target_markup_pct": round((markup - 1.0) * 100, 1),
        "target_net": target_net,
        "commission_pct": round(c * 100, 2),
        "commission": comm,
        "flat_fee": round(flat, 2),
        "effective_price": int(effective),
        "current_price": float(current_price) if current_price is not None else None,
        "net": net,
        "margin": margin,
        "margin_pct": round((margin / base) * 100, 1) if base else 0,
        "margin_safe": bool(base and net >= target_net - 0.01),
    }

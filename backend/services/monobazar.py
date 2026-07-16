"""monoБазар (monobank marketplace) — каркас ціноутворення + статус.

⚠️  СТВОРЕННЯ ОГОЛОШЕНЬ ЗАБЛОКОВАНЕ (станом на 2026-07).
────────────────────────────────────────────────────────────────────────────
monoБазар — маркетплейс вживаних речей усередині застосунку monobank
(Маркет → «Базар»). Публічного API замовлень/створення для України у
специфікації НЕМАЄ. Це партнерська інтеграція, і щоб її реалізувати, потрібно:
  • ФОП-рахунок monobank (доступ для ФОП планувався ~через 3–4 міс. після
    запуску 2025-12-08, тобто орієнтовно весна 2026 — треба підтвердити);
  • запрошення/онбординг від банку в партнерську програму;
  • договір із МУРКОД (оператор);
  • приватну API-документацію (видається партнерам).

Тому цей модуль НЕ створює оголошень. Він готує дві речі, щоб щойно доступ
з'явиться — лишалось дописати лише транспортний шар:
  1) get_status() — чесно повідомляє UI, що інтеграція очікує партнерський доступ;
  2) price_economics() — індивідуальне ціноутворення monoБазар (комісійна модель,
     на відміну від OLX з платою за публікацію).

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

try:
    from services import prom_service
except ImportError:  # pragma: no cover
    from backend.services import prom_service

# Комісія продавця monoБазар (частка). Дефолт — оголошений мінімум 1.9%.
# Уточнюється партнерським договором для ФОП — тому env-конфігуровано.
MONOBAZAR_COMMISSION = float(os.getenv("MONOBAZAR_COMMISSION", "0.019"))
# Опційний фіксований збір (напр. за еквайринг), якщо з'явиться в договорі.
MONOBAZAR_FLAT_FEE = float(os.getenv("MONOBAZAR_FLAT_FEE", "0"))


def get_status() -> dict:
    """Стан інтеграції для UI — чесно про блокер, без імітації готовності."""
    return {
        "ok": True,
        "available": False,
        "reason": "Партнерський доступ ще не відкрито",
        "blockers": [
            "Публічного API створення оголошень для України немає",
            "Потрібен ФОП-рахунок monobank (доступ для ФОП відкривається пізніше)",
            "Потрібне запрошення/онбординг банку в партнерську програму",
            "Потрібен договір із МУРКОД і приватна API-документація",
        ],
        "commission_pct": round(MONOBAZAR_COMMISSION * 100, 2),
        "pricing_ready": True,
        "posting_ready": False,
    }


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

"""OLX Price Engine — індивідуальне ціноутворення для OLX.

OLX — це дошка оголошень, тому логіка ІНША, ніж у Prom/Shafa:
  • комісії з продажу «за фактом» немає — натомість продавець платить за
    ПУБЛІКАЦІЮ (пакет / LISTING_FEE, ~24–32 грн/оголошення, залежить від
    категорії та розміру пакета — тягнемо живу ціну з /api/partner/packets);
  • опційно — витрати на просування/рекламу (топ/виділення) — налаштовувані;
  • якщо ввімкнено OLX Доставку — є комісія за успішний продаж:
    приват 2%+20 грн, бізнес 3%+20 грн, максимум 499 грн (+опц. оплата у
    відділенні: 51–500 грн → +15, 500–1000 → +20).

Тому ефективна ціна OLX має покрити: собівартість × націнка + вартість пакета
+ рекламу + комісію OLX Доставки (якщо є), із психологічним округленням.
"""

from __future__ import annotations

import math
import os
from typing import Optional

try:
    from services import prom_service
except ImportError:  # pragma: no cover
    from backend.services import prom_service


# Запасна вартість 1 публікації, якщо жива ціна пакета недоступна (грн).
DEFAULT_PACKET_UNIT_UAH = float(os.getenv("OLX_PACKET_UNIT_FALLBACK", "30"))
# Комісія OLX Доставки (актуально 2026): приват 2%+20, бізнес 3%+20, cap 499.
OLX_DELIVERY_RATE_BUSINESS = float(os.getenv("OLX_DELIVERY_RATE_BUSINESS", "0.03"))
OLX_DELIVERY_RATE_PRIVATE = float(os.getenv("OLX_DELIVERY_RATE_PRIVATE", "0.02"))
OLX_DELIVERY_FLAT_UAH = float(os.getenv("OLX_DELIVERY_FLAT", "20"))
OLX_DELIVERY_CAP_UAH = float(os.getenv("OLX_DELIVERY_CAP", "499"))


def olx_delivery_commission(price: float, is_business: bool = True,
                            branch_payment: bool = False) -> float:
    """Комісія OLX Доставки за успішний продаж. 0 — якщо ціна невалідна."""
    price = max(float(price or 0), 0.0)
    if price <= 0:
        return 0.0
    rate = OLX_DELIVERY_RATE_BUSINESS if is_business else OLX_DELIVERY_RATE_PRIVATE
    fee = price * rate + OLX_DELIVERY_FLAT_UAH
    if branch_payment:
        # Додаткова плата за «Оплату у відділенні» (Нова пошта).
        if 51 <= price <= 500:
            fee += 15.0
        elif 500 < price <= 1000:
            fee += 20.0
    return round(min(fee, OLX_DELIVERY_CAP_UAH), 2)


def _round_up_10(value: float) -> int:
    """Округлення вгору до 10 грн (як штатна Prom-charm ціна закінчується на 0)."""
    return int(math.ceil(max(float(value or 0), 0.0) / 10.0) * 10)


def markup_multiplier(typename: Optional[str]) -> float:
    """Та сама сітка націнок за типом, що й Prom — щоб платформи були узгоджені."""
    return prom_service._TYPE_MARKUP.get(str(typename or "").strip().lower(), 1.33)


def price_economics(base_price: float, typename: str,
                    packet_unit: Optional[float] = None,
                    ad_spend: float = 0.0,
                    is_business: bool = True,
                    use_delivery: bool = True,
                    branch_payment: bool = False,
                    current_olx_price: Optional[float] = None) -> dict:
    """Порахувати безпечну ціну OLX із повною розкладкою витрат.

    Гарантія: ``price − комісія_доставки − пакет − реклама ≥ base × націнка``.
    Наявну вищу OLX-ціну ніколи не знижуємо.
    """
    base = max(float(base_price or 0), 0.0)
    markup = markup_multiplier(typename)
    target_net = round(base * markup, 2)
    packet = max(float(packet_unit if packet_unit is not None else DEFAULT_PACKET_UNIT_UAH), 0.0)
    ad = max(float(ad_spend or 0), 0.0)
    fixed = packet + ad

    def _net_and_comm(p: float):
        comm = olx_delivery_commission(p, is_business, branch_payment) if use_delivery else 0.0
        return round(p - comm - fixed, 2), comm

    if base <= 0:
        effective = int(current_olx_price or 0)
        net, comm = _net_and_comm(effective) if effective else (0.0, 0.0)
        return _result(base, markup, target_net, packet, ad, effective, comm, net,
                       current_olx_price, is_business, use_delivery)

    # Стартова оцінка + ітеративний підйом до 10 грн, доки чистими не покриємо ціль.
    seed = target_net + fixed + (target_net * OLX_DELIVERY_RATE_BUSINESS + OLX_DELIVERY_FLAT_UAH
                                 if use_delivery else 0.0)
    candidate = _round_up_10(seed)
    for _ in range(100_000):
        net, comm = _net_and_comm(candidate)
        if net >= target_net - 0.01:
            break
        candidate = _round_up_10(candidate + 1)

    existing = max(float(current_olx_price or 0), 0.0)
    effective = int(max(candidate, existing))
    net, comm = _net_and_comm(effective)
    return _result(base, markup, target_net, packet, ad, effective, comm, net,
                   current_olx_price, is_business, use_delivery)


def _result(base, markup, target_net, packet, ad, effective, comm, net,
            current_olx_price, is_business, use_delivery) -> dict:
    margin = round(net - base, 2)
    return {
        "strategy": "olx_cost_plus",
        "base_price": round(base, 2),
        "markup_multiplier": markup,
        "target_markup_pct": round((markup - 1.0) * 100, 1),
        "target_net": target_net,
        "packet_unit": round(packet, 2),
        "ad_spend": round(ad, 2),
        "use_delivery": bool(use_delivery),
        "is_business": bool(is_business),
        "delivery_commission": round(comm, 2),
        "delivery_commission_pct": round((OLX_DELIVERY_RATE_BUSINESS if is_business
                                          else OLX_DELIVERY_RATE_PRIVATE) * 100, 1),
        "delivery_cap": OLX_DELIVERY_CAP_UAH,
        "effective_price": int(effective),
        "current_olx_price": float(current_olx_price) if current_olx_price is not None else None,
        "price_will_change": (current_olx_price is not None
                              and abs(float(current_olx_price) - effective) >= 0.01),
        "net": net,
        "margin": margin,
        "margin_pct": round((margin / base) * 100, 1) if base else 0,
        "total_platform_cost": round(packet + ad + comm, 2),
        "margin_safe": bool(base and net >= target_net - 0.01),
    }


def packet_unit_from_packets(packets: list) -> Optional[float]:
    """Обрати «за замовч.» вартість 1 публікації з живого списку пакетів.

    Беремо середній за розміром пакет (20–30 оголошень) — компроміс між
    разовою витратою й ціною/шт. Якщо таких немає — найдешевший за ціною/шт.
    """
    priced = []
    for p in packets or []:
        size = p.get("size") or 0
        price = p.get("price") or 0
        if size and price:
            priced.append((int(size), float(price), float(price) / int(size)))
    if not priced:
        return None
    mid = [x for x in priced if 20 <= x[0] <= 50]
    chosen = min(mid, key=lambda x: x[2]) if mid else min(priced, key=lambda x: x[2])
    return round(chosen[2], 2)

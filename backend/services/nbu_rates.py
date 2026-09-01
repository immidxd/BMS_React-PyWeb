# -*- coding: utf-8 -*-
"""Офіційний курс НБУ на конкретну дату, з кешем назавжди.

Навіщо кеш «назавжди»
─────────────────────
Курс на МИНУЛУ дату не змінюється ніколи. Тому кожен повторний запит — це
марна мережа і зайвий шанс не отримати відповіді. Гірше: без кешу перерахунок
старої витрати залежав би від того, чи доступний зараз bank.gov.ua, і те саме
число могло б порахуватись по-різному в різні дні. Збережений курс робить
розрахунок ВІДТВОРЮВАНИМ — це головна причина таблиці, а не швидкість.

Що НЕ робить цей модуль: не вигадує курс. Немає даних — повертає None, і
рішення (пропустити рядок, спитати людину) ухвалює той, хто викликав.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("bms.nbu_rates")

NBU_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange"
TIMEOUT_SEC = 8.0

# Раніше за цю дату НБУ по долару даних не віддає, а гривня в сучасному вигляді
# існує з 1996-го. Захист від безглуздих запитів на кшталт 1900-01-01.
_EARLIEST = date(1996, 9, 2)


def _cached(db: Session, day: date, currency: str) -> Optional[Decimal]:
    row = db.execute(text("""
        SELECT rate FROM nbu_rates WHERE rate_date = :d AND currency = :c
    """), {"d": day, "c": currency}).scalar()
    return Decimal(str(row)) if row is not None else None


def _store(db: Session, day: date, currency: str, rate: Decimal) -> None:
    db.execute(text("""
        INSERT INTO nbu_rates (rate_date, currency, rate)
        VALUES (:d, :c, :r)
        ON CONFLICT (rate_date, currency) DO NOTHING
    """), {"d": day, "c": currency, "r": str(rate)})


def fetch_from_nbu(day: date, currency: str = "USD") -> Optional[Decimal]:
    """Один запит до НБУ. Будь-який збій → None, ніяких винятків назовні."""
    try:
        response = requests.get(
            NBU_URL,
            params={"valcode": currency, "date": day.strftime("%Y%m%d"), "json": ""},
            timeout=TIMEOUT_SEC,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — мережа не має валити розрахунок
        logger.warning("НБУ недоступний для %s %s: %s", currency, day, exc)
        return None

    # Порожній список — штатна відповідь НБУ на дату, якої ще/вже немає.
    if not isinstance(payload, list) or not payload:
        return None
    raw = payload[0].get("rate")
    if raw is None:
        return None
    try:
        rate = Decimal(str(raw))
    except Exception:  # noqa: BLE001
        return None
    return rate if rate > 0 else None


def rate_for(db: Session, day: date, currency: str = "USD", *,
             allow_network: bool = True) -> Optional[Decimal]:
    """Курс на дату: спершу кеш, потім НБУ. None = даних немає.

    `allow_network=False` — для сухих прогонів і тестів: працюємо лише з тим,
    що вже збережено, і жодного разу не виходимо в мережу.
    """
    if day < _EARLIEST or day > date.today():
        return None
    currency = (currency or "USD").upper()

    cached = _cached(db, day, currency)
    if cached is not None:
        return cached
    if not allow_network:
        return None

    rate = fetch_from_nbu(day, currency)
    if rate is None:
        return None
    _store(db, day, currency, rate)
    return rate


def to_uah(amount: Decimal, rate: Decimal, *,
           vat_pct: Decimal = Decimal("0"),
           bank_fee_pct: Decimal = Decimal("0")) -> Decimal:
    """Сума у валюті → гривня за курсом НБУ з надбавками.

    ПДВ і комісія множаться ОКРЕМО, а не складаються: комісія банку береться з
    підсумку платежу, у якому ПДВ уже сидить. `20% + 1%` як `×1.21` дало б інше
    число, ніж `×1.20 × 1.01`, і на великих сумах різниця помітна.

    Округлення — до копійки, один раз, у самому кінці.
    """
    total = (Decimal(amount) * Decimal(rate)
             * (Decimal(1) + Decimal(vat_pct) / Decimal(100))
             * (Decimal(1) + Decimal(bank_fee_pct) / Decimal(100)))
    return total.quantize(Decimal("0.01"))

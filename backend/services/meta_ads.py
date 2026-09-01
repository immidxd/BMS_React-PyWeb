# -*- coding: utf-8 -*-
"""Витрати на рекламу Meta → аркуш ефіру → «Статистика».

Ланцюг і чому саме такий
────────────────────────
    Meta Marketing API → meta_ad_charges (списання з картки, ₴ за курсом НБУ)
                       → аркуш «Замовлення» (комірка під «Витрати на рекламу»)
                       → advertising_expenses (наявний парсер, аркуш → база)
                       → «Статистика» (уже читає advertising_expenses)

Записувати одразу в `advertising_expenses` не можна: та таблиця — ДЗЕРКАЛО
аркуша, і `_sync_advertising_expense` видаляє з неї рядок, якщо підпису в
аркуші немає. Тобто прямий запис прожив би рівно до наступного парсу. Тому
єдиний напрямок лишається аркуш → база, а ми дописуємо в АРКУШ.

Джерело грошей — СПИСАННЯ, а не покази
──────────────────────────────────────
Кабінет працює за порогом: Meta знімає гроші, коли баланс сягає $87, а не
щодня. У налаштуваннях платежів прямо написано «Поточний баланс $4,47 + усі
застосовні комісії» — тобто витрати на покази й сума списання це різні числа.
Власник просив саме «суми на моменти списання коштів», тож беремо транзакцію:
у ній податки вже сидять, бо стільки Meta реально зняла з картки.

⚠️ Кожне списання йде ПАРОЮ: спроба на одну картку зі статусом «Помилка» і
успішна на другу. Рахувати можна ЛИШЕ оплачені — інакше витрати подвоюються.

Правило дат (сформульоване власником)
─────────────────────────────────────
Списання 20-го належить найближчому ефіру НА цю дату або ПІЗНІШЕ — 21-го,
22-го, 23-го, якщо раніше ефірів не було. Ефіри нерегулярні: за літо 2026
розриви між ними — від 1 до 9 днів, тож «той самий день» покрив би меншість
випадків. Кілька списань між двома ефірами підсумовуються в ОДИН аркуш.

Списання після останнього ефіру не має куди лягти — і НЕ втрачається: рядок
лишається без `air_date` і чекає, поки з'явиться новий аркуш.

Недоторканне
────────────
Аркуш, де в комірці вже щось є, не чіпаємо ніколи: там ручне значення власника.
Аркуш без блоку «Витрати на рекламу» теж не чіпаємо — створювати структуру в
чужій таблиці небезпечніше, ніж пропустити рядок і сказати про це вголос.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("bms.meta_ads")

PAID = "paid"


def paid_only(charges: Iterable[dict]) -> List[dict]:
    """Лише успішні списання.

    Кожен платіж цього кабінету лишає в історії ДВА рядки: невдалу спробу на
    одну картку і успішну на другу. Без цього фільтра витрати подвоюються, і
    помилка виглядає правдоподібно — суми ж однакові.
    """
    return [c for c in charges if str(c.get("status") or "").lower() == PAID]


# Статуси запису в аркуш.
PENDING = "pending"          # ще не дописано
WRITTEN = "written"          # дописано нами
SKIPPED_MANUAL = "skipped_manual"   # у комірці вже було значення власника
NO_BLOCK = "no_block"        # на аркуші немає підпису «Витрати на рекламу»
NO_AIR = "no_air"            # ефіру на цю дату ще немає


def resolve_air_date(charge_date: date, air_dates: Sequence[date]) -> Optional[date]:
    """Найближчий ефір НА дату витрати або ПІЗНІШЕ. None — ефіру ще немає.

    `air_dates` мусить бути відсортованим за зростанням.
    """
    if not air_dates:
        return None
    idx = bisect_left(air_dates, charge_date)
    return air_dates[idx] if idx < len(air_dates) else None


def group_by_air(charges: Iterable[dict], air_dates: Sequence[date]
                 ) -> Tuple[Dict[date, List[dict]], List[dict]]:
    """Розкласти витрати по ефірах. Другим елементом — ті, що ще не мають ефіру.

    Кілька кампаній одного дня і кілька днів між ефірами природно збираються в
    один список: групування йде за ефіром-адресатом, а не за датою витрати.
    """
    grouped: Dict[date, List[dict]] = {}
    orphans: List[dict] = []
    for charge in charges:
        air = resolve_air_date(charge["charge_date"], air_dates)
        if air is None:
            orphans.append(charge)
            continue
        grouped.setdefault(air, []).append(charge)
    return grouped, orphans


def total_uah(charges: Iterable[dict]) -> Decimal:
    """Сума в гривні. Рядок без порахованої гривні НЕ вважається нулем.

    Нуль замість невідомого — найгірше, що тут можна зробити: у аркуш пішла б
    занижена цифра, і виглядала б вона як справжня. Тому такий рядок узагалі не
    бере участі в підсумку, а виклик мусить перевірити `unpriced`.
    """
    return sum(
        (Decimal(str(s["amount_uah"])) for s in charges if s.get("amount_uah") is not None),
        Decimal("0"),
    ).quantize(Decimal("0.01"))


def unpriced(charges: Iterable[dict]) -> List[dict]:
    """Витрати, для яких не вдалося порахувати гривню (немає курсу НБУ)."""
    return [s for s in charges if s.get("amount_uah") is None]


# ── Налаштування ────────────────────────────────────────────────────────────
def load_config(db: Session) -> dict:
    row = db.execute(text("""
        SELECT id, account_id, enabled, vat_pct, bank_fee_pct, backfill_from,
               last_synced_at, last_error, last_error_at
        FROM meta_ads_config WHERE id = 1
    """)).mappings().first()
    if not row:
        db.execute(text("INSERT INTO meta_ads_config (id) VALUES (1) ON CONFLICT DO NOTHING"))
        db.commit()
        return load_config(db)
    return dict(row)


def save_config(db: Session, **fields) -> dict:
    allowed = {"account_id", "enabled", "vat_pct", "bank_fee_pct", "backfill_from"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if clean:
        sets = ", ".join(f"{k} = :{k}" for k in clean)
        db.execute(text(f"UPDATE meta_ads_config SET {sets}, updated_at = now() WHERE id = 1"),
                   clean)
        db.commit()
    return load_config(db)


# ── Перерахунок у гривню ────────────────────────────────────────────────────
def price_charges(db: Session, charges: Sequence[dict], config: dict, *,
                 allow_network: bool = True) -> List[dict]:
    """Дописати кожній витраті курс НБУ на ЇЇ дату і суму в гривні.

    Курс береться на дату САМОЇ витрати, а не на дату ефіру: гроші списано тоді,
    коли списано, і саме той курс банк і застосував. Прив'язка до ефіру — це
    вже питання обліку, воно на курс не впливає.
    """
    try:
        from services import nbu_rates
    except ImportError:
        from backend.services import nbu_rates

    vat = Decimal(str(config.get("vat_pct") or 0))
    fee = Decimal(str(config.get("bank_fee_pct") or 0))
    priced: List[dict] = []
    for charge in charges:
        row = dict(charge)
        currency = str(row.get("currency") or "USD").upper()
        if currency == "UAH":
            # Кабінет уже в гривні — курс не потрібен, надбавки теж: банк не
            # конвертує, а ПДВ у такому кабінеті Meta виставляє сама.
            row["nbu_rate"] = None
            row["amount_uah"] = Decimal(str(row["amount"])).quantize(Decimal("0.01"))
        else:
            rate = nbu_rates.rate_for(db, row["charge_date"], currency,
                                      allow_network=allow_network)
            row["nbu_rate"] = rate
            row["amount_uah"] = (
                nbu_rates.to_uah(Decimal(str(row["amount"])), rate,
                                 vat_pct=vat, bank_fee_pct=fee)
                if rate is not None else None
            )
        row["vat_pct"] = vat
        row["bank_fee_pct"] = fee
        priced.append(row)
    return priced

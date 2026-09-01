"""Прив'язка витрат Meta до ефірів і перерахунок у гривню.

Правило власника: реклама, куплена 20-го, належить найближчому ефіру НА цю дату
або ПІЗНІШЕ. Ефіри нерегулярні — за літо 2026 розриви між ними від 1 до 9 днів,
тож «той самий день» покрив би меншість випадків.
"""
from datetime import date
from decimal import Decimal

import pytest

from backend.services.meta_ads import (
    group_by_air, resolve_air_date, total_uah, unpriced,
)
from backend.services.nbu_rates import to_uah


AIRS = [date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 9),
        date(2026, 8, 15), date(2026, 8, 23), date(2026, 8, 26)]


# ── Правило дат ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bought,expected", [
    # Куплено 20-го, найближчий ефір 23-го — саме той випадок із постановки.
    (date(2026, 8, 20), date(2026, 8, 23)),
    (date(2026, 8, 21), date(2026, 8, 23)),
    (date(2026, 8, 22), date(2026, 8, 23)),
    # День ефіру належить САМ СОБІ, а не наступному.
    (date(2026, 8, 23), date(2026, 8, 23)),
    # Дрібний розрив теж працює.
    (date(2026, 8, 2), date(2026, 8, 8)),
    # Дуже давня покупка чіпляється до найпершого відомого ефіру.
    (date(2025, 1, 1), date(2026, 8, 1)),
])
def test_spend_belongs_to_the_next_air_on_or_after_it(bought, expected):
    assert resolve_air_date(bought, AIRS) == expected


def test_a_spend_after_the_last_air_waits_instead_of_being_lost():
    """Ефіру ще немає — рядок не можна ні втратити, ні притулити до минулого."""
    assert resolve_air_date(date(2026, 8, 27), AIRS) is None
    assert resolve_air_date(date(2026, 12, 31), AIRS) is None


def test_without_any_airs_nothing_is_resolved():
    assert resolve_air_date(date(2026, 8, 20), []) is None


# ── Групування: кілька кампаній і кілька днів — в один аркуш ────────────────
def _spend(day, name, uah):
    return {"charge_date": day, "campaign_name": name,
            "amount_uah": Decimal(uah) if uah is not None else None}


def test_many_campaigns_across_many_days_collapse_into_one_air():
    """Між ефірами 15.08 і 23.08 сім днів. Усе, що куплено в цьому проміжку,
    в тому числі кілька кампаній одного дня, іде в аркуш 23.08 разом."""
    spends = [
        _spend(date(2026, 8, 16), "Кампанія A", "100.00"),
        _spend(date(2026, 8, 16), "Кампанія B", "50.00"),   # той самий день
        _spend(date(2026, 8, 20), "Кампанія C", "25.50"),
        _spend(date(2026, 8, 23), "Кампанія D", "10.00"),   # день самого ефіру
    ]
    grouped, orphans = group_by_air(spends, AIRS)

    assert orphans == []
    assert list(grouped) == [date(2026, 8, 23)]
    assert total_uah(grouped[date(2026, 8, 23)]) == Decimal("185.50")


def test_spends_are_split_between_neighbouring_airs():
    grouped, _ = group_by_air([
        _spend(date(2026, 8, 2), "до 08.08", "10.00"),
        _spend(date(2026, 8, 9), "у день 09.08", "20.00"),
        _spend(date(2026, 8, 24), "до 26.08", "30.00"),
    ], AIRS)
    assert {k: total_uah(v) for k, v in grouped.items()} == {
        date(2026, 8, 8): Decimal("10.00"),
        date(2026, 8, 9): Decimal("20.00"),
        date(2026, 8, 26): Decimal("30.00"),
    }


def test_orphans_are_returned_separately_not_silently_dropped():
    grouped, orphans = group_by_air([
        _spend(date(2026, 8, 20), "лягає", "10.00"),
        _spend(date(2026, 9, 5), "ефіру ще немає", "99.00"),
    ], AIRS)
    assert list(grouped) == [date(2026, 8, 23)]
    assert [s["campaign_name"] for s in orphans] == ["ефіру ще немає"]


# ── Нуль замість невідомого — найгірше, що тут можна зробити ────────────────
def test_a_spend_without_a_rate_is_never_counted_as_zero():
    """Без курсу НБУ гривні немає. Якби такий рядок пішов у суму нулем, в аркуш
    поїхала б занижена цифра — і виглядала б як справжня."""
    spends = [_spend(date(2026, 8, 20), "є курс", "100.00"),
              _spend(date(2026, 8, 21), "нема курсу", None)]
    assert total_uah(spends) == Decimal("100.00")
    assert [s["campaign_name"] for s in unpriced(spends)] == ["нема курсу"]


# ── Арифметика гривні ───────────────────────────────────────────────────────
def test_vat_and_bank_fee_multiply_rather_than_add():
    """Комісія банку береться з підсумку платежу, у якому ПДВ уже сидить.
    `20% + 1%` як ×1.21 дало б інше число, ніж ×1.20 × 1.01."""
    naive = Decimal("100") * Decimal("44.7006") * Decimal("1.21")
    honest = to_uah(Decimal("100"), Decimal("44.7006"),
                    vat_pct=Decimal("20"), bank_fee_pct=Decimal("1"))
    assert honest == Decimal("5417.71")
    assert honest != naive.quantize(Decimal("0.01"))


def test_without_markups_it_is_plain_nbu_conversion():
    assert to_uah(Decimal("10"), Decimal("44.7006")) == Decimal("447.01")


def test_rounding_happens_once_at_the_very_end():
    """Округляти кожен множник окремо — накопичувати похибку.

    Перевіряємо не магічне число, а сам намір: результат мусить збігатися з
    точним добутком, округленим ОДИН раз, і відрізнятися від «округлив курс,
    потім помножив».
    """
    amount, rate = Decimal("1234.56"), Decimal("44.7006")
    exact = (amount * rate * Decimal("1.2")).quantize(Decimal("0.01"))
    step_by_step = ((amount * rate).quantize(Decimal("0.01"))
                    * Decimal("1.2")).quantize(Decimal("0.01"))

    assert to_uah(amount, rate, vat_pct=Decimal("20")) == exact
    assert exact == Decimal("66222.69")
    # Наочно: покрокове округлення дає інше число.
    assert step_by_step != exact


# ── Кожне списання лишає в історії пару: помилка + успіх ────────────────────
def test_only_paid_charges_count_or_spending_doubles():
    """Бойовий кабінет: 30.08 два рядки по 87,00 USD — на одній картці
    «Помилка», на другій «Оплачено». Без фільтра витрати подвоюються, і
    помилка виглядає правдоподібно, бо суми однакові."""
    from backend.services.meta_ads import paid_only
    charges = [
        {"transaction_id": "A", "status": "paid", "amount": Decimal("87.00")},
        {"transaction_id": "B", "status": "failed", "amount": Decimal("87.00")},
        {"transaction_id": "C", "status": "Paid", "amount": Decimal("8.60")},
    ]
    assert [c["transaction_id"] for c in paid_only(charges)] == ["A", "C"]


def test_a_failed_charge_never_reaches_a_sheet():
    from backend.services.meta_ads import paid_only
    charges = [
        {"charge_date": date(2026, 8, 20), "status": "failed",
         "amount_uah": Decimal("3900.00")},
        {"charge_date": date(2026, 8, 20), "status": "paid",
         "amount_uah": Decimal("3900.00")},
    ]
    grouped, _ = group_by_air(paid_only(charges), AIRS)
    assert total_uah(grouped[date(2026, 8, 23)]) == Decimal("3900.00")


def test_charged_amount_needs_no_vat_multiplier():
    """У сумі списання податки вже сидять — Meta зняла з картки саме стільки.
    Додати 20% зверху означало б завищити витрати на п'яту частину."""
    charged_usd, rate = Decimal("87.00"), Decimal("44.7006")
    assert to_uah(charged_usd, rate) == Decimal("3888.95")
    assert to_uah(charged_usd, rate, vat_pct=Decimal("20")) == Decimal("4666.74")

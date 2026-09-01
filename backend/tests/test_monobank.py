"""Виписка monobank: розбір операцій і зіставлення з транзакціями Meta.

Еталон — справжній чек від 30.08.2026: 87,00 USD списано як 3897,67 ₴,
опис «FACEBK *VP93D4ECP4». Рівно той самий код `VP93D4ECP4` Meta показує в
історії платежів, тому зіставлення точне, а не «та сама дата й схожа сума».
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.services import monobank as mb


def _item(*, amount_uah_kop, usd_cents, description, when, tx_id="t1"):
    return {
        "id": tx_id,
        "time": int(when.timestamp()),
        "description": description,
        "amount": amount_uah_kop,
        "operationAmount": usd_cents,
        "currencyCode": 840,
        "commissionRate": 0,
    }


REAL = _item(
    amount_uah_kop=-389767, usd_cents=-8700,
    description="FACEBK *VP93D4ECP4",
    when=datetime(2026, 8, 30, 23, 2, 6, tzinfo=timezone.utc),
    tx_id="bank-1",
)


def test_the_real_receipt_parses_to_the_exact_hryvnia():
    parsed = mb.parse_charge(REAL)
    assert parsed["amount_uah"] == Decimal("3897.67")
    assert parsed["operation_amount"] == Decimal("87.00")
    assert parsed["operation_currency"] == "USD"
    assert parsed["auth_code"] == "VP93D4ECP4"
    assert parsed["charge_date"] == date(2026, 8, 30)


@pytest.mark.parametrize("description,expected", [
    ("FACEBK *VP93D4ECP4", "VP93D4ECP4"),
    ("FACEBOOK *D7KN832CP4", "D7KN832CP4"),
    ("FACEBK*5JJZE4JBP4", "5JJZE4JBP4"),
    ("Сільпо", None),                 # немає зірочки — немає коду
])
def test_auth_code_is_read_from_the_description(description, expected):
    assert mb.auth_code(description) == expected


def test_only_debits_count_as_advertising():
    """Повернення й кешбек мають додатний amount. Зарахувати їх у витрати
    означало б зменшити рекламний бюджет на суму, якої ніхто не витрачав."""
    refund = dict(REAL, amount=389767, id="refund")
    assert mb.is_meta_charge(REAL) is True
    assert mb.is_meta_charge(refund) is False


def test_unrelated_purchases_are_ignored():
    other = _item(amount_uah_kop=-25000, usd_cents=None,
                  description="Сільпо", when=datetime(2026, 8, 30, tzinfo=timezone.utc))
    assert mb.is_meta_charge(other) is False
    assert mb.meta_charges_from([REAL, other])[0]["auth_code"] == "VP93D4ECP4"


# ── Зіставлення ─────────────────────────────────────────────────────────────
def test_auth_code_beats_guessing_by_date_and_amount():
    """Два списання по 87 USD одного дня (успішне й повторне) відрізняються
    лише кодом. Саме тому код — головний ключ."""
    bank = [
        mb.parse_charge(REAL),
        mb.parse_charge(_item(amount_uah_kop=-389800, usd_cents=-8700,
                              description="FACEBK *ZZZZZZZZZZ",
                              when=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
                              tx_id="bank-2")),
    ]
    meta = [{"transaction_id": "meta-1", "auth_code": "VP93D4ECP4",
             "charge_date": date(2026, 8, 30), "amount": Decimal("87.00")}]
    matched = mb.match_to_meta(meta, bank)
    assert matched["meta-1"]["bank_transaction_id"] == "bank-1"
    assert matched["meta-1"]["match"] == "auth_code"
    assert matched["meta-1"]["amount_uah"] == Decimal("3897.67")


def test_without_a_code_it_falls_back_to_date_and_amount():
    bank = [mb.parse_charge(REAL)]
    meta = [{"transaction_id": "meta-1", "auth_code": None,
             "charge_date": date(2026, 8, 30), "amount": Decimal("87.00")}]
    matched = mb.match_to_meta(meta, bank)
    assert matched["meta-1"]["match"] == "date_amount"


def test_one_bank_row_is_never_spent_twice():
    """Інакше невдала спроба Meta «зіставилась» би з тим самим списанням, і
    витрати подвоїлись — рівно та пастка, що вже є в історії платежів Meta."""
    bank = [mb.parse_charge(REAL)]
    meta = [
        {"transaction_id": "meta-1", "auth_code": None,
         "charge_date": date(2026, 8, 30), "amount": Decimal("87.00")},
        {"transaction_id": "meta-2", "auth_code": None,
         "charge_date": date(2026, 8, 30), "amount": Decimal("87.00")},
    ]
    matched = mb.match_to_meta(meta, bank)
    assert set(matched) == {"meta-1"}


def test_nothing_matches_when_the_bank_says_nothing():
    assert mb.match_to_meta([{"transaction_id": "m", "auth_code": "X",
                              "charge_date": date(2026, 8, 30),
                              "amount": Decimal("87.00")}], []) == {}


# ── Ліміт 1 запит/60 с визначає архітектуру, а не є дрібницею ───────────────
def test_history_is_cut_into_31_day_windows_with_a_pause_between():
    """Історія з 2022-го — це ~55 запитів, тобто майже година очікування.
    Тому вивантаження — генератор із паузою, придатний до переривання."""
    slept, calls = [], []

    def fake_chunk(_acc, since, until):
        calls.append((since.date(), until.date()))
        return []

    original = mb.statement_chunk
    mb.statement_chunk = fake_chunk
    try:
        until = datetime(2026, 9, 1, tzinfo=timezone.utc)
        since = until - timedelta(days=90)
        chunks = list(mb.iter_statement("acc", since, until, sleeper=slept.append))
    finally:
        mb.statement_chunk = original

    assert [c[0] for c in chunks] == [1, 2, 3]
    assert all(c[1] == 3 for c in chunks)
    # Жодне вікно не перевищує стелю банку.
    assert all((u - s) <= timedelta(days=31) for s, u in
               [(datetime.combine(a, datetime.min.time()),
                 datetime.combine(b, datetime.min.time())) for a, b in calls])
    # Пауза МІЖ запитами: три запити — дві паузи, а не три.
    assert slept == [mb.SLEEP_BETWEEN_SEC, mb.SLEEP_BETWEEN_SEC]


def test_a_window_wider_than_the_bank_allows_is_refused():
    with pytest.raises(ValueError):
        mb.statement_chunk("acc", datetime(2026, 1, 1, tzinfo=timezone.utc),
                           datetime(2026, 3, 1, tzinfo=timezone.utc))


def test_a_missing_token_is_a_clear_refusal_not_a_crash(monkeypatch):
    for name in ("BMS_MONO_TOKEN", "MONO_TOKEN", "FM_MONO_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert mb.token() is None
    with pytest.raises(mb.MonoUnavailable):
        mb._headers()

# -*- coding: utf-8 -*-
"""Виписка monobank — точна гривня замість розрахованої.

Навіщо це поруч із курсом НБУ
─────────────────────────────
Курс НБУ × надбавка дає НАБЛИЖЕННЯ: банк застосовує власний курс, який плаває
протягом дня. Виписка ж містить рівно ту гривню, яку списали. Тому:

    є збіг у виписці  → беремо суму банку (точно, до копійки);
    збігу немає       → рахуємо через НБУ (наближено, але завжди).

Другий шлях лишається НЕ як запасний варіант на випадок збою, а тому що
виписка Personal API віддає обмежену глибину, а рекламу купували й раніше.

Як розпізнається операція Meta
──────────────────────────────
⚠️ Контрольний номер (`VP93D4ECP4`) видно лише в PDF-квитанції банку; у виписці
Personal API його НЕМАЄ — там `description` це просто «Facebook». Тому
наскрізного ключа з історією платежів Meta не існує, і зіставлення (якщо колись
знадобиться) йде за датою й сумою в USD. Перевірено на живих даних 01.09.2026.

Розпізнаємо за назвою продавця; `mcc` 7311 («рекламні послуги») зберігаємо як
підтвердження, але НЕ як ознаку: під 7311 підпадає й Google Ads, тож самого
коду замало.

⚠️ Ліміти Personal API (перевірено в іншому проєкті власника — FM/utils/mono.py)
──────────────────────────────────────────────────────────────────────────────
* `/personal/statement` — максимум 31 день за запит;
* **1 запит на 60 секунд**, інакше 429.

Це визначає архітектуру, а не є дрібницею: історія з 2022-го — це ~55 запитів,
тобто майже година тільки на очікування. Тому вивантаження зроблено генератором
із паузою і придатне для фонового, перериваного прогону — а не одним викликом,
який «колись повернеться».
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import requests

logger = logging.getLogger("bms.monobank")

BASE = "https://api.monobank.ua"
CHUNK_DAYS = 31
# 60 с — задокументована стеля; беремо з запасом, бо годинник клієнта й сервера
# не збігаються, а ціна помилки — 429 і втрачений чанк.
SLEEP_BETWEEN_SEC = 62
TIMEOUT_SEC = 60

CURRENCY_BY_CODE = {980: "UAH", 840: "USD", 978: "EUR", 826: "GBP", 985: "PLN"}

# Опис операції Meta у виписці: `FACEBK *VP93D4ECP4`, іноді `FACEBOOK`.
_META_DESCRIPTION = re.compile(r"\b(?:FACEB(?:K|OOK)|META\s*PLATFORMS?|INSTAGRAM)\b",
                               re.IGNORECASE)
# MCC 7311 — «рекламні послуги». Не ознака сама по собі (це й Google Ads), але
# корисне підтвердження в звіті.
MCC_ADVERTISING = 7311
# Контрольний номер після зірочки — той самий, що Meta показує в історії платежів.
_AUTH_CODE = re.compile(r"\*\s*([A-Z0-9]{6,20})")


class MonoUnavailable(RuntimeError):
    """Банк не відповів або впав ліміт. Не привід валити синхронізацію."""


def token() -> Optional[str]:
    """Токен Personal API. Живе лише в середовищі, ніколи в базі й не в git."""
    return (os.getenv("BMS_MONO_TOKEN") or os.getenv("MONO_TOKEN")
            or os.getenv("FM_MONO_TOKEN") or "").strip() or None


def _headers() -> dict:
    tok = token()
    if not tok:
        raise MonoUnavailable("немає токена monobank (BMS_MONO_TOKEN)")
    return {"X-Token": tok}


def _get(path: str) -> object:
    try:
        response = requests.get(f"{BASE}{path}", headers=_headers(), timeout=TIMEOUT_SEC)
    except requests.RequestException as exc:
        raise MonoUnavailable(f"мережа: {exc}") from exc
    if response.status_code == 429:
        raise MonoUnavailable("ліміт monobank: 1 запит на 60 с")
    if response.status_code >= 400:
        raise MonoUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
    return response.json()


def accounts() -> List[dict]:
    """Рахунки клієнта. Гривневий картковий — той, з якого платять за рекламу."""
    info = _get("/personal/client-info")
    out = []
    for acc in (info or {}).get("accounts", []):
        out.append({
            "id": acc.get("id"),
            "currency": CURRENCY_BY_CODE.get(acc.get("currencyCode"), str(acc.get("currencyCode"))),
            "type": acc.get("type"),
            "masked_pan": acc.get("maskedPan") or [],
            "balance": Decimal(str(acc.get("balance", 0))) / 100,
        })
    return out


def statement_chunk(account_id: str, since: datetime, until: datetime) -> List[dict]:
    """Один шматок виписки. Вікно понад 31 день банк не приймає."""
    if (until - since) > timedelta(days=CHUNK_DAYS):
        raise ValueError("вікно виписки не може перевищувати 31 день")
    return _get(f"/personal/statement/{account_id}/{int(since.timestamp())}/{int(until.timestamp())}") or []


def iter_statement(account_id: str, since: datetime, until: datetime, *,
                   sleep_between: int = SLEEP_BETWEEN_SEC,
                   sleeper=time.sleep) -> Iterator[Tuple[int, int, List[dict]]]:
    """Уся виписка шматками по 31 день, від НОВІШИХ до старіших.

    Віддає `(номер шматка, усього шматків, операції)` — щоб виклик міг звітувати
    про поступ і зупинитись посеред довгого вивантаження, не втративши зробленого.

    Пауза стоїть МІЖ запитами, а не після останнього: зайва хвилина очікування в
    кінці нікому не потрібна.
    """
    windows: List[Tuple[datetime, datetime]] = []
    cursor = until
    while cursor > since:
        start = max(cursor - timedelta(days=CHUNK_DAYS), since)
        windows.append((start, cursor))
        cursor = start
    total = len(windows)
    for idx, (start, end) in enumerate(windows, 1):
        if idx > 1 and sleep_between:
            sleeper(sleep_between)
        yield idx, total, statement_chunk(account_id, start, end)


# ── Розбір операцій ─────────────────────────────────────────────────────────
def auth_code(description: str) -> Optional[str]:
    """`FACEBK *VP93D4ECP4` → `VP93D4ECP4`. Немає зірочки — немає коду."""
    match = _AUTH_CODE.search(str(description or ""))
    return match.group(1).upper() if match else None


def is_meta_charge(item: dict) -> bool:
    """Операція Meta: за описом, і обов'язково СПИСАННЯ.

    Повернення й кешбек мають додатний `amount` — зарахувати їх у витрати
    означало б зменшити рекламний бюджет на суму, яку ніхто не витрачав.
    """
    if int(item.get("amount") or 0) >= 0:
        return False
    return bool(_META_DESCRIPTION.search(str(item.get("description") or "")))


def parse_charge(item: dict) -> dict:
    """Операція виписки → нормалізований запис.

    `amount` у monobank — копійки валюти РАХУНКУ (у нас гривні), від'ємні на
    списання. `operationAmount` — у валюті операції (`currencyCode`), тобто
    саме ті долари, які Meta зняла.
    """
    uah = Decimal(abs(int(item.get("amount") or 0))) / 100
    op_raw = item.get("operationAmount")
    op_currency = CURRENCY_BY_CODE.get(item.get("currencyCode"), None)
    op_amount = Decimal(abs(int(op_raw))) / 100 if op_raw is not None else None
    moment = datetime.fromtimestamp(int(item.get("time") or 0), tz=timezone.utc)
    description = str(item.get("description") or "")
    return {
        "bank_transaction_id": str(item.get("id") or ""),
        # Номер квитанції банку — те саме, що надруковано в PDF («Квитанція №
        # 9X42-18TP-5593-954H»). Дає людині звірити рядок із паперовим чеком.
        "receipt_id": str(item.get("receiptId") or "") or None,
        "mcc": item.get("mcc"),
        "charge_date": moment.date(),
        "charged_at": moment,
        "description": description,
        "auth_code": auth_code(description),
        "amount_uah": uah.quantize(Decimal("0.01")),
        "operation_amount": op_amount,
        "operation_currency": op_currency,
        # Банк не бере комісії окремим рядком (у чеку «Комісія 0.00») — вона
        # зашита в курс. Поле лишаємо, бо для інших операцій воно ненульове.
        "commission_uah": Decimal(int(item.get("commissionRate") or 0)) / 100,
    }


def meta_charges_from(items: Iterable[dict]) -> List[dict]:
    return [parse_charge(item) for item in items if is_meta_charge(item)]


# ── Зіставлення з транзакціями Meta ─────────────────────────────────────────
def match_to_meta(meta_rows: Iterable[dict], bank_rows: Iterable[dict]) -> Dict[str, dict]:
    """`{transaction_id Meta: операція банку}`.

    Спершу за контрольним номером — це точний ключ: Meta показує його в історії
    платежів, банк кладе в опис (`FACEBK *VP93D4ECP4`). Лише те, що не зійшлося
    кодом, добираємо за (дата + сума в USD) і позначаємо як слабший збіг, бо
    два однакові списання одного дня цим способом не розрізнити.
    """
    by_code = {row["auth_code"]: row for row in bank_rows if row.get("auth_code")}
    by_date_amount: Dict[Tuple[date, Decimal], List[dict]] = {}
    for row in bank_rows:
        if row.get("operation_amount") is None:
            continue
        by_date_amount.setdefault(
            (row["charge_date"], row["operation_amount"]), []).append(row)

    matched: Dict[str, dict] = {}
    used: set = set()
    for meta in meta_rows:
        code = (meta.get("auth_code") or "").upper()
        hit = by_code.get(code) if code else None
        if hit is not None and hit["bank_transaction_id"] not in used:
            matched[meta["transaction_id"]] = {**hit, "match": "auth_code"}
            used.add(hit["bank_transaction_id"])
            continue
        key = (meta.get("charge_date"), Decimal(str(meta.get("amount"))))
        for candidate in by_date_amount.get(key, []):
            if candidate["bank_transaction_id"] in used:
                continue
            matched[meta["transaction_id"]] = {**candidate, "match": "date_amount"}
            used.add(candidate["bank_transaction_id"])
            break
    return matched

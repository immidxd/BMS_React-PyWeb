# -*- coding: utf-8 -*-
"""Вивантаження списань Meta з виписки monobank — відновлюване й самообмежене.

Дві обставини визначають усе решта
──────────────────────────────────
1. Personal API віддає **31 день за запит** і приймає **1 запит на 60 секунд**.
   Історія в кілька років — це десятки хвилин очікування НА КОЖЕН рахунок, і
   майже весь цей час програма просто спить. Тому прогін мусить переживати
   переривання: стан пишеться після КОЖНОГО вікна, і наступний запуск
   продовжує з того ж місця, а не починає спочатку.

2. Власник не пам'ятає, коли купив першу рекламу. Питати «з якої дати» —
   означає або копати в порожнечу роками, або втратити давні списання. Тому
   початок історії шукається САМ: ідемо назад, доки не натрапимо на кілька
   вікон поспіль БЕЗ ЖОДНОЇ операції — це означає, що картки тоді ще не було.

⚠️ Порожнє вікно рахуємо за відсутністю ВСІХ операцій, а не лише рекламних.
Місяць без реклами — звичайна річ; місяць без жодної покупки на картці — ознака,
що картки не існувало. Плутати ці два випадки означало б обірвати історію на
першій же паузі в рекламі.

⚠️ Сканувати треба ВСІ рахунки. Перевірено 01.09.2026: 20.08 гроші пішли з
білої ···2438, решта — з чорної ···6650. Один рахунок = втрачені списання.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("bms.mono_ad_sync")

# Скільки вікон поспіль без ЖОДНОЇ операції означають «історія скінчилась».
# Два, а не одне: одне порожнє вікно буває у відпустці або при зміні картки.
EMPTY_WINDOWS_TO_STOP = 2

# Жорстка підлога. Не тому, що раніше нічого не було, а щоб помилка в даті
# ніколи не перетворилась на нескінченний прогін.
HISTORY_FLOOR = date(2018, 1, 1)


def _mono():
    try:
        from services import monobank
    except ImportError:
        from backend.services import monobank
    return monobank


def _state(db: Session, account_id: str) -> dict:
    row = db.execute(text("""
        SELECT account_id, masked_pan, oldest_fetched, newest_fetched,
               empty_streak, exhausted, windows_done, charges_found
        FROM mono_sync_state WHERE account_id = :a
    """), {"a": account_id}).mappings().first()
    return dict(row) if row else {}


def _save_state(db: Session, account_id: str, **fields) -> None:
    """Стан пишеться й КОМІТИТЬСЯ після кожного вікна.

    Без окремого коміту переривання посеред годинного прогону відкотило б усе
    зроблене — і наступний запуск знову ліз би в ті самі вікна, витрачаючи по
    хвилині на кожне.
    """
    fields.setdefault("last_run_at", datetime.now(timezone.utc))
    cols = ", ".join(fields)
    vals = ", ".join(f":{k}" for k in fields)
    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields)
    db.execute(text(f"""
        INSERT INTO mono_sync_state (account_id, {cols})
        VALUES (:account_id, {vals})
        ON CONFLICT (account_id) DO UPDATE SET {updates}, updated_at = now()
    """), {"account_id": account_id, **fields})
    db.commit()


def _store_charge(db: Session, account_id: str, charge: dict, raw: dict) -> bool:
    """Записати списання. Повертає True, якщо рядок новий.

    `ON CONFLICT DO NOTHING` за id операції: повторний прохід тим самим вікном
    (а він неминучий при відновленні) нічого не дублює й нічого не переписує —
    зокрема не збиває `write_status` уже дописаного в аркуш рядка.
    """
    result = db.execute(text("""
        INSERT INTO meta_ad_charges (
            source, bank_account_id, transaction_id, receipt_id,
            charge_date, charged_at, description, mcc,
            amount_uah, operation_amount, operation_currency, raw_json
        ) VALUES (
            'monobank', :acc, :tx, :receipt,
            :cdate, :cat, :descr, :mcc,
            :uah, :op_amount, :op_currency, CAST(:raw AS jsonb)
        )
        ON CONFLICT (source, transaction_id) DO NOTHING
        RETURNING id
    """), {
        "acc": account_id,
        "tx": charge["bank_transaction_id"],
        "receipt": charge.get("receipt_id"),
        "cdate": charge["charge_date"],
        "cat": charge["charged_at"],
        "descr": charge.get("description"),
        "mcc": charge.get("mcc"),
        "uah": str(charge["amount_uah"]),
        "op_amount": str(charge["operation_amount"]) if charge.get("operation_amount") is not None else None,
        "op_currency": charge.get("operation_currency"),
        "raw": json.dumps(raw, ensure_ascii=False),
    }).first()
    return result is not None


def sync_account(db: Session, account: dict, *, max_windows: Optional[int] = None,
                 sleeper: Optional[Callable[[float], None]] = None,
                 progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Пройти історію ОДНОГО рахунку назад, доки не скінчиться.

    `max_windows` обмежує один запуск: годинний прогін зручніше різати на
    частини, а стан дозволяє продовжити з того ж місця.
    """
    mono = _mono()
    sleeper = sleeper or __import__("time").sleep
    account_id = account["id"]
    pan = ", ".join(str(p) for p in account.get("masked_pan") or []) or "—"
    state = _state(db, account_id)

    if state.get("exhausted"):
        return {"account_id": account_id, "masked_pan": pan, "skipped": "історію пройдено"}

    # Продовжуємо від найдавнішого прочитаного; уперше — від сьогодні.
    cursor_date = state.get("oldest_fetched") or (datetime.now(timezone.utc).date() + timedelta(days=1))
    cursor = datetime.combine(cursor_date, dtime.min, tzinfo=timezone.utc)
    empty_streak = int(state.get("empty_streak") or 0)
    windows_done = int(state.get("windows_done") or 0)
    charges_found = int(state.get("charges_found") or 0)
    newest = state.get("newest_fetched") or cursor.date()

    found_now = 0
    windows_now = 0
    stopped = None

    while True:
        if max_windows is not None and windows_now >= max_windows:
            stopped = "ліміт вікон цього запуску"
            break
        start = cursor - timedelta(days=mono.CHUNK_DAYS)
        if start.date() < HISTORY_FLOOR:
            start = datetime.combine(HISTORY_FLOOR, dtime.min, tzinfo=timezone.utc)
        if start >= cursor:
            stopped = "досягнуто підлоги історії"
            break

        if windows_now:
            sleeper(mono.SLEEP_BETWEEN_SEC)
        try:
            items = mono.statement_chunk(account_id, start, cursor)
        except Exception as exc:  # noqa: BLE001 — збій одного вікна не втрачає зробленого
            _save_state(db, account_id, masked_pan=pan, last_error=str(exc)[:500])
            return {"account_id": account_id, "masked_pan": pan, "error": str(exc),
                    "windows": windows_now, "found": found_now}

        windows_now += 1
        windows_done += 1
        charges = mono.meta_charges_from(items)
        by_id = {str(i.get("id")): i for i in items}
        for charge in charges:
            if _store_charge(db, account_id, charge,
                             by_id.get(charge["bank_transaction_id"], {})):
                found_now += 1
                charges_found += 1

        empty_streak = empty_streak + 1 if not items else 0
        cursor = start
        _save_state(db, account_id, masked_pan=pan, oldest_fetched=cursor.date(),
                    newest_fetched=newest, empty_streak=empty_streak,
                    windows_done=windows_done, charges_found=charges_found,
                    last_error=None)
        if progress:
            progress({"account_id": account_id, "masked_pan": pan,
                      "window": (start.date(), (cursor + timedelta(days=mono.CHUNK_DAYS)).date()),
                      "operations": len(items), "meta": len(charges),
                      "empty_streak": empty_streak})

        if empty_streak >= EMPTY_WINDOWS_TO_STOP:
            stopped = f"{empty_streak} вікна поспіль без жодної операції — картки тоді не було"
            _save_state(db, account_id, exhausted=True)
            break
        if cursor.date() <= HISTORY_FLOOR:
            stopped = "досягнуто підлоги історії"
            _save_state(db, account_id, exhausted=True)
            break

    return {"account_id": account_id, "masked_pan": pan, "windows": windows_now,
            "found": found_now, "total_found": charges_found,
            "oldest": cursor.date(), "stopped": stopped}


def sync_all(db: Session, *, max_windows_per_account: Optional[int] = None,
             sleeper: Optional[Callable[[float], None]] = None,
             progress: Optional[Callable[[dict], None]] = None) -> List[dict]:
    """Усі рахунки клієнта. Збій одного не спиняє інші."""
    mono = _mono()
    out = []
    for account in mono.accounts():
        try:
            out.append(sync_account(db, account, max_windows=max_windows_per_account,
                                    sleeper=sleeper, progress=progress))
        except Exception as exc:  # noqa: BLE001
            logger.warning("mono sync %s: %s", account.get("id"), exc)
            out.append({"account_id": account.get("id"), "error": str(exc)})
    return out


# ── Свіжі списання: ВПЕРЕД від останньої перевірки ──────────────────────────
# ⚠️ Діра, через яку Статистика → «Реклама» застигла на серпні 2026: прохід
# вище йде лише НАЗАД (шукає початок історії) і, дійшовши до нього, ставить
# `exhausted=True` — після чого рахунок пропускався НАЗАВЖДИ. Свіжі списання
# не дочитувались ніколи: `newest_fetched` застиг на 02.09.2026 з першого
# прогону. Тепер окремий прохід доганяє від `newest_fetched` до сьогодні, а
# фоновий цикл BMS (app/main.py) кличе його кілька разів на добу.

# Перекриття з уже прочитаним: операція «в холді» може лягти у виписку
# заднім числом. Дублікатів не буде — _store_charge робить ON CONFLICT DO NOTHING.
RECENT_OVERLAP_DAYS = 3
# Нова картка (без стану) за один фоновий прохід іде назад не більше ніж на
# стільки вікон — решту історії дочитають наступні проходи.
NEW_ACCOUNT_WINDOWS_PER_RUN = 2

_RECENT_LOCK = threading.Lock()


def sync_recent(db: Session, *, sleeper: Optional[Callable[[float], None]] = None,
                now: Optional[datetime] = None) -> dict:
    """Дочитати нові операції всіх рахунків — від останньої перевірки до сьогодні.

    Ліміт банку (1 запит на 60 с) спільний на токен, тож пауза стоїть між
    УСІМА запитами прогону, а не лише в межах рахунку. Звичайний прохід —
    по одному вікну на рахунок, тобто кілька хвилин сну й кілька запитів.
    Два прогони одночасно (цикл + кнопка) не йдуть: другий одразу повертається.
    """
    if not _RECENT_LOCK.acquire(blocking=False):
        return {"ok": False, "busy": True, "reason": "перевірка виписки вже йде"}
    try:
        return _sync_recent_locked(db, sleeper=sleeper, now=now)
    finally:
        _RECENT_LOCK.release()


def _sync_recent_locked(db: Session, *, sleeper, now) -> dict:
    mono = _mono()
    sleeper = sleeper or time.sleep
    now = now or datetime.now(timezone.utc)
    requests_made = 0

    def _pause():
        nonlocal requests_made
        if requests_made:
            sleeper(mono.SLEEP_BETWEEN_SEC)
        requests_made += 1

    _pause()   # client-info теж рахується в ліміт
    accounts = mono.accounts()
    results: List[dict] = []
    found_total = 0
    for account in accounts:
        account_id = account["id"]
        pan = ", ".join(str(p) for p in account.get("masked_pan") or []) or "—"
        state = _state(db, account_id)
        try:
            if not state.get("newest_fetched"):
                # Нова картка: спершу її власна історія назад — потроху.
                sleeper(mono.SLEEP_BETWEEN_SEC)
                res = sync_account(db, account, max_windows=NEW_ACCOUNT_WINDOWS_PER_RUN,
                                   sleeper=sleeper)
                requests_made += int(res.get("windows") or 0)
                found_total += int(res.get("found") or 0)
                results.append({**res, "mode": "history"})
                continue

            cursor = datetime.combine(state["newest_fetched"] - timedelta(days=RECENT_OVERLAP_DAYS),
                                      dtime.min, tzinfo=timezone.utc)
            found = windows = 0
            while cursor < now:
                end = min(cursor + timedelta(days=mono.CHUNK_DAYS), now)
                _pause()
                items = mono.statement_chunk(account_id, cursor, end)
                windows += 1
                by_id = {str(i.get("id")): i for i in items}
                for charge in mono.meta_charges_from(items):
                    if _store_charge(db, account_id, charge,
                                     by_id.get(charge["bank_transaction_id"], {})):
                        found += 1
                # Межу пишемо після КОЖНОГО вікна: перерваний прохід продовжить звідси.
                _save_state(db, account_id, masked_pan=pan, newest_fetched=end.date(),
                            last_error=None)
                cursor = end
            found_total += found
            results.append({"account_id": account_id, "masked_pan": pan, "mode": "recent",
                            "windows": windows, "found": found})
        except Exception as exc:  # noqa: BLE001 — збій однієї картки не спиняє інші
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("mono recent sync %s: %s", account_id, exc)
            _save_state(db, account_id, masked_pan=pan, last_error=str(exc)[:500])
            results.append({"account_id": account_id, "masked_pan": pan, "error": str(exc)[:300]})
    return {"ok": not any("error" in r for r in results), "found": found_total,
            "requests": requests_made, "accounts": results}


def sync_status(db: Session) -> dict:
    """Для Статистики: коли востаннє дивились у виписку і чи без помилок.

    `checked_until` — НАЙСТАРІША з меж рахунків, з яких Meta колись списувала:
    саме вона каже, до якого дня дані про рекламу точно повні.
    """
    row = db.execute(text("""
        SELECT MAX(last_run_at) AS last_run_at,
               MIN(newest_fetched) FILTER (WHERE charges_found > 0 OR account_id IN
                   (SELECT DISTINCT bank_account_id FROM meta_ad_charges)) AS checked_until,
               string_agg(DISTINCT last_error, ' · ') FILTER (WHERE last_error IS NOT NULL) AS errors
        FROM mono_sync_state
    """)).mappings().first()
    return dict(row) if row else {}

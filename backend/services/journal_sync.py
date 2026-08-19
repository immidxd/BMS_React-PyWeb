"""Черга записів у журнал (Google Sheets) з повторами.

Чому вона існує
───────────────
Правка з картки товару писалась в аркуш так: роутер стартував daemon-потік,
потік кликав ``writeback_field_to_journal`` і, якщо той кидав виняток, писав
warning у лог. Усе. Сама правка лишалась у БД під локом, аркуш відставав
назавжди, і ніде — ні в картці, ні в списку — не було видно, що він відстав.
Усередині ``writeback_field_to_journal`` ретраї є, але вони прикривають лише
сам ``batch_update``: падіння на етапі ``get_gc()`` (оновлення OAuth-токена,
SSL, обрив мережі) вилітає з функції ДО них. Саме такі провали й лежать у
логах — 40 на 218 успішних записів за три робочі дні.

Тепер поле спершу лягає в ``journal_writeback_queue``, і лише потім воркер
несе його в аркуш. Падіння = не втрата, а ``attempts+1`` і наступна спроба з
відступом; черга переживає перезапуск (на старті воркер добирає ``pending``).

Що НЕ ретраїться
────────────────
Є причини, які повтором не лікуються: у журналі нема такої колонки, товар не
прив'язаний до завозу, per-item поле на ростовці з кількох рядків (писати в
усі рядки — затерти сусідні розміри). Такі задачі одразу стають ``skipped`` —
вони видимі, але не крутяться в циклі до посиніння.
"""

from typing import Optional, Dict, Any, List, Iterable
from datetime import datetime, timedelta
import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Відступи між спробами. Шоста невдача → status='failed' (ручний повтор).
BACKOFF_SECONDS = [30, 120, 600, 1800, 7200, 21600]
MAX_ATTEMPTS = len(BACKOFF_SECONDS)

# Фрази з reason, які означають «повторювати марно» (див. sheets_parser).
_PERMANENT_MARKERS = (
    "no journal column",
    "no sheet_title",
    "writeback disabled",
    "not found",          # worksheet '<tab>' not found
    "missing in sheet",   # column 'Номер' or '<X>' missing
    "empty sheet",
    "per-item field",     # ростовка з кількох рядків — свідомий пропуск
)

_worker_lock = threading.Lock()
_worker_running = False


def _sheets_parser():
    try:
        from backend.scripts import sheets_parser as sp
    except ImportError:
        from scripts import sheets_parser as sp
    return sp


def _session_factory():
    try:
        from backend.models.database import SessionLocal
    except ImportError:
        from models.database import SessionLocal
    return SessionLocal


# ── Постановка в чергу ──────────────────────────────────────────────────────
def enqueue(db: Session, product_id: Optional[int], productnumber: str,
            sheet_title: Optional[str], field: str, value: Any) -> None:
    """Поставити (або оновити) задачу на запис одного поля.

    Повторна правка того самого поля ЗАМІНЮЄ значення відкритої задачі — інакше
    в аркуш поїхало б спершу проміжне значення, а потім кінцеве.
    """
    val = None if value is None else str(value)
    db.execute(text("""
        INSERT INTO journal_writeback_queue
              (product_id, productnumber, sheet_title, field, value,
               status, attempts, next_attempt_at, created_at, updated_at)
        VALUES (:pid, :pnum, :sheet, :field, :val,
                'pending', 0, now(), now(), now())
        ON CONFLICT (product_id, field) WHERE status IN ('pending', 'failed')
        DO UPDATE SET value = EXCLUDED.value,
                      sheet_title = EXCLUDED.sheet_title,
                      productnumber = EXCLUDED.productnumber,
                      status = 'pending',
                      attempts = 0,
                      last_error = NULL,
                      next_attempt_at = now(),
                      updated_at = now()
    """), {"pid": product_id, "pnum": productnumber, "sheet": sheet_title,
           "field": field, "val": val})


def enqueue_many(db: Session, product_id: Optional[int], productnumber: str,
                 sheet_title: Optional[str], field_values: Dict[str, Any]) -> int:
    for f, v in field_values.items():
        enqueue(db, product_id, productnumber, sheet_title, f, v)
    return len(field_values)


# ── Воркер ──────────────────────────────────────────────────────────────────
def _classify(reason: str) -> str:
    low = (reason or "").lower()
    return "skipped" if any(m in low for m in _PERMANENT_MARKERS) else "retry"


def _process_one(db: Session, row) -> str:
    sp = _sheets_parser()
    try:
        res = sp.writeback_field_to_journal(row.sheet_title, row.productnumber,
                                            row.field, row.value)
    except Exception as e:  # мережа/токен/SSL — саме те, що раніше губилось
        res = {"ok": False, "reason": f"exception: {e}"}

    if res.get("ok"):
        db.execute(text("""UPDATE journal_writeback_queue
                           SET status='done', done_at=now(), updated_at=now(), last_error=NULL
                           WHERE id=:id"""), {"id": row.id})
        return "done"

    reason = str(res.get("reason") or "unknown")
    if _classify(reason) == "skipped":
        db.execute(text("""UPDATE journal_writeback_queue
                           SET status='skipped', last_error=:err, updated_at=now()
                           WHERE id=:id"""), {"id": row.id, "err": reason})
        return "skipped"

    attempts = int(row.attempts or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        db.execute(text("""UPDATE journal_writeback_queue
                           SET status='failed', attempts=:a, last_error=:err, updated_at=now()
                           WHERE id=:id"""), {"id": row.id, "a": attempts, "err": reason})
        return "failed"
    delay = BACKOFF_SECONDS[attempts - 1]
    db.execute(text("""UPDATE journal_writeback_queue
                       SET attempts=:a, last_error=:err, updated_at=now(),
                           next_attempt_at = now() + (:delay || ' seconds')::interval
                       WHERE id=:id"""),
               {"id": row.id, "a": attempts, "err": reason, "delay": str(delay)})
    return "retry"


def drain(max_items: int = 200) -> Dict[str, int]:
    """Пронести через аркуш усі задачі, яким настав час. Повертає лічильники."""
    SessionLocal = _session_factory()
    db = SessionLocal()
    counts = {"done": 0, "skipped": 0, "failed": 0, "retry": 0}
    try:
        for _ in range(max_items):
            row = db.execute(text("""
                SELECT id, product_id, productnumber, sheet_title, field, value, attempts
                FROM journal_writeback_queue
                WHERE status='pending' AND next_attempt_at <= now()
                ORDER BY created_at
                LIMIT 1
            """)).fetchone()
            if row is None:
                break
            outcome = _process_one(db, row)
            db.commit()
            counts[outcome] = counts.get(outcome, 0) + 1
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.error("[journal-sync] drain перервано: %s", e)
    finally:
        db.close()
    if any(counts.values()):
        logger.info("[journal-sync] %s", counts)
    return counts


def kick() -> None:
    """Розбудити воркера (не блокує викликача; другий потік не плодиться)."""
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True

    def _run():
        global _worker_running
        try:
            drain()
        finally:
            with _worker_lock:
                _worker_running = False

    threading.Thread(target=_run, daemon=True, name="journal-sync").start()


# ── Стан для UI/діагностики ─────────────────────────────────────────────────
def status_counts(db: Session) -> Dict[str, int]:
    rows = db.execute(text("""
        SELECT status, count(*) AS c FROM journal_writeback_queue GROUP BY status
    """)).fetchall()
    return {r.status: int(r.c) for r in rows}


def pending_by_product(db: Session, product_ids: Iterable[int]) -> Dict[int, int]:
    """{product_id: скільки полів ще не лягло в журнал} — для значка в картці."""
    ids = [int(i) for i in product_ids if i]
    if not ids:
        return {}
    rows = db.execute(text("""
        SELECT product_id, count(*) AS c
        FROM journal_writeback_queue
        WHERE status IN ('pending', 'failed') AND product_id = ANY(:ids)
        GROUP BY product_id
    """), {"ids": ids}).fetchall()
    return {int(r.product_id): int(r.c) for r in rows}


def retry_failed(db: Session, include_skipped: bool = False) -> int:
    """Повернути 'failed' (за бажанням і 'skipped') у роботу негайно."""
    statuses = ["failed", "skipped"] if include_skipped else ["failed"]
    res = db.execute(text("""
        UPDATE journal_writeback_queue
        SET status='pending', attempts=0, next_attempt_at=now(), updated_at=now()
        WHERE status = ANY(:st)
    """), {"st": statuses})
    db.commit()
    return res.rowcount or 0

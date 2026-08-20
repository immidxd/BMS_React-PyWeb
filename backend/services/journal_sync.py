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

from typing import Optional, Dict, Any, List, Iterable, Tuple
from datetime import datetime, timedelta
import logging
import threading
from types import SimpleNamespace

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
PROCESSING_TIMEOUT_MINUTES = 5
_incoming_lock = threading.Lock()
_incoming_state: Dict[str, Any] = {"state": "idle", "detail": None, "updated_at": None}


def set_incoming_activity(state: str, detail: Optional[str] = None) -> None:
    """Процесний стан очікування sheet→BMS між modifiedTime-check і parser job."""
    with _incoming_lock:
        _incoming_state.update({"state": state, "detail": detail,
                                "updated_at": datetime.utcnow().isoformat()})


def incoming_activity() -> Dict[str, Any]:
    with _incoming_lock:
        return dict(_incoming_state)


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
                           WHERE id=:id AND status=:expected"""),
                   {"id": row.id, "expected": getattr(row, "status", "pending")})
        return "done"

    # Поки мережевий запит був у польоті, користувач міг ще раз змінити це саме
    # поле. Нова ``pending``-версія вже містить остаточне значення; стару невдалу
    # спробу не можна повертати в pending (це і конфлікт partial unique index, і
    # ризик пізніше записати застаріле значення). Вважаємо її коректно заміщеною.
    newer = db.execute(text("""
        SELECT 1 FROM journal_writeback_queue
        WHERE product_id=:pid AND field=:field AND id<>:id
          AND status IN ('pending', 'processing', 'failed')
        LIMIT 1
    """), {"pid": row.product_id, "field": row.field, "id": row.id}).first()
    if newer:
        db.execute(text("""UPDATE journal_writeback_queue
                           SET status='done', done_at=now(), updated_at=now(),
                               last_error='superseded by newer edit'
                           WHERE id=:id AND status=:expected"""),
                   {"id": row.id,
                    "expected": getattr(row, "status", "pending")})
        return "superseded"

    reason = str(res.get("reason") or "unknown")
    if _classify(reason) == "skipped":
        db.execute(text("""UPDATE journal_writeback_queue
                           SET status='skipped', last_error=:err, updated_at=now()
                           WHERE id=:id AND status=:expected"""),
                   {"id": row.id, "err": reason,
                    "expected": getattr(row, "status", "pending")})
        return "skipped"

    attempts = int(row.attempts or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        db.execute(text("""UPDATE journal_writeback_queue
                           SET status='failed', attempts=:a, last_error=:err, updated_at=now()
                           WHERE id=:id AND status=:expected"""),
                   {"id": row.id, "a": attempts, "err": reason,
                    "expected": getattr(row, "status", "pending")})
        return "failed"
    delay = BACKOFF_SECONDS[attempts - 1]
    db.execute(text("""UPDATE journal_writeback_queue
                       SET status='pending', attempts=:a, last_error=:err, updated_at=now(),
                           next_attempt_at = now() + (:delay || ' seconds')::interval
                       WHERE id=:id AND status=:expected"""),
               {"id": row.id, "a": attempts, "err": reason, "delay": str(delay),
                "expected": getattr(row, "status", "pending")})
    return "retry"


def _recover_stale_processing(db: Session) -> int:
    """Повернути задачі, покинуті процесом під час мережевого виклику.

    ``processing`` відокремлює вже захоплену версію поля від нової правки того
    самого поля. Якщо процес аварійно завершився, задача не має лишитися в цьому
    стані назавжди.
    """
    res = db.execute(text(f"""
        UPDATE journal_writeback_queue
        SET status='pending', next_attempt_at=now(), updated_at=now(),
            last_error=COALESCE(last_error || '; ', '') || 'recovered stale processing'
        WHERE status='processing'
          AND updated_at < now() - interval '{PROCESSING_TIMEOUT_MINUTES} minutes'
    """))
    db.commit()
    return res.rowcount or 0


def _claim_one(db: Session):
    """Атомарно забрати одну готову задачу, не блокуючи інший воркер.

    Після переходу в ``processing`` нова правка цього самого поля створить нову
    ``pending``-задачу, а не підмінить значення, яке вже летить у Google. Так
    завершення старого HTTP-запиту не може позначити новішу правку виконаною.
    """
    row = db.execute(text("""
        WITH candidate AS (
            SELECT id
            FROM journal_writeback_queue
            WHERE status='pending' AND next_attempt_at <= now()
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE journal_writeback_queue q
        SET status='processing', updated_at=now()
        FROM candidate c
        WHERE q.id=c.id
        RETURNING q.id, q.product_id, q.productnumber, q.sheet_title,
                  q.field, q.value, q.attempts, q.status
    """)).fetchone()
    db.commit()
    return row


def _resolve_current_target(db: Session, row) -> Tuple[Optional[Any], Optional[str]]:
    """Перед відправленням звірити номер і вкладку з поточним станом БД.

    ``sheet_title`` у задачі є діагностичним знімком. Джерело правди для цілі —
    актуальні ``products.deliveryid`` та ``deliveries.deliveryname``. Це закриває
    випадок, коли товар переїхав між вкладками вже після редагування картки.
    """
    if not row.product_id:
        return row, None
    target = db.execute(text("""
        SELECT p.productnumber, d.deliveryname
        FROM products p
        LEFT JOIN deliveries d ON d.id=p.deliveryid
        WHERE p.id=:pid
    """), {"pid": int(row.product_id)}).fetchone()
    if not target:
        reason = "product not found while resolving current journal target"
        db.execute(text("""
            UPDATE journal_writeback_queue
            SET status='skipped', last_error=:err, updated_at=now()
            WHERE id=:id AND status='processing'
        """), {"id": row.id, "err": reason})
        db.commit()
        return None, "skipped"
    current_pnum, current_sheet = target[0], target[1]
    if not current_sheet:
        reason = "no sheet_title (product has no delivery)"
        db.execute(text("""
            UPDATE journal_writeback_queue
            SET status='skipped', last_error=:err, updated_at=now()
            WHERE id=:id AND status='processing'
        """), {"id": row.id, "err": reason})
        db.commit()
        return None, "skipped"

    data = dict(row._mapping) if hasattr(row, "_mapping") else dict(vars(row))
    data["productnumber"] = current_pnum
    data["sheet_title"] = current_sheet
    data["status"] = "processing"
    if current_pnum != row.productnumber or current_sheet != row.sheet_title:
        db.execute(text("""
            UPDATE journal_writeback_queue
            SET productnumber=:pnum, sheet_title=:sheet, updated_at=now()
            WHERE id=:id AND status='processing'
        """), {"id": row.id, "pnum": current_pnum, "sheet": current_sheet})
        db.commit()
        logger.info("[journal-sync] retarget #%s: %s/%s → %s/%s",
                    row.id, row.sheet_title, row.productnumber,
                    current_sheet, current_pnum)
    return SimpleNamespace(**data), None


def drain(max_items: int = 200) -> Dict[str, int]:
    """Пронести через аркуш усі задачі, яким настав час. Повертає лічильники."""
    SessionLocal = _session_factory()
    db = SessionLocal()
    counts = {"done": 0, "superseded": 0, "skipped": 0, "failed": 0, "retry": 0}
    try:
        recovered = _recover_stale_processing(db)
        if recovered:
            logger.warning("[journal-sync] recovered %d stale processing task(s)", recovered)
        for _ in range(max_items):
            row = _claim_one(db)
            if row is None:
                break
            try:
                row, pre_outcome = _resolve_current_target(db, row)
            except Exception as e:  # ціль не прочиталась — не лишаємо processing на 5 хв
                claimed_id = row.id
                db.rollback()
                db.execute(text("""
                    UPDATE journal_writeback_queue
                    SET status='pending', next_attempt_at=now() + interval '30 seconds',
                        updated_at=now(), last_error=:err
                    WHERE id=:id AND status='processing'
                """), {"id": claimed_id, "err": f"target resolution failed: {e}"})
                db.commit()
                counts["retry"] += 1
                continue
            if pre_outcome:
                counts[pre_outcome] = counts.get(pre_outcome, 0) + 1
                continue
            if row is None:
                continue
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


def global_activity(db: Session) -> Dict[str, Any]:
    """Єдиний нетехнічний стан синхронізації для глобального індикатора."""
    parse_job = db.execute(text("""
        SELECT id, status, current_step, percent, updated_at
        FROM parsing_jobs
        WHERE status IN ('queued', 'running')
          AND mode LIKE 'sheets_%'
          AND COALESCE(last_heartbeat_at, updated_at, started_at)
                > now() - interval '10 minutes'
        ORDER BY id DESC LIMIT 1
    """)).mappings().first()
    q = db.execute(text(f"""
        SELECT
          count(*) FILTER (WHERE status IN ('pending','processing')) AS active,
          count(*) FILTER (WHERE status IN ('pending','processing') AND attempts > 0) AS retrying,
          count(*) FILTER (WHERE status IN ('pending','processing')
                            AND updated_at < now() - interval '{STALE_PENDING_MINUTES} minutes') AS stale,
          count(*) FILTER (WHERE status='failed') AS failed,
          count(*) FILTER (WHERE status='skipped'
                            AND COALESCE(last_error,'') NOT ILIKE 'per-item field%') AS blocked
        FROM journal_writeback_queue
    """)).mappings().first()
    counts = {k: int((q or {}).get(k) or 0)
              for k in ("active", "retrying", "stale", "failed", "blocked")}
    incoming = incoming_activity()

    if parse_job:
        state = "syncing"
        detail = parse_job.get("current_step") or "Оновлюємо дані з журналу"
    elif incoming.get("state") in ("waiting", "error"):
        state = "delayed" if incoming["state"] == "waiting" else "error"
        detail = incoming.get("detail")
    elif counts["failed"] + counts["blocked"]:
        state = "error"
        detail = "Є зміни BMS, які не вдалося передати в журнал"
    elif counts["retrying"] + counts["stale"]:
        state = "delayed"
        detail = "Передавання змін у журнал затрималось — BMS повторює автоматично"
    elif counts["active"]:
        state = "syncing"
        detail = "Передаємо останні зміни BMS у журнал"
    else:
        state, detail = "idle", None
    return {"state": state, "detail": detail, "incoming": incoming,
            "parse_job": dict(parse_job) if parse_job else None,
            "outgoing": counts}


# Скільки чекати, поки «щойно поставлено в чергу» стане «застрягло».
# Воркер драйнить чергу раз на хвилину, тож нормальний запис живе в черзі
# секунди. Все, що висить довше, — це вже не «в дорозі», а проблема.
STALE_PENDING_MINUTES = 5


def empty_sync_state() -> Dict[str, Any]:
    return {
        "pending": 0, "processing": 0, "active": 0, "retrying": 0,
        "stale": 0, "failed": 0, "blocked": 0, "ignored": 0, "skipped": 0,
        "unsynced": 0, "stuck": False, "state": "idle",
    }


def sync_state_by_product(db: Session, product_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """Стан запису в журнал по товарах — для значка в картці.

    Значок живе рівно стільки, скільки реальна синхронізація: свіжа задача
    показується як ``syncing`` і автоматично зникає після підтвердженого запису.
    Повтор/застаріла задача стає ``delayed``, остаточний збій — ``error``.

      pending  — очікує запису;
      processing — уже передається в Google;
      stale    — висить довше за STALE_PENDING_MINUTES (черга не рухається);
      failed   — спроби вичерпані;
      skipped  — писати нікуди (нема колонки/завозу, per-item поле ростовки).

    ``stuck`` = чи є що показувати людині; ``unsynced`` = скільки саме полів.
    """
    ids = [int(i) for i in product_ids if i]
    if not ids:
        return {}
    rows = db.execute(text(f"""
        SELECT product_id,
               count(*) FILTER (WHERE status = 'pending') AS pending,
               count(*) FILTER (WHERE status = 'processing') AS processing,
               count(*) FILTER (WHERE status IN ('pending', 'processing')
                                  AND attempts > 0) AS retrying,
               count(*) FILTER (WHERE status IN ('pending', 'processing')
                                  AND updated_at < now() - interval '{STALE_PENDING_MINUTES} minutes') AS stale,
               count(*) FILTER (WHERE status = 'failed')  AS failed,
               count(*) FILTER (WHERE status = 'skipped'
                                  AND COALESCE(last_error, '') NOT ILIKE 'per-item field%') AS blocked,
               count(*) FILTER (WHERE status = 'skipped'
                                  AND COALESCE(last_error, '') ILIKE 'per-item field%') AS ignored
        FROM journal_writeback_queue
        WHERE status IN ('pending', 'processing', 'failed', 'skipped')
          AND product_id = ANY(:ids)
        GROUP BY product_id
    """), {"ids": ids}).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        active = int(r.pending) + int(r.processing)
        unsynced = active + int(r.failed) + int(r.blocked)
        if int(r.failed) + int(r.blocked) > 0:
            state = "error"
        elif int(r.retrying) > 0 or int(r.stale) > 0:
            state = "delayed"
        elif active > 0:
            state = "syncing"
        else:
            state = "idle"
        out[int(r.product_id)] = {
            "pending": int(r.pending),
            "processing": int(r.processing),
            "active": active,
            "retrying": int(r.retrying),
            "stale": int(r.stale),
            "failed": int(r.failed),
            "blocked": int(r.blocked),
            "ignored": int(r.ignored),
            "skipped": int(r.blocked) + int(r.ignored),
            "unsynced": unsynced,
            "stuck": state in ("delayed", "error"),
            "state": state,
        }
    return out


def sync_items_by_product(db: Session, product_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT id, field, status, attempts, last_error, sheet_title,
               next_attempt_at, created_at, updated_at
        FROM journal_writeback_queue
        WHERE product_id=:pid
          AND status IN ('pending', 'processing', 'failed', 'skipped')
        ORDER BY created_at, id
    """), {"pid": int(product_id)}).mappings().all()
    return [dict(r) for r in rows]


def pending_by_product(db: Session, product_ids: Iterable[int]) -> Dict[int, int]:
    """Сумісний вигляд: {product_id: скільки полів реально застрягло}."""
    return {pid: st["unsynced"] for pid, st in sync_state_by_product(db, product_ids).items()}


def retry_failed(db: Session, include_skipped: bool = False,
                 product_id: Optional[int] = None) -> int:
    """Повернути 'failed' (за бажанням і 'skipped') у роботу негайно.

    ``product_id`` звужує повтор до однієї картки — саме це робить значок
    «не в журналі»: людина бачить проблему там, де вона виникла, і лагодить
    її звідти ж, не шукаючи окремий екран.
    """
    statuses = ["failed", "skipped"] if include_skipped else ["failed"]
    where = "status = ANY(:st)"
    params: Dict[str, Any] = {"st": statuses}
    if product_id is not None:
        where += " AND product_id = :pid"
        params["pid"] = int(product_id)
    if include_skipped:
        where += " AND (status <> 'skipped' OR COALESCE(last_error, '') NOT ILIKE 'per-item field%')"
    sql = f"""
        WITH candidates AS (
          SELECT DISTINCT ON (product_id, field) id
          FROM journal_writeback_queue candidate
          WHERE {where}
            AND NOT EXISTS (
            SELECT 1 FROM journal_writeback_queue newer
            WHERE newer.product_id=candidate.product_id
              AND newer.field=candidate.field
              AND newer.id<>candidate.id
              AND newer.status IN ('pending', 'processing')
            )
          ORDER BY product_id, field, id DESC
        )
        UPDATE journal_writeback_queue target
        SET status='pending', attempts=0, next_attempt_at=now(), updated_at=now()
        FROM candidates c
        WHERE target.id=c.id
    """
    res = db.execute(text(sql), params)
    db.commit()
    return res.rowcount or 0

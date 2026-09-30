"""Пакетне розпізнавання: один завіз (або виділені товари) — одна задача.

ЧОМУ ФОНОВА ЗАДАЧА, А НЕ ПРОСТО ЦИКЛ У ЗАПИТІ. Один товар — це до 90 секунд
(стеля `photo_autofill.REQUEST_TIMEOUT_S`), тож завіз із двадцяти позицій
живе пів години. HTTP-запит стільки не витримає ані на боці вебв'ю, ані на
боці сервера, а людина не має сидіти з відкритим вікном: закрила картку —
пакет доробляється сам, як і решта довгих операцій BMS. Стан видно в
спільному Task Center, а не в окремій системі сповіщень.

ОДНА ЗАДАЧА ЗА РАЗ. Квота Google добова й СПІЛЬНА на проєкт: два пакети
паралельно не подвоять швидкість, а лише вдвічі швидше з'їдять денну межу й
почнуть ловити хвилинні відмови один одного.

КВОТА — ЦЕ ОЧІКУВАННЯ, А НЕ ПОМИЛКА (та сама домовленість, що й у воркерів
публікацій). Хвилинну межу пакет ПЕРЕЧІКУЄ, не витрачаючи спроб; добову —
чесно називає й зупиняється, лишаючи нерозпізнане на завтра, замість того
щоб двадцять разів отримати одну й ту саму відмову.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Пауза між товарами. Хвилинна квота Google спільна на проєкт, і черга без
# пауз впирається в неї вже на третьому товарі.
PAUSE_BETWEEN_S = 2.0
# Скільки максимум перечікуємо хвилинну відмову за один раз. Більше — це вже
# не «зачекати», а мовчазне зависання.
MAX_WAIT_S = 150

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_ORDER: List[str] = []          # id у порядку створення, для прибирання старих
_MAX_KEPT = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    """Знімок задачі для інтерфейсу — без внутрішніх полів (події скасування)."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def active() -> Optional[Dict[str, Any]]:
    with _LOCK:
        for jid in reversed(_ORDER):
            job = _JOBS.get(jid)
            if job and job["state"] in ("running", "waiting"):
                return _public(job)
    return None


def get(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return _public(job) if job else None


def cancel(job_id: str) -> bool:
    """Попросити задачу спинитись. Поточний товар дороблюється до кінця:
    обірвати виклик моделі посеред роботи означало б заплатити й викинути."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["state"] not in ("running", "waiting"):
            return False
        job["_cancel"].set()
        job["cancel_requested"] = True
        return True


def start(product_ids: List[int], label: str, *, use_paid: bool = False) -> Dict[str, Any]:
    """Поставити пакет у роботу. Кидає RuntimeError, якщо інший ще йде."""
    ids = [int(p) for p in dict.fromkeys(product_ids)]   # без дублів, порядок збережено
    if not ids:
        raise ValueError("немає товарів для розпізнавання")
    with _LOCK:
        for jid in _ORDER:
            j = _JOBS.get(jid)
            if j and j["state"] in ("running", "waiting"):
                raise RuntimeError(
                    f"Уже йде розпізнавання: {j['label']} ({j['done']} з {j['total']}). "
                    "Дочекайтесь або скасуйте його.")
        job_id = f"ab_{uuid.uuid4().hex[:10]}"
        job: Dict[str, Any] = {
            "id": job_id, "label": label, "state": "running",
            "total": len(ids), "done": 0, "proposed_products": 0, "proposed_fields": 0,
            "nothing": 0, "errors": 0, "skipped": 0,
            "current": None, "started_at": _now(), "finished_at": None,
            "stop_reason": None, "cancel_requested": False,
            "results": [], "use_paid": bool(use_paid),
            "_cancel": threading.Event(), "_ids": ids,
        }
        _JOBS[job_id] = job
        _ORDER.append(job_id)
        while len(_ORDER) > _MAX_KEPT:
            _JOBS.pop(_ORDER.pop(0), None)
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return _public(job)


def _set(job: Dict[str, Any], **kw: Any) -> None:
    with _LOCK:
        job.update(kw)


def _run(job_id: str) -> None:
    try:
        from models.database import SessionLocal
        from services import ai_quota, autofill_run, photo_autofill
    except ImportError:  # pragma: no cover
        from backend.models.database import SessionLocal
        from backend.services import ai_quota, autofill_run, photo_autofill

    job = _JOBS.get(job_id)
    if not job:
        return
    cancel_ev: threading.Event = job["_cancel"]
    ids: List[int] = job["_ids"]
    db = SessionLocal()
    try:
        for idx, pid in enumerate(ids):
            if cancel_ev.is_set():
                _set(job, state="cancelled", stop_reason="Скасовано вручну",
                     skipped=job["skipped"] + (len(ids) - idx))
                break

            number = _number_of(db, pid)
            _set(job, current=number or f"#{pid}")

            # ── Квота ПЕРЕД викликом, а не після відмови ────────────────────
            try:
                st = ai_quota.status(db, paid_available=photo_autofill.paid_key_available())
            except Exception as e:  # noqa: BLE001 — облік не має валити пакет
                logger.warning("[autofill-batch] не вдалось прочитати квоту: %s", e)
                st = {}
            free = (st.get("free") or {}) if isinstance(st, dict) else {}
            month = (st.get("month") or {}) if isinstance(st, dict) else {}
            if free.get("exhausted") and not job["use_paid"]:
                _set(job, state="done", stop_reason=(
                    "Добова квота Google вичерпана — решта лишилась нерозпізнаною. "
                    "Спробуйте після скидання квоти."),
                    skipped=job["skipped"] + (len(ids) - idx))
                break
            if month.get("allowed") is False:
                _set(job, state="done",
                     stop_reason=f"Місячна стеля витрат: {month.get('reason') or 'вичерпано'}",
                     skipped=job["skipped"] + (len(ids) - idx))
                break
            wait_s = int(free.get("retry_after_s") or 0)
            if wait_s > 0:
                # Хвилинна межа спільна на проєкт — її саме ПЕРЕЧІКУЮТЬ.
                _set(job, state="waiting",
                     stop_reason=f"Хвилинна межа Google — чекаємо {min(wait_s, MAX_WAIT_S)} с")
                _sleep_interruptible(min(wait_s + 2, MAX_WAIT_S), cancel_ev)
                _set(job, state="running", stop_reason=None)
                if cancel_ev.is_set():
                    continue

            res = autofill_run.run_one(db, pid, use_paid=job["use_paid"])
            _record(job, pid, number, res)

            if idx < len(ids) - 1:
                _sleep_interruptible(PAUSE_BETWEEN_S, cancel_ev)
        else:
            _set(job, state="done")
    except Exception as e:  # noqa: BLE001 — задача не має падати мовчки
        logger.exception("[autofill-batch] job %s failed", job_id)
        _set(job, state="error", stop_reason=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
        _set(job, current=None, finished_at=_now())
        if job.get("state") in ("running", "waiting"):
            _set(job, state="done")


def _record(job: Dict[str, Any], pid: int, number: Optional[str], res: Dict[str, Any]) -> None:
    fields = len(res.get("proposed") or [])
    row = {"product_id": pid, "number": number, "fields": fields,
           "ok": bool(res.get("ok")), "reason": res.get("reason")}
    with _LOCK:
        job["done"] += 1
        job["results"].append(row)
        if fields:
            job["proposed_products"] += 1
            job["proposed_fields"] += fields
        elif res.get("failed"):
            job["errors"] += 1
        elif not res.get("ok"):
            # «Немає знімків і артикула», «стеля бюджету» — це не помилка
            # програми, а чесно порожній результат.
            job["nothing"] += 1
        else:
            job["nothing"] += 1


def _sleep_interruptible(seconds: float, ev: threading.Event) -> None:
    """Пауза, яку скасування перериває одразу, а не через хвилину."""
    ev.wait(timeout=max(0.0, seconds))


def _number_of(db: Any, pid: int) -> Optional[str]:
    from sqlalchemy import text
    try:
        row = db.execute(text("SELECT productnumber FROM products WHERE id = :i"),
                         {"i": pid}).fetchone()
        db.commit()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None

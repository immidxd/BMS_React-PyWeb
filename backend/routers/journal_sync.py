"""Стан синхронізації з журналом: черга, повтор, звірка.

Черга (``services.journal_sync``) страхує майбутні правки; звірка
(``services.journal_reconcile``) знаходить борг, що накопичився, поки запис
працював «вистрелив і забув».
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

try:
    from models.database import get_db
    from services import journal_sync, journal_reconcile
except ImportError:
    from backend.models.database import get_db
    from backend.services import journal_sync, journal_reconcile

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/journal-sync/status")
def get_status(db: Session = Depends(get_db)):
    """Скільки полів чекає запису, скільки провалилось і що саме."""
    counts = journal_sync.status_counts(db)
    rows = db.execute(text("""
        SELECT id, product_id, productnumber, sheet_title, field, status,
               attempts, last_error, next_attempt_at
        FROM journal_writeback_queue
        WHERE status IN ('pending', 'failed', 'skipped')
        ORDER BY (status = 'failed') DESC, updated_at DESC
        LIMIT 100
    """)).fetchall()
    return {
        "counts": counts,
        "items": [dict(r._mapping) for r in rows],
    }


@router.post("/api/journal-sync/retry")
def retry(include_skipped: bool = Query(False, description="Повторити й ті, що пропущені як безнадійні"),
          db: Session = Depends(get_db)):
    """Повернути провалені задачі в роботу негайно."""
    n = journal_sync.retry_failed(db, include_skipped=include_skipped)
    journal_sync.kick()
    return {"requeued": n}


@router.post("/api/journal-sync/reconcile")
def reconcile(apply: bool = Query(False, description="false = лише звіт, нічого не пишемо"),
              sheets: Optional[List[str]] = Query(None, description="Обмежити переліком вкладок"),
              max_sheets: Optional[int] = Query(None, ge=1),
              db: Session = Depends(get_db)):
    """Звірити картки з аркушем по залочених полях.

    За замовчуванням — сухий прогін: показує, що розійшлося, і НЕ пише.
    """
    try:
        return journal_reconcile.reconcile(db, apply=apply, sheet_titles=sheets,
                                           max_sheets=max_sheets)
    except Exception as e:  # noqa: BLE001
        logger.error(f"reconcile failed: {e}")
        raise HTTPException(status_code=502, detail=f"Звірка не вдалася: {e}")

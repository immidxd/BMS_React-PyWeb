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
        WHERE status IN ('pending', 'processing', 'failed', 'skipped')
        ORDER BY (status = 'failed') DESC, updated_at DESC
        LIMIT 100
    """)).fetchall()
    return {
        "counts": counts,
        "items": [dict(r._mapping) for r in rows],
    }


@router.get("/api/journal-sync/activity")
def get_activity(db: Session = Depends(get_db)):
    """Короткий app-wide стан для тимчасового індикатора затримки/роботи."""
    return journal_sync.global_activity(db)


@router.get("/api/journal-sync/product/{product_id}")
def get_product_status(product_id: int, db: Session = Depends(get_db)):
    """Легкий живий стан двох-трьох задач картки без повторного читання товару."""
    exists = db.execute(text("SELECT 1 FROM products WHERE id=:pid"),
                        {"pid": int(product_id)}).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    state = journal_sync.sync_state_by_product(db, [product_id]).get(product_id)
    out = state or journal_sync.empty_sync_state()
    out["items"] = journal_sync.sync_items_by_product(db, product_id)
    return out


@router.post("/api/journal-sync/retry")
def retry(include_skipped: bool = Query(False, description="Повторити й ті, що пропущені як безнадійні"),
          product_id: Optional[int] = Query(None, description="Лише задачі цієї картки"),
          db: Session = Depends(get_db)):
    """Повернути провалені задачі в роботу негайно."""
    n = journal_sync.retry_failed(db, include_skipped=include_skipped,
                                  product_id=product_id)
    journal_sync.kick()
    return {"requeued": n}


@router.post("/api/journal-sync/reconcile")
def reconcile(apply: bool = Query(False, description="false = лише звіт, нічого не пишемо"),
              sheets: Optional[List[str]] = Query(None, description="Обмежити переліком вкладок"),
              max_sheets: Optional[int] = Query(None, ge=1),
              mode: str = Query("locked", regex="^(locked|fill_empty)$",
                                description="locked = синхронізувати правки з картки; "
                                            "fill_empty = заповнити ПОРОЖНІ клітинки з картки"),
              numbers: Optional[List[str]] = Query(None, description="Лише ці номери товарів"),
              db: Session = Depends(get_db)):
    """Звірити картки з аркушем.

    За замовчуванням — сухий прогін по залочених полях: показує, що
    розійшлося, і НЕ пише.
    """
    try:
        return journal_reconcile.reconcile(db, apply=apply, sheet_titles=sheets,
                                           max_sheets=max_sheets, mode=mode,
                                           numbers=numbers)
    except Exception as e:  # noqa: BLE001
        logger.error(f"reconcile failed: {e}")
        raise HTTPException(status_code=502, detail=f"Звірка не вдалася: {e}")

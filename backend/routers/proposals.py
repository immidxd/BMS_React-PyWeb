"""Пропозиції автозаповнення: показати, прийняти, відхилити, запустити.

⚠️ ROUTERS ARE SYNC (`def`, не `async def`) — правило проєкту: усередині
блокуючі виклики БД, і async-обгортка лише зайняла б event loop.

КЛЮЧОВЕ МІСЦЕ ВСЬОГО ЗАДУМУ — `accept`. Він НЕ пише в products сам: бере
payload від сховища пропозицій і проводить його через звичайний
`update_product`. Той самий код, яким працює ручне введення, з локом, чергою
write-back і пропагацією на ростовку. Тому нового шляху в картку не існує, і
модель фізично не може щось записати повз людину.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

try:
    from models.database import get_db
    from schemas import product as schemas
    from services import field_proposals, photo_autofill, product_service, ai_budget
    from services.photo_manager import resolve_category, _kind_files
except ImportError:  # pragma: no cover
    from backend.models.database import get_db
    from backend.schemas import product as schemas
    from backend.services import field_proposals, photo_autofill, product_service, ai_budget
    from backend.services.photo_manager import resolve_category, _kind_files

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/products/{product_id}/proposals", response_model=List[Dict[str, Any]])
def list_proposals(product_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """Невирішені пропозиції товару — те, що картка показує чіпами."""
    return field_proposals.open_for_product(db, product_id)


@router.post("/api/products/{product_id}/proposals/{proposal_id}/accept",
             response_model=Dict[str, Any])
def accept_proposal(product_id: int = Path(..., ge=1),
                    proposal_id: int = Path(..., ge=1),
                    db: Session = Depends(get_db)):
    """Прийняти пропозицію: позначити й ЗАСТОСУВАТИ звичайним update_product.

    Порядок саме такий. Спершу позначаємо прийнятою (це закриває гонку двох
    кліків: другий отримає None), потім застосовуємо. Якщо застосування впаде,
    транзакція відкотить і позначку — пропозиція лишиться відкритою.
    """
    payload = field_proposals.accept(db, proposal_id)
    if payload is None:
        raise HTTPException(status_code=404,
                            detail="Пропозицію вже вирішено або не знайдено")
    if payload["product_id"] != product_id:
        raise HTTPException(status_code=400, detail="Пропозиція належить іншому товару")

    update = schemas.ProductUpdate(**payload["update"])
    updated = product_service.update_product(db, product_id, update)
    if not updated:
        raise HTTPException(status_code=404, detail="Товар не знайдено")

    # ⚠️ Далі роутер товарів сам поставить задачі write-back — тут ми свідомо
    # НЕ дублюємо цю логіку, щоб не зʼявилось другого місця, яке треба
    # синхронізувати. Прийняття проходить рівно тим самим шляхом, що й правка
    # руками, включно з локом і чергою.
    field_values = getattr(updated, "_writeback_fields", set()) or set()
    return {"ok": True, "applied": payload["update"], "locked_fields": sorted(field_values)}


@router.post("/api/products/{product_id}/proposals/{proposal_id}/reject",
             response_model=Dict[str, Any])
def reject_proposal(product_id: int = Path(..., ge=1),
                    proposal_id: int = Path(..., ge=1),
                    db: Session = Depends(get_db)):
    """Відхилити. Це сигнал про якість моделі — на відміну від `stale`."""
    if not field_proposals.reject(db, proposal_id):
        raise HTTPException(status_code=404,
                            detail="Пропозицію вже вирішено або не знайдено")
    db.commit()
    return {"ok": True}


@router.post("/api/products/{product_id}/autofill", response_model=Dict[str, Any])
def run_autofill(product_id: int = Path(..., ge=1),
                 photos: int = Query(3, ge=1, le=10,
                                     description="скільки живих знімків надіслати"),
                 db: Session = Depends(get_db)):
    """Розпізнати товар за його живими знімками й скласти пропозиції.

    Нічого не пише в картку. Якщо бюджет вичерпано — повертає це як звичайну
    відповідь, а не помилку: відмова гальма це штатний стан, і автозаповнення
    просто тихо вимикається.
    """
    product = product_service.get_product(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не знайдено")

    # Живі знімки (kind='real'), у порядку індексу. Студійні official для
    # розпізнавання не годяться: на них немає ані бирки, ані реального стану.
    # ⚠️ resolve_category шукає за НАЯВНИМИ файлами товару, а не лише за типом —
    # інакше товар, у якого є тільки живі знімки, «не знаходить» своєї папки.
    type_name = getattr(product.type, "typename", None) if product.type else None
    category = resolve_category(product.productnumber, type_name)
    paths = _kind_files(product.productnumber, category, "real")[:photos]
    if not paths:
        return {"ok": False, "reason": "у товару немає живих знімків"}

    result = photo_autofill.extract_and_propose(db, product_id, paths)
    # Комітимо в БУДЬ-ЯКОМУ разі: навіть на провалі в сесії лежить запис про
    # витрату, і втратити його означало б занизити витрачене.
    db.commit()
    return result


@router.get("/api/autofill/budget", response_model=Dict[str, Any])
def budget_status(db: Session = Depends(get_db)):
    """Скільки лишилось у місячній стелі — щоб інтерфейс міг це показати."""
    v = ai_budget.guard(db)
    return {"allowed": v.allowed, "spent_usd": round(v.spent_usd, 4),
            "cap_usd": v.cap_usd, "remaining_usd": round(v.remaining_usd, 4),
            "reason": v.reason}

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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from models.database import get_db
    from schemas import product as schemas
    from services import (field_proposals, photo_autofill, product_service, ai_budget,
                          ai_quota, web_enrich, autofill_run, autofill_batch)
except ImportError:  # pragma: no cover
    from backend.models.database import get_db
    from backend.schemas import product as schemas
    from backend.services import (field_proposals, photo_autofill, product_service, ai_budget,
                                  ai_quota, web_enrich, autofill_run, autofill_batch)

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

    # ⚠️ БУЛО НЕПРАВИЛЬНО. Попередній коментар тут стверджував, що «роутер
    # товарів сам поставить задачі write-back». Ні: той код виконується лише на
    # PUT /api/products/{id}, а ми викликаємо update_product НАПРЯМУ. Прийняте
    # лочилось у базі, а в Журнал не їхало — #Ф2084, пʼять полів, у черзі нуль.
    # Тепер черга ставиться спільною функцією сервісу — тією ж, що й для PUT.
    queued = product_service.enqueue_writeback_for(db, updated)
    return {"ok": True, "applied": payload["update"], "locked_fields": sorted(queued)}


@router.post("/api/products/{product_id}/proposals/accept-all",
             response_model=Dict[str, Any])
def accept_all_proposals(product_id: int = Path(..., ge=1),
                         db: Session = Depends(get_db)):
    """Прийняти всі відкриті пропозиції товару одним записом.

    Той самий шлях, що й поодинокі прийняття (update_product + спільна черга
    write-back), лише payload зібрано разом: один запис у базу, один пакет у
    журнал. Порожня черга — не помилка: {ok, accepted: 0}.
    """
    payload = field_proposals.accept_all(db, product_id)
    if payload is None:
        db.commit()
        return {"ok": True, "accepted": 0, "applied": {}, "locked_fields": []}
    update = schemas.ProductUpdate(**payload["update"])
    updated = product_service.update_product(db, product_id, update)
    if not updated:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    queued = product_service.enqueue_writeback_for(db, updated)
    return {"ok": True, "accepted": len(payload["ids"]), "fields": payload["fields"],
            "applied": payload["update"], "locked_fields": sorted(queued)}


@router.get("/api/proposals/pending-summary", response_model=Dict[str, Any])
def proposals_pending_summary(source: Optional[str] = Query(None, regex="^(photo|profile|barcode|photo\\+profile)$"),
                              db: Session = Depends(get_db)):
    """Що саме чекає прийняття — для діалогу перед пакетним прийняттям."""
    return field_proposals.pending_summary(db, source)


@router.post("/api/proposals/accept-bulk", response_model=Dict[str, Any])
def accept_bulk(source: str = Query(..., regex="^(photo|profile|barcode|photo\\+profile)$"),
                limit: int = Query(500, ge=1, le=2000),
                db: Session = Depends(get_db)):
    """Прийняти відкриті пропозиції ОДНОГО шару на всіх товарах.

    Шар «profile» (власна база) історично приймається у 99.6% — переглядати
    його по одному чіпу нема сенсу. Кожен товар іде тим самим шляхом, що й
    «Прийняти всі» в картці: один update_product + один пакет у чергу
    журналу; воркер понесе їх в аркуш у фоні. Провал одного товару не
    зупиняє решту — він у відповіді.
    """
    ids = field_proposals.pending_product_ids(db, source)[:limit]
    done, fields, errors = 0, 0, []
    for pid in ids:
        try:
            payload = field_proposals.accept_all(db, pid, source=source)
            if payload is None:
                continue
            update = schemas.ProductUpdate(**payload["update"])
            updated = product_service.update_product(db, pid, update)
            if not updated:
                raise RuntimeError("товар не знайдено")
            product_service.enqueue_writeback_for(db, updated)
            db.commit()
            done += 1
            fields += len(payload["ids"])
        except Exception as e:  # noqa: BLE001 — один товар не має зупиняти решту
            db.rollback()
            logger.warning("accept-bulk: product %s failed: %s", pid, e)
            errors.append({"product_id": pid, "error": str(e)[:200]})
    return {"ok": True, "source": source, "products": done, "fields": fields,
            "errors": errors, "remaining": len(field_proposals.pending_product_ids(db, source))}


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
                 photos: int = Query(10, ge=1, le=12,
                                     description="скільки живих знімків надіслати (типово всі)"),
                 use_paid: bool = Query(False, description="повторити платним ключем — лише після підтвердження людини"),
                 db: Session = Depends(get_db)):
    """Розпізнати товар за його живими знімками й скласти пропозиції.

    Сам порядок шарів живе в `services/autofill_run.run_one` — той самий код
    виконує й пакетне розпізнавання завозу. Два однакові з вигляду шляхи
    розійшлися б при першій же правці, і різницю помітили б на живих товарах.

    Нічого не пише в картку. Якщо бюджет вичерпано — повертає це як звичайну
    відповідь, а не помилку: відмова гальма це штатний стан, і автозаповнення
    просто тихо вимикається.
    """
    result = autofill_run.run_one(db, product_id, photos=photos, use_paid=use_paid)
    if result.get("not_found"):
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    return result


@router.post("/api/products/{product_id}/enrich-web", response_model=Dict[str, Any])
def run_web_enrich(product_id: int = Path(..., ge=1),
                   db: Session = Depends(get_db)):
    """Знайти офіційні характеристики за АРТИКУЛОМ і скласти пропозиції.

    Четвертий шар (див. services/web_enrich): три наявні дивляться всередину —
    на знімки, штрихкод і наші минулі записи, — а тут ми читаємо те, що про цю
    пару опублікував сам виробник. Як і решта, у картку не пише.
    """
    product = product_service.get_product(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не знайдено")
    result = web_enrich.enrich_by_article(db, product_id)
    # Комітимо завжди: навіть на провалі в сесії лежить запис про витрату.
    db.commit()
    return result


@router.get("/api/autofill/budget", response_model=Dict[str, Any])
def budget_status(db: Session = Depends(get_db)):
    """Скільки лишилось у місячній стелі — щоб інтерфейс міг це показати."""
    v = ai_budget.guard(db)
    return {"allowed": v.allowed, "spent_usd": round(v.spent_usd, 4),
            "cap_usd": v.cap_usd, "remaining_usd": round(v.remaining_usd, 4),
            "reason": v.reason}


@router.get("/api/autofill/limits", response_model=Dict[str, Any])
def limits_status(db: Session = Depends(get_db)):
    """Ліміти ШІ одним поглядом: добова квота безкоштовного рівня (з нашого
    обліку — Google залишок не віддає), місячна стеля, стан платного ключа."""
    return ai_quota.status(db, paid_available=photo_autofill.paid_key_available())


# ─────────────────────────── Пакетне розпізнавання ───────────────────────────
# «Розпізнати» на цілий завіз. Товари беруться або списком id (виділення у
# «Товарах»), або за завозом (кнопка в картці завозу та в «Поставках»).


class BatchAutofillRequest(BaseModel):
    product_ids: Optional[List[int]] = None
    delivery_id: Optional[int] = None
    label: Optional[str] = None
    # ⚠️ Платний ключ у пакеті — лише за явним підтвердженням людини, як і в
    # картці: двадцять товарів поспіль коштують у двадцять разів більше.
    use_paid: bool = False
    # Не витрачати квоту на те, що вже розпізнано: товар із відкритими
    # пропозиціями пропускається, якщо людина не попросила інакше.
    skip_with_proposals: bool = True


@router.post("/api/autofill/batch", response_model=Dict[str, Any])
def start_batch_autofill(req: BatchAutofillRequest, db: Session = Depends(get_db)):
    """Поставити пакет у роботу й одразу повернути задачу (не чекаючи на неї).

    Один товар — це до 90 секунд, тож завіз із двадцяти живе пів години:
    тримати на цьому HTTP-запит не можна ані з боку вебв'ю, ані з боку
    сервера. Прогрес видно у спільному Task Center.
    """
    ids: List[int] = list(req.product_ids or [])
    label = (req.label or "").strip()
    if req.delivery_id:
        rows = db.execute(text("""
            SELECT p.id FROM products p WHERE p.deliveryid = :d
            ORDER BY p.productnumber
        """), {"d": req.delivery_id}).fetchall()
        ids = [r[0] for r in rows]
        if not label:
            name = db.execute(text("SELECT deliveryname FROM deliveries WHERE id = :d"),
                              {"d": req.delivery_id}).scalar()
            label = f"Завіз {name or req.delivery_id}"
    if not ids:
        raise HTTPException(status_code=400, detail="Немає товарів для розпізнавання")

    considered = len(ids)
    if req.skip_with_proposals:
        have = {r[0] for r in db.execute(text("""
            SELECT DISTINCT product_id FROM product_field_proposals
            WHERE status = 'pending' AND product_id = ANY(:ids)
        """), {"ids": ids}).fetchall()}
        ids = [i for i in ids if i not in have]
    if not ids:
        return {"ok": False, "nothing_to_do": True, "considered": considered,
                "reason": "Усі ці товари вже мають нерозглянуті пропозиції — "
                          "спершу підтвердіть або відхиліть їх"}

    try:
        job = autofill_batch.start(ids, label or f"Розпізнавання ({len(ids)})",
                                   use_paid=req.use_paid)
    except RuntimeError as e:
        # Друга задача не помилка інтерфейсу, а зайнятість — 409, з текстом.
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "job": job, "considered": considered, "queued": len(ids)}


@router.get("/api/autofill/batch/active", response_model=Dict[str, Any])
def active_batch_autofill():
    """Чи щось іде просто зараз — щоб Task Center показав це й після
    перезавантаження сторінки, а не лише у вікні, з якого запустили."""
    job = autofill_batch.active()
    return {"active": bool(job), "job": job}


@router.get("/api/autofill/batch/{job_id}", response_model=Dict[str, Any])
def batch_autofill_status(job_id: str = Path(..., min_length=3, max_length=64)):
    job = autofill_batch.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задачу не знайдено")
    return job


@router.post("/api/autofill/batch/{job_id}/cancel", response_model=Dict[str, Any])
def cancel_batch_autofill(job_id: str = Path(..., min_length=3, max_length=64)):
    """Спинити пакет. Поточний товар дороблюється: обірвати виклик моделі
    посеред роботи означало б заплатити за нього й викинути результат."""
    return {"ok": autofill_batch.cancel(job_id), "job": autofill_batch.get(job_id)}


class AcceptProductsRequest(BaseModel):
    product_ids: List[int]


@router.post("/api/proposals/accept-products", response_model=Dict[str, Any])
def accept_for_products(req: AcceptProductsRequest, db: Session = Depends(get_db)):
    """«Підтвердити все» над набором товарів (завіз або виділення).

    Кожен товар іде тим самим шляхом, що й «Прийняти всі» в картці: один
    `update_product` + один пакет у чергу журналу. Провал одного не спиняє
    решту — він у відповіді.
    """
    done, fields, errors = 0, 0, []
    for pid in dict.fromkeys(req.product_ids or []):
        try:
            payload = field_proposals.accept_all(db, pid)
            if payload is None:
                continue
            update = schemas.ProductUpdate(**payload["update"])
            updated = product_service.update_product(db, pid, update)
            if not updated:
                raise RuntimeError("товар не знайдено")
            product_service.enqueue_writeback_for(db, updated)
            db.commit()
            done += 1
            fields += len(payload["ids"])
        except Exception as e:  # noqa: BLE001 — один товар не спиняє решту
            db.rollback()
            logger.warning("accept-products: product %s failed: %s", pid, e)
            errors.append({"product_id": pid, "error": str(e)[:200]})
    return {"ok": True, "products": done, "fields": fields, "errors": errors}

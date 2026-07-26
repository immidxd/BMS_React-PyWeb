"""Candidate-merge UX router.

Workspace-парсер створює пропозиції в `merge_candidates` (status=pending).
Користувач через UI акцептить (виконати merge) або декланіт (більше не пропонувати).
"""

import logging
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

try:
    from models.database import get_db
except ImportError:
    from backend.models.database import get_db

try:
    from services.match_finder import scan_lost_products, record_decision, DEFAULT_MIN_SCORE, DEFAULT_TOP_N
    from models.models import Product
except ImportError:
    from backend.services.match_finder import scan_lost_products, record_decision, DEFAULT_MIN_SCORE, DEFAULT_TOP_N
    from backend.models.models import Product

logger = logging.getLogger(__name__)
router = APIRouter()


# Поля, які при merge переносимо з загубленого в оригінал ЛИШЕ якщо в оригіналі
# вони порожні/відсутні. Наявні (непорожні) значення оригіналу НІКОЛИ не чіпаються.
# Свідомо НЕ включені: productnumber, clonednumbers (обробляються окремо),
# is_lost, deliveryid, quantity, dateadded, id — ідентичність/стан оригіналу.
_FILL_EMPTY_FIELDS = [
    "model", "marking", "description", "extranote", "year",
    "season", "dimensions", "width",
    "sizeeu", "size_letter", "sizeua", "sizeusa", "sizeuk", "sizejp", "sizecn",
    "measurementscm", "oldprice",
    "measurementscm_min", "measurementscm_max",
    "measurements_length_min", "measurements_length_max",
    "measurements_pog_min", "measurements_pog_max",
    "measurements_pob_min", "measurements_pob_max",
    "measurements_pot_min", "measurements_pot_max",
    "measurements_sleeve_min", "measurements_sleeve_max",
    "measurements_height_min", "measurements_height_max",
    "measurements_sole_thickness_min", "measurements_sole_thickness_max",
    "measurements_heel_min", "measurements_heel_max",
    "brandid", "typeid", "subtypeid", "colorid", "genderid", "styleid",
    "conditionid", "current_conditionid",
    "manufacturercountryid", "ownercountryid",
    "soletypeid", "toeshapeid", "fasteningtypeid", "liningid",
]


def _is_blank(v) -> bool:
    """Порожнє = None або рядок з самих пробілів. (0 для FK НЕ вважаємо порожнім.)"""
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _fill_empty_from(target, source) -> list:
    """Заповнити порожні поля `target` (оригінал) значеннями з `source` (загублений).
    Непорожні поля оригіналу не змінюються. Повертає список заповнених полів."""
    filled = []
    for f in _FILL_EMPTY_FIELDS:
        if _is_blank(getattr(target, f, None)) and not _is_blank(getattr(source, f, None)):
            setattr(target, f, getattr(source, f))
            filled.append(f)
    # price: 0/None у оригіналі вважаємо «не вказано» → беремо з загубленого, якщо >0
    tp = getattr(target, "price", None)
    spr = getattr(source, "price", None)
    if (tp is None or tp == 0) and spr and spr > 0:
        target.price = spr
        filled.append("price")
    return filled


# ─────────────────────────────────────────────────────────────────────────────
# SCAN: знайти можливі оригінали для загублених товарів (Фаза 3, зважений скоринг)
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/api/merge-candidates/scan")
def scan_merge_candidates(
    product_id: Optional[int] = Query(None, description="Сканувати лише один загублений товар"),
    min_score: int = Query(DEFAULT_MIN_SCORE, ge=0, le=100, description="Поріг впевненості 0–100"),
    top_n: int = Query(DEFAULT_TOP_N, ge=1, le=20, description="Скільки кандидатів на товар"),
    reset: bool = Query(False, description="Спершу видалити pending-кандидати сканованих товарів"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Запустити пошук оригіналів для is_lost / '???' товарів. Наповнює pending."""
    try:
        result = scan_lost_products(
            db, product_id=product_id, min_score=min_score, top_n=top_n, reset=reset,
        )
        return {"ok": True, **result}
    except Exception as e:
        db.rollback()
        logger.error(f"scan_merge_candidates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# LIST: pending кандидати для конкретного продукту (або всіх)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/merge-candidates")
def list_merge_candidates(
    product_id: Optional[int] = Query(None, description="Filter pending candidates for this NEW product"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Список pending-кандидатів. Якщо product_id заданий — тільки для нього."""
    try:
        sql = """
            SELECT
                mc.id, mc.new_product_id, mc.suggested_id, mc.score, mc.reason,
                mc.created_at,
                np.productnumber AS np_pnum, np.clonednumbers AS np_clones,
                np_b.brandname AS np_brand, np_t.typename AS np_type,
                np.model AS np_model, np.sizeeu AS np_size, np.marking AS np_marking,
                np_c.colorname AS np_color, np.description AS np_desc,
                np_d.deliveryname AS np_delivery, np_d.deliverydate AS np_delivery_date,
                (SELECT count(*) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                   WHERE oi.product_id = np.id AND o.order_status_id IN (1, 7)) AS np_sold,
                sp.productnumber AS sp_pnum, sp.clonednumbers AS sp_clones,
                sp_b.brandname AS sp_brand, sp_t.typename AS sp_type,
                sp.model AS sp_model, sp.sizeeu AS sp_size, sp.marking AS sp_marking,
                sp_c.colorname AS sp_color, sp.description AS sp_desc,
                sp_d.deliveryname AS sp_delivery, sp_d.deliverydate AS sp_delivery_date,
                (SELECT count(*) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                   WHERE oi.product_id = sp.id AND o.order_status_id IN (1, 7)) AS sp_sold
            FROM merge_candidates mc
            JOIN products np ON np.id = mc.new_product_id
            JOIN products sp ON sp.id = mc.suggested_id
            LEFT JOIN brands np_b ON np_b.id = np.brandid
            LEFT JOIN types  np_t ON np_t.id = np.typeid
            LEFT JOIN colors np_c ON np_c.id = np.colorid
            LEFT JOIN deliveries np_d ON np_d.id = np.deliveryid
            LEFT JOIN brands sp_b ON sp_b.id = sp.brandid
            LEFT JOIN types  sp_t ON sp_t.id = sp.typeid
            LEFT JOIN colors sp_c ON sp_c.id = sp.colorid
            LEFT JOIN deliveries sp_d ON sp_d.id = sp.deliveryid
            WHERE mc.status = 'pending'
        """
        params: Dict[str, Any] = {}
        if product_id is not None:
            sql += " AND mc.new_product_id = :pid"
            params["pid"] = product_id
        sql += " ORDER BY mc.score DESC, mc.created_at DESC"

        rows = db.execute(text(sql), params).mappings().all()
        return {"items": [dict(r) for r in rows], "total": len(rows)}
    except Exception as e:
        logger.error(f"list_merge_candidates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# COUNT: скільки pending-кандидатів є взагалі (для бейджа в шапці UI)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/merge-candidates/pending-count")
def merge_candidates_pending_count(db: Session = Depends(get_db)) -> Dict[str, int]:
    row = db.execute(
        text("SELECT COUNT(*) FROM merge_candidates WHERE status = 'pending'")
    ).fetchone()
    return {"count": int(row[0]) if row else 0}


# ─────────────────────────────────────────────────────────────────────────────
# ACCEPT: виконати merge — append clones з NEW в SUGGESTED, видалити NEW
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/api/merge-candidates/{candidate_id}/accept")
def accept_merge_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Виконати запропонований merge:
       1. Скопіювати/доповнити clonednumbers з NEW у SUGGESTED.
       2. Якщо NEW productnumber відрізняється від ???, додати його теж як clone.
       3. Видалити NEW product (cascade чистить merge_candidates).
       4. Помітити candidate як accepted.
    """
    try:
        cand = db.execute(
            text("""SELECT id, new_product_id, suggested_id, status
                    FROM merge_candidates WHERE id = :id"""),
            {"id": candidate_id},
        ).fetchone()
        if not cand:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if cand[3] != "pending":
            raise HTTPException(status_code=400, detail=f"Already {cand[3]}")

        new_pid, sug_pid = cand[1], cand[2]

        # ORM-обʼєкти для стабільного ключа рішення (рахуємо ДО видалення NEW)
        np_obj = db.get(Product, new_pid)
        sp_obj = db.get(Product, sug_pid)

        # Зчитуємо обидва товари
        np_row = db.execute(
            text("SELECT productnumber, clonednumbers FROM products WHERE id = :id"),
            {"id": new_pid},
        ).fetchone()
        sp_row = db.execute(
            text("SELECT productnumber, clonednumbers FROM products WHERE id = :id"),
            {"id": sug_pid},
        ).fetchone()
        if not np_row or not sp_row:
            raise HTTPException(status_code=410, detail="Product gone")

        np_pnum, np_clones = np_row[0], np_row[1]
        sp_pnum, sp_clones = sp_row[0], sp_row[1]

        # Збираємо клони для SUGGESTED
        existing_parts = [c.strip() for c in (sp_clones or "").split(";") if c.strip()]
        to_add: List[str] = []
        # NEW productnumber (якщо це не ???) — теж в клони
        if np_pnum and np_pnum != "???":
            to_add.append(np_pnum.lstrip("#"))
        # Усі клони з NEW
        for c in (np_clones or "").split(";"):
            c = c.strip()
            if c:
                to_add.append(c)
        # Унікалізуємо, зберігаючи порядок
        merged_clones: List[str] = list(existing_parts)
        for c in to_add:
            if c not in merged_clones:
                merged_clones.append(c)
        new_clones_str = "; ".join(merged_clones) if merged_clones else None

        # Оновлюємо SUGGESTED
        db.execute(
            text("""UPDATE products SET clonednumbers = :c, updated_at = NOW()
                    WHERE id = :id"""),
            {"c": new_clones_str, "id": sug_pid},
        )

        # Fill-empty: переносимо в оригінал лише ПОРОЖНІ його поля із загубленого
        # (наявні значення оригіналу не чіпаємо). Flush до видалення NEW.
        filled_fields: List[str] = []
        if np_obj is not None and sp_obj is not None:
            filled_fields = _fill_empty_from(sp_obj, np_obj)
            if filled_fields:
                db.flush()

        # Перепривʼязуємо FK-залежні рядки з NEW на SUGGESTED, щоб історія
        # продажів/пости/etc. не загубились
        db.execute(
            text("UPDATE order_items SET product_id = :sg WHERE product_id = :np"),
            {"sg": sug_pid, "np": new_pid},
        )
        db.execute(
            text("UPDATE telegram_posts SET product_id = :sg WHERE product_id = :np"),
            {"sg": sug_pid, "np": new_pid},
        )

        # Persistent рішення (стабільний ключ) — щоб після ре-парсу не пропонувати знову
        if np_obj is not None and sp_obj is not None:
            record_decision(db, np_obj, sp_obj, "accepted")

        # Від'єднуємо NEW від ORM-сесії (значення вже скопійовані) — щоб raw DELETE
        # нижче не конфліктував з ORM-tracking видаленого рядка.
        if np_obj is not None:
            db.expunge(np_obj)

        # Видаляємо NEW product (CASCADE прибере інші pending кандидати для нього)
        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": new_pid})

        # Помічаємо candidate як accepted (NEW row уже видалено разом з FK, тож
        # candidate уже зникне через cascade — але про всяк випадок UPDATE
        # обережно з catch на FK-фантом)
        db.execute(
            text("""UPDATE merge_candidates
                    SET status = 'accepted', decided_at = NOW()
                    WHERE id = :id"""),
            {"id": candidate_id},
        )

        db.commit()
        logger.info(
            f"[merge-candidates] ACCEPT #{candidate_id}: merged new={new_pid} → suggested={sug_pid}, "
            f"clones now: {new_clones_str!r}, filled empty fields: {filled_fields}"
        )

        # Позначити рядок загубленого у Воркспейс як обʼєднаний (у ФОНІ, не
        # блокує відповідь; рядок НЕ видаляється — лише позначка, а парсер
        # Воркспейс надалі його пропускає, тож товар не відродиться).
        def _mark_merged_bg(lost_pnum=np_pnum, orig_pnum=sp_pnum):
            try:
                from backend.scripts import sheets_parser as _sp
            except ImportError:
                from scripts import sheets_parser as _sp
            try:
                res = _sp.mark_workspace_row_merged(lost_pnum, orig_pnum)
                if not res.get("ok"):
                    logger.warning(f"[merge-mark] skipped: {res.get('reason')}")
            except Exception as me:
                logger.error(f"[merge-mark] failed: {me}")

        threading.Thread(target=_mark_merged_bg, daemon=True).start()
        return {
            "ok": True,
            "suggested_id": sug_pid,
            "merged_clones": new_clones_str,
            "filled_fields": filled_fields,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"accept_merge_candidate({candidate_id}): {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# DECLINE: відмова — більше не пропонувати цю пару
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/api/merge-candidates/{candidate_id}/decline")
def decline_merge_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        res = db.execute(
            text("""UPDATE merge_candidates
                    SET status = 'declined', decided_at = NOW()
                    WHERE id = :id AND status = 'pending'
                    RETURNING id, new_product_id, suggested_id"""),
            {"id": candidate_id},
        ).fetchone()
        if not res:
            raise HTTPException(status_code=404, detail="Candidate not found or already decided")
        # Persistent рішення (стабільний ключ) — переживає ре-парс/зміну id
        np_obj = db.get(Product, res[1])
        sp_obj = db.get(Product, res[2])
        if np_obj is not None and sp_obj is not None:
            record_decision(db, np_obj, sp_obj, "declined")
        db.commit()
        logger.info(f"[merge-candidates] DECLINE #{candidate_id}")
        return {"ok": True, "candidate_id": res[0]}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"decline_merge_candidate({candidate_id}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

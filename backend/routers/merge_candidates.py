"""Candidate-merge UX router.

Workspace-парсер створює пропозиції в `merge_candidates` (status=pending).
Користувач через UI акцептить (виконати merge) або декланіт (більше не пропонувати).
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

try:
    from models.database import get_db
except ImportError:
    from backend.models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# LIST: pending кандидати для конкретного продукту (або всіх)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/api/merge-candidates")
async def list_merge_candidates(
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
                np_c.colorname AS np_color,
                sp.productnumber AS sp_pnum, sp.clonednumbers AS sp_clones,
                sp_b.brandname AS sp_brand, sp_t.typename AS sp_type,
                sp.model AS sp_model, sp.sizeeu AS sp_size, sp.marking AS sp_marking,
                sp_c.colorname AS sp_color
            FROM merge_candidates mc
            JOIN products np ON np.id = mc.new_product_id
            JOIN products sp ON sp.id = mc.suggested_id
            LEFT JOIN brands np_b ON np_b.id = np.brandid
            LEFT JOIN types  np_t ON np_t.id = np.typeid
            LEFT JOIN colors np_c ON np_c.id = np.colorid
            LEFT JOIN brands sp_b ON sp_b.id = sp.brandid
            LEFT JOIN types  sp_t ON sp_t.id = sp.typeid
            LEFT JOIN colors sp_c ON sp_c.id = sp.colorid
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
async def merge_candidates_pending_count(db: Session = Depends(get_db)) -> Dict[str, int]:
    row = db.execute(
        text("SELECT COUNT(*) FROM merge_candidates WHERE status = 'pending'")
    ).fetchone()
    return {"count": int(row[0]) if row else 0}


# ─────────────────────────────────────────────────────────────────────────────
# ACCEPT: виконати merge — append clones з NEW в SUGGESTED, видалити NEW
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/api/merge-candidates/{candidate_id}/accept")
async def accept_merge_candidate(
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
            f"clones now: {new_clones_str!r}"
        )
        return {
            "ok": True,
            "suggested_id": sug_pid,
            "merged_clones": new_clones_str,
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
async def decline_merge_candidate(
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
        db.commit()
        logger.info(f"[merge-candidates] DECLINE #{candidate_id}")
        return {"ok": True, "candidate_id": res[0]}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"decline_merge_candidate({candidate_id}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

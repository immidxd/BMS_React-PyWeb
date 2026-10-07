"""Папки товарів — робочі набори для оперативної роботи в програмі.

Папка — це просто іменований список товарів: «Дії → У папку…» кладе туди
виділене, а «📁 Папки» в шапці «Товарів» відкриває її як ще один фільтр
(`/api/products?folder_id=…`; інші фільтри діють усередині папки).
Товар може бути в кількох папках. Суто локально — у хмару не синкається.
"""
from __future__ import annotations

import logging
import re
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from models.database import get_db
except ImportError:  # pragma: no cover
    from backend.models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/product-folders", tags=["product-folders"])

NAME_MAX = 120
IDS_MAX = 5000


class FolderName(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX)


class FolderItems(BaseModel):
    product_ids: List[int] = Field(..., min_length=1, max_length=IDS_MAX)


def clean_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Назва папки порожня")
    if len(name) > NAME_MAX:
        raise HTTPException(status_code=422, detail=f"Назва папки довша за {NAME_MAX} символів")
    return name


def _ensure_unique(db: Session, name: str, exclude_id: int | None = None) -> None:
    # Порівнюємо в Python: lower() у базі з локаллю C не опускає кирилицю,
    # і «Фото» та «фото» вийшли б різними папками.
    rows = db.execute(text("SELECT id, name FROM product_folders")).fetchall()
    key = name.casefold()
    for fid, fname in rows:
        if fid != exclude_id and (fname or "").casefold() == key:
            raise HTTPException(status_code=409, detail=f"Папка «{fname}» уже є")


def _folder_or_404(db: Session, folder_id: int):
    row = db.execute(
        text("SELECT id, name FROM product_folders WHERE id = :id"), {"id": folder_id}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Папку не знайдено")
    return row


def _count(db: Session, folder_id: int) -> int:
    return db.execute(
        text("SELECT count(*) FROM product_folder_items WHERE folder_id = :id"), {"id": folder_id}
    ).scalar() or 0


@router.get("")
def list_folders(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT f.id, f.name, f.sort_order, COUNT(i.product_id) AS cnt
        FROM product_folders f
        LEFT JOIN product_folder_items i ON i.folder_id = f.id
        GROUP BY f.id
        -- Нові згори: папки здебільшого тимчасові, свіжа — та, з якою працюють.
        ORDER BY f.created_at DESC, f.id DESC
    """)).fetchall()
    return {"folders": [
        {"id": r.id, "name": r.name, "sort_order": r.sort_order, "count": int(r.cnt)} for r in rows
    ]}


@router.post("")
def create_folder(body: FolderName, db: Session = Depends(get_db)):
    name = clean_name(body.name)
    _ensure_unique(db, name)
    try:
        row = db.execute(text("""
            INSERT INTO product_folders (name, sort_order)
            VALUES (:name, COALESCE((SELECT MAX(sort_order) + 1 FROM product_folders), 0))
            RETURNING id, name, sort_order
        """), {"name": name}).fetchone()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Папка «{name}» уже є")
    return {"id": row.id, "name": row.name, "sort_order": row.sort_order, "count": 0}


@router.patch("/{folder_id}")
def rename_folder(folder_id: int, body: FolderName, db: Session = Depends(get_db)):
    _folder_or_404(db, folder_id)
    name = clean_name(body.name)
    _ensure_unique(db, name, exclude_id=folder_id)
    try:
        db.execute(
            text("UPDATE product_folders SET name = :name, updated_at = now() WHERE id = :id"),
            {"name": name, "id": folder_id},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Папка «{name}» уже є")
    return {"id": folder_id, "name": name, "count": _count(db, folder_id)}


@router.delete("/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db)):
    """Видаляє лише папку й зв'язки; самі товари не чіпаються.

    Видалення — без підтвердження в UI, тому відповідь несе назву й товари:
    кнопка «Повернути» в сповіщенні відтворює папку з них.
    """
    row = _folder_or_404(db, folder_id)
    ids = [r[0] for r in db.execute(
        text("SELECT product_id FROM product_folder_items WHERE folder_id = :id ORDER BY added_at"),
        {"id": folder_id},
    ).fetchall()]
    db.execute(text("DELETE FROM product_folders WHERE id = :id"), {"id": folder_id})
    db.commit()
    logger.info("[folders] видалено папку %s «%s» (%d товарів у ній)", folder_id, row.name, len(ids))
    return {"ok": True, "id": folder_id, "name": row.name, "had": len(ids), "product_ids": ids}


@router.post("/{folder_id}/items")
def add_items(folder_id: int, body: FolderItems, db: Session = Depends(get_db)):
    """Додати товари. Повторне додавання — не помилка (already); неіснуючі id пропускаються."""
    _folder_or_404(db, folder_id)
    ids = sorted(set(body.product_ids))
    added = db.execute(text("""
        INSERT INTO product_folder_items (folder_id, product_id)
        SELECT :fid, p.id FROM products p WHERE p.id = ANY(:ids)
        ON CONFLICT DO NOTHING
        RETURNING product_id
    """), {"fid": folder_id, "ids": ids}).fetchall()
    existing = db.execute(
        text("SELECT count(*) FROM products WHERE id = ANY(:ids)"), {"ids": ids}
    ).scalar() or 0
    db.execute(text("UPDATE product_folders SET updated_at = now() WHERE id = :id"), {"id": folder_id})
    db.commit()
    total = _count(db, folder_id)
    return {"added": len(added), "already": existing - len(added),
            "missing": len(ids) - existing, "count": total}


@router.post("/{folder_id}/items/remove")
def remove_items(folder_id: int, body: FolderItems, db: Session = Depends(get_db)):
    _folder_or_404(db, folder_id)
    removed = db.execute(text("""
        DELETE FROM product_folder_items
        WHERE folder_id = :fid AND product_id = ANY(:ids)
        RETURNING product_id
    """), {"fid": folder_id, "ids": sorted(set(body.product_ids))}).fetchall()
    db.execute(text("UPDATE product_folders SET updated_at = now() WHERE id = :id"), {"id": folder_id})
    db.commit()
    return {"removed": len(removed), "count": _count(db, folder_id)}

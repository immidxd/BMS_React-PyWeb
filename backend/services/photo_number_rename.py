# -*- coding: utf-8 -*-
"""Фото їдуть разом із номером товару при його зміні.

Фото в BMS живуть за НОМЕРОМ у назві файлу (`Ф4503_001.webp`), а не за id
товару. Тому зміна номера в картці (PUT …/products/{id}/number) лишала знімки
під старим номером: 04.10.2026 #Ф4503 → #Ф4510 — у картці 0 фото (видно було
лише кеш до перезавантаження).

Кожне фото переноситься тим самим безпечним шляхом, що «↪ В інший товар»
(`photo_manager.move_photo_to_product`: спершу копія й заливка в R2, лише потім
прибирається старе), у порядку галереї — тож порядок зберігається. Позначка
«сховано» їде разом із файлом.

Ростовка: якщо старий номер лишився в ІНШИХ товарах (переіменовано лише один
розмір), фото спільні для номера — не переносимо, кажемо про це.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List
from urllib.parse import unquote

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _category_of(url: str, prefix: str, valid) -> str | None:
    path = unquote((url or "").split("?", 1)[0])
    if path.startswith(prefix):
        cat = path[len(prefix):].split("/", 1)[0]
        if cat in valid:
            return cat
    return None


def move_photos_to_new_number(db: Session, old_pnum: str, new_pnum: str) -> Dict[str, Any]:
    try:
        from services import photo_manager, product_images
    except ImportError:  # pragma: no cover
        from backend.services import photo_manager, product_images

    old_bare = (old_pnum or "").strip().lstrip("#")
    still = db.execute(text(
        "SELECT count(*) FROM products WHERE productnumber IN (:a, :b)"),
        {"a": old_bare, "b": "#" + old_bare}).scalar()
    if still:
        return {"moved": 0, "skipped": "старий номер лишився в інших розмірах ростовки — фото спільні, не переносимо"}

    entries = product_images.list_images(old_pnum, include_hidden=True)
    prefix = product_images.URL_PREFIX.rstrip("/") + "/"
    moved: List[str] = []
    errors: List[str] = []
    for im in entries:
        cat = _category_of(getattr(im, "url", ""), prefix, photo_manager.VALID_CATEGORIES)
        if not cat:
            errors.append(f"{im.filename}: невідома тека")
            continue
        try:
            res = photo_manager.move_photo_to_product(old_pnum, cat, im.filename, new_pnum, cat)
        except Exception as e:  # noqa: BLE001 — одне фото не має зупиняти решту
            logger.warning("[rename-photos] %s → %s: %s", im.filename, new_pnum, e)
            errors.append(f"{im.filename}: {e}")
            continue
        moved.append(res["moved"])
        if getattr(im, "hidden", False):
            db.execute(text("""
                UPDATE product_photo_hidden SET productnumber = :np, filename = :nf
                WHERE lower(productnumber COLLATE "und-x-icu") = lower(:op COLLATE "und-x-icu")
                  AND lower(filename COLLATE "und-x-icu") = lower(:of COLLATE "und-x-icu")
            """), {"np": res["target_pnum"], "nf": res["moved"], "op": old_bare, "of": im.filename})
    db.commit()
    product_images.invalidate_hidden_cache()
    product_images.invalidate_image_list_cache(old_pnum, new_pnum)
    try:
        product_images.get_photo_pnum_set(force=True)
    except Exception:  # noqa: BLE001
        pass
    return {"moved": len(moved), "errors": errors}

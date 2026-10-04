# -*- coding: utf-8 -*-
"""Фото в «Дефекти» → «Поточний стан» = «Пошкоджений» (правило власника 04.10.2026).

«Щойно в картці є будь-яке фото в Дефектах — стан стає Пошкоджений, доки
користувач сам його не змінить; його зміна — завжди в пріоритеті.»

Як це влаштовано, і чому саме так
─────────────────────────────────
* Поле — `current_conditionid` («Поточний стан»), а не `conditionid` («Стан»).
  «Стан» входить у тотожність рядка для парсера (`id_match` у sheets_parser) —
  його зміна в базі без аркуша могла б народити двійника. «Поточний стан»
  у тотожність не входить і створений саме для цього: стан пари зараз.
* Пріоритет людини = лок поля (`manually_edited_fields` містить
  `current_conditionid`). Його ставить кожна ручна зміна в картці, у Mini App
  складу, прийняття пропозиції. Є лок — правило мовчить НАЗАВЖДИ.
* Сам запис іде звичайним `update_product` + `enqueue_writeback_for`, як
  ручна правка: значення потрапляє в колонку «Поточний стан» журналу, і лок
  ставиться теж. Тому правило спрацьовує рівно ОДИН раз: далі поле вже
  залочене, і будь-яку зміну людини воно більше не перебʼє.
* Лише з «Новий» або порожнього (уточнення власника того ж дня: «якщо стан
  зараз вже інший — не змінювати! Тільки для нових товарів»). «Вживаний»,
  «Хороший», «Легковживаний» тощо правило не чіпає ніколи — і заднім числом
  нічого не переписує: воно спрацьовує лише в момент, коли фото лягає в «Дефекти».
* Ростовка: фото спільні на номер, а стан — на пару. Правило чіпає лише
  ТОЙ товар, у картці якого фото потрапило в «Дефекти» (`update_product` стан
  на «братів» не поширює — PER_ITEM_FIELDS).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DAMAGED = "Пошкоджений"
# Стан, з якого правило МАЄ право перейти в «Пошкоджений». Будь-який інший —
# це вже рішення людини чи журналу, і його не чіпаємо.
REPLACEABLE = frozenset({"", "новий"})
LOCK_FIELD = "current_conditionid"


def _locked(product) -> bool:
    raw = getattr(product, "manually_edited_fields", None) or ""
    return LOCK_FIELD in {x.strip() for x in raw.split(",") if x.strip()}


def apply_for_defect_photo(db: Session, product_id: int) -> Optional[Dict[str, Any]]:
    """Викликати ПІСЛЯ того, як фото успішно лягло в «Дефекти» цього товару.

    Повертає None, якщо нічого не змінилось; інакше {"applied": True, "from", "to"}.
    Ніколи не кидає: збій правила не має зірвати саму операцію з фото.
    """
    try:
        try:
            from models import models
            from schemas import product as product_schemas
            from services import product_service
        except ImportError:  # pragma: no cover
            from backend.models import models
            from backend.schemas import product as product_schemas
            from backend.services import product_service

        product = db.query(models.Product).filter(models.Product.id == product_id).first()
        if product is None or _locked(product):
            return None
        # Картка показує поточний стан, а без нього — «Стан» (успадкування).
        cond_id = product.current_conditionid or product.conditionid
        current = ""
        if cond_id:
            current = db.execute(text("SELECT conditionname FROM conditions WHERE id = :i"),
                                 {"i": cond_id}).scalar() or ""
        if current.strip().casefold() not in REPLACEABLE:
            return None     # уже «Пошкоджений» або інший стан — не змінюємо
        updated = product_service.update_product(
            db, product_id, product_schemas.ProductUpdate(current_condition_name=DAMAGED))
        if updated is None:
            return None
        product_service.enqueue_writeback_for(db, updated)
        logger.info("[defect-condition] %s: «%s» → «%s» (фото в Дефектах)",
                    product.productnumber, current or "—", DAMAGED)
        return {"applied": True, "from": current or None, "to": DAMAGED}
    except Exception as e:  # noqa: BLE001
        logger.warning("[defect-condition] product %s: %s", product_id, e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None

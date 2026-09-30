"""Одиничний прогін «Розпізнати» — спільний для картки й для пакета.

Раніше цей порядок (живі знімки → шар виробника, якщо знімків немає) жив
ПРЯМО в роутері `/api/products/{id}/autofill`. Пакетному розпізнаванню
довелось би його повторити, а два однакові з вигляду шляхи розходяться при
першій же правці: один отримав би нову стелю фото чи новий запобіжник, а
другий — ні, і різницю помітили б уже на живих товарах. Тому орудування
шарами живе тут, а роутер і фоновий воркер лише викликають `run_one`.

Функція НІЧОГО не пише в картку: як і завжди, усі шари складають лише
пропозиції (`product_field_proposals`).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

try:
    from services import photo_autofill, product_service, web_enrich
    from services.photo_manager import resolve_category, _kind_files
except ImportError:  # pragma: no cover
    from backend.services import photo_autofill, product_service, web_enrich
    from backend.services.photo_manager import resolve_category, _kind_files

logger = logging.getLogger(__name__)

# Стеля кадрів на один товар. Ліміт Google рахує ЗАПИТИ, а не токени, тож
# зайвий знімок не коштує квоти — лише частки цента; стеля тут лише проти
# товару з півсотнею фото.
MAX_PHOTOS = 12


def run_one(db: Session, product_id: int, *, photos: int = 10,
            use_paid: bool = False) -> Dict[str, Any]:
    """Розпізнати один товар усіма доступними шарами.

    Повертає звичайний звіт (`ok`, `proposed`, `reason`…). Відмова — це
    ВІДПОВІДЬ, а не виняток: пакет із двадцяти товарів не має зупинятись
    через один без фото. Виняток ловиться тут і теж стає відповіддю, бо
    голий 500 не лишає людині ані причини, ані сліду.
    """
    product = product_service.get_product(db, product_id)
    if not product:
        return {"ok": False, "not_found": True, "reason": "Товар не знайдено"}

    # Живі знімки (kind='real'), у порядку індексу. Студійні official для
    # розпізнавання не годяться: на них немає ані бирки, ані реального стану.
    # ⚠️ resolve_category шукає за НАЯВНИМИ файлами товару, а не лише за типом —
    # інакше товар, у якого є тільки живі знімки, «не знаходить» своєї папки.
    type_name = getattr(product.type, "typename", None) if product.type else None
    category = resolve_category(product.productnumber, type_name)
    paths = _kind_files(product.productnumber, category, "real")[:max(1, min(photos, MAX_PHOTOS))]

    if not paths:
        # Знімків немає — але артикул може бути. Тоді запускаємо лише шар
        # виробника: одна дія «Розпізнати» має робити все, що зараз можливо,
        # а не відмовляти цілком через відсутність одного з джерел.
        try:
            cur = photo_autofill._current_values(db, product_id)
            if web_enrich.available(cur):
                result = web_enrich.enrich_by_article(db, product_id)
                db.commit()
                result.setdefault("sources_used", ["артикул"])
                return result
        except Exception as e:  # noqa: BLE001
            logger.exception("autofill (web-only) failed for product %s", product_id)
            _safe_rollback(db)
            return {"ok": False, "failed": True,
                    "reason": f"Розпізнавання перервалось: {type(e).__name__}: {str(e)[:200]}"}
        return {"ok": False, "no_sources": True,
                "reason": "у товару немає ані живих знімків, ані артикула — "
                          "додайте фото або впишіть маркування"}

    try:
        result = photo_autofill.extract_and_propose(db, product_id, paths, use_paid=use_paid)
    except Exception as e:  # noqa: BLE001
        # ⚠️ Голий 500 тут неприпустимий. Людина бачила «Internal server error»
        # і не мала ЖОДНОГО способу дізнатись причину: сліду в обліку витрат
        # немає (виняток стався до запису), лог застосунку йде в консоль.
        logger.exception("autofill failed for product %s", product_id)
        _safe_rollback(db)
        return {"ok": False, "failed": True,
                "reason": f"Розпізнавання перервалось: {type(e).__name__}: {str(e)[:200]}"}
    # Комітимо в БУДЬ-ЯКОМУ разі: навіть на провалі в сесії лежить запис про
    # витрату, і втратити його означало б занизити витрачене.
    db.commit()
    return result


def _safe_rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass

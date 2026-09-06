"""Дозаповнення картки з ВЛАСНОЇ бази: що минулі записи знають про цю модель.

Безкоштовно й без фотографій. Жодного звернення до ШІ: беремо попередні записи
того самого бренда+моделі й пропонуємо те, на чому вони СХОДЯТЬСЯ ПОВНІСТЮ.
Дані ці ввела людина, тож це найнадійніше джерело з усіх, які в нас є.

⚠️ НІЧОГО НЕ ЗАПИСУЄ В products. Створює пропозиції — ті самі чіпи в картці,
які приймає або відхиляє людина. Прийняття йде звичайним `update_product` із
локом і чергою write-back, тобто новий шлях у картку не зʼявляється.

Правила відбору живуть у `services/model_profile.py` (межа 80% одностайності по
базі, ≥2 записи в групі, повна згода в КОЖНІЙ групі окремо) — тут вони не
дублюються.

Використання:
    python backend/scripts/backfill_from_model_profile.py            # сухий прогін
    python backend/scripts/backfill_from_model_profile.py --apply    # створити пропозиції
    python backend/scripts/backfill_from_model_profile.py --apply --limit 50
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

try:
    from models.database import SessionLocal
    from services import field_proposals, model_profile
except ImportError:  # pragma: no cover
    from backend.models.database import SessionLocal
    from backend.services import field_proposals, model_profile


def _groups(db) -> Dict[Tuple[str, str], List[int]]:
    """Товари, згруповані за брендом+моделлю (по одному запиту, не по товару)."""
    rows = db.execute(text("""
        SELECT btrim(b.brandname), btrim(p.model), p.id
        FROM products p JOIN brands b ON b.id = p.brandid
        WHERE nullif(btrim(p.model), '') IS NOT NULL
        ORDER BY 1, 2, 3
    """)).fetchall()
    out: Dict[Tuple[str, str], List[int]] = collections.OrderedDict()
    for brand, model, pid in rows:
        out.setdefault((brand, model), []).append(pid)
    return out


def _pending(db) -> Dict[int, set]:
    """Поля, за якими вже є НЕВИРІШЕНА пропозиція → їх не чіпаємо.

    ⚠️ Це не дрібниця: `field_proposals.propose` робить upsert по (товар, поле),
    тож без цієї перевірки профіль мовчки затер би пропозицію, яку модель
    зробила подивившись на ФОТО саме цієї пари. Профіль знає лише про інші
    пари тієї ж моделі, тож поступатись має він.
    """
    rows = db.execute(text(
        "SELECT product_id, field FROM product_field_proposals WHERE status = 'pending'"
    )).fetchall()
    out: Dict[int, set] = {}
    for pid, field in rows:
        out.setdefault(pid, set()).add(field)
    return out


def collect(db, *, limit: int = 0) -> List[Dict[str, Any]]:
    """Що можна запропонувати. Нічого не пише — придатне і для сухого прогону."""
    pending = _pending(db)
    plan: List[Dict[str, Any]] = []
    for (brand, model), ids in _groups(db).items():
        if len(ids) < model_profile.MIN_RECORDS:
            continue
        # ⚠️ Профіль рахуємо ОДИН раз на групу, без exclude_id. Це не спрощення:
        # агрегат рахує лише НЕпорожні значення, а ми пропонуємо саме туди, де в
        # товару порожньо — тож його власний рядок у цей підрахунок і так не
        # входить. Заразом це різниця між одним запитом і сотнями.
        prof = model_profile.profile_for(db, brand, model)
        agreed = model_profile.unanimous(prof)
        if not agreed:
            continue
        for pid in ids:
            now = model_profile.current_values(db, pid)
            busy = pending.get(pid, set())
            for field, (value, n) in agreed.items():
                if field in busy:
                    continue
                if (now.get(field) or "").strip():
                    continue
                plan.append({"product_id": pid, "field": field, "value": value,
                             "records": n, "brand": brand, "model": model,
                             "confidence": model_profile.confidence_for(n)})
        if limit and len(plan) >= limit:
            break
    return plan[:limit] if limit else plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="створити пропозиції (без прапорця — лише звіт)")
    ap.add_argument("--limit", type=int, default=0, help="стеля на кількість пропозицій")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        plan = collect(db, limit=args.limit)
        by_field = collections.Counter(p["field"] for p in plan)
        products = {p["product_id"] for p in plan}

        print(f"{'поле':<26}{'пропозицій':>12}")
        print("─" * 40)
        for field, n in by_field.most_common():
            print(f"{field:<26}{n:>12}")
        print("─" * 40)
        print(f"{'РАЗОМ':<26}{len(plan):>12}   на {len(products)} товарах\n")

        print("Приклади:")
        for p in plan[:8]:
            print(f"   #{p['product_id']:<7} {p['brand']} «{p['model']}» → "
                  f"{p['field']} = «{p['value']}»  ({p['records']} записів, "
                  f"певність {p['confidence']:.2f})")

        if not args.apply:
            print("\nСУХИЙ ПРОГІН — нічого не записано. Для запису: --apply")
            return 0

        made = 0
        for p in plan:
            note = (f"{p['records']} минулих записів «{p['brand']} {p['model']}» "
                    f"сходяться на «{p['value']}»")
            if field_proposals.propose(db, p["product_id"], p["field"], p["value"],
                                       p["confidence"], source="profile", note=note):
                made += 1
        db.commit()
        print(f"\n✓ створено пропозицій: {made} (пропозиція, не запис — "
              f"кожна чекає на підтвердження в картці)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

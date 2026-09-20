"""Прибрати legacy-двійники замовлень, у яких є tracked-близнюк.

Що це. До парсера v9 (source_pnum_key) той самий рядок аркуша «Замовлення»
міг народити ще одне замовлення при кожній зміні розпізнавання. Такі старі
копії не мають `source_sheet_gid` (legacy). Коли поруч уже є замовлення з
`source_sheet_gid` (tracked — його веде нинішній парсер), legacy-копія —
привид: та сама дата, сума, клієнт і той самий набір номерів (pnum_key).

Чому це шкодить. Привид зі статусом «Підтверджено/Оплачено» рахується як
продаж ДРУГИЙ раз, а його позиції часто привʼязані не до того розміру
(старий імпорт брав перший рядок номера): #Ф1298 — примітка «Ф1298 (43)»,
а позиція на 41.5 → 41.5 показується проданим, хоч він у наявності.
Виміряно 20.09.2026: 100 таких legacy-замовлень, 117 позицій, 64 з них
рахуються як продаж, 17 позицій — не той розмір.

Що робить скрипт (dry-run за замовчуванням, `--apply` застосовує):
  • знаходить групи (pnum_key, сума, клієнт, дата) з ≥1 tracked і ≥1 legacy;
  • legacy-замовлення в таких групах (без ручних правок у BMS) — видаляє
    разом із позиціями; tracked-близнюк лишається як єдина правда рядка;
  • перед видаленням — JSON-бекап у backend/scripts/order_twin_backups/.
Групи tracked+tracked (різні fingerprint, часто «Підтверджено» + «Відміна»)
НЕ чіпає — це різні рядки аркуша.

    python -m backend.scripts.remove_legacy_order_twins
    python -m backend.scripts.remove_legacy_order_twins --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sqlalchemy import text  # noqa: E402

try:
    from models.database import SessionLocal
except ImportError:  # pragma: no cover
    from backend.models.database import SessionLocal

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "order_twin_backups")

GROUPS = """
WITH g AS (
  SELECT source_pnum_key k, total_amount t, COALESCE(client_id, -1) cl, order_date d,
         count(*) FILTER (WHERE source_sheet_gid IS NULL) legacy,
         count(*) FILTER (WHERE source_sheet_gid IS NOT NULL) tracked
  FROM orders WHERE source_pnum_key IS NOT NULL AND source_pnum_key <> ''
  GROUP BY 1,2,3,4 HAVING count(*) > 1)
SELECT o.id, o.order_date, o.total_amount, o.client_id, o.order_status_id, o.payment_status_id,
       o.notes, o.manually_edited_fields,
       (SELECT o2.id FROM orders o2 WHERE o2.source_pnum_key=g.k AND o2.total_amount=g.t
          AND COALESCE(o2.client_id,-1)=g.cl AND o2.order_date=g.d AND o2.source_sheet_gid IS NOT NULL
        ORDER BY o2.updated_at DESC LIMIT 1) AS keeper
FROM orders o
JOIN g ON g.k=o.source_pnum_key AND g.t=o.total_amount AND g.cl=COALESCE(o.client_id,-1) AND g.d=o.order_date
WHERE g.tracked >= 1 AND o.source_sheet_gid IS NULL
ORDER BY o.order_date DESC, o.id
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="видалити (без прапорця — лише звіт)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(text(GROUPS)).mappings().all()
        victims = [dict(r) for r in rows if not (r["manually_edited_fields"] or "").strip()]
        skipped = [dict(r) for r in rows if (r["manually_edited_fields"] or "").strip()]
        items = db.execute(text("""
            SELECT oi.id, oi.order_id, oi.product_id, p.productnumber, p.sizeeu, oi.quantity, oi.price
            FROM order_items oi JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = ANY(:ids)"""), {"ids": [v["id"] for v in victims] or [0]}).mappings().all()
        sold_like = [v for v in victims if v["order_status_id"] == 7 or (v["order_status_id"] == 1 and v["payment_status_id"] == 1)]
        print(f"legacy-двійників із tracked-близнюком: {len(rows)}; до видалення: {len(victims)} "
              f"(позицій {len(items)}, рахувались як продаж: {len(sold_like)}); з ручними правками — пропущено: {len(skipped)}")
        for v in victims[:12]:
            its = ", ".join(f"{i['productnumber']} {i['sizeeu'] or '?'}" for i in items if i["order_id"] == v["id"])
            print(f"  #{v['id']} {v['order_date']} {v['total_amount']} → лишається #{v['keeper']} · {its} · {(v['notes'] or '')[:50]}")
        if len(victims) > 12:
            print(f"  … і ще {len(victims) - 12}")
        if not args.apply:
            print("\nСУХИЙ ПРОГІН — нічого не видалено. Для застосування: --apply")
            return 0
        if not victims:
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_legacy_order_twins.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"orders": victims, "items": [dict(i) for i in items]}, f, ensure_ascii=False, indent=1, default=str)
        ids = [v["id"] for v in victims]
        n_items = db.execute(text("DELETE FROM order_items WHERE order_id = ANY(:ids)"), {"ids": ids}).rowcount
        n_orders = db.execute(text("DELETE FROM orders WHERE id = ANY(:ids)"), {"ids": ids}).rowcount
        db.commit()
        print(f"\nвидалено замовлень: {n_orders}, позицій: {n_items}; бекап: {path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

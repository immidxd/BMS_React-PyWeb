"""Прибрати записи, що лишились у БД після ПЕРЕЙМЕНУВАННЯ номера в аркуші.

Кейс 17.08.2026: у вкладці «13.08.2026(Лісоводи)» номер 4336 перейменували на
Ф4336. Парсер шукає товар за номером із аркуша, тож нового імені він не впізнав
як старий запис — вставив 5 нових рядків, а 5 старих (#4336) лишились назавжди:
глобальний mark&sweep вимкнено, а point-wise reconcile бігав лише з картки завозу.
Гірше: наступний парс замовлень прив'язав до одного зі старих рядків продаж 2023-го
(інший товар із тим самим номером) — і той рядок став «проданим», тобто захищеним
від прибирання.

Що робить скрипт (dry-run за замовчуванням, `--apply` застосовує):
  1. бере товари ЗАВОЗУ зі старим номером (це і є орфани);
  2. кожну позицію замовлення, що на них висить, ПЕРЕВІШУЄ на правильний товар —
     тим самим `_pick_order_candidate`, що тепер працює в парсері (дата завозу +
     ціна + '#' + id), шукаючи серед решти товарів із цим номером;
  3. якщо перевішати нема на що — орфан лишається, і скрипт про це каже;
  4. пише JSON-бекап рядків перед видаленням.

Приклад:
    python -m backend.scripts.fix_renamed_number_orphans --delivery 805 \
        --old 4336 --new Ф4336 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402

from backend.models.database import SessionLocal  # noqa: E402
from backend.scripts.sheets_parser import _pick_order_candidate  # noqa: E402

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ghost_sweep_backups")


def _canon(s: str) -> str:
    return (s or "").strip().lstrip("#").rstrip(";").strip().upper()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delivery", type=int, required=True, help="id завозу (products.deliveryid)")
    ap.add_argument("--old", required=True, help="старий номер, напр. 4336")
    ap.add_argument("--new", required=True, help="новий номер, напр. Ф4336")
    ap.add_argument("--apply", action="store_true", help="без цього — лише показати план")
    args = ap.parse_args()

    old_c, new_c = _canon(args.old), _canon(args.new)
    db = SessionLocal()
    try:
        orphans = db.execute(text("""
            SELECT id, productnumber, sizeeu, quantity, price, dateadded
            FROM products
            WHERE deliveryid = :d
              AND UPPER(TRIM(LEADING '#' FROM productnumber)) = :old
            ORDER BY id
        """), {"d": args.delivery, "old": old_c}).mappings().all()

        successors = db.execute(text("""
            SELECT id, productnumber, sizeeu, quantity, price
            FROM products
            WHERE deliveryid = :d
              AND UPPER(TRIM(LEADING '#' FROM productnumber)) = :new
            ORDER BY id
        """), {"d": args.delivery, "new": new_c}).mappings().all()

        if not orphans:
            print(f"Орфанів зі старим номером {args.old} у завозі {args.delivery} немає.")
            return 0
        if not successors:
            print(f"⛔ У завозі {args.delivery} немає товарів з новим номером {args.new} — "
                  f"це не перейменування. Нічого не роблю.")
            return 1

        print(f"Завіз {args.delivery}: орфани {args.old} = {len(orphans)}, "
              f"наступники {args.new} = {len(successors)}")
        for r in orphans:
            print(f"  орфан id={r['id']} {r['productnumber']} розмір={r['sizeeu']} "
                  f"к-сть={r['quantity']} ціна={r['price']}")

        # ── Позиції замовлень на орфанах → перевішуємо на правильний товар ──
        orphan_ids = [r["id"] for r in orphans]
        items = db.execute(text("""
            SELECT oi.id, oi.order_id, oi.product_id, oi.price, o.order_date
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id = ANY(:ids)
            ORDER BY oi.id
        """), {"ids": orphan_ids}).mappings().all()

        moves, stuck = [], []
        for it in items:
            cands = db.execute(text("""
                SELECT id, productnumber, dateadded, price FROM products
                WHERE UPPER(TRIM(LEADING '#' FROM productnumber)) = :old
                  AND NOT (id = ANY(:ids))
            """), {"old": old_c, "ids": orphan_ids}).mappings().all()
            from types import SimpleNamespace
            objs = [SimpleNamespace(**dict(c)) for c in cands]
            target = _pick_order_candidate(objs, order_date=it["order_date"], item_price=it["price"])
            if target is None:
                stuck.append(it)
            else:
                moves.append((it, target))

        for it, target in moves:
            print(f"  позиція #{it['id']} (замовлення {it['order_id']} від {it['order_date']}, "
                  f"{it['price']}₴): товар {it['product_id']} → {target.id} ({target.productnumber})")
        for it in stuck:
            print(f"  ⚠️ позиція #{it['id']} (замовлення {it['order_id']}): перевішати нема на що — "
                  f"товар {it['product_id']} лишаю")

        stuck_products = {it["product_id"] for it in stuck}
        to_delete = [i for i in orphan_ids if i not in stuck_products]
        print(f"\nПлан: перевісити позицій {len(moves)}, видалити товарів {len(to_delete)}"
              f"{'' if not stuck_products else f', лишити {len(stuck_products)}'}")

        if not args.apply:
            print("\n(dry-run; повтори з --apply)")
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        dump = db.execute(text("SELECT * FROM products WHERE id = ANY(:ids)"),
                          {"ids": to_delete}).mappings().all()
        path = os.path.join(BACKUP_DIR,
                            f"{datetime.now():%Y%m%d_%H%M%S}_renamed_orphans_d{args.delivery}.json")
        with open(path, "w") as f:
            json.dump([{k: (str(v) if v is not None else None) for k, v in row.items()}
                       for row in dump], f, ensure_ascii=False, indent=1)
        print(f"Бекап: {path}")

        for it, target in moves:
            db.execute(text("UPDATE order_items SET product_id = :p, updated_at = now() WHERE id = :i"),
                       {"p": target.id, "i": it["id"]})
        if to_delete:
            db.execute(text("DELETE FROM products WHERE id = ANY(:ids)"), {"ids": to_delete})
        db.commit()
        print("✅ Застосовано")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

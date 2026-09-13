"""Злити записи, що лишились у БД після ПЕРЕЙМЕНУВАННЯ номера в аркуші.

Кейс 17.08.2026: у вкладці «13.08.2026(Лісоводи)» номер 4336 перейменували на
Ф4336 — парсер шукає товар за номером, нового імені не впізнав, вставив нові
рядки, а старі лишились назавжди. 13.09.2026 те саме вдруге: Ф4336 → #Ф4350, старий
номер лишили в «Номера-клони». Орфани при цьому НЕ просто зайві рядки: вони
опубліковані у вітрині, на Prom, в Instagram — і задвоюють залишок.

Що робить скрипт (dry-run за замовчуванням, `--apply` застосовує):
  1. бере ВСІ товари зі старим номером (орфани) і всі — з новим (наступники);
  2. кожному орфану шукає наступника з тим самим розміром (число + буква) і
     кольором; без пари — орфан лишається, скрипт про це каже;
  3. перевішує ВСІ посилання (замовлення, публікації, фото, чернетки…) через
     `repoint_product_refs` — той самий хелпер, що й у злитті двійників, тож
     жоден рядок не лишиться висіти на неіснуючому id; `product_materials` і
     `merge_candidates` свідомо лишаємо каскаду (у наступника свої матеріали, а
     підказки злиття для орфана — сміття);
  4. знімає старий номер з публікації в `catalog_listings` (is_published=false,
     updated_at=now()). Саме ЗНІМАЄ, а не видаляє рядок: хмарний синк вітрини
     зливає таблицю двобічно (newest-wins по updated_at), і видалений локально
     рядок повернувся б із хмари як опублікований;
  5. пише JSON-бекап рядків перед видаленням і видаляє орфанів.

Prom-оголошення старого номера скрипт НЕ чіпає — їх знімає `prom_service`
(див. delete_product_from_prom) ДО запуску, поки товар ще існує.

Приклад:
    python -m backend.scripts.fix_renamed_number_orphans --old Ф4336 --new Ф4350 --apply
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
from backend.scripts.sheets_parser import _size_canon  # noqa: E402
from backend.services.product_refs import count_product_refs, repoint_product_refs  # noqa: E402

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ghost_sweep_backups")
# У наступника свої матеріали; підказки злиття для орфана — сміття. Обидві каскадять.
CASCADE_TABLES = ("product_materials", "merge_candidates")


def _canon(s: str) -> str:
    return (s or "").strip().lstrip("#").rstrip(";").strip().upper()


def _size_key(row) -> tuple:
    return (_size_canon(row["sizeeu"]), (row["size_letter"] or "").strip().upper(), row["colorid"])


def _fetch(db, number: str, delivery: int | None):
    sql = """
        SELECT id, productnumber, sizeeu, size_letter, colorid, quantity, price, deliveryid
        FROM products
        WHERE UPPER(TRIM(LEADING '#' FROM productnumber)) = :n
    """
    params = {"n": number}
    if delivery:
        sql += " AND deliveryid = :d"
        params["d"] = delivery
    return db.execute(text(sql + " ORDER BY id"), params).mappings().all()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="старий номер, напр. Ф4336")
    ap.add_argument("--new", required=True, help="новий номер, напр. Ф4350")
    ap.add_argument("--delivery", type=int, default=None,
                    help="обмежити ОРФАНІВ одним завозом (products.deliveryid); наступники — з усіх")
    ap.add_argument("--apply", action="store_true", help="без цього — лише показати план")
    args = ap.parse_args()

    old_c, new_c = _canon(args.old), _canon(args.new)
    db = SessionLocal()
    try:
        orphans = _fetch(db, old_c, args.delivery)
        successors = _fetch(db, new_c, None)

        if not orphans:
            print(f"Орфанів зі старим номером {args.old} немає.")
            return 0
        if not successors:
            print(f"⛔ Товарів з новим номером {args.new} немає — це не перейменування. Нічого не роблю.")
            return 1

        by_key: dict[tuple, list] = {}
        for s in successors:
            by_key.setdefault(_size_key(s), []).append(s)

        print(f"Орфани {args.old}: {len(orphans)}, наступники {args.new}: {len(successors)}")
        plan, stuck = [], []
        for o in orphans:
            cands = by_key.get(_size_key(o), [])
            refs_total, refs_detail = count_product_refs(db, o["id"])
            line = (f"  орфан id={o['id']} {o['productnumber']} розмір={o['sizeeu']}"
                    f"{('/' + o['size_letter']) if o['size_letter'] else ''} к-сть={o['quantity']} "
                    f"посилань={refs_total}{(' ' + str(refs_detail)) if refs_detail else ''}")
            if len(cands) == 1:
                plan.append((o, cands[0]))
                print(f"{line} → id={cands[0]['id']} {cands[0]['productnumber']} (к-сть={cands[0]['quantity']})")
            else:
                stuck.append(o)
                print(f"{line} ⚠️ наступників з тим самим розміром/кольором: {len(cands)} — лишаю")

        listing = db.execute(text("SELECT productnumber FROM catalog_listings "
                                  "WHERE UPPER(TRIM(LEADING '#' FROM productnumber)) = :n "
                                  "AND is_published"),
                             {"n": old_c}).scalar()
        print(f"\nПлан: злити {len(plan)}, лишити {len(stuck)}"
              f"{', зняти з вітрини ' + listing if listing else ''}")

        if not args.apply:
            print("\n(dry-run; повтори з --apply)")
            return 0
        if not plan:
            return 1

        os.makedirs(BACKUP_DIR, exist_ok=True)
        ids = [o["id"] for o, _ in plan]
        dump = db.execute(text("SELECT * FROM products WHERE id = ANY(:ids)"), {"ids": ids}).mappings().all()
        path = os.path.join(BACKUP_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_renamed_orphans_{old_c}.json")
        with open(path, "w") as f:
            json.dump([{k: (str(v) if v is not None else None) for k, v in row.items()} for row in dump],
                      f, ensure_ascii=False, indent=1)
        if len(dump) != len(ids):
            print(f"⛔ Бекап неповний ({len(dump)} з {len(ids)}) — не видаляю.")
            return 1
        print(f"Бекап: {path}")

        for o, s in plan:
            res = repoint_product_refs(db, o["id"], s["id"], skip_tables=CASCADE_TABLES)
            print(f"  {o['id']} → {s['id']}: {res}")
        db.execute(text("DELETE FROM products WHERE id = ANY(:ids)"), {"ids": ids})
        if listing:
            db.execute(text("UPDATE catalog_listings SET is_published = FALSE, is_featured = FALSE, "
                            "updated_at = now() WHERE productnumber = :pn"), {"pn": listing})
        db.commit()
        print("✅ Застосовано")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

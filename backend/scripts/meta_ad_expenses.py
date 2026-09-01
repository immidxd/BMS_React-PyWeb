# -*- coding: utf-8 -*-
"""Витрати на рекламу Meta: зібрати з виписки → показати план → записати в аркуші.

Три підкоманди, і порядок між ними навмисний:

    ./venv/bin/python backend/scripts/meta_ad_expenses.py collect
        Тягне виписку monobank по ВСІХ рахунках і складає списання Meta в
        `meta_ad_charges`. Нічого не пише ні в аркуші, ні в advertising_expenses.
        ⚠️ Ліміт банку — 1 запит на 60 с, тож повна історія займає години.
        Прогін відновлюваний: `--windows N` ріже його на частини, повторний
        запуск продовжує з того ж місця.

    ./venv/bin/python backend/scripts/meta_ad_expenses.py plan
        Показує, що куди пішло б: суму на кожен аркуш ефіру, що НЕ чіпатиметься
        (заповнене рукою), де немає блоку «Витрати на рекламу» і що чекає на
        майбутній ефір. Тільки читання.

    ./venv/bin/python backend/scripts/meta_ad_expenses.py apply
        Записує заплановане. Викликати ЛИШЕ після перегляду `plan`.

Чому саме так, а не «одна кнопка»: запис іде в бойову таблицю власника, де вже
є числа, поставлені рукою. Людина мусить побачити план ДО того, як щось
зміниться, — і `apply` перечитує кожну комірку перед записом, бо між планом і
застосуванням її могли заповнити.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), ".env"))

from sqlalchemy import text  # noqa: E402

from models.database import SessionLocal  # noqa: E402
from services import meta_ads_writeback as wb  # noqa: E402
from services import mono_ad_sync  # noqa: E402


def _orders_book():
    """Книга «Замовлення». Окремим викликом, щоб `collect` її не відкривав."""
    from scripts import sheets_parser as sp
    return sp.get_gc().open_by_key(sp.ORDERS_ID)


def cmd_collect(db, args) -> int:
    from datetime import datetime

    def progress(p):
        w = p["window"]
        print(f"  [{datetime.now():%H:%M:%S}] {p['masked_pan']:20} {w[0]}..{w[1]}  "
              f"операцій {p['operations']:4}  Meta {p['meta']}  "
              f"порожніх поспіль {p['empty_streak']}", flush=True)

    print("Ліміт monobank — 1 запит на 60 с. Перервати можна будь-коли: "
          "поступ збережеться.\n", flush=True)
    results = mono_ad_sync.sync_all(db, max_windows_per_account=args.windows,
                                    progress=progress)
    print("\n── підсумок ──")
    for r in results:
        print("  ", r)
    row = db.execute(text("""
        SELECT count(*), min(charge_date), max(charge_date), COALESCE(sum(amount_uah), 0)
        FROM meta_ad_charges
    """)).first()
    print(f"\nУсього в базі: {row[0]} списань, {row[1]} … {row[2]}, разом {row[3]} ₴")
    return 0


def cmd_plan(db, args) -> int:
    plan = wb.build_plan(db, _orders_book())
    print(wb.format_plan(plan))
    if plan["planned"]:
        print("\nЦе СУХИЙ ПРОГІН — у Google Sheets нічого не змінилось.")
        print("Щоб застосувати: … meta_ad_expenses.py apply")
    return 0


def cmd_apply(db, args) -> int:
    book = _orders_book()
    plan = wb.build_plan(db, book)
    if not plan["planned"]:
        print(wb.format_plan(plan))
        print("\nЗаписувати нічого.")
        return 0

    print(wb.format_plan(plan))
    if not args.yes:
        print("\nЗапуск без --yes нічого не змінює. Перечитай план вище.")
        return 1

    result = wb.apply_plan(db, plan, sh=book)
    print(f"\nЗаписано аркушів: {len(result['written'])}")
    for e in result["written"]:
        print(f"   {e['title']:16} {e['value_cell']:>6} ← {e['total_uah']} ₴")
    if result["skipped_manual"]:
        print(f"\nНе записано (комірку заповнили між планом і записом): "
              f"{len(result['skipped_manual'])}")
        for e in result["skipped_manual"]:
            print(f"   {e['title']:16} там уже {e['existing']} ₴")
    print("\nЦі суми приїдуть у advertising_expenses наступним парсингом "
          "замовлень, і «Статистика» оновиться сама.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Витрати на рекламу Meta")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_collect = sub.add_parser("collect", help="зібрати списання з виписки monobank")
    p_collect.add_argument("--windows", type=int, default=None,
                           help="максимум вікон на рахунок за цей запуск")
    p_collect.set_defaults(func=cmd_collect)

    sub.add_parser("plan", help="показати, що куди пішло б (нічого не змінює)"
                   ).set_defaults(func=cmd_plan)

    p_apply = sub.add_parser("apply", help="записати заплановане в аркуші")
    p_apply.add_argument("--yes", action="store_true",
                         help="без цього прапорця нічого не пишеться")
    p_apply.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    db = SessionLocal()
    try:
        return args.func(db, args)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

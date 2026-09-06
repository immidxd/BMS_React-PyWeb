"""Пакетне автозаповнення по товарах, які СФОТОГРАФОВАНО.

Проходить усі товари, що мають живі знімки й хоч одне порожнє поле, і складає
пропозиції — тим самим кодом, що й кнопка «З фото» в картці. У products не
пише нічого: кожне значення чекає на підтвердження людини.

МЕЖА МОЖЛИВОГО ТУТ — НЕ МОДЕЛЬ, А ЗЙОМКА. Знято 304 номери з 9290, тобто цей
скрипт фізично дотягається до ~324 товарів. Решту каталогу закриє лише камера.

НА РОСТОВКУ — ОДИН ВИКЛИК. Кілька розмірів під спільним номером мають спільні
знімки, а поля, заради яких усе робиться, — model-level: прийняття рознесе їх
на всю ростовку. Виняток — `gtin`: він per-item, і на багаторозмірному номері
неможливо знати, чиєї саме пари ця бирка, тож підказка чіпа називає знімок.

⚠️ 429 ЗУПИНЯЄ ПРОГІН. Це вичерпана ДОБОВА квота, а не перевантаження;
повторювати безглуздо (одного разу 43 повтори зʼїли 11 хвилин чистого сну).
Скрипт відновлюваний: уже оброблені товари він пропускає за журналом витрат,
тож завтра достатньо запустити його знову.

Витрати йдуть під purpose='backfill' — у бюджеті це окрема половина стелі, щоб
дозаповнення старого не зʼїдало ліміт, призначений новим товарам.

Використання:
    python backend/scripts/backfill_autofill_from_photos.py             # що буде зроблено
    python backend/scripts/backfill_autofill_from_photos.py --apply
    python backend/scripts/backfill_autofill_from_photos.py --apply --limit 20 --delay 5
"""
from __future__ import annotations

import argparse
import collections
import os
import pathlib
import re
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

try:
    from models.database import SessionLocal
    from services import ai_budget, photo_autofill
    from services.photo_manager import resolve_category, _kind_files
except ImportError:  # pragma: no cover
    from backend.models.database import SessionLocal
    from backend.services import ai_budget, photo_autofill
    from backend.services.photo_manager import resolve_category, _kind_files

# Поля, заради яких усе робиться: довгий хвіст, який ніхто не встигає заповнити.
GAP_SQL = ("p.soletypeid IS NULL OR p.fasteningtypeid IS NULL OR p.liningid IS NULL "
           "OR p.toeshapeid IS NULL OR p.treadtypeid IS NULL")

_LIVE = re.compile(r"^(.+?)_(\d{3})\.webp$")   # живий знімок: номер_NNN.webp


def photographed_numbers(root: pathlib.Path) -> set:
    """Номери, у яких на диску є хоч один ЖИВИЙ знімок.

    Живий від студійного відрізняє кількість цифр в індексі — див. пастку
    «номер фото ≠ номер у БД»: у файлі «4372_001», у базі «#Ф4372».
    """
    return {m.group(1) for f in root.rglob("*.webp") if (m := _LIVE.match(f.name))}


def candidates(db, root: pathlib.Path) -> List[Dict[str, Any]]:
    """Товари з фото й порожніми полями, уже оброблені — виключені."""
    nums = photographed_numbers(root)
    both = list(nums) + ["#" + n for n in nums]
    rows = db.execute(text(f"""
        WITH gaps AS (
            SELECT p.id, p.productnumber, t.typename, b.brandname,
                   -- ⚠️ ОДИН РЯДОК НА НОМЕР. Ростовка це кілька розмірів під
                   -- спільним номером і з СПІЛЬНИМИ знімками; поля, заради яких
                   -- усе робиться (підошва, застібка, підкладка, носок,
                   -- протектор), — model-level, і `update_product` рознесе їх на
                   -- всю ростовку при прийнятті. Виклик на кожен розмір спалив
                   -- би квоту заради тієї самої відповіді: на цій вибірці —
                   -- 19 зайвих викликів із 324.
                   -- Беремо рядок, де порожніх полів найбільше: там пропозиції
                   -- потрібніші, а решта розмірів отримає їх пропагацією.
                   row_number() OVER (
                       PARTITION BY p.productnumber
                       ORDER BY (  (p.soletypeid      IS NULL)::int
                                 + (p.fasteningtypeid IS NULL)::int
                                 + (p.liningid        IS NULL)::int
                                 + (p.toeshapeid      IS NULL)::int
                                 + (p.treadtypeid     IS NULL)::int) DESC, p.id
                   ) AS rn
            FROM products p
            LEFT JOIN types t ON t.id = p.typeid
            LEFT JOIN brands b ON b.id = p.brandid
            WHERE p.productnumber = ANY(:nums) AND ({GAP_SQL})
              -- ⚠️ Відновлюваність без окремої таблиці: успішний виклик лишає
              -- слід у журналі витрат, і завтрашній запуск його пропустить.
              AND NOT EXISTS (SELECT 1 FROM ai_spend_log s
                              WHERE s.product_id = p.id AND s.purpose = 'backfill' AND s.ok)
        )
        SELECT id, productnumber, typename, brandname
        FROM gaps WHERE rn = 1 ORDER BY productnumber
    """), {"nums": both}).fetchall()

    out = []
    for pid, num, tname, brand in rows:
        pn = num.lstrip("#")
        paths = _kind_files(pn, resolve_category(pn, tname), "real")[:12]
        if paths:
            out.append({"id": pid, "number": num, "brand": brand, "photos": paths})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="запускати виклики (без прапорця — лише звіт)")
    ap.add_argument("--limit", type=int, default=0, help="стеля на кількість товарів за прогін")
    ap.add_argument("--delay", type=float, default=2.0, help="пауза між товарами, секунд")
    ap.add_argument("--photos-root", default=str(pathlib.Path.home()/"Downloads/Бізнес/Товар"))
    args = ap.parse_args()

    db = SessionLocal()
    try:
        todo = candidates(db, pathlib.Path(args.photos_root))
        if args.limit:
            todo = todo[:args.limit]
        v = ai_budget.guard(db, purpose="backfill")
        print(f"товарів до обробки: {len(todo)}")
        print(f"бюджет дозаповнення: витрачено ${v.spent_usd:.4f}, "
              f"лишилось ${v.remaining_usd:.4f} — {'дозволено' if v.allowed else v.reason}\n")
        if not args.apply:
            for c in todo[:10]:
                print(f"   {c['number']:<9} {str(c['brand']):<16} знімків {len(c['photos'])}")
            if len(todo) > 10:
                print(f"   … і ще {len(todo)-10}")
            print("\nСУХИЙ ПРОГІН — жодного виклику не зроблено. Для запуску: --apply")
            return 0

        stats = collections.Counter()
        fields = collections.Counter()
        spent = 0.0
        for i, c in enumerate(todo, 1):
            res = photo_autofill.extract_and_propose(db, c["id"], c["photos"],
                                                     purpose="backfill")
            db.commit()          # ← після КОЖНОГО товару: прогін переривний
            spent += float(res.get("cost_usd") or 0)
            reason = str(res.get("reason") or "")
            if res.get("ok"):
                stats["готово"] += 1
                for f, *_ in res.get("proposed", []):
                    fields[f] += 1
                print(f"[{i}/{len(todo)}] {c['number']:<9} "
                      f"запропоновано {len(res.get('proposed', []))}, "
                      f"підтверджено {len(res.get('confirmed', []))}", flush=True)
            elif res.get("budget_blocked"):
                print(f"\n⛔ стеля бюджету вичерпана — зупиняюсь на {i-1} товарах")
                break
            elif "429" in reason:
                print(f"\n⛔ добова квота запитів вичерпана — зупиняюсь на {i-1} товарах.\n"
                      f"   Прогін відновлюваний: завтра просто запустіть його знову.")
                break
            else:
                stats["помилка"] += 1
                print(f"[{i}/{len(todo)}] {c['number']:<9} ✗ {reason[:70]}", flush=True)
            time.sleep(args.delay)

        print(f"\n{'─'*54}")
        print(f"оброблено {stats['готово']}, помилок {stats['помилка']}, "
              f"витрачено ${spent:.4f}")
        if fields:
            print("\nзапропоновано по полях:")
            for f, n in fields.most_common():
                print(f"   {f:<24}{n:>5}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

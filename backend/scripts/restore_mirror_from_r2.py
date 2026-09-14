"""Відновити локальний кеш фото з R2 — на день, коли тека зникне.

R2 — джерело правди; `~/Downloads/Бізнес/Товар/` — лише кеш. Застосунок і сам
підтягує знімок із R2 при першому зверненні, але після повної втрати теки
зручніше повернути все одразу, ніж чекати, поки кожна картка відкриється.

Нічого не видаляє і не перезаписує: наявні файли пропускає.

    python backend/scripts/restore_mirror_from_r2.py            # що бракує
    python backend/scripts/restore_mirror_from_r2.py --apply    # відновити
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

try:
    from services import product_images as pi
except ImportError:  # pragma: no cover
    from backend.services import product_images as pi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="завантажити відсутні (без прапорця — лише звіт)")
    ap.add_argument("--category", default=None, help="лише одна категорія, напр. Взуття")
    args = ap.parse_args()

    root = pi.get_images_dir()
    idx = pi._r2_index(force=True)
    keys = sorted(k for v in idx.values() for k in v)
    if args.category:
        keys = [k for k in keys if k.startswith(args.category + "/")]
    missing = [k for k in keys if not os.path.isfile(os.path.join(root, k))]
    print(f"у R2: {len(keys)} файлів · локально є: {len(keys) - len(missing)} · бракує: {len(missing)}")
    if not missing:
        return 0
    by_cat: dict = {}
    for k in missing:
        by_cat[k.split("/", 1)[0]] = by_cat.get(k.split("/", 1)[0], 0) + 1
    for c, n in sorted(by_cat.items()):
        print(f"   {c:<12} {n}")
    if not args.apply:
        print("\nСУХИЙ ПРОГІН — нічого не завантажено. Для відновлення: --apply")
        return 0

    t0, ok, bad = time.time(), 0, 0
    for i, k in enumerate(missing, 1):
        if pi.ensure_local(k):
            ok += 1
        else:
            bad += 1
            print(f"   ✗ {k}")
        if i % 100 == 0:
            print(f"   … {i}/{len(missing)}", flush=True)
    print(f"\nвідновлено {ok}, не вдалось {bad}, за {time.time() - t0:.0f}с")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Синхронізація локального міору фото → Cloudflare R2 (local = майстер).

Робочий принцип проєкту: ЛОКАЛЬНА папка — джерело правди, R2 — дзеркало.
Правки робиш локально (перейменувати/замінити/видалити webp), а ця команда
доводить R2 до стану міору:
  • залити ключі, яких у R2 нема (нові/перейменовані);
  • (опц.) прибрати R2-сиріт — ключі, яких уже нема локально (видалені/старі
    назви після rename).

Видалення СИРІТ безпечне-за-замовчуванням: показуються, але не виконуються
без `--delete-orphans`. `--dry-run` лише показує план.

R2 не має «перейменування»: rename = новий ключ + видалення старого; ця
команда робить це за тебе (залив нового + delete-orphans старого).

Приклади:
  ./venv/bin/python3 backend/scripts/sync_photos_to_r2.py --dry-run
  ./venv/bin/python3 backend/scripts/sync_photos_to_r2.py                 # лише заливка
  ./venv/bin/python3 backend/scripts/sync_photos_to_r2.py --delete-orphans
"""

from __future__ import annotations

import argparse
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")
sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from backend.services import r2_storage
except ImportError:
    sys.path.insert(0, str(_PROJECT_ROOT / "backend"))
    from services import r2_storage  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("sync_r2")

MIRROR_ROOT = Path(os.environ.get(
    "PRODUCT_IMAGES_DIR", os.path.expanduser("~/Downloads/Бізнес/Товар")))
# Синхронізуємо лише webp (майстри). Інші формати в міорі — артефакти.
SYNC_EXT = ".webp"


def local_keys() -> dict[str, Path]:
    """{<ключ R2> : <локальний шлях>} для всіх webp у міорі."""
    out: dict[str, Path] = {}
    for p in MIRROR_ROOT.rglob(f"*{SYNC_EXT}"):
        if p.is_file() and not p.name.startswith("."):
            out[p.relative_to(MIRROR_ROOT).as_posix()] = p
    return out


def main():
    ap = argparse.ArgumentParser(description="Sync локальний мірор → R2")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delete-orphans", action="store_true",
                    help="видалити з R2 ключі, яких уже нема локально")
    ap.add_argument("--reupload", action="store_true",
                    help="перезалити навіть наявні (після зміни вмісту)")
    args = ap.parse_args()

    if not r2_storage.is_enabled():
        logger.error("❌ R2 не сконфігуровано (.env)")
        sys.exit(2)

    local = local_keys()
    remote = set(r2_storage.list_keys(""))
    to_upload = sorted(set(local) - remote) if not args.reupload else sorted(local)
    orphans = sorted(remote - set(local))

    logger.info(
        f"\n🔄 Sync мірор → R2\n   локально webp: {len(local)} | у R2: {len(remote)}\n"
        f"   залити: {len(to_upload)} | сиріт у R2: {len(orphans)}"
        f"{' | DRY-RUN' if args.dry_run else ''}\n"
    )

    up = dele = 0
    for k in to_upload:
        if args.dry_run:
            logger.info(f"  ↑ {k}")
        else:
            r2_storage.upload_file(str(local[k]), k)
        up += 1

    if orphans:
        if args.delete_orphans and not args.dry_run:
            for k in orphans:
                r2_storage.delete(k)
                dele += 1
            logger.info(f"  🗑 видалено сиріт: {dele}")
        else:
            logger.info(
                f"  ⚠ {len(orphans)} сиріт у R2 (нема локально). "
                f"Приклади: {orphans[:3]}\n"
                f"    додай --delete-orphans щоб прибрати."
            )

    logger.info(f"\n── Sync готово ── залито: {up} | видалено: {dele}")


if __name__ == "__main__":
    main()

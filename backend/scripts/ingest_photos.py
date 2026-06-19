#!/usr/bin/env python3
"""Ingest пакета оброблених фото товарів → WebP-майстри → локальний мірор + R2.

Один цикл стандартизації сховища (див. memory project_photo_pipeline):
  пакет (папка/розпакований zip) → для кожного зображення:
    1. конверсія у WebP-майстер (≤1512² по довшій стороні, q88, без EXIF)
    2. запис у локальний мірор  ~/Downloads/Бізнес/Товар/<категорія>/
    3. заливка в Cloudflare R2  під ключем  <категорія>/<ім'я>.webp

Іменування фото НЕ змінюється — лишається конвенція `<pnum>_NN` (official) /
`<pnum>_00NN` (real) / `<pnum>_defN` (defect); міняється тільки розширення на
`.webp`. Категорія = підпапка міору (Взуття/Сумки/Одяг/Аксесуари/Інше).

Приклади:
  # подивитись що буде, нічого не змінюючи:
  ./venv/bin/python3 backend/scripts/ingest_photos.py \
      --src ~/Downloads/19.06.2026_bags --category Сумки --dry-run

  # локально без R2 (поки немає кредів / тест):
  ... --category Сумки --no-upload

  # повний прогін (мірор + R2):
  ... --category Сумки
"""

from __future__ import annotations

import argparse
import os
import sys
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# .env з кореня проєкту (явний шлях — load_dotenv() без аргументу падає у деяких
# контекстах запуску, див. memory project_photo_pipeline).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

# Імпорт r2_storage працює і як пакет, і як скрипт.
sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from backend.services import r2_storage
except ImportError:
    sys.path.insert(0, str(_PROJECT_ROOT / "backend"))
    from services import r2_storage  # type: ignore

from PIL import Image, ImageOps

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ingest_photos")

# ── Налаштування майстра ──────────────────────────────────────────────────────
MASTER_MAX_SIDE = int(os.environ.get("PHOTO_MASTER_MAX_SIDE", "1512"))
WEBP_QUALITY = int(os.environ.get("PHOTO_WEBP_QUALITY", "88"))
WEBP_METHOD = 6  # 0..6, 6 = найкраща компресія (повільніше, але разово)

SOURCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".tif", ".tiff"}

DEFAULT_MIRROR_ROOT = os.environ.get(
    "PRODUCT_IMAGES_DIR", os.path.expanduser("~/Downloads/Бізнес/Товар")
)
VALID_CATEGORIES = {"Взуття", "Сумки", "Одяг", "Аксесуари", "Інше"}


def convert_to_webp_master(src_path: Path, dest_path: Path) -> int:
    """Конвертує зображення у WebP-майстер. Повертає розмір результату в байтах.

    - даунскейл до MASTER_MAX_SIDE по довшій стороні (НЕ апскейлить менші);
    - поважає EXIF-орієнтацію, далі EXIF викидається;
    - зберігає прозорість, якщо є (WebP підтримує alpha), інакше RGB.
    """
    with Image.open(src_path) as im:
        im = ImageOps.exif_transpose(im)  # застосувати поворот, прибрати EXIF

        has_alpha = im.mode in ("RGBA", "LA") or (
            im.mode == "P" and "transparency" in im.info
        )
        im = im.convert("RGBA" if has_alpha else "RGB")

        # тільки зменшення (thumbnail не збільшує)
        im.thumbnail((MASTER_MAX_SIDE, MASTER_MAX_SIDE), Image.LANCZOS)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(
            dest_path,
            format="WEBP",
            quality=WEBP_QUALITY,
            method=WEBP_METHOD,
        )
    return dest_path.stat().st_size


def iter_source_images(src: Path):
    """Файли-зображення у пакеті (один рівень + вкладені)."""
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.suffix.lower() in SOURCE_EXTENSIONS:
            # пропускаємо службове сміття
            if p.name.startswith(".") or p.name == ".DS_Store":
                continue
            yield p


def looks_like_product_photo(name: str) -> bool:
    """Груба перевірка: ім'я починається з токена номера (літера/цифра), не сміття."""
    base = os.path.splitext(name)[0].lstrip("#")
    return bool(base) and not base[0].isspace()


def run(
    src: Path,
    category: str,
    *,
    mirror_root: Path,
    do_upload: bool,
    dry_run: bool,
    overwrite: bool,
) -> int:
    if not src.is_dir():
        logger.error(f"❌ Джерело не знайдено: {src}")
        return 2
    if category not in VALID_CATEGORIES:
        logger.warning(
            f"⚠️  Категорія '{category}' не зі стандартних {sorted(VALID_CATEGORIES)} "
            f"— продовжую, але перевір."
        )

    upload_on = do_upload and r2_storage.is_enabled()
    if do_upload and not r2_storage.is_enabled():
        logger.warning("⚠️  R2 не сконфігуровано (.env) — заливку пропущено, лише мірор.")

    images = list(iter_source_images(src))
    if not images:
        logger.error(f"❌ У {src} не знайдено зображень.")
        return 1

    logger.info(
        f"\n📦 Пакет: {src}\n"
        f"   категорія: {category} | майстер ≤{MASTER_MAX_SIDE}px q{WEBP_QUALITY}\n"
        f"   мірор: {mirror_root / category}\n"
        f"   R2: {'УВІМК (' + r2_storage.R2_BUCKET + ')' if upload_on else 'вимкнено'}"
        f"{' | DRY-RUN' if dry_run else ''}\n"
        f"   файлів: {len(images)}\n"
    )

    n_ok = n_skip = n_warn = n_err = 0
    src_bytes = out_bytes = 0

    for p in images:
        stem = p.stem.lstrip("#")  # '#' зрізаємо — чисті ключі/URL (див. normalize)
        out_name = f"{stem}.webp"
        rel_key = f"{category}/{out_name}"
        dest_local = mirror_root / category / out_name

        if not looks_like_product_photo(p.name):
            logger.warning(f"  ⚠️  не схоже на фото товару: {p.name} (пропускаю)")
            n_warn += 1
            continue

        if dest_local.exists() and not overwrite:
            logger.info(f"  ⏭  є локально: {rel_key}")
            n_skip += 1
            continue

        try:
            in_sz = p.stat().st_size
            if dry_run:
                logger.info(f"  · {p.name}  →  {rel_key}  (dry-run)")
                n_ok += 1
                src_bytes += in_sz
                continue

            out_sz = convert_to_webp_master(p, dest_local)
            src_bytes += in_sz
            out_bytes += out_sz

            if upload_on:
                r2_storage.upload_file(str(dest_local), rel_key)
                where = "мірор+R2"
            else:
                where = "мірор"
            logger.info(
                f"  ✓ {p.name}  →  {rel_key}  "
                f"({in_sz // 1024}→{out_sz // 1024} KB, {where})"
            )
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            logger.error(f"  ❌ {p.name}: {e}")
            n_err += 1

    logger.info(
        f"\n── Підсумок ──\n"
        f"  оброблено: {n_ok} | пропущено(є): {n_skip} | "
        f"warn: {n_warn} | помилок: {n_err}"
    )
    if out_bytes:
        logger.info(
            f"  розмір: {src_bytes // 1024 // 1024} MB → {out_bytes // 1024 // 1024} MB "
            f"({100 - int(out_bytes * 100 / max(src_bytes, 1))}% економії)"
        )
    return 0 if n_err == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Ingest фото → WebP → мірор + R2")
    ap.add_argument("--src", required=True, help="папка пакета (розпакований zip)")
    ap.add_argument("--category", required=True, help="Взуття/Сумки/Одяг/Аксесуари/Інше")
    ap.add_argument("--mirror-root", default=DEFAULT_MIRROR_ROOT, help="корінь локального міору")
    ap.add_argument("--no-upload", action="store_true", help="лише локально, без R2")
    ap.add_argument("--dry-run", action="store_true", help="показати план, нічого не писати")
    ap.add_argument("--overwrite", action="store_true", help="перезаписувати наявні")
    args = ap.parse_args()

    rc = run(
        Path(args.src).expanduser(),
        args.category,
        mirror_root=Path(args.mirror_root).expanduser(),
        do_upload=not args.no_upload,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()

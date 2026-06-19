#!/usr/bin/env python3
"""Міграція наявної бібліотеки фото → WebP-майстри + нормалізація імен + R2.

Одноразова (resumable, idempotent, само-виправна) міграція existing-фото у
стандарт project_photo_pipeline. Для кожного зображення в локальному міорі:

  1. НОРМАЛІЗАЦІЯ імені: `<#?Літера><цифри>_<індекс>` (обрізаємо сміттєвий
     хвіст від тулзи: `Ф1080_0001_Слой 1` → `Ф1080_0001`). Індекс не чіпаємо
     (official/real-класифікація зберігається).
  2. якщо номер НЕ розпізнано (дата-префікс, IMG/Snapshot/Gemini, числовий,
     без чистого індексу) → файл у КАРАНТИН `_до_розбору/<кат>/` (оригінал,
     БЕЗ конверсії; ідентифікуєш вручну пізніше → ingest сконвертує).
  3. конверсія у WebP-майстер (≤1512², q88), запис у мірор під норм-іменем.
  4. заливка у R2  <категорія>/<норм>.webp.
  5. оригінал (jpg/...) → бекап `Товар_originals_backup/` (НЕ видаляється).

Само-виправлення: webp із кривою назвою (з попереднього прогону БЕЗ
нормалізації) — перейменовується локально + у R2 (новий ключ, старий видалити).

Нічого не видаляється безповоротно; усе відкатне (backup/quarantine).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import shutil
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")
sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from backend.services import r2_storage
    from backend.scripts.ingest_photos import (
        convert_to_webp_master, SOURCE_EXTENSIONS, VALID_CATEGORIES,
    )
except ImportError:
    sys.path.insert(0, str(_PROJECT_ROOT / "backend"))
    from services import r2_storage  # type: ignore
    from scripts.ingest_photos import (  # type: ignore
        convert_to_webp_master, SOURCE_EXTENSIONS, VALID_CATEGORIES,
    )

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate_library")

MIRROR_ROOT = Path(
    os.environ.get("PRODUCT_IMAGES_DIR", os.path.expanduser("~/Downloads/Бізнес/Товар"))
)
BACKUP_ROOT = Path(os.environ.get(
    "PHOTO_ORIGINALS_BACKUP", os.path.expanduser("~/Downloads/Бізнес/Товар_originals_backup")))
QUARANTINE_ROOT = Path(os.environ.get(
    "PHOTO_QUARANTINE", os.path.expanduser("~/Downloads/Бізнес/Товар_до_розбору")))

# Номер товару = опц '#', ОДНА літера (будь-яка кир/лат, не цифра/підкреслення)
# + 2-5 цифр; далі мусить бути ЧИСТИЙ індекс `_NN` (1-2 цифри) або `_defN`,
# одразу обмежений (кінець / крапка / підкреслення / пробіл). Рішення користувача
# (2026-06-19): формат лише `хххх_0х`; 4-значні `_0000` від тулзи → карантин.
PNUM_RE = re.compile(r"^#?([^\W\d_])(\d{2,5})", re.UNICODE)
IDX_RE = re.compile(r"^_(\d{1,2}|def\d+)(?=$|[._ ])")


def normalize_stem(stem: str) -> Optional[str]:
    """Нормалізоване ім'я (без розширення), або None → карантин."""
    s = stem.strip()
    m = PNUM_RE.match(s)
    if not m:
        return None
    # '#' зрізаємо: псує веб-URL (# = фрагмент); бекенд матчить товар і без нього
    # (_normalize_number робить lstrip('#')). Ключі/URL мають бути чисті.
    pnum = f"{m.group(1)}{m.group(2)}"
    mi = IDX_RE.match(s[m.end():])
    if not mi:
        return None
    return f"{pnum}_{mi.group(1)}"


def _quarantine(p: Path, cat: str, rel: Path, dry: bool, counters: dict, upload_on: bool):
    dest = QUARANTINE_ROOT / cat / rel
    counters["quarantine"] += 1
    if dry:
        return
    # якщо це криво-названий webp із попереднього прогону — прибрати його R2-сироту
    if upload_on and p.suffix.lower() == ".webp":
        old_key = f"{cat}/{p.name}"
        try:
            if r2_storage.object_exists(old_key):
                r2_storage.delete(old_key)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"  ⚠ не вдалось прибрати R2-сироту {old_key}: {e}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest))


def migrate_category(cat: str, *, do_upload: bool, dry: bool, limit: int = 0) -> dict:
    cat_dir = MIRROR_ROOT / cat
    c = dict(conv=0, renamed=0, uploaded=0, quarantine=0, skip=0, collision=0, err=0)
    if not cat_dir.is_dir():
        return c
    upload_on = do_upload and r2_storage.is_enabled()

    files = sorted(
        p for p in cat_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SOURCE_EXTENSIONS and not p.name.startswith(".")
    )
    claimed: dict[str, str] = {}  # норм-ключ → джерело (детект колізій у прогоні)

    for p in files:
        if limit and (c["conv"] + c["renamed"]) >= limit:
            break
        rel = p.relative_to(cat_dir)
        try:
            norm = normalize_stem(p.stem)
            if norm is None:
                _quarantine(p, cat, rel, dry, c, upload_on)
                continue

            # колізія: інший файл уже зайняв цей норм-ключ у цьому прогоні
            if norm in claimed and claimed[norm] != p.stem:
                c["collision"] += 1
                suffix = 2
                while f"{norm}_dup{suffix}" in claimed:
                    suffix += 1
                logger.warning(f"  ⚠ колізія {cat}/{norm} ← {p.name} → +_dup{suffix}")
                norm = f"{norm}_dup{suffix}"
            claimed[norm] = p.stem

            target = cat_dir / f"{norm}.webp"
            key = f"{cat}/{norm}.webp"

            # 1) Файл уже webp
            if p.suffix.lower() == ".webp":
                if p.name == target.name:
                    # правильно названий webp → лише догнати R2
                    if upload_on and not r2_storage.object_exists(key):
                        if not dry:
                            r2_storage.upload_file(str(p), key)
                        c["uploaded"] += 1
                    else:
                        c["skip"] += 1
                else:
                    # криво названий webp (попередній прогін без нормалізації)
                    old_key = f"{cat}/{p.name}"
                    if not dry:
                        if target.exists():
                            p.unlink()  # норм-версія вже є → прибрати дубль
                        else:
                            p.rename(target)
                        if upload_on:
                            if not r2_storage.object_exists(key):
                                r2_storage.upload_file(str(target), key)
                            if r2_storage.object_exists(old_key):
                                r2_storage.delete(old_key)
                    c["renamed"] += 1
                continue

            # 2) Не-webp оригінал
            if target.exists():
                # вже сконвертовано раніше → лише оригінал у бекап + догнати R2
                if upload_on and not r2_storage.object_exists(key) and not dry:
                    r2_storage.upload_file(str(target), key)
            else:
                if dry:
                    c["conv"] += 1
                    continue
                convert_to_webp_master(p, target)
                if upload_on and not r2_storage.object_exists(key):
                    r2_storage.upload_file(str(target), key)
            # оригінал → бекап
            if not dry:
                bkp = BACKUP_ROOT / cat / rel
                bkp.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(bkp))
            c["conv"] += 1
            if c["conv"] % 100 == 0:
                logger.info(f"    …{cat}: {c['conv']} сконвертовано")
        except Exception as e:  # noqa: BLE001
            logger.error(f"  ❌ {rel}: {e}")
            c["err"] += 1

    logger.info(
        f"  {cat}: конв={c['conv']} перейм={c['renamed']} →R2={c['uploaded']} "
        f"карантин={c['quarantine']} skip={c['skip']} колізій={c['collision']} помилок={c['err']}"
    )
    return c


def main():
    ap = argparse.ArgumentParser(description="Міграція бібліотеки → WebP + норм-імена + R2")
    ap.add_argument("--category")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cats = [args.category] if args.category else list(VALID_CATEGORIES)
    upload_on = (not args.no_upload) and r2_storage.is_enabled()
    logger.info(
        f"\n🔁 Міграція → WebP{' + R2' if upload_on else ' (локально)'}"
        f"{' | DRY-RUN' if args.dry_run else ''}\n"
        f"   мірор:    {MIRROR_ROOT}\n   бекап:    {BACKUP_ROOT}\n"
        f"   карантин: {QUARANTINE_ROOT}\n"
    )
    tot: dict[str, int] = {}
    for cat in cats:
        r = migrate_category(cat, do_upload=not args.no_upload, dry=args.dry_run, limit=args.limit)
        for k, v in r.items():
            tot[k] = tot.get(k, 0) + v
    logger.info(
        f"\n── РАЗОМ ──\n  конв={tot.get('conv',0)} перейм={tot.get('renamed',0)} "
        f"→R2={tot.get('uploaded',0)} карантин={tot.get('quarantine',0)} "
        f"skip={tot.get('skip',0)} колізій={tot.get('collision',0)} помилок={tot.get('err',0)}"
    )
    sys.exit(0 if tot.get("err", 0) == 0 else 1)


if __name__ == "__main__":
    main()

"""Пошук фотографій товару за productnumber.

Зараз: локальна папка (default `~/Downloads/Бізнес/Товар/Взуття`).
Майбутнє: cloud (Google Drive тощо) — реалізується через інший провайдер
з тією ж сигнатурою `list_images(productnumber) -> List[ImageEntry]`.
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_IMAGES_DIR = os.path.expanduser("~/Downloads/Бізнес/Товар/Взуття")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
URL_PREFIX = "/product-images"  # match StaticFiles mount in app/main.py


def get_images_dir() -> str:
    return os.environ.get("PRODUCT_IMAGES_DIR", DEFAULT_IMAGES_DIR)


@dataclass
class ImageEntry:
    filename: str
    url: str
    index: int  # порядковий номер у галереї (0 = головна)


def _normalize_number(productnumber: str) -> str:
    """Повертає очищений номер: без `#` префіксу, trim."""
    if not productnumber:
        return ""
    return productnumber.strip().lstrip("#").strip()


def _matches_productnumber(filename: str, target: str) -> bool:
    """Перевірка чи файл належить товару.

    Файл матчить, якщо починається з `target` або `#target`,
    і одразу після номера йде розділювач (_, ., -, пробіл) або кінець basename.
    Так `Ф31` НЕ матчить `Ф310`, `Ф3108` тощо.
    """
    if not target:
        return False
    base = os.path.splitext(filename)[0]
    pattern = rf"^#?{re.escape(target)}(?=[_.\-\s]|$)"
    return bool(re.match(pattern, base))


def _sort_key(filename: str, target: str) -> tuple:
    """Натуральний порядок: витягуємо першу числову послідовність ПІСЛЯ номера товару.
    Файли без числового суфіксу йдуть в кінець (індекс = inf).
    Tie-breaker: повне ім'я файлу alphabetically.
    """
    base = os.path.splitext(filename)[0]
    # видаляємо префікс # та сам номер товару
    stripped = re.sub(rf"^#?{re.escape(target)}", "", base)
    m = re.search(r"\d+", stripped)
    if m:
        return (0, int(m.group(0)), base.lower())
    return (1, 0, base.lower())


def list_images(productnumber: str) -> List[ImageEntry]:
    """Повертає список фото товару, відсортований так щоб
    найменший суфіксний номер був першим (головне фото).
    """
    target = _normalize_number(productnumber)
    if not target:
        return []

    images_dir = get_images_dir()
    if not os.path.isdir(images_dir):
        logger.debug(f"Images dir not found: {images_dir}")
        return []

    matched: List[str] = []
    try:
        for entry in os.scandir(images_dir):
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue
            if _matches_productnumber(entry.name, target):
                matched.append(entry.name)
    except OSError as e:
        logger.warning(f"Failed to scan images dir {images_dir}: {e}")
        return []

    matched.sort(key=lambda fn: _sort_key(fn, target))

    return [
        ImageEntry(
            filename=fn,
            url=f"{URL_PREFIX}/{quote(fn)}",
            index=i,
        )
        for i, fn in enumerate(matched)
    ]

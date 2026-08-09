"""Збереження файлів у теку «Завантаження» користувача.

Навіщо це на бекенді. Застосунок працює у вбудованому вебв'ю (PyWebView:
WKWebView на macOS, WebView2 на Windows), а там **атрибут `<a download>` не
працює**. Клік по такому посиланню не зберігає файл, а переходить на нього:
фото розгортається на весь екран поверх SPA і застосунком не можна далі
користуватись, доки не перезапустиш. Архів (application/zip) вебв'ю показати не
може, тож там просто нічого не відбувається — «ніби процес пішов і зник».

Оскільки бекенд у десктоп-режимі працює на ТІЙ САМІЙ машині, що й вікно,
надійний шлях — записати файл напряму в «Завантаження» і повернути шлях, щоб UI
показав людині, куди саме збережено.

У режимі звичайного браузера (не десктоп) цей шлях НЕ використовується — там
працює штатне завантаження засобами браузера.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Символи, неприпустимі в іменах файлів на Windows (на macOS проблемний лише '/',
# але тримаємо єдине правило — файли переносяться між машинами).
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def downloads_dir() -> Path:
    """Тека «Завантаження» поточного користувача.

    Перевизначається через BMS_DOWNLOADS_DIR. Якщо стандартної теки немає
    (рідкісні конфігурації) — падаємо на домашню теку, а не на помилку:
    користувач має отримати файл, навіть якщо не в ідеальному місці.
    """
    override = os.getenv("BMS_DOWNLOADS_DIR")
    if override:
        p = Path(override).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    home = Path.home()
    candidate = home / "Downloads"
    if sys.platform.startswith("win"):
        # На Windows теку можуть перенести; USERPROFILE\Downloads — стандарт.
        profile = os.getenv("USERPROFILE")
        if profile:
            candidate = Path(profile) / "Downloads"
    if candidate.is_dir():
        return candidate
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        logger.warning("Тека завантажень недоступна, зберігаю в домашню теку")
        return home


def safe_filename(name: str, fallback: str = "file") -> str:
    """Ім'я файлу, безпечне для запису на диск (без шляхів і службових символів)."""
    base = os.path.basename((name or "").strip())
    base = _UNSAFE_CHARS.sub("_", base).strip(" .")
    return base or fallback


def _unique_path(directory: Path, filename: str) -> Path:
    """Шлях, що не перетирає наявний файл: `назва.webp` → `назва (2).webp`.

    Мовчки перезаписати чужий файл у «Завантаженнях» — гірше, ніж зберегти
    копію: користувач міг качати ті самі фото свідомо, для порівняння.
    """
    target = directory / filename
    if not target.exists():
        return target
    stem, ext = os.path.splitext(filename)
    for i in range(2, 1000):
        candidate = directory / f"{stem} ({i}){ext}"
        if not candidate.exists():
            return candidate
    # Практично недосяжно; краще перезаписати, ніж впасти.
    return target


def save_bytes(data: bytes, filename: str, fallback_name: str = "file") -> Tuple[str, str]:
    """Записати байти у «Завантаження». Повертає (повний шлях, підсумкове ім'я)."""
    directory = downloads_dir()
    path = _unique_path(directory, safe_filename(filename, fallback_name))
    # Пишемо через тимчасовий файл + rename, щоб перерваний запис не лишив
    # напівфайл із правильним іменем (користувач відкрив би «битий» архів).
    tmp = path.with_name(path.name + ".part")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    logger.info(f"Збережено {len(data)} байт → {path}")
    return str(path), path.name

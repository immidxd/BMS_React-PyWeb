"""Мініатюри фото товару: генерація на льоту + диск-кеш.

Навіщо. Галерея картки, стрічка прев'ю, плитки менеджера і швидкий перегляд
показують фото розміром 64–320 px, але тягнули ОРИГІНАЛ: локальний webp ≈100 КБ
(p90 187 КБ), а фото з Drive — ≈800 КБ. Картка з 8 фото = кілька мегабайт на
кожне відкриття, і саме ці секунди виглядають як «фото не вантажаться».

Мініатюра тих самих 8 фото — десятки кілобайт разом.

Принципи:
  • кеш на диску (`~/.cache/bms_thumbs`), ключ включає ідентичність джерела —
    для локального файлу це mtime+size, тож заміна фото під тією ж назвою
    автоматично дає новий ключ (застаріла мініатюра не «прилипає»);
  • промах кешу коштує один decode+resize; влучання — просто читання файлу;
  • будь-який збій → None, і роут віддає оригінал. Мініатюри — прискорення,
    а не новий спосіб втратити фото.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

THUMB_CACHE_DIR = os.path.expanduser(
    os.environ.get("PRODUCT_THUMBS_CACHE_DIR", "~/.cache/bms_thumbs")
)

# Дозволені ширини — закритий список, щоб довільний `?w=` не роздував кеш
# нескінченною кількістю варіантів. Значення підібрані під реальні місця:
#   96  — плитки менеджера фото і стрічка мініатюр (64 px @2x)
#   320 — швидкий перегляд при наведенні (264 px)
#   640 — прогресивний плейсхолдер під головним фото картки
ALLOWED_WIDTHS = (96, 320, 640)
DEFAULT_WIDTH = 320

_WEBP_QUALITY = 82
_dir_lock = threading.Lock()


def normalize_width(w: Optional[int]) -> int:
    """Найближча дозволена ширина (не менша за запитану, якщо така є)."""
    if not w:
        return DEFAULT_WIDTH
    for allowed in ALLOWED_WIDTHS:
        if w <= allowed:
            return allowed
    return ALLOWED_WIDTHS[-1]


def _cache_path(key: str, width: int) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    # Підпапка за перші 2 символи — щоб не робити одну теку на десятки тисяч файлів.
    return os.path.join(THUMB_CACHE_DIR, str(width), digest[:2], f"{digest}.webp")


def _read_cached(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _write_cached(path: str, data: bytes) -> None:
    try:
        with _dir_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        # Запис через тимчасовий файл + rename: паралельні запити на ту саму
        # мініатюру ніколи не дадуть половинчастий файл іншому читачеві.
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"Thumb cache write failed for {path}: {e}")


def _render(raw: bytes, width: int) -> Optional[bytes]:
    """Оригінальні байти → WebP-мініатюра шириною `width` (пропорційно)."""
    try:
        import io
        from PIL import Image, ImageOps
    except ImportError:
        logger.warning("Pillow недоступний — мініатюри вимкнено")
        return None

    try:
        with Image.open(io.BytesIO(raw)) as im:
            # exif_transpose: фото з телефона мають орієнтацію в EXIF, інакше
            # мініатюра лежала б на боці, а оригінал — ні.
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            if im.width > width:
                height = max(1, round(im.height * width / im.width))
                im = im.resize((width, height), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="WEBP", quality=_WEBP_QUALITY, method=4)
            return out.getvalue()
    except Exception as e:  # битий файл, невідомий формат — не наша проблема
        logger.info(f"Thumb render failed: {e}")
        return None


def thumb_for_local(abs_path: str, width: int) -> Optional[bytes]:
    """Мініатюра локального файлу. Ключ кешу враховує mtime+size."""
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    key = f"local:{abs_path}:{int(st.st_mtime)}:{st.st_size}"
    path = _cache_path(key, width)
    cached = _read_cached(path)
    if cached is not None:
        return cached
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    data = _render(raw, width)
    if data:
        _write_cached(path, data)
    return data


def thumb_for_drive(file_id: str, width: int) -> Optional[bytes]:
    """Мініатюра файлу з Drive. Оригінал бере з існуючого байтового диск-кешу
    Drive-провайдера (а той сам вирішує, качати чи віддати з кешу)."""
    key = f"drive:{file_id}"
    path = _cache_path(key, width)
    cached = _read_cached(path)
    if cached is not None:
        return cached
    try:
        try:
            from services.product_images_drive import get_drive_file_bytes
        except ImportError:
            from backend.services.product_images_drive import get_drive_file_bytes
    except ImportError:
        return None
    result = get_drive_file_bytes(file_id)
    if not result:
        return None
    data = _render(result[0], width)
    if data:
        _write_cached(path, data)
    return data


def cache_stats() -> Tuple[int, int]:
    """(кількість файлів, сумарний розмір у байтах) — для діагностики."""
    count = 0
    size = 0
    for root, _dirs, files in os.walk(THUMB_CACHE_DIR):
        for fn in files:
            if not fn.endswith(".webp"):
                continue
            try:
                size += os.path.getsize(os.path.join(root, fn))
                count += 1
            except OSError:
                pass
    return count, size

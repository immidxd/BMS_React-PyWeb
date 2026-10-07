"""Експорт фото товарів назовні: одразу PNG і пакетом по кількох товарах.

Майстри фото зберігаються у WebP (легкі, для R2 і вітрини), але людині для
роботи поза програмою (месенджери, редактори, маркетплейси) зручніше PNG —
тому все, що BMS віддає «зберегти/завантажити», конвертується тут.

Пакетний архів пишеться у ФАЙЛ, а не в пам'ять: PNG ~1 МБ на фото, і пакет на
кількадесят товарів — сотні мегабайт. Байти з R2 тягнемо паралельно (мережа —
найповільніше), конвертуємо й пишемо в архів послідовно, у порядку галереї.
"""
from __future__ import annotations

import io
import logging
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FORMATS = ("png", "original")
FETCH_WORKERS = 6


def to_png(data: bytes) -> bytes:
    """WebP/JPEG → PNG без втрат якості (прозорість зберігається, якщо є)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        out = io.BytesIO()
        # compress_level=3: розмір майже як у 6, а швидше вдвічі.
        im.save(out, "PNG", compress_level=3)
        return out.getvalue()


def export_name(filename: str, fmt: str) -> str:
    if fmt == "png":
        return os.path.splitext(filename)[0] + ".png"
    return filename


def export_bytes(data: bytes, fmt: str) -> bytes:
    return to_png(data) if fmt == "png" else data


def write_zip(
    fileobj,
    items: Sequence[Tuple[str, object]],
    read_bytes: Callable[[object], Optional[bytes]],
    fmt: str,
) -> Tuple[int, List[str]]:
    """Записати архів у `fileobj`. items = [(ім'я в архіві, джерело)].

    Повертає (скільки запаковано, імена, які не вдалося прочитати). PNG уже
    стиснутий — ZIP_STORED (deflate тут лише марнував би час).
    """
    compression = zipfile.ZIP_STORED if fmt == "png" else zipfile.ZIP_DEFLATED
    packed, failed = 0, []
    used: set = set()

    def fetch(item):
        arcname, src = item
        try:
            data = read_bytes(src)
            return arcname, (export_bytes(data, fmt) if data is not None else None)
        except Exception as e:  # noqa: BLE001 — одне бите фото не валить архів
            logger.warning("Експорт фото %s не вдався: %s", arcname, e)
            return arcname, None

    with zipfile.ZipFile(fileobj, "w", compression=compression) as zf, \
            ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        # map зберігає порядок; наперед тягне паралельно.
        for arcname, data in pool.map(fetch, items):
            if data is None:
                failed.append(arcname)
                continue
            name = arcname
            if name.lower() in used:  # колізія (власне фото й донорське)
                stem, ext = os.path.splitext(name)
                i = 2
                while f"{stem}_{i}{ext}".lower() in used:
                    i += 1
                name = f"{stem}_{i}{ext}"
            used.add(name.lower())
            zf.writestr(name, data)
            packed += 1
    return packed, failed


def folder_name(productnumber: str, product_id: int) -> str:
    """Тека товару в пакетному архіві: номер без «#» (безпечний для ФС)."""
    try:
        from services.file_saver import safe_filename
    except ImportError:  # pragma: no cover
        from backend.services.file_saver import safe_filename
    stem = (productnumber or "").lstrip("#").strip()
    return safe_filename(stem, f"product-{product_id}")


def pick(images: Iterable, kind: str) -> list:
    """Фото для пакета: official / real / all (= офіційні + реальні).

    Приховані й дефекти в пакет не йдуть: пакет — для роботи назовні, а
    сховане власник свідомо прибрав з показу, дефекти — внутрішні.
    """
    out = []
    for img in images:
        if getattr(img, "hidden", False):
            continue
        k = getattr(img, "kind", "official")
        if kind == "all" and k in ("official", "real"):
            out.append(img)
        elif k == kind:
            out.append(img)
    return out

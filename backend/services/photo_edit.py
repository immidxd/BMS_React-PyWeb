"""Кадрування фото товару (1:1) і поворот — до того, як знімок стане майстром.

Навіщо. Знімки приходять різних форматів (вертикальні з телефона, широкі
студійні), а в картці, каталозі й маркетплейсах стандарт — квадрат. Кадр
вибирає людина в редакторі (як «Обрізати» в галереї iPhone), а сюди приходить
лише опис кадру; самі пікселі ріжемо тут, з ОРИГІНАЛУ, тож у R2 лягає вже
квадратна версія повної якості, а не скріншот прев'ю.

Опис кадру (`edit`) — нормалізовані координати, незалежні від розміру прев'ю:

    {"rotate": 0|90|180|270,                # за годинниковою, ДО обрізки
     "crop": {"x": ..., "y": ..., "w": ..., "h": ...}}   # частки повороненого кадру

`x/y/w/h` відраховуються від лівого верхнього кута ВЖЕ поверненого знімка
(після EXIF-орієнтації — так само, як його показує браузер). Рамка може
виходити за межі кадру: тоді поле доповнюється білим (прозорим для PNG з
альфою) — так вертикальне взуття влазить у квадрат цілим, без обрізаних носків.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from PIL import Image

ROTATIONS = (0, 90, 180, 270)
# Рамка може вилазити за кадр (доповнення полями), але не безмежно: 3× по
# стороні — уже не кадрування, а помилка в розрахунку на фронті.
_MAX_SPAN = 3.0
_MAX_OFFSET = 2.0


def parse_edit(raw: Any) -> Optional[Dict[str, Any]]:
    """Перевірити й нормалізувати опис кадру. None — «без змін».

    Кидає ValueError на сміття: краще відмовити, ніж мовчки зберегти кадр,
    якого людина не вибирала.
    """
    if raw is None or raw == {} or raw == "":
        return None
    if not isinstance(raw, dict):
        raise ValueError("Опис кадру має бути об'єктом")
    rotate = raw.get("rotate", 0) or 0
    try:
        rotate = int(rotate) % 360
    except (TypeError, ValueError):
        raise ValueError(f"Некоректний поворот: {raw.get('rotate')!r}")
    if rotate not in ROTATIONS:
        raise ValueError(f"Поворот лише на 90°: {rotate}")

    crop = raw.get("crop")
    out_crop = None
    if crop is not None:
        if not isinstance(crop, dict):
            raise ValueError("crop має бути об'єктом")
        try:
            x, y, w, h = (float(crop[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise ValueError("crop потребує чисел x, y, w, h")
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            raise ValueError("crop містить нечислові значення")
        if not (0 < w <= _MAX_SPAN and 0 < h <= _MAX_SPAN):
            raise ValueError("Розмір рамки поза межами")
        if not (-_MAX_OFFSET <= x <= _MAX_OFFSET and -_MAX_OFFSET <= y <= _MAX_OFFSET):
            raise ValueError("Положення рамки поза межами")
        # Рамка мусить хоч трохи накривати знімок — інакше вийде порожній квадрат.
        if x >= 1 or y >= 1 or x + w <= 0 or y + h <= 0:
            raise ValueError("Рамка не накриває знімок")
        out_crop = {"x": x, "y": y, "w": w, "h": h}

    if rotate == 0 and out_crop is None:
        return None
    return {"rotate": rotate, "crop": out_crop}


_TRANSPOSE_CW = {
    90: Image.Transpose.ROTATE_270,   # PIL крутить проти годинникової
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def apply_edit(im: Image.Image, edit: Optional[Dict[str, Any]]) -> Image.Image:
    """Застосувати кадр до вже EXIF-повернутого знімка (RGB або RGBA)."""
    if not edit:
        return im
    rotate = edit.get("rotate") or 0
    if rotate:
        im = im.transpose(_TRANSPOSE_CW[rotate])
    crop = edit.get("crop")
    if not crop:
        return im

    W, H = im.size
    left = round(crop["x"] * W)
    top = round(crop["y"] * H)
    cw = max(1, round(crop["w"] * W))
    ch = max(1, round(crop["h"] * H))
    # Квадрат має бути квадратом і після округлення: фронт рахує w·W == h·H,
    # але дві окремі округлені сторони можуть розійтись на піксель.
    if abs(cw - ch) <= 2:
        cw = ch = min(cw, ch)

    if left >= 0 and top >= 0 and left + cw <= W and top + ch <= H:
        return im.crop((left, top, left + cw, top + ch))

    # Рамка ширша за кадр — поля. `Image.crop` залив би їх чорним.
    fill = (255, 255, 255, 0) if im.mode == "RGBA" else (255, 255, 255)
    canvas = Image.new(im.mode, (cw, ch), fill)
    canvas.paste(im, (-left, -top))
    return canvas

"""Стікери товарів із QR для складу: рендер, розкладка на аркуш, черга, друк.

Що таке стікер. Стандартизована бірка на товар: QR з ідентифікатором + номер,
розмір, бренд/модель, вид·колір·стать·сезон, (опційно) ціна. Її сканують
телефоном на складі (Telegram Mini App), щоб відкрити товар і покласти/вийняти
з коробки. Стікер унікальний для РЯДКА товару, а не для номера: один номер
може бути ростовкою (кілька рядків із різним розміром) або належати різним
товарам — див. памʼять product-identity-key-trap.

QR-навантаження: `bms:p:<products.id>:<productnumber>` — id як ключ, номер як
резерв на випадок, якщо рядок зникне (злиття фантомів, перейменування номера).
Номер кладемо ЯК У БАЗІ (з `#`), бо 116 історичних номерів без `#` — інші
товари. На стікері для людини `#` прибираємо: так пишуть зелені бірки й аркуш.

Аркуш. Принтер — термо 100×100 мм (Xprinter, 203 dpi), стікери ріжуться з
аркуша по 2×2 / 2×3 / 3×3 (колонки × рядки). Сторінка рендериться як растр
1-біт (термодрук і так чорно-білий; у PDF це CCITT G4 — без JPEG-артефактів на
QR). Новий принтер під окремі наклейки — це інший `LayoutSpec` з іншим
розміром носія, конвеєр той самий.

Кількість копій. Рядок із quantity>1 — кілька фізичних пар → стільки ж
стікерів. За замовчуванням копій = наявних (quantity − продано), бо на
продану пару стікер не потрібен; 0 наявних → позначаємо `sold`, копій 0,
користувач може задати вручну.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DPI = 203  # Xprinter 4" — 8 точок/мм

QR_PREFIX_PRODUCT = "bms:p:"
QR_PREFIX_BOX = "bms:b:"


# ───────────────────────────── розкладка ─────────────────────────────────────

@dataclass(frozen=True)
class LayoutSpec:
    key: str
    cols: int
    rows: int
    label: str
    media_w_mm: float = 100.0
    media_h_mm: float = 100.0
    margin_mm: float = 2.0   # зовнішнє поле аркуша (термопринтери не друкують край)
    gap_mm: float = 2.0      # проміжок між стікерами — сюди йде лінія різу

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    def cell_mm(self) -> Tuple[float, float]:
        w = (self.media_w_mm - 2 * self.margin_mm - (self.cols - 1) * self.gap_mm) / self.cols
        h = (self.media_h_mm - 2 * self.margin_mm - (self.rows - 1) * self.gap_mm) / self.rows
        return w, h

    def to_dict(self) -> Dict[str, Any]:
        w, h = self.cell_mm()
        return {
            "key": self.key, "label": self.label, "cols": self.cols, "rows": self.rows,
            "per_page": self.per_page, "sticker_mm": [round(w, 1), round(h, 1)],
            "media_mm": [self.media_w_mm, self.media_h_mm],
        }


LAYOUTS: Dict[str, LayoutSpec] = {
    "2x2": LayoutSpec("2x2", 2, 2, "4 на аркуш · 47×47 мм"),
    "2x3": LayoutSpec("2x3", 2, 3, "6 на аркуш · 47×31 мм"),
    "3x3": LayoutSpec("3x3", 3, 3, "9 на аркуш · 31×31 мм"),
}
DEFAULT_LAYOUT = "2x2"


def get_layout(key: Optional[str]) -> LayoutSpec:
    spec = LAYOUTS.get((key or DEFAULT_LAYOUT).strip().lower())
    if not spec:
        raise ValueError(f"Невідома розкладка «{key}». Доступні: {', '.join(LAYOUTS)}")
    return spec


def mm_px(mm: float, dpi: int = DPI) -> int:
    return int(round(mm / 25.4 * dpi))


# ───────────────────────────── шрифти ────────────────────────────────────────

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _font_candidates(bold: bool) -> List[str]:
    """Roboto з репозиторію → системні шрифти з кирилицею → вбудований Pillow."""
    ours = _FONT_DIR / ("Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf")
    cands = [str(ours)]
    if sys.platform == "darwin":
        cands += [f"/System/Library/Fonts/Supplemental/{'Arial Bold' if bold else 'Arial'}.ttf"]
    elif sys.platform.startswith("win"):
        windir = os.getenv("WINDIR", r"C:\Windows")
        cands += [os.path.join(windir, "Fonts", "arialbd.ttf" if bold else "arial.ttf")]
    else:
        cands += [f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"]
    return cands


@lru_cache(maxsize=256)
def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(6, int(size))
    for path in _font_candidates(bold):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    logger.warning("Шрифт із кирилицею не знайдено — стікери буде надруковано вбудованим шрифтом")
    return ImageFont.load_default(size=size)


def _text_w(font, s: str) -> float:
    return font.getlength(s)


def _fit_font(bold: bool, s: str, max_size: int, max_w: int, min_size: int = 8):
    """Найбільший кегль, за якого рядок вміщується по ширині."""
    size = max_size
    while size > min_size:
        f = _font(bold, size)
        if _text_w(f, s) <= max_w:
            return f
        size -= max(1, size // 12)
    return _font(bold, min_size)


SEG = " · "


def _fit_segments(font, s: str, max_w: int) -> str:
    """Вмістити рядок «бренд · модель · …» без «…»: сегменти відкидаються
    ЦІЛКОМ з кінця, доки решта не вміститься. Жодних обрізаних слів на бірці —
    краще без моделі, ніж «Trekking Ul…». Порожній рядок = не малювати."""
    parts = [p for p in s.split(SEG) if p]
    while parts and _text_w(font, SEG.join(parts)) > max_w:
        parts.pop()
    return SEG.join(parts)


# ───────────────────────────── елемент стікера ───────────────────────────────

@dataclass
class LabelItem:
    payload: str                 # текст у QR
    number: str                  # великий рядок (номер без «#»)
    size: str = ""               # «EU 40» / «XL»
    insole: str = ""             # «26 см» (устілка) / «Г 48 · Д 66» (заміри одягу)
    line1: str = ""              # бренд · модель
    line2: str = ""              # вид · колір · стать · сезон
    price: Optional[str] = None  # «1 200 ₴» (друкується лише за show_price)
    condition: str = ""          # «Новий» / «Вживаний» … (поточний стан)
    copies: int = 1
    product_id: Optional[int] = None
    productnumber: Optional[str] = None


def qr_payload_product(product_id: int, productnumber: str) -> str:
    return f"{QR_PREFIX_PRODUCT}{int(product_id)}:{(productnumber or '').strip()}"


def qr_payload_box(code: str) -> str:
    return f"{QR_PREFIX_BOX}{(code or '').strip()}"


def parse_payload(raw: str) -> Optional[Dict[str, Any]]:
    """Розібрати скан: `bms:p:<id>:<номер>` → {kind:'product', id, number};
    `bms:b:<код>` → {kind:'box', code}. Чуже — None."""
    s = (raw or "").strip()
    if s.startswith(QR_PREFIX_PRODUCT):
        body = s[len(QR_PREFIX_PRODUCT):]
        pid, _, number = body.partition(":")
        if pid.isdigit():
            return {"kind": "product", "id": int(pid), "number": number or None}
        return None
    if s.startswith(QR_PREFIX_BOX):
        code = s[len(QR_PREFIX_BOX):].strip()
        return {"kind": "box", "code": code} if code else None
    return None


def display_number(productnumber: Optional[str]) -> str:
    return (productnumber or "").strip().lstrip("#").strip()


def _cap(s: str) -> str:
    """Довідники в базі з малої («чорний») — на бірці перша велика."""
    return (s[:1].upper() + s[1:]) if s else s


_GENDER_ABBR = {"жіноче": "Ж", "чоловіче": "Ч", "унісекс": "У", "дитяче": "Д"}


def _season_short(season: Optional[str]) -> str:
    if not season:
        return ""
    parts = [p.strip() for p in re.split(r"[,/;]+", season) if p.strip()]
    parts = [("Демі" if p.lower().startswith("демі") else p) for p in parts]
    return "/".join(parts[:2])


def _size_text(row: Dict[str, Any]) -> str:
    eu = (row.get("sizeeu") or "").strip()
    letter = (row.get("size_letter") or "").strip()
    ua = (row.get("sizeua") or "").strip()
    if eu:
        return f"EU {eu}"
    if letter:
        return letter
    if ua:
        return f"UA {ua}"
    return ""


def _insole_text(row: Dict[str, Any]) -> str:
    cm = (row.get("measurementscm") or "").strip()
    return f"{cm} см" if cm else ""


# Заміри одягу на бірці: груди · талія · бедра · рукав · довжина (н/о — половина
# кола, як у картці). Одна літера замість «о/г»: на стікері 47×31 мм місця
# рівно на «Г 48 · Т 36 · Б 50 · Д 95» в один рядок.
_CLOTHING_MEASURE_LABELS = (
    ("pog", "Г"), ("pot", "Т"), ("pob", "Б"), ("sleeve", "Р"), ("length", "Д"),
)


def _fmt_cm(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{f:g}"


def _clothing_measure_text(row: Dict[str, Any]) -> str:
    """«Г 48 · Д 66» для одягу; для решти — "" (там слот зайнятий устілкою).

    Показуємо ВСІ заповнені заміри, а не лише «типові» для підкатегорії: якщо
    хтось виміряв рукав у сукні, він потрібен на бірці так само."""
    try:
        from services.product_category import category_of
    except ImportError:  # pragma: no cover
        from backend.services.product_category import category_of
    if category_of(row.get("typename")) != "clothing":
        return ""
    parts = []
    for key, abbr in _CLOTHING_MEASURE_LABELS:
        lo = _fmt_cm(row.get(f"measurements_{key}_min"))
        hi = _fmt_cm(row.get(f"measurements_{key}_max"))
        val = lo or hi
        if lo and hi and lo != hi:
            val = f"{lo}-{hi}"
        if val:
            parts.append(f"{abbr} {val}")
    return SEG.join(parts)


def _price_text(price: Any) -> Optional[str]:
    try:
        v = float(price or 0)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    s = f"{int(round(v)):,}".replace(",", " ")
    return f"{s} ₴"


def item_from_row(row: Dict[str, Any], copies: Optional[int] = None) -> LabelItem:
    gender = (row.get("gendername") or "").strip().lower()
    # Довідники в базі з малої («чорний», «кросівки») — на бірці кожна частина з великої.
    line2 = " · ".join(p for p in [
        _cap((row.get("typename") or "").strip()),
        _cap((row.get("colorname") or "").strip()),
        _GENDER_ABBR.get(gender, ""),
        _cap(_season_short(row.get("season"))),
    ] if p)
    line1 = " · ".join(p for p in [
        (row.get("brandname") or "").strip(),
        (row.get("model") or "").strip(),
    ] if p)
    condition = _cap(((row.get("current_condition_name") or row.get("condition_name")) or "").strip())
    return LabelItem(
        payload=qr_payload_product(row["id"], row["productnumber"]),
        number=display_number(row.get("productnumber")),
        size=_size_text(row),
        insole=_clothing_measure_text(row) or _insole_text(row),
        line1=line1,
        line2=line2,
        price=_price_text(row.get("price")),
        condition=condition,
        copies=int(copies if copies is not None else 1),
        product_id=int(row["id"]),
        productnumber=row.get("productnumber"),
    )


# ───────────────────────────── рендер ────────────────────────────────────────

def _make_qr(payload: str, target_px: int) -> Image.Image:
    """QR рівно під target_px (квадрат), модуль — ціле число точок принтера."""
    import qrcode  # локальний імпорт: важка залежність лише тут

    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
    q.add_data(payload)
    q.make(fit=True)
    modules = q.modules_count + 2 * q.border
    # Модуль — ціле число точок принтера (інакше растеризація «пливе»). Округлюємо
    # до найближчого, дозволяючи трохи перевищити ціль; <3 точок на модуль
    # термодрук уже не гарантує читання.
    box = max(3, int(round(target_px / modules)))
    if box < 4 and 4 * modules <= target_px * 1.25:
        box = 4  # 4 точки на модуль — нижня межа впевненого читання
    if box * modules > target_px * 1.25:
        box = max(3, target_px // modules)
    q.box_size = box
    img = q.make_image(fill_color="black", back_color="white").get_image().convert("L")
    return img


def _ink(font, text: str) -> Tuple[int, int, int, int]:
    """Фактична рамка чорнила (l, t, r, b) відносно точки малювання."""
    return font.getbbox(text)


def _ink_h(font, text: str) -> int:
    l, t, r, b = _ink(font, text)
    return max(1, b - t)


def _draw_tight(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font) -> int:
    """Намалювати так, щоб ЧОРНИЛО починалось рівно в (x, y) — без «повітря»
    ascender-а над великими літерами. Повертає висоту чорнила."""
    l, t, r, b = _ink(font, text)
    draw.text((x - l, y - t), text, font=font, fill=0)
    return b - t


@lru_cache(maxsize=64)
def _column_style(cw: int, hq: int, lgap: int):
    """Сталі кеглі й висоти слотів колонки для (ширина колонки, висота QR).

    Слоти: розмір · устілка · стан · ціна — завжди в цьому порядку й на тих
    самих місцях. Кеглі підібрані під НАЙДОВШІ реальні значення («EU 40.5»,
    «29-29.5 см», «Легковживаний», «12 900 ₴»), тож на всіх стікерах однієї
    розкладки текст однакового розміру, а не «де більше — де менше».
    """
    refs = [("EU 40.5", True, 0.36), ("29-29.5 см", False, 0.20),
            ("Легковживаний", False, 0.20), ("12 900 ₴", True, 0.27)]
    scale = 1.0
    for _ in range(10):
        fonts = [_fit_font(bold, ref, max(7, int(hq * k * scale)), cw, min_size=7)
                 for ref, bold, k in refs]
        heights = [_ink_h(f, ref) for f, (ref, _b, _k) in zip(fonts, refs)]
        if sum(heights) + lgap * (len(refs) - 1) <= hq:
            break
        scale *= 0.92
    return tuple(fonts), tuple(heights)


def _draw_cell(page: Image.Image, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int],
               item: LabelItem, show_price: bool) -> None:
    """Компонування стікера — три смуги в рамці, одна ліва вісь:

        ╭────────────────────────────╮
        │ Ф2264                      │  1. номер: на всю ширину, найбільший
        │ ───────────────────────────│
        │ ▣▣▣▣▣   EU 37.5            │  2. QR ліворуч; праворуч — сталі слоти:
        │ ▣ QR ▣  24 см              │     розмір · устілка · стан · ціна
        │ ▣▣▣▣▣   Новий              │     (кеглі сталі; порожній слот
        │         2 550 ₴            │     стягується — без дірок)
        │ ───────────────────────────│
        │ New Balance · 410          │  3. опис: бренд·модель, вид·колір·стать
        │ Кросівки · Сірий · Ж       │     (стільки рядків, скільки вміщується)
        ╰────────────────────────────╯
    Висоти рахуються за фактичним чорнилом; вільне місце ділиться порівну
    між смугами. Номер лише стискається під ширину, описові рядки —
    відкидають цілі сегменти (жодних «…»).
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < mm_px(14) or h < mm_px(14):
        return

    # Рамка — надає стікеру форму бірки й підказує, де різати.
    fr = 2
    draw.rounded_rectangle((x0 + fr, y0 + fr, x1 - fr - 1, y1 - fr - 1),
                           radius=mm_px(1.6), outline=0, width=2)
    pad = mm_px(2.2)
    ix0, iy0, ix1, iy1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
    iw, ih = ix1 - ix0, iy1 - iy0
    lgap = mm_px(1.1)         # між рядками в межах смуги (за чорнилом)
    band_gap = mm_px(1.6)     # мінімум між смугами (тут же лягає розділювач)

    # ── 1. Номер ────────────────────────────────────────────────────────────
    number = item.number or "?"
    # У великих клітинках номер — 30 % висоти (≈10 мм великих літер); у малих
    # 26 %, інакше не лишається рядка на бренд.
    f_num = _fit_font(True, number, int(h * (0.30 if h >= mm_px(40) else 0.26)), iw, min_size=9)
    num_h = _ink_h(f_num, number)

    # ── 3. Опис: цілими сегментами, без «…»; скільки рядків вміщується,
    #      лишаючи QR не меншим за мінімум ──────────────────────────────────
    footer: List[Tuple[str, Any]] = []
    for text_, sz in [(item.line1, int(h * 0.088)), (item.line2, int(h * 0.08))]:
        if not text_:
            continue
        f = _font(False, sz)
        fitted = _fit_segments(f, text_, iw)
        if not fitted:  # навіть перший сегмент завеликий — спробувати дрібніше
            f = _font(False, max(7, int(sz * 0.85)))
            fitted = _fit_segments(f, text_, iw)
        if fitted:
            footer.append((fitted, f))

    def footer_height(rows):
        return sum(_ink_h(f, t) for t, f in rows) + lgap * max(0, len(rows) - 1)

    min_qr = mm_px(14)
    while footer and ih - num_h - footer_height(footer) - 2 * band_gap < min_qr:
        footer.pop()
    f_h = footer_height(footer)
    gaps = 2 if footer else 1

    # ── 2. QR + колонка сталих слотів ───────────────────────────────────────
    # Розмір QR — сталий у межах розкладки (≈45 % висоти, але не менше 14 мм),
    # а не «весь лишок»: інакше сусідні стікери мають різні QR і виглядають
    # неохайно. Лишок місця йде у проміжки між смугами.
    qr_band = ih - num_h - f_h - gaps * band_gap
    qr = _make_qr(item.payload, max(min_qr, min(qr_band, int(iw * 0.5), int(ih * 0.45))))
    cx = ix0 + qr.width + mm_px(2.0)
    cw = ix1 - cx
    fonts, slot_h = _column_style(cw, qr.height, lgap)
    # Порядок і кеглі слотів сталі, але порожні слоти місця НЕ тримають:
    # «XL → Вживаний → 650 ₴» без дірки на місці устілки.
    values = [item.size, item.insole, item.condition, item.price if show_price else ""]
    col_rows = []
    for v, f, hh in zip(values, fonts, slot_h):
        if not v:
            continue
        for line, lf in _wrap_slot(v, f, cw):
            col_rows.append((line, lf, hh if lf is f else _ink_h(lf, "29-29.5 см")))
    col_h = sum(hh for _v, _f, hh in col_rows) + lgap * max(0, len(col_rows) - 1)
    # Заміри на два рядки можуть не влізти поруч із QR — тоді жертвуємо описом
    # (бренд є на самому товарі), а не цифрами.
    # Рахуємо лише ПРИРІСТ понад QR: у дрібних розкладках QR і так тримає
    # мінімум і займає всю висоту — це не привід знімати бренд.
    extra = col_h - qr.height
    while footer and extra > 0 and extra > ih - num_h - qr.height - footer_height(footer) - 2 * band_gap:
        footer.pop()
    f_h = footer_height(footer)

    # ── Вертикальний розподіл: лишок — порівну між смугами ─────────────────
    body_h = max(qr.height, col_h)
    leftover = ih - num_h - body_h - f_h
    # Є опис — лишок порівну між двома проміжками; нема — тіло по центру
    # решти (половина лишку зверху, половина знизу), а не «на дні».
    gap = max(band_gap, leftover // 2)

    y = iy0
    _draw_tight(draw, ix0, y, number, f_num)
    y += num_h + gap // 2
    draw.line([(ix0, y), (ix1, y)], fill=0, width=1)          # розділювач
    y += gap - gap // 2

    body_top = y
    page.paste(qr, (ix0, body_top + (body_h - qr.height) // 2))
    if cw >= mm_px(6):
        cy = body_top + (body_h - col_h) // 2
        for text_, f, hh in col_rows:
            # Значення ширше за еталон (рідкість) — стиснути лише його.
            if _text_w(f, text_) > cw:
                bold = "Bold" in str(getattr(f, "path", ""))
                f = _fit_font(bold, text_, f.size, cw, min_size=7)
            _draw_tight(draw, cx, cy, text_, f)
            cy += hh + lgap
    y = body_top + body_h

    if footer:
        y += gap // 2
        draw.line([(ix0, y), (ix1, y)], fill=0, width=1)
        y = iy1 - f_h                                         # опис — донизу
        for t, f in footer:
            _draw_tight(draw, ix0, y, t, f)
            y += _ink_h(f, t) + lgap


def _wrap_slot(text_: str, font, max_w: int):
    """Значення слота → [(рядок, шрифт)]. Влазить — один рядок. Ні, і воно
    складене із сегментів («Г 48 · Р 60 · Д 66») — ділимо на два рядки по
    сегментах, без розриву числа. Лише якщо й так завелике — зменшуємо кегль."""
    if _text_w(font, text_) <= max_w:
        return [(text_, font)]
    parts = [p for p in text_.split(SEG) if p]
    lines = [text_]
    if len(parts) > 1:
        best = None
        for k in range(1, len(parts)):
            a, b = SEG.join(parts[:k]), SEG.join(parts[k:])
            wmax = max(_text_w(font, a), _text_w(font, b))
            if best is None or wmax < best[0]:
                best = (wmax, [a, b])
        lines = best[1]
    widest = max(lines, key=lambda t: _text_w(font, t))
    f = font
    if _text_w(font, widest) > max_w:
        bold = "Bold" in str(getattr(font, "path", ""))
        f = _fit_font(bold, widest, font.size, max_w, min_size=7)
    return [(t, f) for t in lines]


def _draw_cut_marks(draw: ImageDraw.ImageDraw, spec: LayoutSpec, page_w: int, page_h: int) -> None:
    """Пунктир у проміжках між стікерами — лінія різу ножицями."""
    m = mm_px(spec.margin_mm)
    gap = mm_px(spec.gap_mm)
    cw = (page_w - 2 * m - (spec.cols - 1) * gap) / spec.cols
    ch = (page_h - 2 * m - (spec.rows - 1) * gap) / spec.rows
    dash, space = mm_px(1.5), mm_px(1.0)

    def dashed_v(x):
        y = m
        while y < page_h - m:
            draw.line([(x, y), (x, min(y + dash, page_h - m))], fill=0, width=1)
            y += dash + space

    def dashed_h(y):
        x = m
        while x < page_w - m:
            draw.line([(x, y), (min(x + dash, page_w - m), y)], fill=0, width=1)
            x += dash + space

    for c in range(1, spec.cols):
        dashed_v(int(round(m + c * cw + (c - 0.5) * gap)))
    for r in range(1, spec.rows):
        dashed_h(int(round(m + r * ch + (r - 0.5) * gap)))


def expand_copies(items: Sequence[LabelItem]) -> List[LabelItem]:
    out: List[LabelItem] = []
    for it in items:
        out.extend([it] * max(0, int(it.copies or 0)))
    return out


def render_pages(items: Sequence[LabelItem], layout: str = DEFAULT_LAYOUT, *,
                 show_price: bool = False, cut_marks: bool = True,
                 max_pages: Optional[int] = None) -> List[Image.Image]:
    """Розкласти стікери (з урахуванням копій) по сторінках. Повертає 1-біт сторінки."""
    spec = get_layout(layout)
    flat = expand_copies(items)
    page_w, page_h = mm_px(spec.media_w_mm), mm_px(spec.media_h_mm)
    m, gap = mm_px(spec.margin_mm), mm_px(spec.gap_mm)
    cw = (page_w - 2 * m - (spec.cols - 1) * gap) / spec.cols
    ch = (page_h - 2 * m - (spec.rows - 1) * gap) / spec.rows

    pages: List[Image.Image] = []
    for start in range(0, len(flat), spec.per_page):
        if max_pages is not None and len(pages) >= max_pages:
            break
        page = Image.new("L", (page_w, page_h), 255)
        draw = ImageDraw.Draw(page)
        for i, it in enumerate(flat[start:start + spec.per_page]):
            r, c = divmod(i, spec.cols)
            x0 = int(round(m + c * (cw + gap)))
            y0 = int(round(m + r * (ch + gap)))
            _draw_cell(page, draw, (x0, y0, int(round(x0 + cw)), int(round(y0 + ch))), it, show_price)
        if cut_marks and spec.per_page > 1:
            _draw_cut_marks(draw, spec, page_w, page_h)
        # Поріг замість дизерингу: термодрук — 1 біт, антиаліасинг тексту лише
        # розмив би краї. QR лишається геометрично точним.
        pages.append(page.point(lambda v: 255 if v > 150 else 0).convert("1"))
    return pages


def render_box_label(code: str, title: str = "", location: str = "", *,
                     media_w_mm: float = 100.0, media_h_mm: float = 100.0) -> Image.Image:
    """Етикетка коробки — один аркуш 100×100: величезний код, QR `bms:b:<код>`,
    назва й місце. Коробки стоять роками, а термопапір вицвітає — тому код
    настільки великий, щоб його було видно й після того, як QR зблідне."""
    page_w, page_h = mm_px(media_w_mm), mm_px(media_h_mm)
    page = Image.new("L", (page_w, page_h), 255)
    draw = ImageDraw.Draw(page)
    fr, pad = 3, mm_px(4)
    draw.rounded_rectangle((fr, fr, page_w - fr - 1, page_h - fr - 1), radius=mm_px(3), outline=0, width=3)
    ix0, iy0, ix1, iy1 = pad, pad, page_w - pad, page_h - pad
    iw = ix1 - ix0

    code = (code or "").strip().upper()
    f_code = _fit_font(True, code, int(page_h * 0.30), iw, min_size=20)
    y = iy0
    y += _draw_tight(draw, ix0, y, code, f_code) + mm_px(2)
    draw.line([(ix0, y), (ix1, y)], fill=0, width=2)
    y += mm_px(3)

    # QR — ліворуч, великий; праворуч — назва (кілька рядків) і місце.
    qr = _make_qr(qr_payload_box(code), min(iy1 - y, int(iw * 0.56)))
    page.paste(qr, (ix0, y))
    cx, cw = ix0 + qr.width + mm_px(3), ix1 - (ix0 + qr.width + mm_px(3))
    cy = y
    if title:
        f_t = _font(True, int(page_h * 0.075))
        # Назва — по словах у кілька рядків, без обрізань.
        words, line, lines = title.split(), "", []
        for w_ in words:
            cand = (line + " " + w_).strip()
            if _text_w(f_t, cand) <= cw or not line:
                line = cand
            else:
                lines.append(line); line = w_
        if line:
            lines.append(line)
        for ln in lines[:4]:
            if cy + _ink_h(f_t, ln) > y + qr.height:
                break
            cy += _draw_tight(draw, cx, cy, ln, f_t) + mm_px(1.2)
    if location:
        f_l = _font(False, int(page_h * 0.06))
        loc = _fit_segments(f_l, location, cw) or location[:24]
        if cy + _ink_h(f_l, loc) <= y + qr.height:
            cy += mm_px(1)
            _draw_tight(draw, cx, cy, loc, f_l)
    return page.point(lambda v: 255 if v > 150 else 0).convert("1")


def page_count(items: Sequence[LabelItem], layout: str) -> Tuple[int, int]:
    """(к-сть стікерів з копіями, к-сть аркушів)."""
    spec = get_layout(layout)
    n = sum(max(0, int(it.copies or 0)) for it in items)
    return n, math.ceil(n / spec.per_page) if n else 0


def pages_to_pdf(pages: Sequence[Image.Image]) -> bytes:
    if not pages:
        raise ValueError("Немає сторінок для друку")
    buf = io.BytesIO()
    first, rest = pages[0], list(pages[1:])
    # resolution=203 → сторінка PDF рівно 100×100 мм; mode «1» → CCITT G4 (без втрат).
    first.save(buf, "PDF", resolution=float(DPI), save_all=True, append_images=rest,
               title="BMS стікери", producer="BMS")
    return buf.getvalue()


def page_to_png(page: Image.Image) -> bytes:
    buf = io.BytesIO()
    page.convert("L").save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ───────────────────────────── БД: товари, черга, історія ────────────────────

def _from_sql() -> str:
    try:
        from services.product_service import _PRODUCT_FROM_SQL
    except ImportError:  # pragma: no cover
        from backend.services.product_service import _PRODUCT_FROM_SQL
    return _PRODUCT_FROM_SQL


_ROW_SELECT = """
    SELECT p.id, p.productnumber, p.model, p.price, p.quantity,
           p.sizeeu, p.size_letter, p.sizeua, p.measurementscm, p.season,
           p.measurements_pog_min, p.measurements_pog_max,
           p.measurements_pot_min, p.measurements_pot_max,
           p.measurements_pob_min, p.measurements_pob_max,
           p.measurements_sleeve_min, p.measurements_sleeve_max,
           p.measurements_length_min, p.measurements_length_max,
           p.mainimage, p.deliveryid, p.label_printed_at,
           b.brandname, t.typename, st.subtypename, c.colorname, g.gendername,
           s.statusname,
           cond.conditionname AS condition_name, cur_cond.conditionname AS current_condition_name,
           COALESCE(sold.sold_count, 0) AS sold_count,
           GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold.sold_count, 0), 0) AS available_qty
"""


def load_rows(db: Session, product_ids: Sequence[int]) -> List[Dict[str, Any]]:
    """Рядки товарів у порядку переданих id (канонічні JOIN-и й «продано» —
    той самий фрагмент, що й у списку товарів, без п'ятої копії формули)."""
    ids = [int(i) for i in product_ids if i is not None]
    if not ids:
        return []
    sql = _ROW_SELECT + _from_sql() + " WHERE p.id = ANY(:ids)"
    rows = {int(r["id"]): dict(r) for r in db.execute(text(sql), {"ids": ids}).mappings()}
    return [rows[i] for i in ids if i in rows]


def product_ids_for_delivery(db: Session, delivery_id: int) -> List[int]:
    rows = db.execute(text(
        "SELECT id FROM products WHERE deliveryid = :d ORDER BY productnumber, sizeeu"
    ), {"d": int(delivery_id)}).fetchall()
    return [int(r[0]) for r in rows]


def queue_pending(db: Session) -> List[Dict[str, Any]]:
    rows = db.execute(text(
        "SELECT id, product_id, copies, source, added_at FROM label_print_queue "
        "WHERE printed_at IS NULL ORDER BY added_at, id"
    )).mappings()
    return [dict(r) for r in rows]


def queue_count(db: Session) -> int:
    return int(db.execute(text(
        "SELECT COUNT(*) FROM label_print_queue WHERE printed_at IS NULL"
    )).scalar() or 0)


def enqueue(db: Session, entries: Iterable[Tuple[int, int]], source: str) -> Dict[str, int]:
    """Покласти (product_id, copies) у чергу. Повтор для товару, що вже чекає, —
    оновлює copies (частковий унікальний індекс + ON CONFLICT), не дублює.
    Транзакцію НЕ комітить — це справа виклику."""
    added = updated = 0
    for pid, copies in entries:
        copies = max(1, int(copies or 1))
        res = db.execute(text("""
            INSERT INTO label_print_queue (product_id, copies, source)
            VALUES (:pid, :copies, :src)
            ON CONFLICT (product_id) WHERE printed_at IS NULL
            DO UPDATE SET copies = EXCLUDED.copies, source = EXCLUDED.source, added_at = now()
            RETURNING (xmax = 0) AS inserted
        """), {"pid": int(pid), "copies": copies, "src": (source or "")[:24]}).scalar()
        if res:
            added += 1
        else:
            updated += 1
    return {"added": added, "updated": updated}


def queue_update_copies(db: Session, item_id: int, copies: int) -> bool:
    n = db.execute(text(
        "UPDATE label_print_queue SET copies = :c WHERE id = :i AND printed_at IS NULL"
    ), {"c": max(1, int(copies)), "i": int(item_id)}).rowcount
    return bool(n)


def queue_remove(db: Session, item_id: Optional[int] = None) -> int:
    if item_id is None:
        return db.execute(text("DELETE FROM label_print_queue WHERE printed_at IS NULL")).rowcount
    return db.execute(text(
        "DELETE FROM label_print_queue WHERE id = :i AND printed_at IS NULL"
    ), {"i": int(item_id)}).rowcount


def mark_printed(db: Session, items: Sequence[LabelItem], *, layout: str, pages: int,
                 mode: str, printer: Optional[str], file_path: Optional[str]) -> int:
    """Позначити товари як зі стікером, закрити їх у черзі, записати історію."""
    ids = sorted({int(it.product_id) for it in items if it.product_id and it.copies > 0})
    if not ids:
        return 0
    now = datetime.utcnow()
    db.execute(text("UPDATE products SET label_printed_at = :t WHERE id = ANY(:ids)"),
               {"t": now, "ids": ids})
    db.execute(text(
        "UPDATE label_print_queue SET printed_at = :t WHERE printed_at IS NULL AND product_id = ANY(:ids)"
    ), {"t": now, "ids": ids})
    stickers = sum(int(it.copies) for it in items if it.copies > 0)
    payload = [{"product_id": it.product_id, "productnumber": it.productnumber, "copies": it.copies}
               for it in items if it.copies > 0]
    job_id = db.execute(text("""
        INSERT INTO label_print_jobs (layout, pages, stickers, mode, printer, file_path, items)
        VALUES (:layout, :pages, :stickers, :mode, :printer, :path, CAST(:items AS jsonb))
        RETURNING id
    """), {"layout": layout, "pages": int(pages), "stickers": int(stickers), "mode": mode[:12],
           "printer": (printer or None), "path": file_path,
           "items": json.dumps(payload, ensure_ascii=False)}).scalar()
    return int(job_id or 0)


def recent_jobs(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
    rows = db.execute(text(
        "SELECT id, created_at, layout, pages, stickers, mode, printer, file_path, items "
        "FROM label_print_jobs ORDER BY id DESC LIMIT :n"
    ), {"n": int(limit)}).mappings()
    return [dict(r) for r in rows]


# ───────────────────────────── принтери ──────────────────────────────────────

def _lpstat(args: List[str]) -> str:
    lpstat = shutil.which("lpstat")
    if not lpstat:
        return ""
    try:
        return subprocess.run([lpstat, *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def list_printers() -> List[Dict[str, Any]]:
    """Доступні принтери: CUPS (macOS/Linux) через lpstat; Windows — win32print,
    якщо є. Імена беремо з `lpstat -a` — перший токен не залежить від локалі."""
    if sys.platform.startswith("win"):
        try:
            import win32print  # type: ignore
            default = win32print.GetDefaultPrinter()
            names = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL
                                                             | win32print.PRINTER_ENUM_CONNECTIONS)]
            return [{"name": n, "default": n == default} for n in names]
        except Exception:  # noqa: BLE001
            return []
    names = [ln.split()[0] for ln in _lpstat(["-a"]).splitlines() if ln.strip()]
    default = ""
    d = _lpstat(["-d"]).strip()
    if ":" in d:
        default = d.rsplit(":", 1)[1].strip()
    return [{"name": n, "default": n == default} for n in names]


def preferred_printer(printers: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Принтер для стікерів: BMS_LABEL_PRINTER → перший, схожий на Xprinter → системний."""
    printers = list_printers() if printers is None else printers
    env = (os.getenv("BMS_LABEL_PRINTER") or "").strip()
    names = [p["name"] for p in printers]
    if env and env in names:
        return env
    for n in names:
        if re.search(r"xprinter|xp[-_ ]?\d|label", n, re.I):
            return n
    for p in printers:
        if p.get("default"):
            return p["name"]
    return names[0] if names else None


def can_print_here() -> bool:
    return bool(shutil.which("lp")) and not sys.platform.startswith("win")


def print_pdf(path: str, printer: Optional[str], spec: LayoutSpec) -> str:
    """Надіслати PDF на принтер. macOS/Linux — CUPS `lp`. Повертає id завдання.
    Спершу з розміром носія; якщо драйвер відкидає опцію — без неї."""
    if sys.platform.startswith("win"):
        raise RuntimeError("Прямий друк із Windows ще не налаштовано — файл збережено, "
                           "відкрийте його і надрукуйте вручну.")
    lp = shutil.which("lp")
    if not lp:
        raise RuntimeError("Служба друку CUPS (lp) недоступна на цій машині")
    base = [lp] + (["-d", printer] if printer else []) + ["-t", "BMS стікери"]
    media = f"media=Custom.{spec.media_w_mm:g}x{spec.media_h_mm:g}mm"
    extra = (os.getenv("BMS_LABEL_LP_OPTIONS") or "").split()
    attempts = [base + ["-o", media, "-o", "fit-to-page", *extra, path], base + [*extra, path]]
    last_err = ""
    for cmd in attempts:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            last_err = str(exc)
            continue
        if res.returncode == 0:
            return (res.stdout or "").strip()
        last_err = (res.stderr or res.stdout or "").strip()
    raise RuntimeError(f"lp: {last_err or 'невідома помилка'}")


# ───────────────────────────── мережевий друк (TSPL, порт 9100) ─────────────

_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "label_printer.json"


def _read_settings() -> Dict[str, Any]:
    try:
        return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_network_printer_host(host: Optional[str]) -> None:
    """Запамʼятати обраний у діалозі мережевий принтер (backend/label_printer.json —
    поза git, як .env). Порожньо — забути."""
    data = _read_settings()
    if host:
        data["host"] = host.strip()
    else:
        data.pop("host", None)
    _SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def network_printer_host() -> Optional[str]:
    """IP/хост принтера етикеток у мережі (Xprinter XP-480B по Wi-Fi):
    BMS_LABEL_PRINTER_HOST=192.168.1.150[:9100] або збережений у діалозі.
    Порожньо — друк через CUPS/файл."""
    return (os.getenv("BMS_LABEL_PRINTER_HOST") or "").strip() or _read_settings().get("host") or None


def _split_host(host: str) -> Tuple[str, int]:
    if ":" in host:
        h, _, p = host.rpartition(":")
        if p.isdigit():
            return h, int(p)
    return host, 9100


def network_printer_reachable(host: Optional[str] = None, timeout: float = 1.5) -> bool:
    host = host or network_printer_host()
    if not host:
        return False
    h, port = _split_host(host)
    try:
        with socket.create_connection((h, port), timeout=timeout):
            return True
    except OSError:
        return False


def page_to_tspl(page: Image.Image, spec: LayoutSpec, *, density: int = 8, gap_mm: float = 3.0) -> bytes:
    """Одна сторінка → пакет TSPL (рідна мова Xprinter/TSC): розмір носія, проміжок
    між етикетками, растр 1-біт і команда друку. У TSPL-бітмапі 1 = білий,
    0 = чорний — рівно як у Pillow «1», тож байти йдуть без інверсії."""
    img = page.convert("1")
    w, h = img.size
    row_bytes = (w + 7) // 8
    if w % 8:  # добиваємо ширину до байта білим
        padded = Image.new("1", (row_bytes * 8, h), 1)
        padded.paste(img, (0, 0))
        img = padded
    data = img.tobytes()
    head = (f"SIZE {spec.media_w_mm:g} mm,{spec.media_h_mm:g} mm\r\n"
            f"GAP {gap_mm:g} mm,0 mm\r\n"
            f"DENSITY {int(density)}\r\n"
            "DIRECTION 1\r\n"
            "CLS\r\n"
            f"BITMAP 0,0,{row_bytes},{h},0,").encode("ascii")
    return head + data + b"\r\nPRINT 1,1\r\n"


def print_tspl(pages: Sequence[Image.Image], spec: LayoutSpec, host: Optional[str] = None,
               *, copies: int = 1, timeout: float = 15.0) -> int:
    """Надіслати сторінки прямо на принтер по TCP 9100. Повертає к-сть надісланих
    аркушів. Без драйверів і системних діалогів — так само працюватиме з Windows."""
    host = host or network_printer_host()
    if not host:
        raise RuntimeError("Мережевий принтер не задано (BMS_LABEL_PRINTER_HOST)")
    h, port = _split_host(host)
    density = int(os.getenv("BMS_LABEL_DENSITY", "8") or 8)
    gap = float(os.getenv("BMS_LABEL_GAP_MM", "3") or 3)
    payload = b"".join(page_to_tspl(p, spec, density=density, gap_mm=gap) for p in pages) * max(1, int(copies))
    try:
        with socket.create_connection((h, port), timeout=timeout) as sock:
            sock.sendall(payload)
    except OSError as exc:
        raise RuntimeError(f"Принтер {h}:{port} недоступний: {exc}") from exc
    return len(pages) * max(1, int(copies))


def local_ipv4() -> Optional[str]:
    """IPv4 цього компʼютера в локальній мережі (UDP-«connect» нічого не шле)."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        return None


def network_hint(host: Optional[str] = None, my_ip: Optional[str] = None) -> Optional[str]:
    """Людське пояснення, чому збережений принтер НЕ може відповісти: він в іншій
    мережі, ніж цей Mac. 23.09 так і було — Mac у 192.168.0.x, міст збережений як
    192.168.1.105, а діалог казав лише «не відповідає». None — підмережі збігаються
    або порівняти нема з чим (хост — імʼя, немає мережі)."""
    host = host or network_printer_host()
    my_ip = my_ip or local_ipv4()
    if not host or not my_ip:
        return None
    h, _port = _split_host(host)
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", h) or h.startswith("127."):
        return None
    mine, theirs = my_ip.rsplit(".", 1)[0], h.rsplit(".", 1)[0]
    if mine == theirs:
        return None
    return (f"Цей Mac зараз у мережі {mine}.x, а принтер збережений як {h} — це інша мережа. "
            "Підключіть Mac до того самого Wi-Fi, що й Windows-ПК з принтером, "
            "або натисніть «Знайти Xprinter у Wi-Fi».")


def discover_network_printers(timeout: float = 0.5) -> List[str]:
    """Хости локальної /24 з відкритим портом 9100 (принтери етикеток). Швидкий
    скан у потоках — для кнопки «Знайти принтер у мережі»."""
    import concurrent.futures as cf
    my_ip = local_ipv4()
    if not my_ip:
        return []
    sub = my_ip.rsplit(".", 1)[0]

    def check(ip: str) -> Optional[str]:
        try:
            with socket.create_connection((ip, 9100), timeout=timeout):
                return ip
        except OSError:
            return None
    with cf.ThreadPoolExecutor(64) as ex:
        return [ip for ip in ex.map(check, [f"{sub}.{i}" for i in range(1, 255)]) if ip]


def open_file(path: str) -> None:
    """Відкрити PDF системним переглядачем (десктоп-режим, коли друкувати нема чим)."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не вдалося відкрити %s: %s", path, exc)


def platform_name() -> str:
    return platform.system()

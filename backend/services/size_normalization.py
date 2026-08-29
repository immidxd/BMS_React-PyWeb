"""Розміри й заміри — тільки десяткові дроби, ніяких ½ ⅓ ⅔.

Постачальники пишуть третинні розміри знаком (38⅔, 43⅓) або текстом (38 2/3).
У журналі ті самі розміри поряд записані десятковими (45.3), і через це один
фізичний розмір існує у двох написаннях: фільтр «Розмір» показує їх окремо,
сортування ставить 43⅓ поза числовим рядом, а маркетплейси отримують символ,
який їхні форми не приймають.

Прийнята шкала — та, що вже є в аркуші: ⅓ → .3, ⅔ → .6, ½ → .5.
(Не 0.33/0.67: 45.3 у журналі з'явилось раніше за цей код і задає домовленість.)

Діапазони («38-39», «24.5-25»), літерні розміри (S/M/XL) і будь-який інший
текст лишаються недоторканими — перетворюється ЛИШЕ дробова частина.
"""

from __future__ import annotations

import re
from typing import Optional


# Знак дробу → десяткова частина. Третини — за домовленістю журналу (.3/.6).
VULGAR_FRACTIONS = {
    "½": ".5",
    "⅓": ".3",
    "⅔": ".6",
    "¼": ".25",
    "¾": ".75",
}

# Ті самі дроби, записані текстом: «38 2/3».
ASCII_FRACTIONS = {
    (1, 2): ".5",
    (1, 3): ".3",
    (2, 3): ".6",
    (1, 4): ".25",
    (3, 4): ".75",
}

_GLYPH_AFTER_NUMBER = re.compile(r"(\d)\s*([" + "".join(VULGAR_FRACTIONS) + r"])")
_GLYPH_ALONE = re.compile(r"(?<![\d])([" + "".join(VULGAR_FRACTIONS) + r"])")
# «38 2/3» — обов'язково число, пробіл, дріб. Без числа попереду не чіпаємо:
# «41/42» — це діапазон розмірів, а «G 1/2» — ширина колодки.
_ASCII_MIXED = re.compile(r"(\d)\s+(\d)\s*/\s*(\d)")


def _ascii_decimal(num: int, den: int) -> Optional[str]:
    known = ASCII_FRACTIONS.get((num, den))
    if known:
        return known
    if den == 0 or num >= den:
        return None
    return ("%.2f" % (num / den)).lstrip("0").rstrip("0").rstrip(".") or None


def decimalize_fractions(value: object) -> object:
    """'38⅔' → '38.6', '43 1/3' → '43.3', '½' → '0.5'.

    Не-рядки й значення без дробів повертаються як є (зокрема діапазони
    «38-39», літерні розміри та ширина «G 1/2»).
    """
    if value is None or not isinstance(value, str):
        return value
    s = value
    if not s.strip():
        return value

    s = _GLYPH_AFTER_NUMBER.sub(lambda m: m.group(1) + VULGAR_FRACTIONS[m.group(2)], s)
    s = _GLYPH_ALONE.sub(lambda m: "0" + VULGAR_FRACTIONS[m.group(1)], s)

    def _mixed(m: re.Match) -> str:
        dec = _ascii_decimal(int(m.group(2)), int(m.group(3)))
        return m.group(1) + dec if dec else m.group(0)

    s = _ASCII_MIXED.sub(_mixed, s)
    return s


def has_vulgar_fraction(value: object) -> bool:
    """Чи лишився в значенні дріб, який треба звести до десяткового."""
    if value is None or not isinstance(value, str):
        return False
    return decimalize_fractions(value) != value

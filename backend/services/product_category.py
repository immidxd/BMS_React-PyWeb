"""Категорія товару за назвою «Тип» — бекендове дзеркало
`frontend/src/components/products/productCategory.ts`.

Потрібна там, де форма запиту залежить від того, ЩО перед нами: автозаповнення
з фото питає модель про підошву й устілку, а на кофті чи костюмі цих речей
нема — зате є буквений розмір і заміри одягу, про які схема для взуття не
питала взагалі. Саме тому на #Ф4425 (кофта) модель ПРОЧИТАЛА стікер «XXL,
о/г 63, д 80» дослівно, але в картку не потрапило нічого: поля розміру EU і
устілки в см для цих цифр — чужі.

⚠️ Регекси ТРИМАТИ синхронними з productCategory.ts (`categoryOf`,
`clothingSubcat`, `CLOTHING_MEASUREMENTS`): тест `test_product_category`
стереже ключові випадки, а не буквальну рівність.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Set

# Латинські гомогліфи → кирилиця («Cумка» з латинською C).
_HOMOGLYPH = str.maketrans({
    "a": "а", "c": "с", "e": "е", "i": "і", "k": "к", "m": "м", "o": "о",
    "p": "р", "t": "т", "x": "х", "y": "у", "b": "ь", "h": "н",
})

_SUITCASE = re.compile(r"валіз|чемодан")
_BAG = re.compile(r"сумк|рюкзак|клатч|барсетк|борсетк|гаман|косметичк|шопер|портфел|саквояж")
_CLOTHING = re.compile(
    r"куртк|штан|джинс|футболк|сорочк|світшот|худі|плат|сукн|спідниц|шорт|пальт|кофт|светр|"
    r"комбінезон|костюм|жилет|толстовк|лонгслів|майк|бомбер|вітровк|пуховик|парк|жакет|"
    r"кардиган|поло|туніка|блуз|рейтуз|лосин|легінс|бермуд|сарафан"
)
_ACCESSORY = re.compile(
    r"ремін|пасок|пояс|шапк|кепк|панам|берет|капелюх|бейсболк|шарф|хустк|снуд|бандан|"
    r"рукавиц|рукавичк|перчатк|мітенк|окуляр|краватк|метелик|підтяжк|запонк|брелок|гетр|"
    r"шкарпетк|панчох|нараменник"
)
_BOTTOM = re.compile(r"штан|джинс|шорт|спідниц|лосин|рейтуз|легінс|бермуд")
_SUIT = re.compile(r"костюм")
_DRESS = re.compile(r"плат|сукн|комбінезон|сарафан")


def _key(type_name: Optional[str]) -> str:
    return (type_name or "").lower().translate(_HOMOGLYPH)


def category_of(type_name: Optional[str]) -> str:
    """'shoe' | 'bag' | 'suitcase' | 'clothing' | 'accessory'."""
    s = _key(type_name)
    if _SUITCASE.search(s):
        return "suitcase"
    if _BAG.search(s):
        return "bag"
    if _CLOTHING.search(s):
        return "clothing"
    if _ACCESSORY.search(s):
        return "accessory"
    return "shoe"


def clothing_subcat(type_name: Optional[str]) -> str:
    """'bottom' | 'suit' | 'dress' | 'top' — визначає, які заміри мають сенс.

    Костюм — окремо від плаття: це ДВІ речі (кофта/жакет + штани), у нього є
    рукав, а довжин дві. Рішення власника: у поле «Довжина» іде СУМА двох
    довжин, а розклад («кофта 65, штани 102») — у примітку.
    """
    s = _key(type_name)
    if _BOTTOM.search(s):
        return "bottom"
    if _SUIT.search(s):
        return "suit"
    if _DRESS.search(s):
        return "dress"
    return "top"


# Заміри одягу за підкатегорією (ключі = MEASUREMENT_EDIT_FIELDS сервісу товарів).
CLOTHING_MEASUREMENTS: Dict[str, Set[str]] = {
    "bottom": {"pot", "pob", "length"},                     # талія, бедра, довжина
    "dress":  {"pog", "pot", "pob", "length"},              # груди, талія, бедра, довжина
    "suit":   {"pog", "pot", "pob", "sleeve", "length"},    # + рукав; довжина = верх + низ
    "top":    {"pog", "sleeve", "length"},                  # груди, рукав, довжина
}

# Що модель читає зі стікера для підкатегорії. У костюма замість однієї
# довжини — дві (верх/низ), які потім складаються в «Довжина».
STICKER_MEASUREMENT_KEYS: Dict[str, tuple] = {
    "bottom": ("pot", "pob", "length"),
    "dress":  ("pog", "pot", "pob", "length"),
    "suit":   ("pog", "pot", "pob", "sleeve", "length_top", "length_bottom"),
    "top":    ("pog", "sleeve", "length"),
}

# Як заміри підписують на рукописному стікері — щоб модель звʼязала скорочення
# з полем, а не вгадувала. «н/о» = напівобхват: половина кола.
MEASUREMENT_STICKER_HINTS: Dict[str, str] = {
    "pog":    "груди, напівобхват (половина кола); на стікері «о/г», «ОГ», «г», «груди»",
    "pot":    "талія, напівобхват; на стікері «о/т», «ОТ», «т», «талія»",
    "pob":    "бедра, напівобхват; на стікері «о/б», «ОБ», «б», «бедра»",
    "length": "довжина виробу; на стікері «д», «дл», «дов», «довж», «довжина»",
    "sleeve": ("довжина рукава; на стікері «р», «Р», «рук», «рукав». ⚠️ «Р 54» — це РУКАВ 54 см, "
               "а НЕ розмір: розмір тут завжди буквений (XL)"),
    "length_top": ("довжина ВЕРХУ костюма (кофти/жакета); на стікері «д», «дов», «довж» без "
                   "згадки штанів"),
    "length_bottom": ("довжина НИЗУ костюма (штанів/брюк); на стікері «б-д», «б д», «брюки», "
                      "«штани», «шт» з числом, напр. «б-д-102» → 102"),
}

# Межі здорового глузду для замірів одягу, см (поза ними — хибне читання).
MEASUREMENT_BOUNDS: Dict[str, tuple] = {
    "pog":    (30.0, 100.0),
    "pot":    (25.0, 100.0),
    "pob":    (30.0, 100.0),
    "length": (15.0, 200.0),
    "sleeve": (15.0, 100.0),
    "length_top": (15.0, 120.0),
    "length_bottom": (30.0, 140.0),
}

# Буквені розміри одягу — те, що реально є в базі (XXXXXL — 5XL, XXXXXXL — 6XL).
SIZE_LETTERS = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "XXXXL", "XXXXXL", "XXXXXXL")


def normalize_size_letter(value: Optional[str]) -> Optional[str]:
    """'2xl' → 'XXL', '3XL' → 'XXXL', ' m ' → 'M'; не-розмір → None."""
    if not value:
        return None
    s = str(value).strip().upper().replace(" ", "")
    m = re.match(r"^(\d)XL$", s)
    if m:
        s = "X" * int(m.group(1)) + "L"
    return s if s in SIZE_LETTERS else None

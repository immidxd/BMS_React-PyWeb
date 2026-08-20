"""Exact, reviewed canonicalization rules for product types and subtypes.

Only deterministic spelling, punctuation, homoglyph and clear synonym fixes
belong here.  Fuzzy matching is intentionally excluded: close taxonomy labels
can represent genuinely different product classes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal


TaxonomyKind = Literal["type", "subtype"]

_HOMOGLYPHS: Final = str.maketrans(
    {
        "A": "А",
        "a": "а",
        "B": "В",
        "C": "С",
        "c": "с",
        "E": "Е",
        "e": "е",
        "H": "Н",
        "I": "І",
        "i": "і",
        "K": "К",
        "k": "к",
        "M": "М",
        "O": "О",
        "o": "о",
        "P": "Р",
        "p": "р",
        "T": "Т",
        "X": "Х",
        "x": "х",
        "Y": "У",
        "y": "у",
        "`": "'",
        "’": "'",
        "ʼ": "'",
    }
)


def taxonomy_comparison_key(value: str | None) -> str:
    """Stable lookup key tolerant of whitespace, punctuation and homoglyphs."""
    if value is None:
        return ""
    folded = unicodedata.normalize("NFKC", str(value)).translate(_HOMOGLYPHS)
    folded = re.sub(r"\s+", " ", folded).strip().casefold()
    return re.sub(r"[^0-9a-zа-яіїєґ]+", "", folded)


# Approved high-confidence aliases.  Canonical spellings are also registered
# below, so different casing/punctuation of the canonical value remains stable.
TYPE_ALIASES: Final[dict[str, str]] = {
    "Cумка": "Сумка",
    "Сумки": "Сумка",
    "Босоніжкиї": "Босоніжки",
    "Ботиник": "Ботинки",
    "Ботинок": "Ботинки",
    "Ботінки": "Ботинки",
    "Еспадрилії": "Еспадрильї",
    "Комбенізон": "Комбінезон",
    "Комбінізон": "Комбінезон",
    "Напісапоги": "Напівсапоги",
    # User-approved semantic grouping: these two labels are one product class.
    "Сандалі": "Босоніжки",
    "Сандалії": "Босоніжки",
    "Сороконожки": "Сороконіжки",
    "Тапочки": "Тапки",
    "Тапчки": "Тапки",
    "Футзалкии": "Футзалки",
    "Шльлопанці": "Шльопанці",
    "Шльпанці": "Шльопанці",
}

SUBTYPE_ALIASES: Final[dict[str, str]] = {
    "Cліпони": "Сліпони",
    "Cлінгбеки": "Слінгбеки",
    "В`єтнамки": "В'єтнамки",
    "Крос-боді": "Кросбоді",
    "Топ-сайдери": "Топсайдери",
    "Панцирь": "Панцир",
    "Ручна кладь": "Ручна поклажа",
    "Шоппер": "Шопер",
    "Для нотубука": "Для ноутбука",
    "Для ноутбуку": "Для ноутбука",
    "Повсякденне": "Повсякденні",
    "Сороконожки": "Сороконіжки",
    "Еспадрилії": "Еспадрильї",
    "Ескадрильї": "Еспадрильї",
    "Кардеган": "Кардиган",
    "Слипони": "Сліпони",
    "Плечева": "Плечова",
    "Кедм": "Кеди",
    "Угги": "Уггі",
    "Угі": "Уггі",
    "Мблі": "Мюлі",
    "Сонцезахистні": "Сонцезахисні",
    "Стограмовка": "Стограмівка",
    "Хатній": "Хатні",
    "Бігова": "Бігові",
    "Дутіки": "Дутики",
    "Тапочки": "Тапки",
    # User-approved grammatical variants shown as one filter value.
    "Робочий": "Робочі",
    "Сандалі": "Босоніжки",
    "Сандалії": "Босоніжки",
}


# Reviewed source values that historically put two (sometimes three) taxonomy
# levels into the Journal's single ``Вид`` cell.  Values are written back as an
# explicit canonical (type, subtype) pair.  The boot/sneaker hybrids were
# specifically confirmed by the user as high-top sneakers.
COMBINED_TYPE_SUBTYPE: Final[dict[str, tuple[str, str]]] = {
    "Напівсапоги/ботинки": ("Напівсапоги", "Ботинки"),
    "Шльопанці/босоніжки": ("Шльопанці", "Босоніжки"),
    "Кросівки/кеди": ("Кросівки", "Кеди"),
    "Кеди/Кросівки": ("Кросівки", "Кеди"),
    "Ботинки-челсі": ("Ботинки", "Челсі"),
    "Напівсапоги-челсі": ("Напівсапоги", "Челсі"),
    "Напівсапоги/ботинки-челсі": ("Напівсапоги", "Челсі"),
    "Ботинки-кросівки": ("Кросівки", "Хайтопи"),
    "Кросівки-ботинки": ("Кросівки", "Хайтопи"),
    "Ботинки/кросівки-хайтопи": ("Кросівки", "Хайтопи"),
    "Кросівки-хайтопи": ("Кросівки", "Хайтопи"),
    "Кросівки-хайтопи/ботинки": ("Кросівки", "Хайтопи"),
    "Ботинки-уггі": ("Ботинки", "Уггі"),
    "Туфлі-лофери": ("Туфлі", "Лофери"),
    "Ботинки-слипони": ("Ботинки", "Сліпони"),
    "Ботинки-туфлі": ("Ботинки", "Туфлі"),
    "Напівсапоги/Ботинки": ("Напівсапоги", "Ботинки"),
    "Напісапоги/Ботинки": ("Напівсапоги", "Ботинки"),
    "Сапоги-чулки": ("Сапоги", "Чулки"),
    "Сапоги/Напівсапоги": ("Сапоги", "Напівсапоги"),
    "Сумка/рюкзак": ("Сумка", "Рюкзак"),
    "Туфлі-кросівки": ("Туфлі", "Кросівки"),
    "Туфлі/Кросівки": ("Туфлі", "Кросівки"),
    "Шльопанці-в'єтнамки": ("Шльопанці", "В'єтнамки"),
}


def _build_lookup(aliases: dict[str, str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for alias, canonical in aliases.items():
        for value in (alias, canonical):
            key = taxonomy_comparison_key(value)
            previous = lookup.get(key)
            if previous is not None and previous != canonical:
                raise RuntimeError(
                    f"Conflicting taxonomy aliases for {value!r}: "
                    f"{previous!r} vs {canonical!r}"
                )
            lookup[key] = canonical
    return lookup


_TYPE_LOOKUP: Final = _build_lookup(TYPE_ALIASES)
_SUBTYPE_LOOKUP: Final = _build_lookup(SUBTYPE_ALIASES)
_COMBINED_LOOKUP: Final = {
    taxonomy_comparison_key(alias): pair
    for alias, pair in COMBINED_TYPE_SUBTYPE.items()
}


def canonicalize_taxonomy_name(
    value: str | None,
    kind: TaxonomyKind,
) -> str | None:
    """Return a reviewed canonical label, otherwise a whitespace-clean value."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if not cleaned:
        return None
    lookup = _TYPE_LOOKUP if kind == "type" else _SUBTYPE_LOOKUP
    return lookup.get(taxonomy_comparison_key(cleaned), cleaned)


def canonicalize_type_name(value: str | None) -> str | None:
    return canonicalize_taxonomy_name(value, "type")


def canonicalize_subtype_name(value: str | None) -> str | None:
    return canonicalize_taxonomy_name(value, "subtype")


def split_reviewed_combined_type(value: str | None) -> tuple[str, str] | None:
    """Resolve only explicitly reviewed combined source values."""
    if value is None:
        return None
    pair = _COMBINED_LOOKUP.get(taxonomy_comparison_key(value))
    if pair is None:
        return None
    type_name = canonicalize_type_name(pair[0])
    subtype_name = canonicalize_subtype_name(pair[1])
    if not type_name or not subtype_name:
        return None
    return type_name, subtype_name

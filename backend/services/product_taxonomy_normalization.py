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
    "Черевики": "Ботинки",
    "Черевини": "Ботинки",
    "Еспадрилії": "Еспадрильї",
    "Комбенізон": "Комбінезон",
    "Комбінізон": "Комбінезон",
    "Напісапоги": "Напівсапоги",
    "НапівБотинки": "Напівботинки",
    "Напівчеревики": "Напівботинки",
    "Напівчоботи": "Напівботинки",
    # User-approved semantic grouping: these two labels are one product class.
    "Сандалі": "Босоніжки",
    "Сандалії": "Босоніжки",
    "Сороконожки": "Сороконіжки",
    "Тапочки": "Тапки",
    "Тапчки": "Тапки",
    "Труси": "Білизна",
    "Устілка": "Устілки",
    "Футболки": "Футболка",
    "Футзалкии": "Футзалки",
    "Шльлопанці": "Шльопанці",
    "Шльпанці": "Шльопанці",
}

SUBTYPE_ALIASES: Final[dict[str, str]] = {
    "Cліпони": "Сліпони",
    "Cлінгбеки": "Слінгбеки",
    "В`єтнамки": "В'єтнамки",
    "Крос-боді": "Кросбоді",
    "Крос-баді": "Кросбоді",
    "Крос боді": "Кросбоді",
    "Крос баді": "Кросбоді",
    "Кросбаді": "Кросбоді",
    "Crossbody": "Кросбоді",
    "Cross body": "Кросбоді",
    "Через плече": "Кросбоді",
    "Топ-сайдери": "Топсайдери",
    "Сайдери": "Топсайдери",
    "Панцирь": "Панцир",
    "Ручна кладь": "Ручна",
    "Ручна поклажа": "Ручна",
    "Шоппер": "Шопер",
    "Для нотубука": "Для ноутбука",
    "Для ноутбуку": "Для ноутбука",
    "Для ноутбуків": "Для ноутбука",
    "Ноутбук": "Для ноутбука",
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
    "Устілка": "Устілки",
    "Футболки": "Футболка",
    "Черевики": "Ботинки",
    "Черевини": "Ботинки",
    "НапівБотинки": "Напівботинки",
    "Напівчеревики": "Напівботинки",
    "Напівчоботи": "Напівботинки",
    "Рушник-Пончо": "Пончо",
    "Рушник-Почно": "Пончо",
    "Батфорди": "Ботфорти",
    "Труси": "Білизна",
    "Дитяча": "Дитячі",
    "Дитячий": "Дитячі",
    "Джинс": "Джинсові",
    "Джинсова": "Джинсові",
    "Класика": "Класичні",
    "Класична": "Класичні",
    "Платформа": "На платформі",
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
    "Батфорд": ("Сапоги", "Ботфорти"),
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
    "Кросівки трекінгові": ("Кросівки", "Трекінгові"),
    "Кросівки-трекінгові": ("Кросівки", "Трекінгові"),
    # A standalone adjective in Type was used for the same shoe class. All
    # affected rows have EU shoe sizes and no competing type/subtype.
    "Трекінгові": ("Кросівки", "Трекінгові"),
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
    "Рушник-Пончо": ("Рушник", "Пончо"),
    "Рушник-Почно": ("Рушник", "Пончо"),
}


# These values describe season, not a product class.  The parser and the
# audited cleanup move them into products.season / the Sheet's ``Сезон``
# column, then clear the taxonomy field.  Autumn and spring map to the BMS
# canonical transitional season ``Демі``.
SEASON_TAXONOMY_ALIASES: Final[dict[str, str]] = {
    "Демі": "Демі",
    "Демісезонні": "Демі",
    "Демісезонна": "Демі",
    "Демісезонний": "Демі",
    "Зимні": "Зима",
    "Зимові": "Зима",
    "Зимова": "Зима",
    "Зимовий": "Зима",
    "Літні": "Літо",
    "Літня": "Літо",
    "Літній": "Літо",
    "Осіні": "Демі",
    "Осінні": "Демі",
    "Осіння": "Демі",
    "Осінній": "Демі",
    "Весняні": "Демі",
    "Весняна": "Демі",
    "Весняний": "Демі",
}

SEASON_CANONICAL_ORDER: Final[tuple[str, ...]] = (
    "Зима",
    "Єврозима",
    "Демі",
    "Літо",
    "Всесезон",
)

# Service text leaked from another column must never become a Type/Subtype.
BLOCKED_TAXONOMY_NAMES: Final[frozenset[str]] = frozenset({"Бренд не вказано"})

# Exact values stored in the taxonomy columns even though they describe a
# different dimension.  They are cleared only after the destination field has
# accepted the value (the parser/cleanup script explicitly checks conflicts).
STYLE_FROM_TAXONOMY: Final[dict[str, str]] = {
    "Спорт": "Спортивний",
    "Спортзал": "Спортивний",
    "Спортивний": "Спортивний",
    "Спортивні": "Спортивний",
    "Спортивный": "Спортивний",
    "Повсякдений": "Повсякденний",
    "Повсякденне": "Повсякденний",
    "Повсякденний": "Повсякденний",
    "Повсякденні": "Повсякденний",
    "Святкові": "Святковий",
}
FORCE_STYLE_FROM_TAXONOMY_NAMES: Final[frozenset[str]] = frozenset(
    set(STYLE_FROM_TAXONOMY) - {"Святкові"}
)
PACKAGING_FROM_TAXONOMY: Final[dict[str, str]] = {
    "У футлярі": "Футляр",
}

# Exact cross-column repairs that cannot be represented as a one-label alias.
# ``Взуття`` is too generic; when the real class is already in Subtype, promote
# it to Type and clear the redundant subtype.
TYPE_SUBTYPE_RELOCATIONS: Final[dict[tuple[str, str], tuple[str, str | None]]] = {
    ("Взуття", "Кросівки"): ("Кросівки", None),
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
_SEASON_LOOKUP: Final = _build_lookup(SEASON_TAXONOMY_ALIASES)
_STYLE_FROM_TAXONOMY_LOOKUP: Final = {
    taxonomy_comparison_key(alias): target
    for alias, target in STYLE_FROM_TAXONOMY.items()
}
_PACKAGING_FROM_TAXONOMY_LOOKUP: Final = {
    taxonomy_comparison_key(alias): target
    for alias, target in PACKAGING_FROM_TAXONOMY.items()
}
_FORCE_STYLE_FROM_TAXONOMY_KEYS: Final = {
    taxonomy_comparison_key(value) for value in FORCE_STYLE_FROM_TAXONOMY_NAMES
}
_BLOCKED_KEYS: Final = {
    taxonomy_comparison_key(value) for value in BLOCKED_TAXONOMY_NAMES
}
_PAIR_RELOCATION_LOOKUP: Final = {
    (taxonomy_comparison_key(type_name), taxonomy_comparison_key(subtype_name)): pair
    for (type_name, subtype_name), pair in TYPE_SUBTYPE_RELOCATIONS.items()
}


def season_from_taxonomy_name(value: str | None) -> str | None:
    """Return the canonical season when a taxonomy label is actually seasonal."""
    if value is None:
        return None
    return _SEASON_LOOKUP.get(taxonomy_comparison_key(value))


def style_from_taxonomy_name(value: str | None) -> str | None:
    """Return a style value when an exact taxonomy label belongs to Style."""
    if value is None:
        return None
    return _STYLE_FROM_TAXONOMY_LOOKUP.get(taxonomy_comparison_key(value))


def style_from_taxonomy_overrides_existing(value: str | None) -> bool:
    """Whether an approved taxonomy→Style move replaces a current style."""
    if value is None:
        return False
    return taxonomy_comparison_key(value) in _FORCE_STYLE_FROM_TAXONOMY_KEYS


def packaging_from_taxonomy_name(value: str | None) -> str | None:
    """Return a packaging value when an exact taxonomy label belongs there."""
    if value is None:
        return None
    return _PACKAGING_FROM_TAXONOMY_LOOKUP.get(taxonomy_comparison_key(value))


def merge_season_values(*values: str | None) -> str:
    """Merge season CSV values in stable BMS order without losing unknowns."""
    known_by_key = {value.casefold(): value for value in SEASON_CANONICAL_ORDER}
    found_known: set[str] = set()
    unknown: list[str] = []
    unknown_keys: set[str] = set()
    for raw in values:
        if not raw:
            continue
        for part in str(raw).split(","):
            cleaned = re.sub(r"\s+", " ", part).strip()
            if not cleaned:
                continue
            canonical = known_by_key.get(cleaned.casefold())
            if canonical:
                found_known.add(canonical)
                continue
            key = cleaned.casefold()
            if key not in unknown_keys:
                unknown_keys.add(key)
                unknown.append(cleaned)
    ordered = [value for value in SEASON_CANONICAL_ORDER if value in found_known]
    return ", ".join(ordered + unknown)


def normalize_taxonomy_pair(
    type_value: str | None,
    subtype_value: str | None,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Normalize a pair and return seasons that must move to ``Сезон``."""
    seasons = tuple(
        dict.fromkeys(
            value
            for value in (
                season_from_taxonomy_name(type_value),
                season_from_taxonomy_name(subtype_value),
            )
            if value
        )
    )
    type_name = canonicalize_type_name(type_value)
    subtype_name = canonicalize_subtype_name(subtype_value)

    reviewed_pair = split_reviewed_combined_type(type_value)
    if reviewed_pair:
        type_name, subtype_name = reviewed_pair
    else:
        relocated = _PAIR_RELOCATION_LOOKUP.get(
            (
                taxonomy_comparison_key(type_name),
                taxonomy_comparison_key(subtype_name),
            )
        )
        if relocated:
            type_name, subtype_name = relocated

    # A subtype that repeats the type adds no information and causes a visible
    # duplicate in the shared Type/Subtype filter.
    if (
        type_name
        and subtype_name
        and taxonomy_comparison_key(type_name)
        == taxonomy_comparison_key(subtype_name)
    ):
        subtype_name = None
    return type_name, subtype_name, seasons


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
    key = taxonomy_comparison_key(cleaned)
    if (
        key in _BLOCKED_KEYS
        or key in _SEASON_LOOKUP
        or key in _STYLE_FROM_TAXONOMY_LOOKUP
        or key in _PACKAGING_FROM_TAXONOMY_LOOKUP
    ):
        return None
    lookup = _TYPE_LOOKUP if kind == "type" else _SUBTYPE_LOOKUP
    return lookup.get(key, cleaned)


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

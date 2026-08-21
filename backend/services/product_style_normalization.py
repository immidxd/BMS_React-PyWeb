"""Reviewed canonicalization rules for the product ``Стиль`` field.

The list is intentionally exact.  Similar-looking labels are not merged unless
they are an unambiguous spelling, grammatical or user-approved synonym.
"""

from __future__ import annotations

import re
from typing import Final

try:
    from backend.services.product_taxonomy_normalization import taxonomy_comparison_key
except ImportError:
    from services.product_taxonomy_normalization import taxonomy_comparison_key


STYLE_ALIASES: Final[dict[str, str]] = {
    "Баскетбольні": "Баскетбольний",
    "Гірнолижна": "Гірнолижний",
    "Класика": "Класичний",
    "Масажні": "Масажний",
    "Ортопедичні": "Ортопедичний",
    "Повсякдений": "Повсякденний",
    "Повсякденне": "Повсякденний",
    "Повсякденні": "Повсякденний",
    "Спорт": "Спортивний",
    "Спортзал": "Спортивний",
    "Спортивный": "Спортивний",
    "Спортивні": "Спортивний",
    "Трекінгові": "Трекінговий",
    "Туристичний": "Трекінговий",
    "Футбольні": "Футбольний",
    "Святкові": "Святковий",
}


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for alias, canonical in STYLE_ALIASES.items():
        for value in (alias, canonical):
            key = taxonomy_comparison_key(value)
            previous = lookup.get(key)
            if previous is not None and previous != canonical:
                raise RuntimeError(
                    f"Conflicting style aliases for {value!r}: "
                    f"{previous!r} vs {canonical!r}"
                )
            lookup[key] = canonical
    return lookup


_STYLE_LOOKUP: Final = _build_lookup()


def canonicalize_style_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if not cleaned:
        return None
    return _STYLE_LOOKUP.get(taxonomy_comparison_key(cleaned), cleaned)

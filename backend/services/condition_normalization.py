"""Canonical product-condition names shared by every Sheets parser.

The condition lookup is a small business dictionary, not a free-text field.
Keeping normalization here prevents notes shifted into the wrong Sheet column
from becoming permanent filter options.
"""

from __future__ import annotations

import re
from typing import Optional


CANONICAL_CONDITION_NAMES = (
    "Новий",
    "Хороший",
    "Легковживаний",
    "Вживаний",
    "Пошкоджений",
)


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


_CONDITION_ALIASES = {
    # Canonical values.
    **{_key(name): name for name in CANONICAL_CONDITION_NAMES},
    # Grammatical variants occasionally used in Sheets.
    "нове": "Новий",
    "нова": "Новий",
    "нові": "Новий",
    "хороше": "Хороший",
    "хороша": "Хороший",
    "хороші": "Хороший",
    "легковживане": "Легковживаний",
    "легковживана": "Легковживаний",
    "легковживані": "Легковживаний",
    "вживане": "Вживаний",
    "вживана": "Вживаний",
    "вживані": "Вживаний",
    "б/у": "Вживаний",
    "б.у.": "Вживаний",
    "бу": "Вживаний",
    "пошкоджене": "Пошкоджений",
    "пошкоджена": "Пошкоджений",
    "пошкоджені": "Пошкоджений",
    # Confirmed typo found in production data.
    "пошкодженний": "Пошкоджений",
}


def normalize_condition_name(value: object) -> Optional[str]:
    """Return a canonical condition or ``None`` for non-condition free text."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return _CONDITION_ALIASES.get(_key(raw))

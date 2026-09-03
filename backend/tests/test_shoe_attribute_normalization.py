"""Канонізація взуттєвих атрибутів: лише перевірені варіанти, ніякого fuzzy."""
from __future__ import annotations

import pytest

from backend.services.shoe_attribute_normalization import (
    CANONICAL_GROUPS,
    DEAD_VALUES,
    all_known_variants,
    canonicalize_shoe_attribute,
    is_dead_value,
    is_known_variant,
)


@pytest.mark.parametrize("attribute, variant, canonical", [
    # Одруківки з ЖИВИМИ товарами — саме заради них модуль і існує.
    ("toe_shape",      "круголий",    "круглий"),
    ("fastening_type", "шунурівка",   "шнурівка"),
    ("sole_type",      "споривна",    "спортивна"),
    # Рід і число як форми одного поняття.
    ("toe_shape",      "кругла",      "круглий"),
    ("sole_type",      "спортивний",  "спортивна"),
    ("toe_shape",      "квадрат",     "квадратний"),
    ("fastening_type", "кнопки",      "кнопка"),
    # Рішення власника: не задум, а запис нашвидкуруч.
    ("sole_type",      "підбора",     "каблук"),
    ("fastening_type", "магніт",      "магнітна кнопка"),
])
def test_reviewed_variants_map_to_canonical(attribute, variant, canonical):
    assert canonicalize_shoe_attribute(attribute, variant) == canonical


def test_canonical_value_is_stable():
    """Канон індексується теж — інше написання самого канону не зсуває його."""
    assert canonicalize_shoe_attribute("fastening_type", "Шнурівка") == "шнурівка"
    assert canonicalize_shoe_attribute("fastening_type", " шнурівка ") == "шнурівка"
    assert canonicalize_shoe_attribute("toe_shape", "круглий") == "круглий"


def test_homoglyph_is_folded():
    """Латинська «c» у кириличному слові — реальна пастка цієї бази."""
    assert canonicalize_shoe_attribute("sole_type", "cпортивна") == "спортивна"


@pytest.mark.parametrize("attribute, value", [
    # Свідомо РІЗНІ значення — зливати їх заборонено.
    ("sole_type", "танкетка"),
    ("sole_type", "платформа"),
    ("lining",    "поліестер"),
    ("lining",    "синтетика"),
])
def test_deliberately_distinct_values_are_untouched(attribute, value):
    assert canonicalize_shoe_attribute(attribute, value) == value
    assert not is_known_variant(attribute, value)


def test_unknown_value_is_cleaned_but_not_guessed():
    """Ключове: невідоме НЕ підганяється під схоже. Fuzzy тут немає."""
    assert canonicalize_shoe_attribute("sole_type", "  Термополіуретанова  ") == "Термополіуретанова"
    assert not is_known_variant("sole_type", "Термополіуретанова")


def test_empty_is_none():
    for empty in (None, "", "   "):
        assert canonicalize_shoe_attribute("sole_type", empty) is None


def test_dead_values_are_recognized():
    assert is_dead_value("sole_type", "goodyear welt")
    assert is_dead_value("toe_shape", "wingtip")
    assert not is_dead_value("sole_type", "спортивна")


def test_no_variant_claimed_by_two_canonicals():
    """Індекс будується без конфліктів (конструктор кинув би ValueError)."""
    for attribute in CANONICAL_GROUPS:
        assert isinstance(all_known_variants(attribute), dict)


def test_dead_values_never_overlap_live_canonicals():
    """Значення не може бути одночасно мертвим і канонічним — це б означало,
    що канонізація вказує на назву, яку ми ж не пропонуємо."""
    for attribute, groups in CANONICAL_GROUPS.items():
        dead = {d.strip().lower() for d in DEAD_VALUES.get(attribute, ())}
        canonicals = {c.strip().lower() for c in groups}
        assert not (dead & canonicals), f"{attribute}: {sorted(dead & canonicals)}"

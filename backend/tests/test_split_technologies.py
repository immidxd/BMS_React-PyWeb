"""Розбір складених технологій на атоми.

Від цієї функції залежить, чи рядок, зібраний назад, збігатиметься з тим, що
лежить у колонці «Технології» Журналу. Не збігся — і найближчий парс побачить
розбіжність там, де її нема.
"""
from __future__ import annotations

import pytest

from backend.scripts.backfill_product_technologies import split_technologies


@pytest.mark.parametrize("raw, atoms", [
    ("Vibram, MEGAGRIP", ["Vibram", "MEGAGRIP"]),
    ("Vibram, MEGAGRIP, Gore-tex", ["Vibram", "MEGAGRIP", "Gore-tex"]),
    ("Meta-Rocker", ["Meta-Rocker"]),
    # Рекордсмен: сім технологій в одній комірці.
    ("Gore-Tex, All Terrain Contagrip, Advanced Chassis, EnergyCell EVA, "
     "SensiFIT, OrthoLite, Quicklace",
     ["Gore-Tex", "All Terrain Contagrip", "Advanced Chassis", "EnergyCell EVA",
      "SensiFIT", "OrthoLite", "Quicklace"]),
])
def test_comma_separated(raw, atoms):
    assert split_technologies(raw) == atoms


def test_period_is_not_a_separator():
    """«gore-tex. Meta-Rocker» (4 товари) — одруківка, а не роздільник.

    Робити з крапки роздільник заради одного значення означало б поламати
    назви на кшталт «U.S. Grip». Одруківка лікується канонізацією написань,
    яка й так змінює аркуш; цей крок мусить лишатись writeback-нейтральним.
    """
    assert split_technologies("gore-tex. Meta-Rocker") == ["gore-tex. Meta-Rocker"]
    assert split_technologies("gore-tex, Meta-Rocker") == ["gore-tex", "Meta-Rocker"]


def test_trademark_symbols_are_part_of_the_name():
    """™/® не відрізаються: це написання, а не роздільник. Канонізацію написань
    робить окремий крок, який змінює й аркуш."""
    assert split_technologies("LiteRide™, Croslite™") == ["LiteRide™", "Croslite™"]


def test_dotted_names_are_not_broken():
    """Назви з крапками лишаються цілими — саме заради цього роздільник звужено."""
    assert split_technologies("U.S. Grip") == ["U.S. Grip"]
    assert split_technologies("Dr. Martens AirWair") == ["Dr. Martens AirWair"]


def test_order_is_preserved():
    assert split_technologies("B, A, C") == ["B", "A", "C"]


def test_duplicates_collapse():
    assert split_technologies("Vibram, Vibram") == ["Vibram"]


@pytest.mark.parametrize("raw", ["", "   ", ",", " , , ", None])
def test_empty_gives_nothing(raw):
    assert split_technologies(raw) == []


def test_rebuild_matches_original_shape():
    """Головний інваріант: зібране назад = те, що в аркуші."""
    for raw in ("Vibram, MEGAGRIP", "Cloudfoam, Ortholite", "REPREVE, Mush™"):
        assert ", ".join(split_technologies(raw)) == raw

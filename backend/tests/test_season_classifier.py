"""Парсер: сезон для рядка без «Сезону» в журналі."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.scripts.sheets_parser import _classify_season  # noqa: E402


def test_unknown_type_gets_no_invented_allseason():
    """Новий лот без виду: раніше «Всесезон», потім ШІ додавало «Демі» (#Ф4489)."""
    assert _classify_season("", "", "", "") == ""


def test_known_types_keep_their_rules():
    assert _classify_season("Ботинки", "", "", "") == "Єврозима"
    assert _classify_season("Кросівки", "", "", "") == "Всесезон"
    assert _classify_season("Шльопанці", "", "", "") == "Літо"
    assert _classify_season("", "", "", "футзалки для залу") == "Всесезон"   # спорт — як був

"""Назва моделі — заголовок товару в каталозі, а не артикул.

30.09.2026: схема ПИТАЛА `model_text`, модель його читала (у прогонах є
«M ANACAPA BREEZE LOW»), запис лягав у `ai_autofill_runs` — і на цьому все:
жодної пропозиції в поле «Модель» не було створено за всю історію таблиці.
Поле показується покупцю заголовком картки в каталозі, тож тиха втрата тут
найдорожча.

Друга половина задачі — не підмінити назву артикулом: модель уже віддавала
«GWTIAH5-EL» як назву моделі, а в базі осіли «MLR-W-CC1-03» і
«INT1222K075-KRK2PR», що затекли з журналу.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import photo_autofill as pa  # noqa: E402


@pytest.mark.parametrize("value", [
    "GWTIAH5-EL",            # віддала сама модель замість назви
    "MLR-W-CC1-03",          # затекло в базу з журналу
    "INT1222K075-KRK2PR",
    "9-25100-45",            # артикул Caprice
    "9-25404-45-855",
])
def test_article_codes_are_not_model_names(value):
    assert pa.looks_like_article_code(value) is True


@pytest.mark.parametrize("value", [
    "M ANACAPA BREEZE LOW",  # реально прочитане з бирки Merrell
    "Air Max 90",            # число стоїть ОКРЕМИМ словом — це назва
    "Gazelle 85",
    "Melissa",
    "SLIP",
    "574",                   # New Balance називає моделі числами
    "990", "1906",
    "Ботильйони",
    "", None,
])
def test_real_model_names_pass(value):
    assert pa.looks_like_article_code(value) is False


def test_model_name_is_offered_as_a_proposal(monkeypatch):
    """Головне: прочитана з бирки назва більше не зникає у звіті."""
    calls = []

    def fake_propose(db, pid, field, value, conf, **kw):
        calls.append((field, value)); return True
    monkeypatch.setattr(pa.field_proposals, "propose", fake_propose)

    fields = [f for f, _u in (("article_text", "marking"), ("brand_text", "brand_name"),
                              ("model_text", "model"))]
    assert "model_text" in fields, "поле назви моделі має бути серед текстових"


def test_code_never_reaches_the_catalog_title():
    """Заголовок картки в каталозі — це `model`. Артикул там означав би, що
    покупець бачить «GWTIAH5-EL» замість назви."""
    assert pa.looks_like_article_code("GWTIAH5-EL")
    assert not pa.looks_like_article_code("Anacapa Breeze Low")

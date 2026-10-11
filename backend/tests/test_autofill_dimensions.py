"""Автозаповнення: габарити сумки зі стікера.

#Ф4540 (сумка): модель прочитала стікер «1300 33x18g9.5 ф4540» дослівно, ціну
запропонувала, а габарити — ні: поля для них у розборі стікера не було. Тип у
картці ще був порожній, тож і запит ішов як для взуття.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import photo_autofill as pa  # noqa: E402

# Патчимо той обʼєкт, яким КОРИСТУЄТЬСЯ photo_autofill (dual-module-import-trap).
_FP_USED = pa.field_proposals


class _Rec:
    def __init__(self): self.calls = []
    def __call__(self, db, pid, field, value, conf, **kw):
        self.calls.append((field, value, conf, kw.get("note"))); return True


@pytest.mark.parametrize("text,expected", [
    ("1300 33x18g9.5 ф4540", "33х18х9.5"),          # #Ф4540: глибина через «g»
    ("1300 33х18 г 9,5 Ф4540", "33х18х9.5"),         # кирилиця, кома
    ("2500 42*24*6.5 ф4380", "42х24х6.5"),
    ("38 × 27 × 12", "38х27х12"),
    ("98х2 ремінь", "98х2"),                          # ремінь — два виміри
    ("1300 33X18", "33х18"),
])
def test_parse_sticker_dimensions(text, expected):
    assert pa.parse_sticker_dimensions(text) == expected


@pytest.mark.parametrize("text", [
    "2500 39 25,5 Ф4403",      # взуттєвий стікер — без «х»
    "XXL о/г 63 д 80",
    "1300 0x5",                # 0 см — не габарит
    "999x18",                  # поза межами
    "",
    None,
])
def test_parse_sticker_dimensions_rejects(text):
    assert pa.parse_sticker_dimensions(text) is None


def _run(monkeypatch, pred, current):
    rec = _Rec(); monkeypatch.setattr(_FP_USED, "propose", rec)
    proposed, below, already = [], [], []
    pa._dimensions_proposal(None, 1, pred, current, "x", "m", proposed, below, already)
    return rec, proposed, below, already


def test_bag_with_empty_type_uses_predicted_type(monkeypatch):
    """Саме випадок #Ф4540: тип у картці порожній, модель упізнала сумку."""
    pred = {"type": "Сумка", "sticker_text": "1300 33x18g9.5 ф4540"}
    rec, proposed, _b, _a = _run(monkeypatch, pred, {"type_name": None})
    assert [(f, v) for f, v, _c in proposed] == [("dimensions", "33х18х9.5")]
    assert "33x18g9.5" in rec.calls[0][3]       # у примітці — що саме прочитано


def test_shoe_gets_no_dimensions(monkeypatch):
    pred = {"type": "Кросівки", "sticker_text": "2500 39x25"}
    rec, proposed, _b, _a = _run(monkeypatch, pred, {"type_name": "Кросівки"})
    assert proposed == [] and rec.calls == []


def test_existing_dimensions_are_already_correct(monkeypatch):
    pred = {"type": "Сумка", "sticker_text": "1300 33x18g9.5"}
    rec, proposed, _b, already = _run(monkeypatch, pred, {"type_name": "Сумка", "dimensions": "33x18x9.5"})
    assert proposed == [] and ("dimensions", "33х18х9.5") in already


def test_card_type_bag_wins_even_if_model_says_other(monkeypatch):
    pred = {"type": "Аксесуар", "sticker_text": "800 25x15x7"}
    _rec, proposed, _b, _a = _run(monkeypatch, pred, {"type_name": "Сумка"})
    assert proposed and proposed[0][1] == "25х15х7"


def test_full_sticker_path_proposes_price_and_dimensions(monkeypatch):
    """Через `_sticker_proposals`: номер збігся → і ціна, і габарити."""
    rec = _Rec(); monkeypatch.setattr(_FP_USED, "propose", rec)
    pred = {"type": "Сумка", "sticker_text": "1300 33x18g9.5 ф4540", "sticker_number": "ф4540",
            "sticker_price": 1300.0, "sticker_price_confidence": 1.0}
    current = {"productnumber": "#Ф4540", "type_name": None, "price": None, "dimensions": None}
    proposed, below, already = [], [], []
    out = pa._sticker_proposals(None, 1, pred, current, "x", "m", proposed, below, already)
    assert out["matched"] is True
    fields = {f: v for f, v, _c in proposed}
    assert fields == {"dimensions": "33х18х9.5", "price": "1300"}

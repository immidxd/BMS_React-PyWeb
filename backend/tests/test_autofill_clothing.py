"""Автозаповнення для ОДЯГУ: буквений розмір і заміри зі стікера.

#Ф4425 (кофта): модель прочитала стікер «XXL, о/г 63, д 80» дослівно, але в
картку не потрапило нічого — схема питала EU-розмір числом і устілку в см,
і для кофти ці цифри були «чужі». Тут стережемо, щоб форма запиту залежала
від категорії товару, а прочитане мало куди лягти.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import field_proposals as fp  # noqa: E402
from backend.services import photo_autofill as pa  # noqa: E402
from backend.services import product_category as pc  # noqa: E402


# ── Категорія ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("type_name,cat", [
    ("Кофта", "clothing"), ("Костюм", "clothing"), ("Штани", "clothing"),
    ("Кросівки", "shoe"), ("Ботинки", "shoe"), (None, "shoe"),
    ("Сумка", "bag"), ("Cумка", "bag"),          # латинська C — гомогліф
    ("Валіза", "suitcase"), ("Ремінь", "accessory"),
])
def test_category_mirrors_frontend(type_name, cat):
    assert pc.category_of(type_name) == cat


@pytest.mark.parametrize("type_name,sub", [
    ("Кофта", "top"), ("Футболка", "top"), ("Костюм", "suit"),
    ("Плаття", "dress"), ("Штани", "bottom"), ("Джинси", "bottom"),
])
def test_clothing_subcategory(type_name, sub):
    assert pc.clothing_subcat(type_name) == sub


@pytest.mark.parametrize("raw,expected", [
    ("XXL", "XXL"), ("xxl", "XXL"), ("2XL", "XXL"), ("3xl", "XXXL"), (" m ", "M"),
    ("44", None), ("", None), (None, None),
])
def test_size_letter_normalization(raw, expected):
    assert pc.normalize_size_letter(raw) == expected


# ── Схема ───────────────────────────────────────────────────────────────────

class _NoDB:
    """build_schema читає довідники; для одягу закриті взуттєві поля не
    питаються, а решта — порожній перелік."""
    def execute(self, *_a, **_k):
        class _R:
            def fetchall(self): return []
        return _R()


def test_clothing_schema_asks_for_letter_size_and_measurements():
    props = pa.build_schema(_NoDB(), category="clothing", subcat="top")["properties"]
    assert "sticker_size_letter" in props
    assert props["sticker_size_letter"]["enum"][:3] == ["XXS", "XS", "S"]
    meas = props["sticker_measurements"]["properties"]
    # Кофта: груди, рукав, довжина — без талії й бедер.
    assert set(meas) == {"pog", "sleeve", "length"}
    # Взуттєвого — жодного.
    for shoe_only in ("sole_type", "tread_type", "heel_type", "toe_shape",
                      "sticker_size", "sticker_cm", "materials_pictogram"):
        assert shoe_only not in props, shoe_only


def test_suit_asks_for_sleeve_and_two_lengths():
    props = pa.build_schema(_NoDB(), category="clothing", subcat="suit")["properties"]
    assert set(props["sticker_measurements"]["properties"]) == {
        "pog", "pot", "pob", "sleeve", "length_top", "length_bottom"}


def test_suit_lengths_are_summed_and_explained_in_note(monkeypatch):
    """Рішення власника: у «Довжина» — сума верху й низу, розклад — у примітку."""
    rec = _Rec(); monkeypatch.setattr(_FP_USED, "propose", rec)
    pred = {"sticker_measurements": {"pog": 58, "sleeve": 54, "length_top": 65, "length_bottom": 102},
            "sticker_measurements_confidence": 0.9}
    proposed, below, already = [], [], []
    pa._clothing_sticker_proposals(None, 1, pred, {"extranote": "стара примітка"}, "x", "m",
                                   proposed, below, already)
    fields = {f: v for f, v, _c in proposed}
    assert fields["meas:length"] == "167" and fields["meas:sleeve"] == "54"
    assert fields["extranote"] == "Довжина: кофта 65 см, штани 102 см"
    # у базу йде обʼєднання з наявною приміткою, а не заміна
    saved = {f: v for f, v, _c in rec.calls}
    assert saved["extranote"] == "стара примітка\nДовжина: кофта 65 см, штани 102 см"


def test_suit_with_one_length_has_no_note(monkeypatch):
    rec = _Rec(); monkeypatch.setattr(_FP_USED, "propose", rec)
    pred = {"sticker_measurements": {"length_top": 65, "length_bottom": None},
            "sticker_measurements_confidence": 0.9}
    proposed, below, already = [], [], []
    pa._clothing_sticker_proposals(None, 1, pred, {}, "x", "m", proposed, below, already)
    assert [f for f, _v, _c in proposed] == ["meas:length"]


def test_prompt_says_r_is_sleeve_not_size():
    assert "РУКАВА" in pa.PROMPT_CLOTHING and "не розмір" in pa.PROMPT_CLOTHING


def test_bottom_asks_for_waist_and_hips_not_sleeve():
    props = pa.build_schema(_NoDB(), category="clothing", subcat="bottom")["properties"]
    assert set(props["sticker_measurements"]["properties"]) == {"pot", "pob", "length"}


def test_shoe_schema_unchanged():
    props = pa.build_schema(_NoDB(), category="shoe")["properties"]
    assert "sticker_size" in props and "sticker_cm" in props
    assert "sticker_size_letter" not in props and "sticker_measurements" not in props
    assert "materials_pictogram" in props


def test_clothing_schema_converts_to_gemini_dialect():
    schema = pa.build_schema(_NoDB(), category="clothing", subcat="dress")
    g = pa.to_gemini_schema(schema)
    meas = g["properties"]["sticker_measurements"]["properties"]["pog"]
    assert meas.get("nullable") is True and "null" not in str(meas.get("type"))


def test_clothing_prompt_explains_sticker_abbreviations():
    for token in ("о/г", "о/т", "о/б", "«д»", "«р»"):
        assert token in pa.PROMPT_CLOTHING


# ── Пропозиції → ProductUpdate ──────────────────────────────────────────────

def test_measurement_proposals_become_measurements_edit():
    update = {}
    fp._merge_update(update, "meas:pog", "63")
    fp._merge_update(update, "meas:length", "80")
    fp._merge_update(update, "size_letter", "XXL")
    assert update == {"measurements_edit": {"pog": "63", "length": "80"}, "size_letter": "XXL"}


def test_measurement_threshold_is_the_sticker_one():
    assert fp.threshold_for("meas:pog") == 0.70
    assert fp.threshold_for("size_letter") == 0.75


# ⚠️ Патчити той обʼєкт модуля, яким КОРИСТУЄТЬСЯ photo_autofill (services.X і
# backend.services.X — два різні обʼєкти, див. dual-module-import-trap).
_FP_USED = pa.field_proposals


class _Rec:
    """Збирає propose(), не ходячи в базу."""
    def __init__(self): self.calls = []
    def __call__(self, db, pid, field, value, conf, **kw):
        self.calls.append((field, value, conf)); return True


def test_sticker_measurements_out_of_bounds_are_not_proposed(monkeypatch):
    rec = _Rec(); monkeypatch.setattr(_FP_USED, "propose", rec)
    pred = {"sticker_size_letter": "XXL", "sticker_size_letter_confidence": 0.95,
            "sticker_measurements": {"pog": 63, "length": 800, "sleeve": 74},
            "sticker_measurements_confidence": 0.9}
    proposed, below, already = [], [], []
    pa._clothing_sticker_proposals(None, 1, pred, {}, "x", "m", proposed, below, already)
    assert [f for f, _v, _c in proposed] == ["size_letter", "meas:pog", "meas:sleeve"]
    assert ("meas:length", 800, 0.9) in below       # 800 см — хибне читання


def test_sticker_value_equal_to_card_is_already_correct(monkeypatch):
    rec = _Rec(); monkeypatch.setattr(_FP_USED, "propose", rec)
    pred = {"sticker_size_letter": "xxl", "sticker_size_letter_confidence": 0.9,
            "sticker_measurements": {"pog": 63.0}, "sticker_measurements_confidence": 0.9}
    current = {"size_letter": "XXL", "meas_pog_min": 63.0, "meas_pog_max": None}
    proposed, below, already = [], [], []
    pa._clothing_sticker_proposals(None, 1, pred, current, "x", "m", proposed, below, already)
    assert proposed == []
    assert ("size_letter", "XXL") in already and ("meas:pog", "63") in already


# ── #Ф4420: «кофта на блискавці» у підвиді костюма ──────────────────────────

class _EmptySubtypesDB:
    """Довідник, де у типу немає ЖОДНОГО підвиду (як у «Костюма»), а решта
    переліків має по одному значенню."""
    def execute(self, sql, params=None):
        q = str(sql)
        class _R:
            def __init__(self, rows): self._rows = rows
            def fetchall(self): return self._rows
        if "subtypes" in q:
            return _R([])
        if "fastening_types" in q:
            return _R([("блискавка", 5)])
        return _R([("x", 1)])


def test_field_without_options_is_not_asked_at_all():
    """Порожній enum у діалекті Gemini = «будь-який рядок»: модель вигадала
    підвид «кофта на блискавці» для костюма. Немає з чого обирати — не питаємо."""
    props = pa.build_schema(_EmptySubtypesDB(), type_id=181, category="clothing", subcat="dress")["properties"]
    assert "subtype" not in props
    assert "subtype_confidence" not in props


def test_clothing_keeps_fastening_and_lining():
    """Застібка й підкладка — універсальні: у костюма теж є блискавка. Без поля
    «застібка» модель тягне блискавку в підвид."""
    props = pa.build_schema(_EmptySubtypesDB(), category="clothing", subcat="dress")["properties"]
    assert "fastening_type" in props and "lining" in props
    assert "блискавка" in props["fastening_type"]["enum"]
    for shoe_only in ("sole_type", "tread_type", "toe_shape", "heel_type"):
        assert shoe_only not in props


# ── Підкладка: піктограма ЄС занадто груба ──────────────────────────────────

def _material_run(monkeypatch, pred, current=None):
    calls = []

    def fake_propose(db, pid, field, value, conf, **kw):
        calls.append((field, value, kw.get("note"))); return True
    monkeypatch.setattr(_FP_USED, "propose", fake_propose)
    monkeypatch.setattr(pa, "_current_materials", lambda db, pid: dict(current or {}))
    proposed, below, already = [], [], []
    pa._material_proposals(None, 1, pred, "x", "m", proposed, below, already)
    return calls, proposed, already


def test_fur_lining_beats_the_pictogram_textile(monkeypatch):
    """#Ф4406: на знімку всередині хутро, а піктограма ЄС знає лише чотири
    символи — за директивою хутро теж «текстиль». Формально вірно, а покупцю
    зимових ботинок потрібне саме хутро."""
    pred = {"materials_pictogram": {"upper": "шкіра", "lining": "текстиль", "outsole": "інше"},
            "materials_pictogram_confidence": 0.9,
            "lining_visual": "штучне хутро", "lining_visual_confidence": 0.9}

    calls, _proposed, _already = _material_run(monkeypatch, pred)

    middle = [(v, n) for f, v, n in calls if f == "material:middle"]
    assert middle and middle[0][0] == "штучне хутро"
    assert "видно на знімку" in middle[0][1] and "текстиль" in middle[0][1]


def test_visual_never_overrides_leather_pictogram(monkeypatch):
    """Декларація виробника важить більше за здогад по фото: якщо піктограма
    каже «шкіра», побачене її не перебиває."""
    pred = {"materials_pictogram": {"upper": "шкіра", "lining": "шкіра", "outsole": "інше"},
            "materials_pictogram_confidence": 0.9,
            "lining_visual": "штучне хутро", "lining_visual_confidence": 0.95}

    calls, _p, _a = _material_run(monkeypatch, pred)

    middle = [v for f, v, _n in calls if f == "material:middle"]
    assert middle == ["шкіра"]


def test_plain_textile_stays_textile(monkeypatch):
    pred = {"materials_pictogram": {"upper": "шкіра", "lining": "текстиль", "outsole": "інше"},
            "materials_pictogram_confidence": 0.9,
            "lining_visual": "текстиль", "lining_visual_confidence": 0.9}

    calls, _p, _a = _material_run(monkeypatch, pred)

    assert [v for f, v, _n in calls if f == "material:middle"] == ["текстиль"]


def test_unsure_visual_does_not_refine(monkeypatch):
    """Нижче порога — лишаємо піктограму: вигадане хутро гірше за грубий, але
    чесний «текстиль»."""
    pred = {"materials_pictogram": {"upper": "шкіра", "lining": "текстиль", "outsole": "інше"},
            "materials_pictogram_confidence": 0.9,
            "lining_visual": "штучне хутро", "lining_visual_confidence": 0.4}

    calls, _p, _a = _material_run(monkeypatch, pred)

    assert [v for f, v, _n in calls if f == "material:middle"] == ["текстиль"]

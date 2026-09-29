"""Четвертий шар: офіційна сторінка виробника за артикулом.

Привід (29.09.2026, #Ф4400 Caprice): на сторінці виробника за артикулом
9-25404-45-855 прямо написано «Ширина взуття: G-ширина», «Висота вала: 13 см»,
«Висота каблука: 3,2 см» — рівно той довгий хвіст, заради якого автозаповнення
й робилось. Жоден із трьох наявних шарів туди не дивиться.

Головне, що стережуть ці тести: БЕЗ ДЖЕРЕЛА нічого не приймається. Саме тут
модель найбільше схильна «пригадати» характеристики бренда напамʼять.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import web_enrich as we  # noqa: E402


CAPRICE = {
    "found": True, "confidence": 0.95,
    "source_url": "https://www.caprice.de/9-25404-45-855",
    "model_name": "Melissa",
    "width": "G-ширина",
    "toe_shape": "заокруглена", "fastening_type": "блискавка",
    "lining": "текстиль", "heel_type": "блок", "sole_type": "каблук",
    "heel_height_cm": 3.2, "shaft_height_cm": 13, "sole_thickness_cm": None,
    "material_upper": "шкіра", "material_lining": "текстиль", "material_sole": "синтетичний",
    "technologies": ["CAPRICE AIRMOTION"],
    "_sources": ["https://www.caprice.de/9-25404-45-855"],
    "_usage": {"promptTokenCount": 900, "candidatesTokenCount": 180},
}

CURRENT = {"productnumber": "#Ф4400", "brand_name": "Caprice", "marking": "9-25404-45-855",
           "type_name": "Ботинки", "model": None, "width": None,
           "heel_type_name": "блок"}          # уже стоїть у картці


class _DB:
    def execute(self, sql, params=None):
        class _R:
            def fetchall(self): return []
            def fetchone(self): return None
            def scalar(self): return 0
        return _R()
    def commit(self): pass


def _setup(monkeypatch, answer, current=None):
    calls = []
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "k")
    monkeypatch.setattr(we.photo_autofill, "_current_values",
                        lambda db, pid: dict(current or CURRENT))
    monkeypatch.setattr(we.photo_autofill, "_record_run", lambda *a, **k: None)
    monkeypatch.setattr(we.photo_autofill, "closed_enum_values",
                        lambda db, f, **k: {"toe_shape": ["заокруглена", "круглий"],
                                            "fastening_type": ["блискавка", "шнурівка"],
                                            "lining": ["текстиль", "шкіра"],
                                            "heel_type": ["блок", "шпилька"],
                                            "sole_type": ["каблук", "платформа"]}.get(f, []))
    monkeypatch.setattr(we.ai_budget, "guard", lambda *a, **k: type(
        "V", (), {"allowed": True, "reason": None, "spent_usd": 0.0})())
    monkeypatch.setattr(we.ai_budget, "record", lambda *a, **k: 0.004)
    monkeypatch.setattr(we, "_call", lambda *a, **k: dict(answer))

    def fake_propose(db, pid, field, value, conf, **kw):
        calls.append((field, value, kw.get("source"), kw.get("note")))
        return True
    monkeypatch.setattr(we.field_proposals, "propose", fake_propose)
    return calls


def test_no_article_means_no_search(monkeypatch):
    """Ключ пошуку — артикул. Немає його — шар мовчить, як профіль без моделі."""
    _setup(monkeypatch, CAPRICE, current={**CURRENT, "marking": ""})
    out = we.enrich_by_article(_DB(), 1)
    assert out["ok"] is False and "артикула" in out["reason"]


def test_values_from_the_page_become_proposals(monkeypatch):
    calls = _setup(monkeypatch, CAPRICE)

    out = we.enrich_by_article(_DB(), 1)

    assert out["ok"] is True and out["found"] is True
    got = {f: v for f, v, _s, _n in calls}
    # Ширина проходить через наш нормалізатор: «G-ширина» → «G».
    assert got["width"] == "G"
    assert got["model"] == "Melissa"
    assert got["meas:heel"] == "3.2" and got["meas:height"] == "13"
    assert got["material:upper"] == "шкіра" and got["material:sole"] == "синтетичний"
    assert got["technology_name"] == "CAPRICE AIRMOTION"
    assert got["toe_shape_name"] == "заокруглена"


def test_every_proposal_carries_its_source(monkeypatch):
    """Джерело — не прикраса: за ним людина перевіряє, звідки взялось значення."""
    calls = _setup(monkeypatch, CAPRICE)
    we.enrich_by_article(_DB(), 1)

    assert calls, "мали бути пропозиції"
    for _f, _v, source, note in calls:
        assert source == "web"
        assert "caprice.de" in note


def test_value_already_in_card_is_not_proposed_again(monkeypatch):
    calls = _setup(monkeypatch, CAPRICE)
    out = we.enrich_by_article(_DB(), 1)

    assert "heel_type_name" not in {f for f, _v, _s, _n in calls}
    assert ("heel_type_name", "блок") in out["already_correct"]


def test_nothing_is_accepted_without_a_source(monkeypatch):
    """Модель «пригадала» характеристики, але сторінки не знайшла — це найнебезпечніший
    випадок, і він має давати НУЛЬ пропозицій."""
    calls = _setup(monkeypatch, {**CAPRICE, "_sources": []})
    out = we.enrich_by_article(_DB(), 1)

    assert out["found"] is False
    assert calls == []


def test_found_false_is_respected(monkeypatch):
    calls = _setup(monkeypatch, {**CAPRICE, "found": False})
    out = we.enrich_by_article(_DB(), 1)

    assert out["found"] is False and calls == []


def test_absurd_measurement_is_rejected(monkeypatch):
    """«Висота каблука 320 см» — хибне читання сторінки, а не рекорд."""
    calls = _setup(monkeypatch, {**CAPRICE, "heel_height_cm": 320})
    out = we.enrich_by_article(_DB(), 1)

    assert "meas:heel" not in {f for f, _v, _s, _n in calls}
    assert any(f == "meas:heel" for f, _v, _c in out["below_threshold"])


def test_garbage_width_does_not_reach_the_card(monkeypatch):
    """Ширину проводимо через той самий нормалізатор, що й ручне введення."""
    calls = _setup(monkeypatch, {**CAPRICE, "width": "дуже широка колодка"})
    we.enrich_by_article(_DB(), 1)

    assert "width" not in {f for f, _v, _s, _n in calls}


def test_paid_key_missing_is_explained_not_crashed(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    monkeypatch.setattr(we.photo_autofill, "_current_values", lambda db, pid: dict(CURRENT))

    out = we.enrich_by_article(_DB(), 1)

    assert out["ok"] is False and out["needs_paid"] is True


def test_402_is_translated_into_human_words(monkeypatch):
    _setup(monkeypatch, {"_error": "HTTP 402: no credits", "_status": 402})
    out = we.enrich_by_article(_DB(), 1)

    assert out["needs_paid"] is True
    assert "поповни" in out["reason"].lower()
    assert "402" not in out["reason"]


# ── Одна дія на всі джерела ─────────────────────────────────────────────────

def test_layer_is_skipped_without_article_or_paid_key(monkeypatch):
    """Спільний прогін «Розпізнати» не має падати на товарі без артикула."""
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "k")
    assert we.available({"marking": "9-25404"}) is True
    assert we.available({"marking": ""}) is False
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    assert we.available({"marking": "9-25404"}) is False


def test_agreement_with_photo_raises_confidence(monkeypatch):
    """Знімок і сторінка виробника сказали те саме — певність росте, і в
    підписі видно обидва джерела."""
    calls = _setup(monkeypatch, CAPRICE)
    proposed = [("toe_shape_name", "заокруглена", 0.7)]
    made = {"toe_shape_name": ("заокруглена", 0.7, "фото")}

    we.layer(_DB(), 1, dict(CURRENT), proposed=proposed, already=[], confirmed=[], made=made)

    assert made["toe_shape_name"][1] == pytest.approx(0.95)   # max(0.7, 0.95)
    assert ("toe_shape_name", "заокруглена", 0.95) in proposed
    note = [n for f, _v, _s, n in calls if f == "toe_shape_name"][0]
    assert "фото" in note and "caprice.de" in note


def test_disagreement_keeps_the_photo_value_and_shows_the_other(monkeypatch):
    """Фото каже «круглий», виробник — «заокруглена». Рішення за знімком (він
    бачив саме цю пару), але альтернатива має бути на очах — це і є звірка."""
    calls = _setup(monkeypatch, CAPRICE)
    proposed = [("toe_shape_name", "круглий", 0.8)]
    made = {"toe_shape_name": ("круглий", 0.8, "фото")}

    we.layer(_DB(), 1, dict(CURRENT), proposed=proposed, already=[], confirmed=[], made=made)

    kept = [(f, v) for f, v, _s, _n in calls if f == "toe_shape_name"]
    assert kept == [("toe_shape_name", "круглий")], "значення знімка не підмінюємо"
    note = [n for f, _v, _s, n in calls if f == "toe_shape_name"][0]
    assert "у виробника: заокруглена" in note


def test_fields_untouched_by_photo_come_straight_from_the_page(monkeypatch):
    calls = _setup(monkeypatch, CAPRICE)
    proposed: list = []
    made: dict = {}

    we.layer(_DB(), 1, dict(CURRENT), proposed=proposed, already=[], confirmed=[], made=made)

    got = {f: v for f, v, _s, _n in calls}
    assert got["width"] == "G"          # знімки про ширину колодки не знають
    assert made["width"][0] == "G"

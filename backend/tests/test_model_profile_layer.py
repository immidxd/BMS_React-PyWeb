"""Третій шар: що каже НАША власна база про цю саму модель.

Головна перевірка тут — стриманість. Шар має озиватись лише там, де минулі
записи сходяться повністю, і мовчати скрізь інде.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import model_profile as mp  # noqa: E402
from backend.services import photo_autofill as pa  # noqa: E402
from backend.tests.test_photo_autofill import _DB, _photo  # noqa: E402


def _agg(value, share, total):
    return {"value": value, "share": share, "total": total}


# ── Стриманість ─────────────────────────────────────────────────────────────

def test_unanimous_requires_full_agreement():
    """Три записи з двома думками — не свідчення, а суперечка."""
    prof = {"fields": {"sole_type_name": _agg("плоска", 2, 3)}}
    assert mp.unanimous(prof) == {}


def test_unanimous_requires_two_records():
    """Один минулий запис — це не «сходяться», а чиясь одна думка."""
    prof = {"fields": {"sole_type_name": _agg("плоска", 1, 1)}}
    assert mp.unanimous(prof) == {}
    prof = {"fields": {"sole_type_name": _agg("плоска", 2, 2)}}
    assert mp.unanimous(prof) == {"sole_type_name": ("плоска", 2)}


@pytest.mark.parametrize("field", ["marking", "gender_name", "season",
                                   "manufacturer_country_name"])
def test_unreliable_fields_are_excluded(field):
    """Чотири поля виключені за виміром 06.09.2026 — межа відбору 80%.

    marking 48% — модель одна, а артикул кодує ще й розцвітку (GR530AA);
    gender_name 61% — і картка такого поля не приймає;
    season 74% — одна модель буває і літньою, і демісезонною;
    manufacturer_country_name 74% — той самий Gazelle шиють і у Вʼєтнамі,
    і в Індонезії, залежно від партії.

    Нижче межі одностайність малої групи — радше збіг, ніж свідчення, і саме
    такий збіг дає найнебезпечніший результат: правдоподібний і безпідставний.
    """
    assert field not in mp.PROPOSABLE
    assert mp.unanimous({"fields": {field: _agg("будь-що", 5, 5)}}) == {}


def test_confidence_never_reaches_certainty():
    """Тут висновок за подібністю, а не контрольна сума. Одиниці не буває."""
    assert mp.confidence_for(2) == pytest.approx(0.91)
    assert mp.confidence_for(50) == 0.97
    assert all(mp.confidence_for(n) < 1.0 for n in range(1, 100))


def test_every_proposable_field_is_accepted_by_the_card():
    """Пропозиція, яку нікуди застосувати, — це помилка при прийнятті."""
    from backend.schemas.product import ProductUpdate
    bad = [f for f in mp.PROPOSABLE if f not in ProductUpdate.model_fields]
    assert not bad, f"ProductUpdate не приймає: {bad}"


def test_proposable_is_a_subset_of_the_aggregate():
    """Поле, якого агрегат не рахує, шар ніколи не побачить — це була б
    мовчазна дірка, а не обмеження."""
    assert set(mp.PROPOSABLE) <= set(mp.FIELDS)


# ── Зустріч трьох шарів ─────────────────────────────────────────────────────

class _ProfDB(_DB):
    """Двійник, що вміє віддати профіль моделі."""


def _setup(monkeypatch, *, profile_fields, ai=None, card=None):
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: card or {})
    monkeypatch.setattr(pa.model_profile, "profile_for",
                        lambda db, b, m, exclude_id=None: {
                            "records": 3, "numbers": [], "materials": {},
                            "fields": profile_fields})
    monkeypatch.setattr(pa.model_profile, "current_values", lambda db, pid: card or {})
    payload = dict(ai or {})
    payload["_usage"] = {"promptTokenCount": 10, "candidatesTokenCount": 1}
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: payload)


def test_profile_fills_what_the_model_did_not_see(monkeypatch, tmp_path):
    """Модель не розгледіла підкладку — минулі записи знають. І це безкоштовно."""
    _setup(monkeypatch,
           card={"brand_name": "New Balance", "model": "530"},
           profile_fields={"lining_name": _agg("текстиль", 4, 4)})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("lining_name", "текстиль", pytest.approx(0.97)) in out["proposed"]


def test_agreement_between_layers_raises_confidence(monkeypatch, tmp_path):
    """Модель побачила на фото те саме, що люди вписували в минулі пари."""
    seen = {}
    _setup(monkeypatch,
           card={"brand_name": "New Balance", "model": "530"},
           profile_fields={"sole_type_name": _agg("спортивна", 4, 4)},
           ai={"sole_type": "спортивна", "sole_type_confidence": 0.75})
    monkeypatch.setattr(pa.field_proposals, "propose",
                        lambda db, pid, f, v, c, **kw: seen.update({f: (v, c, kw)}) or True)
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    value, conf, kw = seen["sole_type_name"]
    assert conf == pytest.approx(0.97), "збіг двох шарів не підняв певність"
    assert kw["source"] == "photo+profile"
    assert ("sole_type_name", "спортивна", pytest.approx(0.97)) in out["proposed"]


def test_disagreement_keeps_the_model_and_shows_the_alternative(monkeypatch, tmp_path):
    """Модель дивилась на ЦЮ пару — її слово лишається. Але людина має бачити
    й те, що стоїть у минулих записах тієї ж моделі."""
    seen = {}
    _setup(monkeypatch,
           card={"brand_name": "New Balance", "model": "530"},
           profile_fields={"sole_type_name": _agg("плоска", 4, 4)},
           ai={"sole_type": "спортивна", "sole_type_confidence": 0.95})
    monkeypatch.setattr(pa.field_proposals, "propose",
                        lambda db, pid, f, v, c, **kw: seen.update({f: (v, c, kw)}) or True)
    pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    value, conf, kw = seen["sole_type_name"]
    assert value == "спортивна", "профіль затер пропозицію моделі"
    assert "плоска" in kw["note"], "альтернативу від профілю приховано"


def test_profile_confirms_what_the_card_already_has(monkeypatch, tmp_path):
    _setup(monkeypatch,
           card={"brand_name": "New Balance", "model": "530",
                 "lining_name": "текстиль"},
           profile_fields={"lining_name": _agg("текстиль", 4, 4)})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("lining_name", "текстиль", "profile") in out["confirmed"]
    assert not any(f == "lining_name" for f, *_ in out["proposed"])


def test_profile_works_without_the_ai(monkeypatch, tmp_path):
    """Безкоштовний шар не має гаснути разом із платним."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _setup(monkeypatch,
           card={"brand_name": "New Balance", "model": "530"},
           profile_fields={"lining_name": _agg("текстиль", 4, 4)})
    out = pa.extract_and_propose(_DB(), 7, [_photo(tmp_path)], api_key=None)
    assert ("lining_name", "текстиль", pytest.approx(0.97)) in out["proposed"]


def test_model_read_from_the_tag_can_open_the_profile(monkeypatch, tmp_path):
    """У нового товару бренда й моделі в картці ще немає — їх читає модель.

    Це не робить шар залежним від здогаду: сам факт, що прочитана назва
    ЗНАЙШЛАСЬ у нашому каталозі, і є її перевіркою. Вигадка ні на що не
    натрапить, і шар промовчить.
    """
    _setup(monkeypatch, card={},
           profile_fields={"lining_name": _agg("текстиль", 4, 4)},
           ai={"brand_text": "New Balance", "model_text": "530"})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("lining_name", "текстиль", pytest.approx(0.97)) in out["proposed"]


def test_silent_when_the_model_is_unknown_to_us(monkeypatch, tmp_path):
    _setup(monkeypatch, card={"brand_name": "Nike", "model": "Вигадка"},
           profile_fields={})
    monkeypatch.setattr(pa.model_profile, "profile_for",
                        lambda db, b, m, exclude_id=None: {"records": 0, "fields": {}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert out["proposed"] == []


def test_silent_without_a_model_name(monkeypatch, tmp_path):
    """Без назви моделі шукати нічого — шар мовчить, а не шукає за брендом."""
    calls = []
    _setup(monkeypatch, card={"brand_name": "Nike"}, profile_fields={})
    monkeypatch.setattr(pa.model_profile, "profile_for",
                        lambda db, b, m, exclude_id=None: calls.append(1) or {"records": 0})
    pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert calls == [], "шукали профіль за самим брендом"


# ── Одностайне сміття ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value, leaked", [
    ("4.5", True),          # розмір, що затік у довідник пакування (2 товари)
    ("719-04", True),       # код виробника в полі колекції (Anna Lucci «8101»)
    ("530", True),
    ("D", False),           # повнота — легітимна літера
    ("2E", False),          # повнота теж буває з цифрою
    ("Hoka One One", False),
    ("Без рукавів", False), # блуза esmara — у базі не лише взуття
    ("Шотландський", False),
])
def test_leaked_data_is_recognised(value, leaked):
    assert mp.looks_like_leaked_data(value) is leaked


def test_unanimous_garbage_is_still_garbage():
    """Найважливіше обмеження шару, назване прямо.

    Перевірка одностайності захищає від СУПЕРЕЧКИ між записами, але не від
    послідовно НЕПРАВИЛЬНОГО значення. «4.5» у довіднику пакування стояло на
    двох товарах — шар збирався додати його ще двом і підвищити сміття до
    чотирьох.
    """
    prof = {"fields": {"packaging_name": _agg("4.5", 2, 2)}}
    assert mp.unanimous(prof) == {}
    # а справжня назва тієї ж групи проходить
    assert mp.unanimous({"fields": {"packaging_name": _agg("коробка", 2, 2)}})

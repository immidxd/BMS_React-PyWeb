"""Сервіс розпізнавання: діалект схеми, гальмо бюджету, межа модуля."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import photo_autofill as pa  # noqa: E402


# ── Діалект Gemini ──────────────────────────────────────────────────────────

def test_union_type_becomes_nullable():
    """Gemini не розуміє ["string","null"] — у нього окреме поле nullable."""
    out = pa.to_gemini_schema({"type": ["string", "null"], "description": "x"})
    assert out["type"] == "STRING"
    assert out["nullable"] is True


def test_null_is_stripped_from_enum():
    """`null` усередині enum Gemini теж не приймає — його несе nullable."""
    out = pa.to_gemini_schema({"type": ["string", "null"], "enum": ["a", "b", None]})
    assert out["enum"] == ["a", "b"]
    assert None not in out["enum"]
    assert out["nullable"] is True


def test_array_items_are_converted_too():
    out = pa.to_gemini_schema({"type": "array", "items": {"type": ["string", "null"]}})
    assert out["type"] == "ARRAY"
    assert out["items"]["type"] == "STRING"


def test_nested_properties_and_required_survive():
    out = pa.to_gemini_schema({
        "type": "object", "required": ["a"],
        "properties": {"a": {"type": ["string", "null"], "enum": ["x", None]}},
    })
    assert out["type"] == "OBJECT"
    assert out["required"] == ["a"]
    assert out["properties"]["a"]["enum"] == ["x"]


# ── Межа модуля й гальмо ────────────────────────────────────────────────────

class _DB:
    def __init__(self, spent=0.0):
        self.spent = spent
        self.sql: list[str] = []
    def execute(self, stmt, params=None):
        self.sql.append(" ".join(str(stmt).split()))
        return _Res(self.spent)


class _Res:
    """Двійник результату. `.mappings()` потрібен для _current_values —
    він читає поточні значення картки, щоб не пропонувати вже правильне."""
    def __init__(self, v): self._v = v
    def scalar(self): return self._v
    def fetchall(self): return []
    def fetchone(self): return None
    def mappings(self): return _Res(self._v)


def test_budget_block_prevents_any_call(monkeypatch, tmp_path):
    """За вичерпаною стелею провайдер НЕ викликається взагалі."""
    called = []
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: called.append(1) or {})
    photo = tmp_path / "x_001.webp"
    photo.write_bytes(b"fake")

    from backend.services import ai_budget
    db = _DB(spent=ai_budget.MONTHLY_CAP_USD)
    out = pa.extract_and_propose(db, 1, [photo], api_key="k")

    assert out["ok"] is False
    assert out["budget_blocked"] is True
    assert called == [], "гальмо не спрацювало — виклик усе одно пішов"


def test_missing_key_is_reported_not_crashed(tmp_path, monkeypatch):
    """Без ключа — зрозуміла відмова, а не виняток.

    ⚠️ Змінну оточення треба саме ПРИБРАТИ: `api_key or os.getenv(...)` вважає
    порожній рядок відсутнім і підставляє ключ із .env, тож у повному наборі
    тестів цей випадок інакше не відтворюється.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    out = pa.extract_and_propose(_DB(), 1, [photo], api_key=None)
    assert out["ok"] is False and "GEMINI_API_KEY" in out["reason"]


def test_no_photos_is_reported(tmp_path):
    out = pa.extract_and_propose(_DB(), 1, [tmp_path / "нема.webp"], api_key="k")
    assert out["ok"] is False and "знімк" in out["reason"]


def test_module_never_writes_to_products(monkeypatch, tmp_path):
    """Архітектурна гарантія: сервіс кладе пропозиції, а не значення в картку.

    ⚠️ Забороняємо саме ЗАПИС. Читати products сервіс мусить — інакше не
    дізнається, що поле вже заповнене, і пропонуватиме вже правильне.
    """
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "sole_type": "плоска", "sole_type_confidence": 0.95,
        "_usage": {"promptTokenCount": 100, "candidatesTokenCount": 10},
    })
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    db = _DB(spent=0.0)
    pa.extract_and_propose(db, 7, [photo], api_key="k")
    for sql in db.sql:
        up = sql.upper()
        assert not up.startswith("UPDATE PRODUCTS"), sql
        assert not up.startswith("INSERT INTO PRODUCTS"), sql
        assert not up.startswith("DELETE FROM PRODUCTS"), sql


def test_failed_call_still_records_spend(monkeypatch, tmp_path):
    """Провайдер тарифікує вхід навіть на провалі — витрата має бути записана."""
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_error": "HTTP 500", "_usage": {"promptTokenCount": 5000},
    })
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    db = _DB(spent=0.0)
    out = pa.extract_and_propose(db, 7, [photo], api_key="k")
    assert out["ok"] is False
    assert any("INSERT INTO ai_spend_log" in s for s in db.sql), \
        "витрата на невдалому виклику не записана"


# ── Контракт імен ───────────────────────────────────────────────────────────

def test_every_closed_field_maps_to_a_product_update_field():
    """Пʼятий елемент — ім'я для ProductUpdate. Розбіжність тут означала б, що
    прийняту пропозицію нікуди застосувати."""
    from backend.schemas.product import ProductUpdate
    allowed = set(ProductUpdate.model_fields)
    bad = [f[4] for f in pa.CLOSED_FIELDS.values() if f[4] not in allowed]
    assert not bad, f"ProductUpdate не приймає: {bad}"


def test_closed_fields_have_thresholds():
    """Кожне поле, яке ми пропонуємо, мусить мати свій поріг певності."""
    from backend.services import field_proposals as fp
    for _f, (_t, _c, _fk, _l, upd) in pa.CLOSED_FIELDS.items():
        assert upd in fp.CONFIDENCE_THRESHOLD, f"немає порога для {upd}"


# ── Шум, який прибрано за зауваженнями власника ─────────────────────────────

def test_absence_values_are_never_proposed():
    """«Без каблука» — це порожнє поле, а не запис.

    12111 товарів із 12177 мають порожній тип каблука, і лише 32 кросівки з
    4175 позначені «без каблука». Конвенція бази — тиша, тож пропонувати запис
    означало б засмічувати картку.
    """
    from backend.services.shoe_attribute_normalization import is_absence_value
    assert is_absence_value("heel_type_name", "без каблука")
    assert is_absence_value("heel_type_name", "плоский")
    assert not is_absence_value("heel_type_name", "танкетка")


def test_the_absence_rule_is_shared_between_layers():
    """Правило живе в ОДНОМУ місці — інакше шар, що його не бачить, порушує.

    Саме так і сталось: копія лежала лише в `photo_autofill`, шар профілю про
    неї не знав і створив 25 пропозицій «без каблука».
    """
    import inspect
    from backend.services import model_profile, shoe_attribute_normalization
    assert not hasattr(pa, "ABSENCE_VALUES"), \
        "у photo_autofill знову зʼявилась власна копія правила"
    assert "is_absence_value" in inspect.getsource(model_profile.unanimous)
    assert shoe_attribute_normalization.ABSENCE_VALUES


def test_tread_values_have_definitions_for_the_model():
    """Без визначень модель має лише слово й тяжіє до найчастішого.

    «Рифлена» проти «рельєфна» не розрізняються ніяк, якщо не пояснити різницю
    — саме тому «рифлена» пропонувалась майже всюди.
    """
    hints = pa.VALUE_HINTS["tread_type"]
    for v in ("рифлена", "рельєфна", "тракторна", "гладка"):
        assert v in hints and len(hints[v]) > 20, f"немає визначення для «{v}»"


@pytest.mark.parametrize("current, proposed, same", [
    ("Hey Dude", "HEY DUDE", True),      # модель читає лого дослівно
    ("Hey Dude", "hey dude", True),
    (" плоска ", "плоска", True),
    ("плоска", "танкетка", False),
    (None, "плоска", False),             # порожнє поле — пропозиція потрібна
    ("", "плоска", False),
])
def test_same_as_current_ignores_case_and_edges(current, proposed, same):
    assert pa._same_as_current(current, proposed) is same


def test_brand_is_canonicalised_before_comparing():
    """«HEY DUDE» з лого має стати «Hey Dude» — інакше пропозиція «виправляла б»
    правильне значення на крик із коробки."""
    from backend.services.brand_normalization import canonicalize_brand_name
    assert canonicalize_brand_name("HEY DUDE") == "Hey Dude"
    assert canonicalize_brand_name("hey dude") == "Hey Dude"


# ── Захист від вигаданого артикула ──────────────────────────────────────────
#
# Артикул — ЄДИНЕ поле без захисту enum'ом: решту модель обирає зі списку, а
# артикул читає, і тому може вигадати. Зафіксовано на живих товарах:
#   #Ф4372 Adidas — «HQ8708», потім «JQ8716» при правді «JQ8356»;
#   #Ф4128 Reebok — «1000200018-100070332» при правді «100033704».
# Обидві вигадки мали певність 0.95 І процитований якір — модель вигадує і
# якір теж, тож сам по собі він захистом не є. Захист — ДРУГИЙ СВІДОК.

def _two_call_fake(main: dict, second_article):
    """Двійник, що розрізняє основний виклик і перечитування артикула.

    Перечитування йде вузькою `ARTICLE_SCHEMA` — саме за нею й розрізняємо.
    """
    def fake(model, api_key, photos, schema):
        if schema is pa.ARTICLE_SCHEMA:
            return {"article_text": second_article, "article_source_text": "x",
                    "_usage": {"promptTokenCount": 10, "candidatesTokenCount": 1}}
        out = dict(main)
        out["_usage"] = {"promptTokenCount": 100, "candidatesTokenCount": 10}
        return out
    return fake


def test_article_is_refused_when_the_reread_disagrees(monkeypatch, tmp_path):
    """Два прочитання розійшлись → пропозиції немає взагалі.

    Саме так поводився #Ф4128: «1000200018-100070332» проти «100070332».
    Порожнє поле коштує людині кілька секунд, а впевнено неправильний артикул
    їде в аркуш і на маркетплейси.
    """
    monkeypatch.setattr(pa, "call_gemini", _two_call_fake(
        {"article_text": "HQ8708", "article_text_confidence": 0.95,
         "article_source_text": "LHG 029003 A HQ8708"}, "JQ8716"))
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert out["ok"] is True
    assert not any(f == "marking" for f, *_ in out["proposed"]), \
        "артикул без другого свідка потрапив у пропозиції"
    assert any(f == "marking" for f, *_ in out["below_threshold"])


def test_article_is_refused_when_the_reread_fails(monkeypatch, tmp_path):
    """Помилка перечитування (429, 5xx) — це «не підтверджено», а не «нехай іде».

    Закрита відмова: збій мережі не має ставати мовчазним дозволом.
    """
    def fake(model, api_key, photos, schema):
        if schema is pa.ARTICLE_SCHEMA:
            return {"_error": "HTTP 429: quota", "_usage": {"promptTokenCount": 10}}
        return {"article_text": "HQ8708", "article_text_confidence": 0.95,
                "article_source_text": "LHG 029003 A HQ8708",
                "_usage": {"promptTokenCount": 100, "candidatesTokenCount": 10}}
    monkeypatch.setattr(pa, "call_gemini", fake)
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert not any(f == "marking" for f, *_ in out["proposed"])


def test_article_passes_when_the_reread_agrees(monkeypatch, tmp_path):
    """Обидва прочитання збіглись — свідок є. Певність не нижча за 0.90, але й
    не 1.0: це та сама модель, лише зосереджена вузькою схемою."""
    monkeypatch.setattr(pa, "call_gemini", _two_call_fake(
        {"article_text": "JQ8356", "article_text_confidence": 0.7,
         "article_source_text": "LHG 029003 A JQ8356"}, "jq-8356"))
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("marking", "JQ8356", 0.90) in out["proposed"]


def test_barcode_spares_the_extra_call(monkeypatch, tmp_path):
    """Штрихкод — кращий свідок за перечитування, і зайвий виклик не потрібен."""
    calls = []
    def fake(model, api_key, photos, schema):
        calls.append("вузька" if schema is pa.ARTICLE_SCHEMA else "повна")
        return {"article_text": "JQ8356", "article_text_confidence": 0.95,
                "article_source_text": "LHG 029003 A JQ8356",
                "_usage": {"promptTokenCount": 100, "candidatesTokenCount": 10}}
    monkeypatch.setattr(pa, "call_gemini", fake)
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("Code128", "F2 JQ8356")])
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("marking", "JQ8356", 1.0) in out["proposed"]
    assert calls == ["повна"], f"зайвий виклик при наявному штрихкоді: {calls}"


def test_the_verification_call_is_billed(monkeypatch, tmp_path):
    """Другий виклик коштує грошей — стеля має про нього знати."""
    monkeypatch.setattr(pa, "call_gemini", _two_call_fake(
        {"article_text": "JQ8356", "article_text_confidence": 0.95,
         "article_source_text": "x"}, "JQ8356"))
    db = _DB(spent=0.0)
    pa.extract_and_propose(db, 7, [_photo(tmp_path)], api_key="k")
    assert sum(1 for q in db.sql if "INSERT INTO ai_spend_log" in q) == 2, \
        "перечитування не записане у витрати"


def test_article_schema_asks_one_thing(monkeypatch):
    """Сенс вузької схеми саме у вузькості: на #Ф4372 повна схема з двадцяти
    полів дала «HQ8708» і «JQ8716», а ця — двічі правильний «JQ8356»."""
    assert set(pa.ARTICLE_SCHEMA["properties"]) == {"article_text", "article_source_text"}


def test_text_fields_use_model_confidence_not_ours():
    """Раніше для артикула й бренда стояло жорстке 0.9 — НАШЕ припущення
    подавалось як оцінка моделі, і вигадка виглядала майже впевненою."""
    import inspect
    src = inspect.getsource(pa.extract_and_propose)
    assert "0.9)" not in src and ", 0.9," not in src, \
        "у коді лишилась захардкоджена певність для текстових полів"
    assert 'pred.get(f"{src}_confidence")' in src


def test_schema_asks_for_confidence_on_every_text_field():
    """Кожне текстове поле мусить мати власну оцінку певності в схемі."""
    import re
    src = inspect_source = __import__("inspect").getsource(pa.build_schema)
    for f in ("brand_text", "article_text", "model_text"):
        assert f'props["{f}_confidence"]' in src, f"немає певності для {f}"


# ── Шар штрихкодів і зустріч двох шарів ─────────────────────────────────────

def _bc(fmt, text, photo="x_001.webp"):
    from backend.services.barcode_reader import BarcodeHit
    return BarcodeHit(fmt, text, photo)


def _photo(tmp_path):
    p = tmp_path / "x_001.webp"
    p.write_bytes(b"f")
    return p


def test_gtin_from_barcode_is_proposed(monkeypatch, tmp_path):
    """EAN13 → поле gtin із певністю 1.0. Контрольна сума не «майже певна»."""
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("EAN13", "4895245119084")])
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_usage": {"promptTokenCount": 10, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("gtin", "4895245119084", 1.0) in out["proposed"]


def test_barcode_layer_survives_exhausted_budget(monkeypatch, tmp_path):
    """Головне в багатошаровості: вимкнена модель не гасить безкоштовний шар.

    Зчитування коду не коштує нічого й не залежить від провайдера — блокувати
    його разом із платним шаром означало б втратити єдине джерело `gtin`.
    """
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("EAN13", "4895245119084")])
    called = []
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: called.append(1) or {})

    from backend.services import ai_budget
    out = pa.extract_and_propose(_DB(spent=ai_budget.MONTHLY_CAP_USD), 7,
                                 [_photo(tmp_path)], api_key="k")
    assert out["budget_blocked"] is True
    assert called == [], "платний шар усе одно викликали"
    assert ("gtin", "4895245119084", 1.0) in out["proposed"]


def test_barcode_layer_survives_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("EAN13", "4895245119084")])
    out = pa.extract_and_propose(_DB(), 7, [_photo(tmp_path)], api_key=None)
    assert out["ok"] is False
    assert ("gtin", "4895245119084", 1.0) in out["proposed"]


def test_barcode_confirms_article_without_anchor(monkeypatch, tmp_path):
    """Збіг двох незалежних шарів замінює якір і піднімає певність до 1.0.

    Модель прочитала очима те саме, що машина витягла з коду. Так збігтися
    вигадка не може — це сильніше свідчення, ніж процитований рядок бирки.
    """
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("DataMatrix", "F2,0225,POP454928,JQ8356")])
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "article_text": "jq-8356",            # інше написання того самого коду
        "article_text_confidence": 0.4,       # модель сама собі не вірить
        "article_source_text": None,          # якоря немає
        "_usage": {"promptTokenCount": 10, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("marking", "jq-8356", 1.0) in out["proposed"]


def test_disagreement_is_shown_not_hidden(monkeypatch, tmp_path):
    """Розбіжність не викриває вигадку — артикул часто не вкладений у код.

    Тому пропозицію не знімаємо, але поруч показуємо, що саме лежить у коді.
    """
    seen = {}
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("DataMatrix", "F2,POP454928")])
    monkeypatch.setattr(pa.field_proposals, "propose",
                        lambda db, pid, f, v, c, **kw: seen.update({f: kw}) or True)
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "article_text": "JQ8356", "article_text_confidence": 0.95,
        "article_source_text": "LHG 029003 A JQ8356",
        "_usage": {"promptTokenCount": 10, "candidatesTokenCount": 1}})
    pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert "POP454928" in seen["marking"]["note"]
    assert "LHG 029003 A JQ8356" in seen["marking"]["note"]


def test_barcode_does_not_repeat_a_filled_gtin(monkeypatch, tmp_path):
    """Код уже стоїть у картці — пропозиція була б чистим шумом."""
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("EAN13", "4895245119084")])
    monkeypatch.setattr(pa, "_current_values",
                        lambda db, pid: {"gtin": "4895245119084"})
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_usage": {"promptTokenCount": 10, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert out["proposed"] == []
    assert ("gtin", "4895245119084") in out["already_correct"]


@pytest.mark.parametrize("a, b, same", [
    ("CW2288-111", "cw2288 111", True),
    ("JQ8356", "jq-8356", True),
    ("JQ8356", "HQ8708", False),
])
def test_norm_code_ignores_punctuation_and_case(a, b, same):
    assert (pa._norm_code(a) == pa._norm_code(b)) is same


def test_gtin_threshold_forbids_a_guessed_code():
    """Поріг 0.99 — заборона на майбутнє: «прочитати цифри очима» не пройде."""
    from backend.services import field_proposals as fp
    assert fp.threshold_for("gtin") > 0.95


@pytest.mark.parametrize("in_card, scanned, same", [
    # #Ф2523: у картці UPC-A (12 цифр), сканер дає EAN-13 із провідним нулем.
    ("197002067565", "0197002067565", True),
    ("0197002067565", "197002067565", True),
    ("4895245119084", "4895245119084", True),
    ("4895245119084", "2230059181797", False),
])
def test_gtin_lengths_are_the_same_number(in_card, scanned, same):
    """GTIN-8/-12/-13/-14 — один номер, доповнений нулями до різної довжини."""
    assert (pa._norm_gtin(in_card) == pa._norm_gtin(scanned)) is same


def test_padded_gtin_is_not_offered_as_a_correction(monkeypatch, tmp_path):
    """Реальний #Ф2523: пропозиція «виправити» код на нього ж — чистий шум."""
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("EAN13", "0197002067565")])
    monkeypatch.setattr(pa, "_current_values",
                        lambda db, pid: {"gtin": "197002067565"})
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_usage": {"promptTokenCount": 10, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert out["proposed"] == []


def test_barcode_confirms_existing_marking_without_the_ai(monkeypatch, tmp_path):
    """Підтвердження — факт чистого шару, і воно не має зникати без моделі.

    Реальний #Ф4132: DataMatrix містить «GR530AA», рівно той артикул, що вже
    вписаний у картку.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(pa.barcode_reader, "read_photos",
                        lambda ps: [_bc("DataMatrix", "F2,0225,196432723249,POP454928,GR530AA")])
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: {"marking": "GR530AA"})
    out = pa.extract_and_propose(_DB(), 7, [_photo(tmp_path)], api_key=None)
    assert out["confirmed"] == [("marking", "GR530AA", "barcode")]
    assert out["proposed"] == [], "підтвердження не має ставати пропозицією"


# ── Три рішення власника від 14.09.2026 ─────────────────────────────────────

def test_slip_on_is_an_absence_not_a_fastening():
    """«Сліпони» — не застібка, а її відсутність. 1839 із 1924 мокасинів,
    лоферів, сабо, балеток і туфель мають порожню застібку; до того ж
    «сліпони» уже є підтипом, і в застібці воно лише дублювало б його."""
    from backend.services.shoe_attribute_normalization import is_absence_value
    assert is_absence_value("fastening_type_name", "сліпони")
    assert is_absence_value("fastening_type_name", "Сліпони")
    assert not is_absence_value("fastening_type_name", "шнурівка")


@pytest.mark.parametrize("value", ["платформа", "танкетка", "тракторний"])
def test_sole_concepts_are_never_heel_types(value):
    """Каблук — це блок, шпилька, низький, конусний. «Платформа» й «танкетка» —
    підошва, «тракторний» — протектор; вони затекли в heel_types на 9 товарів,
    і модель пропонувала їх як каблук лише тому, що бачила в переліку."""
    from backend.services.shoe_attribute_normalization import is_misplaced_value
    assert is_misplaced_value("heel_type_name", value)
    assert not is_misplaced_value("heel_type_name", "блок")
    # для підошви «платформа» — легітимна
    assert not is_misplaced_value("sole_type_name", "платформа")


def test_schema_excludes_misplaced_values_from_enum():
    """Модель фізично не має побачити «платформа» серед типів каблука."""
    import inspect
    src = inspect.getsource(pa.build_schema)
    assert "is_misplaced_value(_upd, n)" in src


def test_sole_type_hints_distinguish_platform_from_heel():
    """Лофери DeeZee з тракторною підошвою і вирізом під склепінням отримали
    «платформа» замість «каблук»: модель бачить товсту підошву, а різницю їй
    ніхто не пояснював. Платформа в цій базі — суцільна, БЕЗ вирізу; є окремий
    блок ззаду — це «каблук» (так позначено 17 із 21 туфель із каблуком)."""
    h = pa.VALUE_HINTS["sole_type"]
    assert "БЕЗ вирізу" in h["платформа"]
    assert "ОКРЕМИЙ каблук" in h["каблук"] and "виріз" in h["каблук"]
    assert "танкетка" in h and "плоска" in h and "спортивна" in h


def test_profile_layer_also_refuses_misplaced_values():
    """Одностайні 4 записи з «платформа» в каблуку — усе одно чуже значення."""
    from backend.services import model_profile as mp
    prof = {"fields": {"heel_type_name": {"value": "платформа", "share": 4, "total": 4}}}
    assert mp.unanimous(prof) == {}


# ── Платний ключ — лише з підтвердження людини ──────────────────────────────

def test_quota_exhaustion_is_reported_as_a_choice(monkeypatch, tmp_path):
    """429 на безкоштовному ключі — це не помилка для людини, а вибір.

    Виміряно 14.09.2026: безкоштовна квота — 8 викликів на добу. Замість сирої
    помилки відповідь каже «квоту вичерпано» і чи є платний ключ — інтерфейс
    показує діалог, і лише з підтвердження повторює платно.
    """
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_error": "HTTP 429: quota", "_quota_exhausted": True, "_usage": {}})
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "paid-key")
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="free")
    assert out["ok"] is False and out["quota_exhausted"] is True
    assert out["paid_available"] is True


def test_paid_key_is_never_used_without_explicit_consent(monkeypatch, tmp_path):
    """Без use_paid=True платний ключ не береться, навіть якщо він є."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "paid-key")
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    seen = {}
    monkeypatch.setattr(pa, "call_gemini", lambda m, key, *a, **k: seen.update(key=key) or {
        "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)])
    assert out["ok"] is False and "GEMINI_API_KEY" in out["reason"]
    assert seen == {}, "платний ключ пішов у хід без згоди"


def test_paid_key_is_used_only_with_consent_and_is_accounted_separately(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "paid-key")
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    seen = {}
    monkeypatch.setattr(pa, "call_gemini", lambda m, key, *a, **k: seen.update(key=key) or {
        "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1}})
    recorded = {}
    monkeypatch.setattr(pa.ai_budget, "record", lambda db, **kw: recorded.update(kw) or 0.0)
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], use_paid=True)
    assert out["ok"] is True and seen["key"] == "paid-key"
    assert recorded["purpose"] == "autofill:paid", "платні виклики мають рахуватись окремо"


def test_quota_message_is_not_shown_on_a_paid_retry(monkeypatch, tmp_path):
    """Якщо 429 прийшов уже на платному ключі — це справжня помилка, не вибір."""
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "paid-key")
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_error": "HTTP 429: quota", "_quota_exhausted": True, "_usage": {}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], use_paid=True)
    assert out["ok"] is False and "quota_exhausted" not in out


# ── Два питання власника від 15.09.2026 ─────────────────────────────────────

def test_defined_values_enter_the_enum_even_with_zero_products(monkeypatch):
    """«Гладка» — 0 товарів, тож фільтр `k > 0` викидав її з переліку, і на
    кожній гладкій підошві модель МУСИЛА обирати з трьох, що лишились —
    звідси «рифлена» на класичних черевиках, відхилена чотири рази.
    Значення з визначенням у VALUE_HINTS — канонічне за побудовою."""
    class _R:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    def execute(stmt, params=None):
        sql = str(stmt)
        if "tread_types" in sql:
            return _R([("гладка", 0), ("рифлена", 38), ("тракторна", 32), ("рельєфна", 9)])
        if "technologies" in sql:
            return _R([])
        return _R([("x", 1)])
    db = type("DB", (), {"execute": staticmethod(execute)})()
    enum = [v for v in pa.build_schema(db)["properties"]["tread_type"]["enum"] if v]
    assert "гладка" in enum


def test_manufacturer_country_is_asked_and_absence_is_not_offered(monkeypatch):
    """Тег «Made in Bangladesh under quality control of Caprice Germany» — а
    модель мовчала, бо схема про країну НЕ ПИТАЛА. Тепер питає, «Unknown»
    (257 товарів) у перелік не потрапляє, а визначення пояснює різницю між
    країною виробництва й країною бренда."""
    from backend.schemas.product import ProductUpdate
    from backend.services import field_proposals as fp
    from backend.services.shoe_attribute_normalization import is_absence_value
    assert "manufacturer_country" in pa.CLOSED_FIELDS
    upd = pa.CLOSED_FIELDS["manufacturer_country"][4]
    assert upd in ProductUpdate.model_fields and upd in fp.CONFIDENCE_THRESHOLD
    assert is_absence_value(upd, "Unknown") and is_absence_value(upd, "unknown")
    note = pa.VALUE_HINTS["manufacturer_country"]["__field__"]
    assert "Made in" in note and "Бангладеш" in note and "Німеччина" in note


def test_field_note_is_not_treated_as_a_value():
    """Службовий ключ __field__ — пояснення поля, а не значення переліку."""
    class _R:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    def execute(stmt, params=None):
        sql = str(stmt)
        if "countries" in sql: return _R([("Бангладеш", 140), ("Unknown", 257)])
        if "technologies" in sql: return _R([])
        return _R([("x", 1)])
    db = type("DB", (), {"execute": staticmethod(execute)})()
    p = pa.build_schema(db)["properties"]["manufacturer_country"]
    assert "__field__" not in p["enum"] and "Unknown" not in p["enum"]
    assert "Made in" in p["description"]


# ── Стікер від руки: ціна, розмір, замір — лише зі своїм номером ────────────

@pytest.mark.parametrize("card, sticker, ok", [
    ("#Ф4419", "ф4419", True),      # кирилична ф, без #
    ("#Ф4419", "F4419", True),      # латинська F → Ф
    ("#Ф4419", "4419", True),       # без літери — цифри збігаються
    ("#Ф4419", "ф4400", False),     # чужий номер
    ("#Ф4419", "Т4419", False),     # інша літера
    ("#Ф4419", None, False),
])
def test_sticker_belongs_to_the_card_only_when_number_matches(card, sticker, ok):
    assert pa._number_matches(card, sticker) is ok


def _run_sticker(monkeypatch, tmp_path, pred_extra, current_extra=None):
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa.model_profile, "profile_for", lambda *a, **k: {"records": 0, "fields": {}})
    cur = {"productnumber": "#Ф4419", "price": None, "sizeeu": None, "measurementscm": None}
    cur.update(current_extra or {})
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: cur)
    payload = {"_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1}}
    payload.update(pred_extra)
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: payload)
    return pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")


def test_sticker_values_are_proposed_when_number_matches(monkeypatch, tmp_path):
    out = _run_sticker(monkeypatch, tmp_path, {
        "sticker_text": "2200 36 23,5 ф4419", "sticker_number": "ф4419",
        "sticker_price": 2200, "sticker_price_confidence": 0.95,
        "sticker_size": 36, "sticker_size_confidence": 0.95,
        "sticker_cm": 23.5, "sticker_cm_confidence": 0.9})
    got = {f: v for f, v, c in out["proposed"]}
    assert got == {"price": "2200", "sizeeu": "36", "measurementscm": "23.5"}
    assert out["sticker"]["matched"] is True


def test_foreign_sticker_is_discarded_entirely(monkeypatch, tmp_path):
    """Стікер із чужим номером — чужий: ані ціна, ані розмір з нього не йдуть."""
    out = _run_sticker(monkeypatch, tmp_path, {
        "sticker_text": "1500 38 ф4400", "sticker_number": "ф4400",
        "sticker_price": 1500, "sticker_price_confidence": 0.99,
        "sticker_size": 38, "sticker_size_confidence": 0.99})
    assert not any(f in ("price", "sizeeu") for f, *_ in out["proposed"])
    assert out["sticker"]["matched"] is False and out["sticker"]["sticker_number"] == "ф4400"
    assert "не збігся" in out["sticker"]["reason"]        # людина бачить, ЧОМУ


def test_sticker_number_is_rescued_from_the_verbatim_text(monkeypatch, tmp_path):
    """#Ф4403: sticker_number прочитано як «ФЧЧ03», але дослівний рядок
    «2500 39 25,5 Ф4403» містить наш номер — стікер наш."""
    out = _run_sticker(monkeypatch, tmp_path, {
        "sticker_text": "2500 39 25,5 Ф4419", "sticker_number": "ФЧЧ19",
        "sticker_price": 2500, "sticker_price_confidence": 0.8,
        "sticker_size": 39, "sticker_size_confidence": 0.8,
        "sticker_cm": 25.5, "sticker_cm_confidence": 0.75})
    got = {f: v for f, v, c in out["proposed"]}
    assert got == {"price": "2500", "sizeeu": "39", "measurementscm": "25.5"}


def test_foreign_digits_in_text_do_not_rescue(monkeypatch, tmp_path):
    out = _run_sticker(monkeypatch, tmp_path, {
        "sticker_text": "2500 39 Ф4400", "sticker_number": None,
        "sticker_price": 2500, "sticker_price_confidence": 0.99})
    assert out["proposed"] == [] and out["sticker"]["matched"] is False


def test_handwritten_sticker_thresholds_admit_a_plain_read():
    """Рукописне «2500» модель оцінює на 0.8 — це має проходити. Поріг ОДИН —
    у field_proposals; друга копія в STICKER_FIELDS уже коштувала #Ф4403."""
    from backend.services import field_proposals as fp
    for _key, (upd_field, _lo, _hi) in pa.STICKER_FIELDS.items():
        assert fp.threshold_for(upd_field) <= 0.8


def test_every_run_is_recorded_with_its_raw_prediction(monkeypatch, tmp_path):
    """Без запису відповідь на «чому не розпізнало» коштує ще один виклик."""
    db = _DB(spent=0.0)
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa.model_profile, "profile_for", lambda *a, **k: {"records": 0, "fields": {}})
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: {"productnumber": "#Ф4403"})
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        "sticker_text": "2500 39 Ф4400", "sticker_number": "Ф4400"})
    pa.extract_and_propose(db, 7, [_photo(tmp_path)], api_key="k")
    ins = [s for s in db.sql if "INSERT INTO ai_autofill_runs" in s]
    assert len(ins) == 1


def test_out_of_range_sticker_values_never_reach_the_card(monkeypatch, tmp_path):
    """Розмір 360 або ціна 5 — хибне читання; межі від реальних значень бази."""
    out = _run_sticker(monkeypatch, tmp_path, {
        "sticker_number": "ф4419",
        "sticker_price": 5, "sticker_price_confidence": 0.99,
        "sticker_size": 360, "sticker_size_confidence": 0.99})
    assert out["proposed"] == []
    assert {f for f, *_ in out["below_threshold"]} == {"price", "sizeeu"}


def test_sticker_does_not_repeat_what_the_card_has(monkeypatch, tmp_path):
    out = _run_sticker(monkeypatch, tmp_path,
        {"sticker_number": "ф4419", "sticker_price": 2200, "sticker_price_confidence": 0.95},
        current_extra={"price": 2200.0})
    assert out["proposed"] == [] and ("price", "2200") in out["already_correct"]


def test_subtype_enum_is_narrowed_by_product_type():
    """Підвид залежить від виду: сукні не пропонують «Челсі»."""
    seen = {}
    class _R:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    def execute(stmt, params=None):
        sql = str(stmt)
        if "subtypes" in sql:
            seen["sql"] = sql; seen["params"] = params
            return _R([("Челсі", 215)])
        if "technologies" in sql: return _R([])
        return _R([("x", 1)])
    db = type("DB", (), {"execute": staticmethod(execute)})()
    pa.build_schema(db, type_id=35)
    assert "p.typeid = :tid" in seen["sql"] and seen["params"] == {"tid": 35}


# ── Сезон: доповнює, не замінює ─────────────────────────────────────────────

@pytest.mark.parametrize("current, seen, expected", [
    ("Єврозима", ["Демі"], "Єврозима, Демі"),            # випадок власника
    ("Єврозима", ["Зима", "Демі"], "Зима, Єврозима, Демі"),  # канонічний порядок
    ("Єврозима", ["Єврозима"], None),                     # нічого нового
    ("Демі, Літо", ["Літо"], None),                       # підмножина
    ("", ["Літо"], "Літо"),
    (None, ["Всесезон", "Літо"], "Літо, Всесезон"),
    ("Зима", ["Осінь"], None),                            # не з переліку — ігнор
])
def test_seasons_are_merged_in_canonical_order(current, seen, expected):
    assert pa.merge_seasons(current, seen) == expected


def test_season_vocabulary_matches_the_parser():
    """Один порядок і один перелік на парсер і на модель — інакше пропозиція
    записала б рядок, який парсер потім «виправив» би у свій."""
    from backend.scripts.sheets_parser import SEASON_CANONICAL_ORDER
    assert tuple(pa.SEASONS) == tuple(SEASON_CANONICAL_ORDER)
    assert set(pa.SEASON_HINTS) == set(pa.SEASONS)


def test_season_proposal_adds_to_existing(monkeypatch, tmp_path):
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa.model_profile, "profile_for", lambda *a, **k: {"records": 0, "fields": {}})
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: {"season": "Єврозима"})
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "season": ["Демі", "Єврозима"], "season_confidence": 0.85,
        "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert ("season", "Єврозима, Демі", 0.85) in out["proposed"]


def test_season_already_covered_is_not_proposed(monkeypatch, tmp_path):
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa.model_profile, "profile_for", lambda *a, **k: {"records": 0, "fields": {}})
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: {"season": "Зима, Єврозима"})
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "season": ["Єврозима"], "season_confidence": 0.9,
        "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1}})
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")
    assert not any(f == "season" for f, *_ in out["proposed"])
    assert ("season", "Зима, Єврозима") in out["already_correct"]


def test_boot_subtypes_have_definitions_along_the_height_axis():
    """У ботинок 28 підвидів, а модель без визначень тяжіла до двох-трьох
    знайомих назв. Головна вісь розрізнення — висота халяви й застібка."""
    h = pa.VALUE_HINTS["subtype"]
    for v in ("Челсі", "Ботильйони", "Напівботинки", "Напівсапоги", "Чоботи", "Хайтопи", "Уггі"):
        assert v in h and len(h[v]) > 20, f"немає визначення для «{v}»"
    assert "КАБЛУЦІ" in h["Ботильйони"] and "ПЛОСКІЙ" in h["Напівботинки"]
    assert "БЕЗ шнурівки" in h["Челсі"]


# ── Артикул: бирка з двома кодами ───────────────────────────────────────────
# Caprice/Tamaris друкують поруч два коди («9-26201-25-170 / 9-26201-43-170»).
# Два незалежні читання беруть різні — точна рівність відкидала читабельну бирку.

@pytest.mark.parametrize("first, second, anchor, ok", [
    ("9-26201-25-170", "9-26201-25-170", None, True),                     # точний збіг
    ("9-26201-25-170", "9-26201-25-170/9-26201-43-170", None, True),     # перший є серед другого
    ("9-26201-25-170", "9-26201-43-170", "9-26201-25-170 / 9-26201-43-170", True),  # обидва є на бирці
    ("9-26201-25-170", "9-26201-43-170", None, False),                    # різні коди, якоря нема
    ("9-26201-25-170", "9-26201-43-170", "9-26201-25-170", False),        # другого на бирці нема
    ("9-26201-25-170", None, "9-26201-25-170 / 9-26201-43-170", False),   # другого читання нема
    ("CW2288-111", "cw2288 111", None, True),                             # пробіл ≠ інший код (Nike)
    ("CW2288-111", "CW2288 111", "CW2288 111 / 9-26201-43-170", True),   # те саме через якір
])
def test_two_codes_on_one_tag_still_count_as_agreement(first, second, anchor, ok):
    assert pa._article_reads_agree(first, second, anchor) is ok


def test_code_tokens_split_on_tag_separators_and_drop_short_noise():
    toks = pa._code_tokens("9-26201-25-170 / 9-26201-43-170; EU 38")
    assert {"92620125170", "92620143170"} <= toks and "EU" not in toks and "38" not in toks
    assert pa._code_tokens("CW2288 111") >= {"CW2288111", "CW2288"}   # рядок цілком — теж код
    assert pa._code_tokens(None) == set()


# ── Матеріали з піктограм ЄС ────────────────────────────────────────────────

def test_pictogram_symbols_map_only_into_our_vocabulary():
    """Кожен символ директиви 94/11/EC → назва, що вже є в довіднику матеріалів."""
    assert set(pa.PICTOGRAM_TO_MATERIAL) == set(pa.PICTOGRAM_SYMBOLS)
    assert set(pa.PICTOGRAM_TO_MATERIAL.values()) <= {"шкіра", "текстиль", "синтетика"}
    assert pa.PICTOGRAM_TO_MATERIAL["шкіра з покриттям"] == "шкіра"   # не «екошкіра»
    assert set(pa.PICTOGRAM_ROWS.values()) == {"upper", "middle", "sole"}


def test_schema_asks_for_the_three_pictogram_rows():
    p = pa.build_schema(_DB())["properties"]["materials_pictogram"]
    assert set(p["properties"]) == set(pa.PICTOGRAM_ROWS)
    for row in pa.PICTOGRAM_ROWS:
        assert set(p["properties"][row]["enum"]) - {None} == set(pa.PICTOGRAM_SYMBOLS)


def _run_pictogram(monkeypatch, tmp_path, pic, conf=0.9, current=None):
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa.model_profile, "profile_for", lambda *a, **k: {"records": 0, "fields": {}})
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: {"productnumber": "#Ф4411"})
    monkeypatch.setattr(pa, "_current_materials", lambda db, pid: current or {})
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        "materials_pictogram": pic, "materials_pictogram_confidence": conf})
    return pa.extract_and_propose(_DB(spent=0.0), 7, [_photo(tmp_path)], api_key="k")


def test_pictogram_rows_become_material_proposals_per_position(monkeypatch, tmp_path):
    out = _run_pictogram(monkeypatch, tmp_path,
                         {"upper": "шкіра з покриттям", "lining": "текстиль", "outsole": "інше"})
    got = {f: v for f, v, c in out["proposed"]}
    assert got == {"material:upper": "шкіра", "material:middle": "текстиль", "material:sole": "синтетика"}
    assert out["materials"] == {"present": True,
                                "proposed": {"upper": "шкіра", "middle": "текстиль", "sole": "синтетика"}}


def test_pictogram_does_not_repeat_a_material_already_in_the_card(monkeypatch, tmp_path):
    out = _run_pictogram(monkeypatch, tmp_path, {"upper": "шкіра", "outsole": "інше"},
                         current={"upper": "Шкіра, текстиль"})
    assert {f for f, *_ in out["proposed"]} == {"material:sole"}
    assert ("material:upper", "Шкіра, текстиль") in out["already_correct"]


def test_uncertain_pictogram_read_stays_below_threshold(monkeypatch, tmp_path):
    out = _run_pictogram(monkeypatch, tmp_path, {"upper": "текстиль"}, conf=0.6)
    assert out["proposed"] == []
    assert ("material:upper", "текстиль", 0.6) in out["below_threshold"]


def test_missing_pictogram_is_reported_as_absent(monkeypatch, tmp_path):
    out = _run_pictogram(monkeypatch, tmp_path, None)
    assert out["materials"] == {"present": False}


# ── Форма носка: «якщо не прям кругла — то заокруглена» ─────────────────────

def test_toe_shape_enum_offers_only_canonical_names():
    """«Мигдалевидний» має товар (k=1), але це варіант «заокругленої» — у
    переліку його нема, інакше модель обере синонім, який власник щойно
    виправляв руками. Варіант без товарів («заокруглений») теж не входить."""
    class _R:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    def execute(stmt, params=None):
        sql = str(stmt)
        if "toe_shapes" in sql:
            return _R([("круглий", 477), ("мигдалевидний", 1), ("заокруглена", 1),
                       ("заокруглений", 0), ("гострий", 7), ("квадратний", 8)])
        if "technologies" in sql:
            return _R([])
        return _R([("x", 1)])
    db = type("DB", (), {"execute": staticmethod(execute)})()
    p = pa.build_schema(db)["properties"]["toe_shape"]
    enum = [v for v in p["enum"] if v]
    assert "заокруглена" in enum and "круглий" in enum
    assert "мигдалевидний" not in enum and "заокруглений" not in enum
    assert "не прям" not in p["description"] and "заокруглена" in p["description"]


def test_toe_shape_rule_is_spelled_out_for_the_model():
    hints = pa.VALUE_HINTS["toe_shape"]
    assert "ЛИШЕ" in hints["__field__"] and "заокруглена" in hints["__field__"]
    assert "ЗВУЖУЄТЬСЯ" in hints["заокруглена"] and "мигдалевидн" in hints["заокруглена"]
    assert "мигдалевидний" not in hints          # синонім — не значення


# ── Протектор: дрібна насічка — це «гладка» ─────────────────────────────────

def test_fine_texture_is_smooth_not_ribbed():
    """Власник (Caprice #Ф4402): «маленькі вирізи і незначні рифленості — не
    рифлена, і навіть не рельєфна». Визначення мусять казати це моделі прямо,
    бо попереднє «дрібні… неглибокий малюнок» запрошувало до протилежного."""
    h = pa.VALUE_HINTS["tread_type"]
    assert "насічк" in h["гладка"] and "логотип" in h["гладка"]
    assert "ВСІЙ" in h["рифлена"] and "не дрібна" in h["рифлена"]
    assert "дрібн" not in h["рифлена"].split("не дрібна")[0]      # «дрібні» більше не означення рифленої
    assert "здалеку" in h["__field__"] and "завжди «гладка»" in h["__field__"]
    assert "ботильйони" in h["__field__"]


def test_blocks_and_chevrons_are_relief_not_ribbed():
    """Власник (#Ф4408, підошва з прямокутних блоків і шевронів): «це не
    рифлена, а рельєфна!». Різниця — у ФОРМІ малюнка: лінії → рифлена,
    фігури → рельєфна, глибокі шашки з широкими проміжками → тракторна."""
    h = pa.VALUE_HINTS["tread_type"]
    assert "ЛИШЕ паралельні" in h["рифлена"] and "жодних фігур" in h["рифлена"]
    assert "блоки" in h["рифлена"] and "«рельєфна»" in h["рифлена"]     # межа названа прямо
    assert "ФІГУР" in h["рельєфна"] and "блоки" in h["рельєфна"] and "шеврони" in h["рельєфна"]
    assert "ШИРОКИМИ проміжками" in h["тракторна"]
    assert "ФОРМУ малюнка" in h["__field__"]

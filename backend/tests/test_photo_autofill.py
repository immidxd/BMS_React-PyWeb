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
    assert "без каблука" in pa.ABSENCE_VALUES["heel_type"]
    assert "плоский" in pa.ABSENCE_VALUES["heel_type"]


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

def test_article_without_anchor_is_refused(monkeypatch, tmp_path):
    """Артикул без процитованого рядка з бирки НЕ пропонується.

    Реальний випадок 06.09.2026: на чіткому фото бирки Adidas написано
    «JQ8356», а модель видала «HQ8708» — не помилилась у символі, а вигадала
    правдоподібний код. Поля із закритим переліком від цього захищені enum'ом;
    текстові — ні, бо їх читають, а не обирають. Якір і є їхнім захистом.
    """
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "article_text": "HQ8708", "article_text_confidence": 0.9,
        "article_source_text": None,            # ← контексту немає
        "_usage": {"promptTokenCount": 100, "candidatesTokenCount": 10},
    })
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [photo], api_key="k")
    assert out["ok"] is True
    assert not any(f == "marking" for f, *_ in out["proposed"]), \
        "артикул без якоря потрапив у пропозиції"
    assert any(f == "marking" for f, *_ in out["below_threshold"])


def test_article_with_anchor_is_proposed(monkeypatch, tmp_path):
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "article_text": "JQ8356", "article_text_confidence": 0.95,
        "article_source_text": "LHG 029003 A JQ8356",
        "_usage": {"promptTokenCount": 100, "candidatesTokenCount": 10},
    })
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    out = pa.extract_and_propose(_DB(spent=0.0), 7, [photo], api_key="k")
    assert ("marking", "JQ8356", 0.95) in out["proposed"]


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
    assert out["confirmed_by_barcode"] == [("marking", "GR530AA")]
    assert out["proposed"] == [], "підтвердження не має ставати пропозицією"

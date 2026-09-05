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

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
    def __init__(self, v): self._v = v
    def scalar(self): return self._v
    def fetchall(self): return []
    def fetchone(self): return None


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
    """Архітектурна гарантія: сервіс кладе пропозиції, а не значення в картку."""
    monkeypatch.setattr(pa, "call_gemini", lambda *a, **k: {
        "sole_type": "плоска", "sole_type_confidence": 0.95,
        "_usage": {"promptTokenCount": 100, "candidatesTokenCount": 10},
    })
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    db = _DB(spent=0.0)
    pa.extract_and_propose(db, 7, [photo], api_key="k")
    for sql in db.sql:
        assert " products " not in f" {sql} " or "LEFT JOIN products" in sql, \
            f"сервіс писав у products: {sql}"
        assert not sql.upper().startswith("UPDATE PRODUCTS"), sql
        assert not sql.upper().startswith("INSERT INTO PRODUCTS"), sql


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

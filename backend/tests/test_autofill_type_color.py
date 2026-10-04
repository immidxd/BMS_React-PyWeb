"""Вид і колір у ШІ-розпізнаванні — лише для ПОРОЖНЬОЇ картки.

04.10.2026, завіз «01.10.2026(NikolenkoOPT)»: 21 товар без виду й кольору,
пакетне розпізнавання не заповнило жодного — схема про них не питала. Підвид
тоді обирався з усіх значень без виду, і в нього потрапляли назви ВИДІВ.
Колір — у ключі тотожності товару, тому НАЯВНИЙ колір не пропонуємо міняти.
"""
from __future__ import annotations

from pathlib import Path
import sys

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import photo_autofill as pa  # noqa: E402
from backend.services import field_proposals as fp  # noqa: E402


class _R:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar
    def fetchall(self): return self._rows
    def fetchone(self): return None
    def scalar(self): return self._scalar
    def mappings(self): return self


class _VocabDB:
    """Довідники: види, кольори, решта переліків по одному значенню."""
    def __init__(self, pair_count=0):
        self.pair_count = pair_count
        self.sql: list[str] = []
    def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        self.sql.append(q)
        if q.startswith("SELECT count(*) FROM products p JOIN types"):
            return _R(scalar=self.pair_count)
        if "FROM types l" in q:
            return _R([("Кросівки", 3900), ("Ботинки", 1887), ("???", 65), ("Невизначено", 72),
                       ("Челсі", 2), ("Туфлі", 683)])
        if "FROM colors l" in q:
            return _R([("чорний", 3330), ("білий/молочний", 85), ("бордовий", 111),
                       ("чорний, білий", 40), ("сірий з напиленням", 1), ("червоний", 107)])
        if "technologies" in q:
            return _R([])
        if "ai_spend" in q or "sum(" in q.lower():
            return _R(scalar=0.0)
        return _R([("x", 1)], scalar=0.0)


# ── Перелік значень ─────────────────────────────────────────────────────────

def test_type_enum_drops_placeholders_and_rare_noise():
    vals = pa.fill_empty_enum_values(_VocabDB(), "type")
    assert vals == ["Кросівки", "Ботинки", "Туфлі"]
    assert "???" not in vals and "Невизначено" not in vals
    assert "Челсі" not in vals          # 2 товари — підвид не в тій колонці


def test_color_enum_offers_only_simple_frequent_colors():
    vals = pa.fill_empty_enum_values(_VocabDB(), "color")
    assert vals == ["чорний", "бордовий", "червоний"]


def test_schema_asks_type_and_color_only_on_request():
    plain = pa.build_schema(_VocabDB())["properties"]
    assert "type" not in plain and "color" not in plain
    asked = pa.build_schema(_VocabDB(), ask_type=True, ask_color=True)["properties"]
    assert asked["type"]["enum"][-1] is None and "Ботинки" in asked["type"]["enum"]
    assert "бордовий" in asked["color"]["enum"]
    assert "type_confidence" in asked and "color_confidence" in asked
    # Пояснення поля доходить до моделі: колір верху, не підошви.
    assert "підошви" in asked["color"]["description"]


def test_fill_empty_fields_map_to_update_fields_with_thresholds():
    from backend.schemas.product import ProductUpdate
    allowed = set(ProductUpdate.model_fields)
    for _f, (_t, _c, _fk, _l, upd) in pa.FILL_EMPTY_FIELDS.items():
        assert upd in allowed, upd
        assert upd in fp.CONFIDENCE_THRESHOLD, upd


def test_placeholder_type_counts_as_empty():
    assert pa.is_placeholder_type(None) and pa.is_placeholder_type("  ")
    assert pa.is_placeholder_type("???") and pa.is_placeholder_type("Невизначено")
    assert not pa.is_placeholder_type("Ботинки")


# ── Пропозиції ──────────────────────────────────────────────────────────────

def _run(monkeypatch, tmp_path, pred, current, pair_ok=True):
    monkeypatch.setattr(pa.barcode_reader, "read_photos", lambda ps: [])
    monkeypatch.setattr(pa.model_profile, "profile_for", lambda *a, **k: {"records": 0, "fields": {}})
    monkeypatch.setattr(pa, "_current_values", lambda db, pid: dict(current))
    monkeypatch.setattr(pa, "_subtype_fits_type", lambda db, t, s: pair_ok and bool(t))
    seen_schema = {}

    def fake_call(model, key, photos, schema, prompt=None):
        seen_schema.update(schema["properties"])
        return {**pred, "_usage": {"promptTokenCount": 1, "candidatesTokenCount": 1}}
    monkeypatch.setattr(pa, "call_gemini", fake_call)
    photo = tmp_path / "x_001.webp"; photo.write_bytes(b"f")
    out = pa.extract_and_propose(_VocabDB(), 7, [photo], api_key="k")
    return out, seen_schema


def test_empty_card_gets_type_and_color_proposals(monkeypatch, tmp_path):
    out, schema = _run(monkeypatch, tmp_path,
        {"type": "Ботинки", "type_confidence": 0.9, "color": "бордовий", "color_confidence": 0.85},
        {"productnumber": "#Ф4441"})
    assert "type" in schema and "color" in schema
    assert ("type_name", "Ботинки", 0.9) in out["proposed"]
    assert ("color_name", "бордовий", 0.85) in out["proposed"]


def test_filled_color_is_never_proposed_to_change(monkeypatch, tmp_path):
    """Колір у ключі тотожності (номер+розмір+колір): зміна = ризик двійника."""
    out, schema = _run(monkeypatch, tmp_path,
        {"color": "бордовий", "color_confidence": 0.99, "type": "Туфлі", "type_confidence": 0.99},
        {"productnumber": "#Ф1", "color_name": "червоний", "type_name": "Ботинки", "typeid": 5})
    assert "color" not in schema and "type" not in schema
    assert not any(f in ("color_name", "type_name") for f, *_ in out["proposed"])


def test_placeholder_type_is_asked_as_empty(monkeypatch, tmp_path):
    out, schema = _run(monkeypatch, tmp_path, {"type": "Туфлі", "type_confidence": 0.9},
                       {"productnumber": "#Ф1", "type_name": "???", "typeid": 99})
    assert "type" in schema
    assert ("type_name", "Туфлі", 0.9) in out["proposed"]


def test_low_confidence_type_is_not_proposed(monkeypatch, tmp_path):
    out, _ = _run(monkeypatch, tmp_path, {"type": "Ботинки", "type_confidence": 0.5},
                  {"productnumber": "#Ф1"})
    assert not any(f == "type_name" for f, *_ in out["proposed"])
    assert ("type_name", "Ботинки", 0.5) in out["below_threshold"]


def test_subtype_without_type_is_held_back(monkeypatch, tmp_path):
    """Без виду підвид неоднозначний — у #Ф4458 у підвид лягло «Ботинки»."""
    out, _ = _run(monkeypatch, tmp_path, {"subtype": "Ботинки", "subtype_confidence": 0.9},
                  {"productnumber": "#Ф4458"})
    assert not any(f == "subtype_name" for f, *_ in out["proposed"])


def test_subtype_passes_with_a_known_pair(monkeypatch, tmp_path):
    out, _ = _run(monkeypatch, tmp_path,
        {"type": "Ботинки", "type_confidence": 0.9, "subtype": "Челсі", "subtype_confidence": 0.9},
        {"productnumber": "#Ф1"}, pair_ok=True)
    assert ("subtype_name", "Челсі", 0.9) in out["proposed"]


def test_subtype_with_unknown_pair_is_held_back(monkeypatch, tmp_path):
    out, _ = _run(monkeypatch, tmp_path,
        {"type": "Кросівки", "type_confidence": 0.9, "subtype": "Челсі", "subtype_confidence": 0.9},
        {"productnumber": "#Ф1"}, pair_ok=False)
    assert ("type_name", "Кросівки", 0.9) in out["proposed"]
    assert not any(f == "subtype_name" for f, *_ in out["proposed"])


def test_subtype_on_typed_card_is_unchanged(monkeypatch, tmp_path):
    """Картка з видом — стара поведінка: перелік уже звужений видом."""
    out, _ = _run(monkeypatch, tmp_path, {"subtype": "Челсі", "subtype_confidence": 0.9},
                  {"productnumber": "#Ф1", "type_name": "Ботинки", "typeid": 5}, pair_ok=False)
    assert ("subtype_name", "Челсі", 0.9) in out["proposed"]


# ── Перевірка пари в базі ───────────────────────────────────────────────────

def test_pair_check_needs_enough_records_and_exact_names():
    assert pa._subtype_fits_type(_VocabDB(pair_count=7), "Ботинки", "Напівботинки")
    assert not pa._subtype_fits_type(_VocabDB(pair_count=1), "Валіза", "Туфлі")
    db = _VocabDB(pair_count=99)
    assert not pa._subtype_fits_type(db, "Ботинки", "Ботинки")   # повтор виду — не підвид
    assert not pa._subtype_fits_type(db, None, "Челсі")
    # lower() у локалі C не опускає кирилицю — порівнюємо точно.
    assert all("lower(" not in q for q in db.sql)

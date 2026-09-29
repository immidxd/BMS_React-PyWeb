"""Перевантажена модель (503) — привід узяти сусідню, а не здатись.

29.09.2026: `gemini-3.5-flash` віддавав 503 «experiencing high demand» підряд,
і розпізнавання зупинилось повністю — хоча `gemini-3.6-flash` і
`gemini-3.5-flash-lite` тієї ж хвилини відповідали 200. Це НЕ квота (429):
чекати нема чого, треба просто спитати іншу модель.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import photo_autofill as pa  # noqa: E402


OVERLOAD = ('HTTP 503: {"error": {"code": 503, "status": "UNAVAILABLE"}}')


@pytest.mark.parametrize("err,expected", [
    (OVERLOAD, True),
    ("HTTP 500: internal", True),
    ('HTTP 429: {"error": {"status": "RESOURCE_EXHAUSTED"}}', False),   # квота — не перевантаження
    ("HTTP 400: bad request", False),
    ("JSONDecodeError: x", False),
    (None, False),
])
def test_only_overload_is_worth_another_model(err, expected):
    assert pa._is_overloaded(err) is expected


class _DB:
    """Мінімальна сесія: рахує виклики, нічого не пише."""
    def __init__(self): self.sql = []
    def execute(self, sql, params=None):
        self.sql.append(str(sql))
        class _R:
            def fetchall(self): return []
            def fetchone(self): return None
            def scalar(self): return 0
            def mappings(self): return self
        return _R()
    def commit(self): pass
    def rollback(self): pass


def _run(monkeypatch, tmp_path, answers):
    """answers: model → відповідь call_gemini. Повертає (звіт, список моделей)."""
    calls = []

    def fake_call(model, api_key, photos, schema, prompt=pa.PROMPT):
        calls.append(model)
        return dict(answers[model])

    monkeypatch.setattr(pa, "call_gemini", fake_call)
    monkeypatch.setattr(pa, "MODEL_FALLBACKS", ("m2", "m3"))
    monkeypatch.setattr(pa.ai_budget, "guard", lambda *a, **k: type(
        "V", (), {"allowed": True, "reason": None, "spent_usd": 0.0})())
    monkeypatch.setattr(pa.ai_budget, "record", lambda *a, **k: 0.001)
    monkeypatch.setattr(pa, "_current_values", lambda *a, **k: {"productnumber": "#Ф1"})
    monkeypatch.setattr(pa, "build_schema", lambda *a, **k: {"properties": {}})
    monkeypatch.setattr(pa, "_record_run", lambda *a, **k: None)
    monkeypatch.setattr(pa, "_profile_layer", lambda *a, **k: None)
    monkeypatch.setattr(pa, "_read_barcodes", lambda photos: type("F", (), {"result": lambda self: []})())
    photo = tmp_path / "Ф1_001.webp"; photo.write_bytes(b"x")
    report = pa.extract_and_propose(_DB(), 1, [photo], model="m1", api_key="k")
    return report, calls


def test_falls_back_to_the_next_model(monkeypatch, tmp_path):
    ok = {"_usage": {"promptTokenCount": 10, "candidatesTokenCount": 2}}
    report, calls = _run(monkeypatch, tmp_path, {"m1": {"_error": OVERLOAD}, "m2": ok})

    assert calls == ["m1", "m2"], "після 503 мала піти наступна модель"
    assert report["ok"] is True
    assert report["model"] == "m2"
    assert report["model_fallback"] is True


def test_healthy_model_is_not_second_guessed(monkeypatch, tmp_path):
    ok = {"_usage": {"promptTokenCount": 10, "candidatesTokenCount": 2}}
    report, calls = _run(monkeypatch, tmp_path, {"m1": ok})

    assert calls == ["m1"], "працездатну модель другою не дублюємо"
    assert report["model_fallback"] is False


def test_quota_error_does_not_burn_other_models(monkeypatch, tmp_path):
    """429 — наша вичерпана квота. Сусідня модель ділить ту саму квоту ключа,
    тож питати її безглуздо: це лише спалило б час і ще один запит."""
    quota = {"_error": 'HTTP 429: {"error": {"status": "RESOURCE_EXHAUSTED"}}',
             "_quota_exhausted": True}
    report, calls = _run(monkeypatch, tmp_path, {"m1": quota})

    assert calls == ["m1"]
    assert report["ok"] is False
    assert report.get("quota_exhausted") is True


def test_all_models_overloaded_says_it_plainly(monkeypatch, tmp_path):
    over = {"_error": OVERLOAD}
    report, calls = _run(monkeypatch, tmp_path, {"m1": over, "m2": over, "m3": over})

    assert calls == ["m1", "m2", "m3"]
    assert report["ok"] is False
    assert report["overloaded"] is True
    # Людині — слова, а не сире тіло 503.
    assert "перевантаж" in report["reason"].lower()
    assert "503" not in report["reason"]
    assert report["models_tried"] == ["m1", "m2", "m3"]

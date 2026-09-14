"""Бренд і вид не можуть називатись однаково — і одна помилка не ламає все.

РЕАЛЬНИЙ ВИПАДОК 15.09.2026. У #Я30 в колонці «Бренд» стояло «Ботинки». Парсер
створив із цього бренд, і далі запобіжник «вид не може збігатись із брендом»
відкидав вид «Ботинки» для КОЖНОГО нового товару — хоча такий вид мають 1844
товари. Увесь новий завоз із 20 черевиків лишився без виду, а блок
характеристик у картці — порожнім. У логу був лише warning.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.models.models import Type, Subtype, Brand  # noqa: E402
from backend.scripts import sheets_parser as sp  # noqa: E402


class _Session:
    """Двійник сесії. Ключ — ТАБЛИЦЯ, не клас: у повному прогоні поруч живуть
    `models.models.Type` і `backend.models.models.Type`, і словник за класом
    повертав би порожній список для одного з них — рівно та пастка, яку
    закриває сам помічник."""
    def __init__(self, types=(), subtypes=()):
        self._rows = {"types": [SimpleNamespace(typename=t) for t in types],
                      "subtypes": [SimpleNamespace(subtypename=t) for t in subtypes]}
    def query(self, model):
        rows = self._rows.get(getattr(model, "__tablename__", ""), [])
        return SimpleNamespace(all=lambda: rows)


def test_known_type_is_recognised_case_and_space_insensitive():
    s = _Session(types=["Ботинки", "Кросівки"])
    assert sp._is_known_taxonomy_name(s, Type, "ботинки")
    assert sp._is_known_taxonomy_name(s, Type, "  Кросівки ")
    assert not sp._is_known_taxonomy_name(s, Type, "Caprice")


def test_subtype_is_checked_by_its_own_column():
    s = _Session(subtypes=["Челсі"])
    assert sp._is_known_taxonomy_name(s, Subtype, "Челсі")
    assert not sp._is_known_taxonomy_name(s, Type, "Челсі")


def test_works_regardless_of_import_path():
    """Подвійний імпорт: `models.models.Type` і `backend.models.models.Type` —
    різні обʼєкти. Порівняння за таблицею, не за класом."""
    import importlib
    alt = importlib.import_module("models.models") if "models.models" in sys.modules else None
    s = _Session(types=["Ботинки"])
    for model in filter(None, [Type, getattr(alt, "Type", None)]):
        assert sp._is_known_taxonomy_name(s, model, "Ботинки")


def test_guard_text_documents_both_directions():
    """Запобіжник симетричний: бренд не називається як вид, а відомий вид не
    блокується брендом-двійником."""
    src = (BACKEND / "scripts" / "sheets_parser.py").read_text(encoding="utf-8")
    assert "Rejected Brand" in src and "це назва виду/підвиду" in src
    assert "але це відомий вид — приймаю" in src

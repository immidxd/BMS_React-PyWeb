"""Резолвер довідників: самозастосовний канон і строгий режим.

Два рубежі з трьох живуть тут. Перший (enum у схемі) — на боці провайдера,
а другий і третій — у цій функції: вона приводить перевірені варіанти до
канону й відмовляється створювати нові значення з машинного джерела.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

# ⚠️ product_service робить `from models import models`, тож backend/ мусить бути
# у sys.path — інакше ModuleNotFoundError. Той самий заголовок, що в
# test_product_journal_bidirectional_sync.
BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services.product_service import _resolve_lookup_id_by_name  # noqa: E402


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows


class _FakeDB:
    """Мінімальний двійник сесії: тримає довідник у памʼяті й ЛІЧИТЬ вставки.

    Справжня БД тут не потрібна — перевіряємо логіку вибору, а не SQL.
    """
    def __init__(self, rows: dict[str, int]):
        self.rows = dict(rows)          # назва → id
        self.inserts: list[str] = []
        self._next_id = max(rows.values(), default=0) + 1

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        # ⚠️ Порядок важливий: «SELECT id, name FROM …» теж починається з
        # «SELECT ID», тож повний скан треба ловити ПЕРШИМ, інакше він
        # перехоплюється гілкою точного пошуку й повертає не ту форму рядка.
        if "SELECT id," in sql:                      # повний скан для згортання регістру
            return _FakeResult([(i, n) for n, i in self.rows.items()])
        if sql.strip().upper().startswith("SELECT ID"):
            v = params.get("v")
            return _FakeResult([(self.rows[v],)] if v in self.rows else [])
        if sql.strip().upper().startswith("INSERT"):
            v = params.get("v")
            self.inserts.append(v)
            self.rows[v] = self._next_id
            self._next_id += 1
            return _FakeResult([(self.rows[v],)])
        return _FakeResult([])


SOLE = {"каблук": 1, "підбора": 2, "спортивна": 3, "плоска": 4}


@pytest.mark.parametrize("typed, expect_name", [
    ("підбора", "каблук"),        # рішення власника: не задум, а запис нашвидкуруч
    ("каблук", "каблук"),
    ("спортивний", "спортивна"),  # рід як форма того самого
    ("споривна", "спортивна"),    # одруківка
])
def test_reviewed_variant_resolves_to_canonical_row(typed, expect_name):
    """Ключове: введене в картці старе написання НЕ повертає рядок-варіант.

    Рядки «підбора»/«споривна» лишились у довіднику з нулем товарів після
    злиття. Без канонізації в резолвері вони б знову набирали товари, і
    злиття тихо відкочувалось би по одному.
    """
    db = _FakeDB(SOLE)
    got = _resolve_lookup_id_by_name(db, "sole_types", "soletypename", typed)
    assert got == SOLE[expect_name]
    assert db.inserts == [], f"нічого не мало створюватись, а створено {db.inserts}"


def test_unknown_value_is_created_for_a_human():
    """Людина, що свідомо вписує новий тип, і далі має це робити."""
    db = _FakeDB(SOLE)
    got = _resolve_lookup_id_by_name(db, "sole_types", "soletypename", "мікропора")
    assert got is not None
    assert db.inserts == ["мікропора"]


def test_unknown_value_is_refused_in_strict_mode():
    """Другий рубіж: машинне джерело нових значень не створює.

    Саме тут гине «Термополіуретанова» — не в базі, а на вході.
    """
    db = _FakeDB(SOLE)
    got = _resolve_lookup_id_by_name(db, "sole_types", "soletypename",
                                     "Термополіуретанова", strict=True)
    assert got is None
    assert db.inserts == []


def test_strict_still_resolves_known_and_variants():
    """Строгий режим забороняє СТВОРЮВАТИ, а не знаходити."""
    db = _FakeDB(SOLE)
    assert _resolve_lookup_id_by_name(db, "sole_types", "soletypename",
                                      "підбора", strict=True) == SOLE["каблук"]
    assert db.inserts == []


def test_table_without_canon_map_is_untouched():
    """Таблиці поза мапою (styles, packaging_types…) працюють як раніше."""
    db = _FakeDB({"Класичний": 7})
    assert _resolve_lookup_id_by_name(db, "packaging_types", "packagingname",
                                      "Класичний") == 7
    assert db.inserts == []


def test_empty_is_none_in_both_modes():
    for strict in (False, True):
        db = _FakeDB(SOLE)
        assert _resolve_lookup_id_by_name(db, "sole_types", "soletypename",
                                          "   ", strict=strict) is None
        assert db.inserts == []

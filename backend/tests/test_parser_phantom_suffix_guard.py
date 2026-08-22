"""Парсер не сміє плодити фантомних двійників '-N'.

22.08.2026: у базі знайшовся #В51-2 — точна копія #В51, якого в журналі немає
взагалі. Народився з гілки «бренд відрізняється» після правки картки, після чого
рядок аркуша почав чіплятись до фантома (порядок записів із БД довільний), а
pnum-sync кожні 10 хвилин безуспішно намагався повернути йому номер:
    [pnum-sync] id=348923 → '#В51' CONFLICT, reverted to '#В51-2'

Правила, які тут закріплені:
  • рядок аркуша чіпляється до СВОГО запису (точний номер), а не до суфіксованого;
  • суфікс -N можна створювати ЛИШЕ коли номер справді ділять кілька рядків
    аркуша, або коли всі вільні записи продані (їх правити не можна).
"""
from types import SimpleNamespace

from backend.scripts.sheets_parser import (
    _number_affinity, _suffix_block_reason, _updatable_twins,
)


SOLD = 9


def _p(pid, num, status=1, size=None):
    return SimpleNamespace(id=pid, productnumber=num, statusid=status, sizeeu=size)


def _sold_guard(row_status=1, row_size=None):
    """Копія предиката з парсера: продане недоторканне лише коли рядок аркуша
    живий або не збігається розміром."""
    def guard(p):
        if p.statusid != SOLD:
            return False
        return not (row_status == SOLD and (p.sizeeu or None) == (row_size or None))
    return guard


# ── Порядок кандидатів ──────────────────────────────────────────────────────
def test_own_record_wins_over_suffixed_twin():
    family = [_p(348923, "#В51-2"), _p(123558, "#В51")]
    family.sort(key=lambda p: _number_affinity(p.productnumber, "#В51"))
    assert [p.id for p in family] == [123558, 348923]


def test_hashless_legacy_number_beats_suffix():
    family = [_p(3, "#В36-2"), _p(2, "В36"), _p(1, "#В36")]
    family.sort(key=lambda p: _number_affinity(p.productnumber, "#В36"))
    assert [p.id for p in family] == [1, 2, 3]


def test_suffixes_ordered_by_their_number():
    family = [_p(3, "#Ф1-10"), _p(2, "#Ф1-3"), _p(1, "#Ф1-2")]
    family.sort(key=lambda p: _number_affinity(p.productnumber, "#Ф1"))
    assert [p.id for p in family] == [1, 2, 3]


# ── Коли суфікс заборонений ─────────────────────────────────────────────────
def test_single_sheet_row_never_mints_a_twin():
    """Саме цей випадок і народив #В51-2."""
    reason = _suffix_block_reason([_p(123558, "#В51")], {}, None, _sold_guard())
    assert reason and "123558" in reason


def test_second_sheet_row_may_mint_a_twin():
    """Номер реально ділять два рядки аркуша → двійник законний."""
    assert _suffix_block_reason(
        [_p(123558, "#В51")], {123558: 1}, None, _sold_guard()
    ) is None


def test_sold_record_is_never_overwritten():
    """Продане чіпати не можна — краще новий запис (пастка protect-sold)."""
    assert _suffix_block_reason(
        [_p(123558, "#В51", status=SOLD, size="42")], {}, None, _sold_guard(row_status=1)
    ) is None


def test_sold_record_is_not_offered_for_update():
    """Продане не потрапляє в пул оновлення — правку прийме непроданий двійник."""
    family = [_p(1, "#В51", status=SOLD, size="42"), _p(2, "#В51-2", status=1)]
    guard = _sold_guard(row_status=1)
    assert [p.id for p in _updatable_twins(family, {}, None, guard)] == [2]
    assert "id=2" in _suffix_block_reason(family, {}, None, guard)


def test_pool_and_reason_never_drift():
    """Причина блокування називає рівно той запис, який парсер піде оновлювати."""
    family = [_p(1, "#В51", status=SOLD, size="42"), _p(2, "#В51-2"), _p(3, "#В51-3")]
    guard = _sold_guard(row_status=1)
    pool = _updatable_twins(family, {2: 1}, None, guard)
    assert [p.id for p in pool] == [3]
    assert "id=3" in _suffix_block_reason(family, {2: 1}, None, guard)


# ── Суфікс у журналі — не помилка ───────────────────────────────────────────
# #В37-2, #В38-3 живуть у вкладці «Валізи(Андрій)» як окремі реальні товари.
# Гард проти фантомів не сміє їх ані затирати, ані підміняти базовим номером.
def test_suffixed_sheet_row_updates_only_its_own_record():
    family = [_p(1, "#В37"), _p(2, "#В37-2")]
    pool = _updatable_twins(family, {}, "#В37-2", _sold_guard())
    assert [p.id for p in pool] == [2]


def test_bare_sheet_row_never_touches_a_suffixed_sibling():
    family = [_p(1, "#В37"), _p(2, "#В37-2")]
    pool = _updatable_twins(family, {}, "#В37", _sold_guard())
    assert [p.id for p in pool] == [1]


def test_suffixed_row_whose_record_is_taken_may_mint_its_own_twin():
    """Два рядки '#В37-2' у журналі — законна підстава для '#В37-3'."""
    family = [_p(1, "#В37"), _p(2, "#В37-2")]
    assert _suffix_block_reason(family, {2: 1}, "#В37-2", _sold_guard()) is None


def test_free_pnum_prefers_the_rows_own_number():
    """Якщо власний номер рядка вільний — беремо його, а не вигадуємо -N."""
    from backend.scripts.sheets_parser import _free_pnum_for_row
    assert _free_pnum_for_row(None, "#В51", [_p(9, "#В51-2")]) == "#В51"


def test_free_pnum_falls_back_to_suffix_when_own_number_is_taken(monkeypatch):
    from backend.scripts import sheets_parser as sp
    monkeypatch.setattr(sp, "_next_suffix_pnum", lambda _s, n: n + "-2")
    assert sp._free_pnum_for_row(None, "#В51", [_p(1, "#В51")]) == "#В51-2"


# ── Продане: уточнення бренду ≠ новий товар ─────────────────────────────────
# 22.08.2026: видалили #4248-2, і наступний прогін зробив його заново. У журналі
# 'Sprandi', у базі 'sprandi OUTDOOR PERFOMANCE' — той самий проданий черевик,
# просто дописали лінійку. Правило «продане не чіпати» було надто грубим.
def test_sold_record_accepts_a_brand_refinement_from_a_sold_row():
    family = [_p(129650, "#4248", status=SOLD, size="37")]
    guard = _sold_guard(row_status=SOLD, row_size="37")
    assert [p.id for p in _updatable_twins(family, {}, "#4248", guard)] == [129650]
    assert _suffix_block_reason(family, {}, "#4248", guard)   # суфікс заборонено


def test_sold_record_is_shielded_from_a_live_row():
    """Живий рядок на проданому номері — найімовірніше номер перевикористали."""
    family = [_p(129650, "#4248", status=SOLD, size="37")]
    guard = _sold_guard(row_status=1, row_size="37")
    assert _updatable_twins(family, {}, "#4248", guard) == []
    assert _suffix_block_reason(family, {}, "#4248", guard) is None


def test_sold_record_is_shielded_when_the_size_differs():
    family = [_p(129650, "#4248", status=SOLD, size="37")]
    guard = _sold_guard(row_status=SOLD, row_size="44")
    assert _updatable_twins(family, {}, "#4248", guard) == []
    assert _suffix_block_reason(family, {}, "#4248", guard) is None

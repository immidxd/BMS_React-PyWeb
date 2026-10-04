"""Синк картки завозу підтягує рядки, які перенесли в ІНШУ вкладку.

04.10.2026: #Ф4440–#Ф4460 перенесли з «01.10.2026(Андрій)» у
«01.10.2026(NikolenkoOPT)» — картка Андрія показувала їх до повного парсу.
"""
from backend.scripts.sheets_parser import _journal_number_index, _moved_rows_plan

OWN = "01.10.2026(Андрій)"
NEW = "01.10.2026(NikolenkoOPT)"


def test_rows_moved_to_one_other_tab_are_planned():
    prods = [(1, "#Ф4440"), (2, "#Ф4441"), (3, "#Ф4461")]
    where = {"Ф4440": {NEW}, "Ф4441": {NEW}, "Ф4461": {OWN}}
    assert _moved_rows_plan(prods, {"Ф4461"}, where, OWN) == {NEW: {"Ф4440", "Ф4441"}}


def test_number_still_in_own_tab_is_left_alone():
    """Ростовка з суфіксом теж «своя», якщо база є у вкладці."""
    prods = [(1, "#Ф4461"), (2, "#Ф3477-2")]
    where = {"Ф4461": {OWN, NEW}, "Ф3477": {OWN}}
    assert _moved_rows_plan(prods, {"Ф4461", "Ф3477"}, where, OWN) == {}


def test_number_in_several_other_tabs_is_ambiguous_and_skipped():
    """Повторно використані старі номери (#1002 у 2022 і 2023) — не вгадуємо."""
    prods = [(1, "#1002")]
    where = {"1002": {"12.07.2022", "02.11.2023(Анна)"}}
    assert _moved_rows_plan(prods, set(), where, OWN) == {}


def test_number_gone_from_journal_or_placeholder_is_not_planned():
    prods = [(1, "#Ф9999"), (2, "#???"), (3, "__tmp_rename_7")]
    assert _moved_rows_plan(prods, set(), {"???": {NEW}}, OWN) == {}


def test_journal_index_maps_numbers_to_tabs_without_extra_reads():
    class _WS:
        def __init__(self, t): self.title = t

    class _SH:
        calls = 0
        def worksheets(self): return [_WS(OWN), _WS(NEW), _WS("New")]
        def values_batch_get(self, ranges):
            _SH.calls += 1
            data = {OWN: [["Номер"], ["#Ф4461"], ["#В127"]], NEW: [["Номер"], ["#Ф4440"], ["#Ф4441;"]]}
            return {"valueRanges": [{"values": data[r.split("'")[1]]} for r in ranges]}

    where: dict = {}
    nums, counts = _journal_number_index(_SH(), where)
    assert _SH.calls == 1, "карта вкладок будується з того ж пакетного читання"
    assert where["Ф4440"] == {NEW} and where["Ф4441"] == {NEW} and where["Ф4461"] == {OWN}
    assert "New" not in {t for v in where.values() for t in v}, "шаблон «New» — не завоз"
    assert {"Ф4440", "Ф4461"} <= nums


def test_old_archive_tabs_are_never_touched():
    """Симуляція 04.10.2026: 66 давніх розбіжностей у завозах 2024 р. Відкриття
    старої картки не має тихо пересувати архів між поставками."""
    from datetime import date
    from backend.scripts.sheets_parser import _recent_tab
    today = date(2026, 10, 4)
    assert _recent_tab("01.10.2026(NikolenkoOPT)", today)
    assert not _recent_tab("16.10.2024(Андрій)", today)
    assert not _recent_tab("Валізи(Андрій)", today), "без дати — не свіжа"
    prods = [(1, "#Т540")]
    where = {"Т540": {"16.10.2024(Андрій)"}}
    assert _moved_rows_plan(prods, set(), where, "20.10.2024(Андрій)",
                            allow_tab=lambda t: _recent_tab(t, today)) == {}

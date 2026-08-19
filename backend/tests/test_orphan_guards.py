"""Гарди прибирання орфанів — після інциденту 19.08.2026 (знесло 135 живих товарів).

Кожен із цих випадків колись виглядав як «номера нема у вкладці» і йшов під ніж.
"""
from backend.scripts.sheets_parser import (
    _canon_sheet_num, _is_placeholder_num, _num_base, _sheet_numbers,
)


def test_parser_made_suffixes_fold_to_their_base():
    # Суфікси '-N' і '(…)' створює сам парсер для повторів; в аркуші їх нема.
    assert _num_base("Ф3477-2") == "Ф3477"
    assert _num_base("Ф1810 - 3") == "Ф1810"
    assert _num_base("0738(Л5)") == "0738"
    assert _num_base("Ф955") == "Ф955"


def test_placeholder_numbers_are_never_orphans():
    for n in ("???", "???_347337", "__tmp_rename_512", "#???_1", "", None):
        assert _is_placeholder_num(n) is True
    for n in ("Ф955", "#Ф3477-2", "0738(Л5)"):
        assert _is_placeholder_num(n) is False


def test_sheet_numbers_reads_the_numer_column():
    rows = [["Номер", "Вид"], ["#Ф955", "Кросівки"], ["  ф956 ;", "Черевики"], ["", ""]]
    assert _sheet_numbers(rows) == {"Ф955", "Ф956"}


def test_sheet_numbers_is_empty_when_the_tab_is_unreadable():
    # Порожня/недочитана вкладка не сміє означати «завіз зник».
    assert _sheet_numbers([]) == set()
    assert _sheet_numbers([["Вид", "Бренд"], ["Кросівки", "Ecco"]]) == set()


def test_canon_strips_hash_and_semicolon():
    assert _canon_sheet_num(" #Ф955; ") == "Ф955"


def test_counter_and_database_must_agree():
    from backend.scripts.sheets_parser import _added_mismatch
    # Чесний прогін: 60 доданих, 0 видалених, база виросла на 60.
    assert _added_mismatch(60, 0, 12000, 12060) is None
    # Прибирання враховується.
    assert _added_mismatch(10, 4, 100, 106) is None
    # Класика #Ф955: лічильник каже +59, у базі не змінилось нічого.
    msg = _added_mismatch(59, 0, 12000, 12000)
    assert msg and "розбіжність -59" in msg


def test_journal_numbers_skip_headers_and_blanks():
    from backend.scripts.sheets_parser import _numbers_from_value_ranges
    vrs = [
        {"values": [["Номер"], ["#Ф955"], [""], ["ф956;"]]},
        {"values": [["Номер"], ["#Ф986-2"]]},
        {},                      # вкладка без даних
    ]
    assert _numbers_from_value_ranges(vrs) == {"Ф955", "Ф956", "Ф986-2"}

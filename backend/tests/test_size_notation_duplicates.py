"""Розмір, записаний інакше, — це ТОЙ САМИЙ розмір, а не новий товар.

Інцидент 27.08.2026 (завіз «24.08.2026(Андрій)»): в аркуші переписали `38⅔` на
`38.6`. Парсер порівнював розмір як текст, не впізнав запис БД як той самий, а що
бренд/тип/стан/колір збігались — пішов у гілку «Ростовка» і створив ДРУГИЙ рядок.
Товар роздвоївся: парсер товарів оновлював один рядок, парсер замовлень чіплявся
за інший. Так постраждали 4 товари.

Тут перевіряється контракт `_sizes_match`, який це закриває, і — не менш важливо —
що він НЕ склеює справді різні розміри, інакше зламається законна ростовка.
"""
import pytest

from backend.scripts.sheets_parser import _size_canon, _sizes_match


# ── Те, через що все сталось: один розмір, різні написання ────────────────────
@pytest.mark.parametrize("db_value,sheet_value", [
    ("38⅔", "38.6"),          # рівно інцидент 27.08.2026
    ("43⅓", "43.3"),
    ("44⅔", "44.6"),
    ("45⅓", "45.3"),
    ("38 2/3", "38.6"),       # той самий дріб текстом
    ("42 1/2", "42.5"),
    ("44,6", "44.6"),         # кома замість крапки
    ("42.0", "42"),           # незначущий нуль
    ("38.60", "38.6"),
    ("  45.3  ", "45⅓"),      # пробіли й зворотний напрямок
    ("41/42", "41-42"),       # діапазон двома написаннями
])
def test_same_size_written_differently_is_one_product(db_value, sheet_value):
    assert _sizes_match(db_value, sheet_value) is True
    assert _sizes_match(sheet_value, db_value) is True


# ── Ростовка має вижити: різні розміри лишаються різними ──────────────────────
@pytest.mark.parametrize("a,b", [
    ("38.5", "39"),
    ("38", "39"),
    ("44.6", "44.3"),         # дві третини одного числа — РІЗНІ розміри
    ("42", "42.5"),
    ("38-39", "38"),          # діапазон ≠ окремий розмір
    ("S", "M"),
    ("XL", "XXL"),
    ("45.3", "45.6"),
])
def test_genuinely_different_sizes_stay_separate(a, b):
    assert _sizes_match(a, b) is False


# ── Контракт порожнього поля — той самий, що в _fields_match ──────────────────
@pytest.mark.parametrize("a,b", [
    (None, "42"), ("42", None), ("", "42"), ("   ", "42"), (None, None),
])
def test_missing_data_still_counts_as_match(a, b):
    assert _sizes_match(a, b) is True


# ── Головна безпекова властивість, через яку це окрема функція ────────────────
@pytest.mark.parametrize("raw", ["40x32x14", "7340734", ".12-13", "86/92", "G 1/2", "???"])
def test_canon_never_turns_a_non_empty_size_into_empty(raw):
    """`_normalize_size` має право викинути сміття в '' — і тоді порівняння
    вважало б два різні сміттєві розміри «порожніми», тобто однаковими.
    `_size_canon` такого права не має: він лише зводить написання."""
    assert _size_canon(raw) != ""


def test_canon_does_not_round_or_invent_values():
    # Шкала журналу: ⅓ → .3, а не 0.33. Канон не сміє «уточнювати» число.
    assert _size_canon("43⅓") == "43.3"
    assert _size_canon("43.3") == "43.3"
    assert _sizes_match("43⅓", "43.33") is False


def test_width_letter_block_is_not_a_fraction():
    # «G 1/2» — ширина колодки, а не розмір-дріб: перед дробом нема числа.
    assert "0.5" not in _size_canon("G 1/2")
    assert _sizes_match("G 1/2", "G 1/2") is True
    assert _sizes_match("G 1/2", "G 1/3") is False


# ── Аварійна лампочка після парсу ────────────────────────────────────────────
class _Row:
    def __init__(self, id, productnumber, sizeeu, colorid, deliveryid=808):
        self.id, self.productnumber, self.sizeeu = id, productnumber, sizeeu
        self.colorid, self.deliveryid = colorid, deliveryid


class _FakeSession:
    """Мінімальний дубль сесії: детектор робить рівно один SELECT."""
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_a, **_kw):
        rows = self._rows
        return type("R", (), {"fetchall": staticmethod(lambda: rows)})()


def _twins(rows):
    from backend.scripts.sheets_parser import _find_size_notation_twins
    return _find_size_notation_twins(_FakeSession(rows), [808])


def test_detector_catches_one_size_written_two_ways():
    found = _twins([
        _Row(349635, "#Ф4374", "45⅓", 2417),
        _Row(349685, "#Ф4374", "45.3", 2417),
    ])
    assert len(found) == 1
    assert found[0]["productnumber"] == "#Ф4374"
    assert found[0]["canon_size"] == "45.3"
    assert {r["id"] for r in found[0]["rows"]} == {349635, 349685}


def test_detector_leaves_genuine_rostovka_alone():
    # Один номер, різні РОЗМІРИ — нормальна ростовка, не двійник.
    assert _twins([
        _Row(1, "#Ф4374", "44", 2417),
        _Row(2, "#Ф4374", "45", 2417),
        _Row(3, "#Ф4374", "45.3", 2417),
    ]) == []


def test_detector_does_not_confuse_different_colors():
    # Той самий номер і розмір, але різний колір — окремі товари.
    assert _twins([
        _Row(1, "#Ф4374", "45⅓", 2417),
        _Row(2, "#Ф4374", "45.3", 999),
    ]) == []


def test_detector_is_quiet_when_nothing_was_parsed():
    from backend.scripts.sheets_parser import _find_size_notation_twins
    assert _find_size_notation_twins(_FakeSession([]), []) == []


# ── Звіт про орфанів за РОЗМІРОМ (сліпа пляма звірки за номером) ─────────────
def _sheet(rows):
    from backend.scripts.sheets_parser import _sheet_number_sizes
    return _sheet_number_sizes(rows)


def test_sheet_sizes_go_through_the_same_pipeline_as_parsing():
    # '3XL' в аркуші лежить у базі як 'XXXL', '38⅔' — як '38.6', '44,6' — як '44.6'.
    # Без спільного конвеєра звіт брехав би на кожному такому рядку.
    got = _sheet([["Номер", "Розмір"],
                  ["#Ф4374", "38⅔"], ["#Ф4375", "3XL"], ["#Ф4376", "44,6"]])
    assert got["Ф4374"] == {"38.6"}
    assert got["Ф4375"] == {"xxxl"}
    assert got["Ф4376"] == {"44.6"}


def test_sheet_without_size_column_yields_nothing():
    # Без колонки «Розмір» звіряти нічого — і мовчати краще, ніж вигадувати.
    assert _sheet([["Номер", "Вид"], ["#Ф4374", "Кросівки"]]) == {}
    assert _sheet([]) == {}


def test_rostovka_keeps_every_size_of_one_number():
    got = _sheet([["Номер", "Розмір"], ["#Ф955", "44"], ["#Ф955", "45"], ["#Ф955", "45.3"]])
    assert got["Ф955"] == {"44", "45", "45.3"}


def _orphans(rows, sheet, seen=frozenset()):
    from backend.scripts.sheets_parser import _find_size_level_orphans
    return _find_size_level_orphans(_FakeSession(rows), 808, sheet, set(seen))


def test_report_catches_a_size_that_vanished_while_the_number_stayed():
    # Номер у вкладці є (його тримає інший рядок ростовки), а цього розміру вже
    # нема. Звірка за НОМЕРОМ такий рядок не бачить — саме ця сліпа пляма.
    found = _orphans([_Row(349616, "#Ф4355", "44", 3924)], {"Ф4355": {"45", "45.3"}})
    assert [o["id"] for o in found] == [349616]
    assert found[0]["sheet_sizes"] == ["45", "45.3"]


def test_notation_twins_are_not_this_reports_business():
    # '38⅔' і '38.6' — ОДИН розмір: після `_sizes_match` рядок не орфан.
    # Двійниками за написанням займається _find_size_notation_twins, і плутати
    # ці дві ролі не можна — інакше звіт вимагав би видалити живий рядок.
    assert _orphans([_Row(1, "#Ф4355", "38⅔", 3924)], {"Ф4355": {"38.6"}}) == []


def test_report_is_silent_when_the_size_is_in_the_sheet():
    assert _orphans([_Row(1, "#Ф4355", "38.6", 3924)], {"Ф4355": {"38.6"}}) == []
    # Різні написання одного розміру — теж збіг, не орфан.
    assert _orphans([_Row(1, "#Ф4355", "38⅔", 3924)], {"Ф4355": {"38.6", "39"}}) == []


def test_report_never_accuses_a_row_the_parser_just_touched():
    # Якщо рядок матчився у цьому прогоні — він живий за визначенням.
    assert _orphans([_Row(1, "#Ф4355", "38⅔", 3924)], {"Ф4355": {"39"}}, seen={1}) == []


def test_report_is_silent_when_the_number_is_absent_or_sizeless():
    # Номера нема у вкладці — це справа звірки за НОМЕРОМ, не наша.
    assert _orphans([_Row(1, "#Ф4355", "38.6", 3924)], {"Ф9999": {"40"}}) == []
    # Номер є, але розміри в аркуші порожні — судити нема на чому.
    assert _orphans([_Row(1, "#Ф4355", "38.6", 3924)], {"Ф4355": set()}) == []


# ── Скоринг workspace-merge теж має бачити збіг ──────────────────────────────
def test_strict_size_match_counts_notation_as_a_match():
    from backend.scripts.sheets_parser import _sizes_strict_match
    assert _sizes_strict_match("38⅔", "38.6") is True
    assert _sizes_strict_match("44,6", "44.6") is True
    assert _sizes_strict_match("38.5", "39") is False


def test_strict_size_match_still_refuses_empty():
    # На відміну від _sizes_match, порожнє тут НЕ збіг: у workspace-merge
    # порожнє поле не сміє зараховуватись як «це той самий товар».
    from backend.scripts.sheets_parser import _sizes_strict_match
    for a, b in ((None, "42"), ("42", ""), ("   ", "42"), (None, None)):
        assert _sizes_strict_match(a, b) is False

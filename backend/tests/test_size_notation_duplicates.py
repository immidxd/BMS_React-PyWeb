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

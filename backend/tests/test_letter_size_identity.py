"""Буквений розмір — частина тотожності товару, а не декор.

ЩО СТАЛОСЬ. Ключ тотожності парсера роками порівнював лише `sizeeu`. Для
взуття це працює: розмір числовий. Для одягу `sizeeu` порожній, розмір живе в
`size_letter`, і два рядки аркуша під одним номером — «XL» та «M» — виглядали
однаковими: порожньо проти порожнього це «нема даних → збіг». Спрацьовувала
гілка дубліката, `quantity` ставало 2, а буква дописувалась лише в порожнє
поле. Другий розмір зникав мовчки.

Реальний випадок #Ф4384 (Karl Lagerfeld, завіз 05.09.2026): в аркуші окремими
рядками XL і M, у базі — один запис «XL ×2». Інтерфейс не брехав; він чесно
показував те, чого в базі вже не було.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from types import SimpleNamespace

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.scripts.sheets_parser import (  # noqa: E402
    _letters_match, _normalize_size_letter, _same_size,
)

PARSER_SRC = (BACKEND / "scripts" / "sheets_parser.py").read_text(encoding="utf-8")


def _prod(sizeeu=None, size_letter=None):
    return SimpleNamespace(sizeeu=sizeeu, size_letter=size_letter)


# ── Порівняння букв ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("a, b, same", [
    ("XL", "M", False),          # ← випадок #Ф4384
    ("XL", "XL", True),
    ("xl", "XL", True),          # регістр
    ("М", "M", True),            # кирилична М = латинська M
    ("3XL", "XXXL", True),       # домовленість про запис
    ("L (44)", "L", True),       # числова підказка в дужках
])
def test_letters_match(a, b, same):
    assert _letters_match(a, b) is same


@pytest.mark.parametrize("a, b", [
    ("XL", None), (None, "XL"), ("XL", ""), ("", "XL"), (None, None),
])
def test_empty_letter_still_means_no_data(a, b):
    """Порожнє з будь-якого боку — «нема даних», а не конфлікт.

    Без цього правила старі аркуші без колонки «Буквений» почали б
    розщеплювати наявні записи на кожному перепарсі.
    """
    assert _letters_match(a, b) is True


def test_pure_number_is_not_a_letter_size():
    """«44» у колонці букв — це сміття, і воно не має нікого розрізняти."""
    assert _normalize_size_letter("44") == ""
    assert _letters_match("44", "XL") is True


# ── Спільне порівняння розміру ──────────────────────────────────────────────

def test_the_case_that_broke_it():
    """#Ф4384: обидва рядки без числового розміру, букви різні → РІЗНІ товари."""
    assert _same_size(_prod(size_letter="XL"), None, "M") is False


def test_same_letter_is_still_one_item():
    """Дві однакові футболки XL — це справді quantity=2, і так і має лишитись."""
    assert _same_size(_prod(size_letter="XL"), None, "XL") is True


def test_footwear_is_untouched():
    """Взуття має числовий розмір і порожню букву — поведінка не змінюється.

    9477 товарів мають числовий розмір, і в жодного взуттєвого немає букви,
    тож нове порівняння для них завжди повертає те саме, що й раніше.
    """
    assert _same_size(_prod(sizeeu="42"), "42", None) is True
    assert _same_size(_prod(sizeeu="42"), "43", None) is False


def test_numeric_difference_still_wins():
    """Якщо різняться числа — це різні товари, хоч би букви й збігались."""
    assert _same_size(_prod(sizeeu="42", size_letter="L"), "43", "L") is False


# ── Захист від повторного відкриття дірки ───────────────────────────────────

def test_every_identity_check_uses_the_shared_comparison():
    """Чотири місця вирішують «той самий товар». Буква мусить бути в КОЖНОМУ.

    Пара `_sizes_match` + `_letters_match`, розписана по місцях, — рівно та
    форма дірки, коли поле враховане у трьох умовах із чотирьох.
    """
    stray = [ln.strip() for ln in PARSER_SRC.splitlines()
             if "_sizes_match(p.sizeeu" in ln and "def _same_size" not in ln]
    # Єдине дозволене вживання — усередині самого `_same_size`.
    body = ast.get_source_segment(
        PARSER_SRC,
        next(n for n in ast.parse(PARSER_SRC).body
             if isinstance(n, ast.FunctionDef) and n.name == "_same_size"))
    assert all(ln in body for ln in stray), (
        f"порівняння розміру повз _same_size: {stray}")


def test_same_size_is_actually_used():
    assert PARSER_SRC.count("_same_size(p, size_val, letter_val)") >= 4


# ── Унікальний ключ у БД мусить знати про букву ─────────────────────────────
#
# ⚠️ ЧОМУ ЦЬОГО ФАЙЛУ БУЛО ЗАМАЛО. Тести нижче спершу проходили ВСІ, а парсер
# усе одно склеював XL і M. Порівняння працювало, `id_match` чесно казав
# «різні» — але вставка другого рядка падала на унікальному індексі
# `(номер, sizeeu, колір)`, а обробник IntegrityError шукав «той самий» рядок
# ТИМ САМИМ неповним ключем і піднімав quantity наявному. Вада була на два
# поверхи нижче, ніж перевіряли тести.
#
# Наскрізна перевірка живе в `backend/scripts/verify_parser_size_branching.py`
# (потребує БД). Тут закріплюємо лише те, що можна перевірити статично.

def test_unique_index_declaration_includes_letter():
    """Оголошення індексу в database.py мусить містити size_letter."""
    src = (BACKEND / "models" / "database.py").read_text(encoding="utf-8")
    assert "uix_products_num_size_color_letter" in src
    idx = next(ln for ln in src.splitlines()
               if "create unique index" in ln and "uix_products_num_size_color_letter" in ln)
    for col in ("productnumber", "sizeeu", "size_letter", "colorid"):
        assert col in idx, f"індекс не враховує {col}"
    assert "drop index if exists uix_products_num_size_color" in src, \
        "старий індекс не прибирається — наявні бази лишаться на вужчому ключі"


def test_conflict_lookup_mirrors_the_index():
    """Запит після IntegrityError шукає рядок ТИМ САМИМ ключем, що й індекс.

    Розбіжність тут означає: вставка впала через одне поле, а «винуватця»
    шукають за іншим — і знаходять чужий рядок.
    """
    body = ast.get_source_segment(
        PARSER_SRC,
        next(n for n in ast.parse(PARSER_SRC).body
             if isinstance(n, ast.FunctionDef) and n.name == "_row_by_unique_key"))
    for col in ("productnumber", "sizeeu", "size_letter", "colorid"):
        assert f"Product.{col}" in body, f"пошук конфлікту не враховує {col}"


def test_no_hand_rolled_conflict_lookups_remain():
    """Усі обробники конфлікту йдуть через спільний помічник.

    Чотири копії запиту — рівно та форма дірки, коли поле додали у три з них.
    """
    assert "Product.colorid == color_id," in PARSER_SRC
    stray = PARSER_SRC.count("Product.sizeeu == (size_val or None),\n                        Product.colorid")
    assert stray == 0, "лишився запит конфлікту повз _row_by_unique_key"

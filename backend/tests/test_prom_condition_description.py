import pytest

from backend.services.prom_service import _build_description, _build_name, _condition_line


@pytest.mark.parametrize("condition", ["Новий", "Нове", "Хороший"])
def test_stock_footwear_without_box_uses_plural_form(condition):
    product = {
        "typename": "Кросівки",
        "conditionname": condition,
        "packagingname": None,
    }

    assert _condition_line(product, "uk") == "Нові (Сток), без коробки"
    assert _condition_line(product, "ru") == "Новые (Сток), без коробки"


@pytest.mark.parametrize("typename", ["Сумка", "Cумка"])
def test_stock_bag_without_box_uses_feminine_form(typename):
    product = {
        "typename": typename,
        "conditionname": "Новий",
        "packagingname": "Без коробки",
    }

    assert _condition_line(product, "uk") == "Нова (Сток), без коробки"
    assert _condition_line(product, "ru") == "Новая (Сток), без коробки"


def test_boxed_bag_keeps_box_wording_without_stock_marker():
    product = {
        "typename": "Сумка",
        "conditionname": "Новий",
        "packagingname": "Коробка",
    }

    assert _condition_line(product, "uk") == "Нова, в коробці"
    assert _condition_line(product, "ru") == "Новая, в коробке"


def test_used_condition_is_not_rewritten_as_stock():
    product = {
        "typename": "Сумка",
        "conditionname": "Легковживаний",
        "packagingname": None,
    }

    assert _condition_line(product, "uk") == "Легковживаний"
    assert _condition_line(product, "ru") == "Легкое б/у"


def test_prom_html_contains_feminine_stock_condition_for_bag():
    product = {
        "typename": "Сумка",
        "conditionname": "Новий",
        "packagingname": None,
    }

    description = _build_description(product, "uk")

    assert "<li><b>Стан:</b> Нова (Сток), без коробки</li>" in description


@pytest.mark.parametrize(
    ("color", "expected"),
    [
        ("світло-бежевий", "світло-бежева"),
        ("бежевий", "бежева"),
        ("чорний", "чорна"),
        ("коричневий", "коричнева"),
        ("білий/чорний", "біла/чорна"),
    ],
)
def test_bag_title_uses_feminine_gender_and_color(color, expected):
    product = {
        "typename": "Сумка",
        "gendername": "Жіноча",
        "brandname": "Gino Rossi",
        "model": "Wen",
        "colorname": color,
        "sizes": [],
    }

    assert _build_name(product, "uk") == f"Жіноча сумка Gino Rossi Wen {expected}"


def test_legacy_latin_c_bag_title_is_also_feminine():
    product = {
        "typename": "Cумка",
        "gendername": "Жіноча",
        "brandname": "Gino Rossi",
        "colorname": "бежевий",
        "sizes": [],
    }

    assert _build_name(product, "uk") == "Жіноча сумка Gino Rossi бежева"
    assert _build_name(product, "ru") == "Женская сумка Gino Rossi бежевая"


@pytest.mark.parametrize(
    ("color", "expected"),
    [
        ("помаранчовий", "оранжевая"),
        ("білий/чорний", "белая/черная"),
        ("світло-коричневий", "світло-коричнева"),
    ],
)
def test_russian_bag_title_never_uses_malformed_mixed_suffixes(color, expected):
    product = {
        "typename": "Сумка",
        "gendername": "Жіноча",
        "colorname": color,
        "sizes": [],
    }

    assert _build_name(product, "ru") == f"Женская сумка {expected}"


def test_footwear_title_remains_unchanged():
    product = {
        "typename": "Кросівки",
        "gendername": "Жіноча",
        "brandname": "Ecco",
        "colorname": "бежевий",
        "sizes": [],
    }

    assert _build_name(product, "uk") == "Жіночі кросівки Ecco бежевий"


def test_description_heading_uses_the_same_feminine_bag_grammar():
    product = {
        "typename": "Сумка",
        "gendername": "Жіноча",
        "brandname": "Gino Rossi",
        "model": "Wen",
        "colorname": "бежевий",
        "conditionname": "Новий",
        "packagingname": None,
    }

    description = _build_description(product, "uk")

    assert description.startswith("<p>Жіноча сумка <b>Gino Rossi Wen</b> бежева</p>")

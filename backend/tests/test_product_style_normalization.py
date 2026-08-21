from backend.services.product_style_normalization import (
    canonicalize_style_name,
    subtype_from_style_name,
)


def test_user_visible_style_duplicates_have_one_canonical_value():
    expected = {
        "Класика": "Класичний",
        "Ортопедичні": "Ортопедичний",
        "Повсякдений": "Повсякденний",
        "Повсякденне": "Повсякденний",
        "Повсякденні": "Повсякденний",
        "Спорт": "Спортивний",
        "Спортзал": "Спортивний",
        "Спортивный": "Спортивний",
        "Спортивні": "Спортивний",
        "Трекінгові": "Трекінговий",
        "Туристичний": "Трекінговий",
        "Футбольні": "Футбольний",
    }
    for value, canonical in expected.items():
        assert canonicalize_style_name(value) == canonical


def test_unreviewed_style_is_preserved_without_fuzzy_guessing():
    assert canonicalize_style_name("Пляжний") == "Пляжний"


def test_user_approved_styles_relocate_to_subtype():
    for value in ("Танкетка", "Футзалки", "Гумові", "Кросбоді"):
        assert subtype_from_style_name(value) == value

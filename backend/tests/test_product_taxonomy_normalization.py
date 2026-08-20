from backend.services.product_taxonomy_normalization import (
    canonicalize_subtype_name,
    canonicalize_type_name,
    split_reviewed_combined_type,
    taxonomy_comparison_key,
)


def test_type_aliases_are_exact_and_reviewed():
    assert canonicalize_type_name("Cумка") == "Сумка"
    assert canonicalize_type_name("Босоніжкиї") == "Босоніжки"
    assert canonicalize_type_name("Сандалі") == "Босоніжки"
    assert canonicalize_type_name("Шльпанці") == "Шльопанці"
    assert canonicalize_type_name("Комбінізон") == "Комбінезон"


def test_subtype_aliases_include_user_approved_groups():
    assert canonicalize_subtype_name("Плечева") == "Плечова"
    assert canonicalize_subtype_name("Шоппер") == "Шопер"
    assert canonicalize_subtype_name("Сороконожки") == "Сороконіжки"
    assert canonicalize_subtype_name("Сандалі") == "Босоніжки"
    assert canonicalize_subtype_name("Робочий") == "Робочі"


def test_distinct_portmone_and_wallet_are_not_merged():
    assert canonicalize_type_name("Портмоне") == "Портмоне"
    assert canonicalize_type_name("Гаманець") == "Гаманець"
    assert canonicalize_subtype_name("Портмоне") == "Портмоне"
    assert canonicalize_subtype_name("Гаманець") == "Гаманець"


def test_homoglyph_and_apostrophe_keys_are_stable():
    assert taxonomy_comparison_key("Cліпони") == taxonomy_comparison_key("Сліпони")
    assert taxonomy_comparison_key("В`єтнамки") == taxonomy_comparison_key("В'єтнамки")


def test_unknown_close_value_is_not_fuzzy_merged():
    assert canonicalize_subtype_name("Портмонетка") == "Портмонетка"
    assert canonicalize_type_name("Кросівка") == "Кросівка"


def test_reviewed_combined_values_split_into_explicit_type_and_subtype():
    assert split_reviewed_combined_type("Ботинки-кросівки") == (
        "Кросівки",
        "Хайтопи",
    )
    assert split_reviewed_combined_type("Кросівки-ботинки") == (
        "Кросівки",
        "Хайтопи",
    )
    assert split_reviewed_combined_type("Напівсапоги/ботинки-челсі") == (
        "Напівсапоги",
        "Челсі",
    )
    assert split_reviewed_combined_type("Кеди/Кросівки") == (
        "Кросівки",
        "Кеди",
    )
    assert split_reviewed_combined_type("невідомий-гібрид") is None

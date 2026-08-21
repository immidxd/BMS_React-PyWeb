from backend.services.product_taxonomy_normalization import (
    canonicalize_subtype_name,
    canonicalize_type_name,
    merge_season_values,
    normalize_taxonomy_pair,
    packaging_from_taxonomy_name,
    season_from_taxonomy_name,
    split_reviewed_combined_type,
    style_from_taxonomy_name,
    taxonomy_comparison_key,
)


def test_type_aliases_are_exact_and_reviewed():
    assert canonicalize_type_name("Cумка") == "Сумка"
    assert canonicalize_type_name("Босоніжкиї") == "Босоніжки"
    assert canonicalize_type_name("Сандалі") == "Босоніжки"
    assert canonicalize_type_name("Шльпанці") == "Шльопанці"
    assert canonicalize_type_name("Комбінізон") == "Комбінезон"
    assert canonicalize_type_name("Устілка") == "Устілки"
    assert canonicalize_type_name("Футболки") == "Футболка"
    assert canonicalize_type_name("Черевики") == "Ботинки"
    assert canonicalize_type_name("Черевини") == "Ботинки"
    assert canonicalize_type_name("НапівБотинки") == "Напівботинки"
    assert canonicalize_type_name("Напівчеревики") == "Напівботинки"
    assert canonicalize_type_name("Напівчоботи") == "Напівботинки"
    assert canonicalize_type_name("Труси") == "Білизна"


def test_subtype_aliases_include_user_approved_groups():
    assert canonicalize_subtype_name("Плечева") == "Плечова"
    assert canonicalize_subtype_name("Шоппер") == "Шопер"
    assert canonicalize_subtype_name("Сороконожки") == "Сороконіжки"
    assert canonicalize_subtype_name("Сандалі") == "Босоніжки"
    assert canonicalize_subtype_name("Робочий") == "Робочі"
    assert canonicalize_subtype_name("Футболки") == "Футболка"
    assert canonicalize_subtype_name("Ноутбук") == "Для ноутбука"
    assert canonicalize_subtype_name("Дитячий") == "Дитячі"
    assert canonicalize_subtype_name("Джинсова") == "Джинсові"
    assert canonicalize_subtype_name("Класика") == "Класичні"
    assert canonicalize_subtype_name("Платформа") == "На платформі"
    assert canonicalize_subtype_name("Черевики") == "Ботинки"
    assert canonicalize_subtype_name("Ручна поклажа") == "Ручна"
    assert canonicalize_subtype_name("Рушник-Почно") == "Пончо"
    assert canonicalize_subtype_name("Батфорди") == "Ботфорти"


def test_crossbody_variants_have_one_canonical_spelling():
    for value in (
        "Через плече",
        "Кросбаді",
        "Крос-баді",
        "Крос боді",
        "Крос-боді",
        "Crossbody",
    ):
        assert canonicalize_subtype_name(value) == "Кросбоді"


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
    assert split_reviewed_combined_type("Кросівки трекінгові") == (
        "Кросівки",
        "Трекінгові",
    )
    assert split_reviewed_combined_type("Трекінгові") == (
        "Кросівки",
        "Трекінгові",
    )
    assert split_reviewed_combined_type("невідомий-гібрид") is None


def test_season_labels_are_relocated_out_of_taxonomy():
    expected = {
        "Демі": "Демі",
        "Демісезонні": "Демі",
        "Зимні": "Зима",
        "Зимові": "Зима",
        "Літні": "Літо",
        "Осіні": "Демі",
        "Осінні": "Демі",
        "Весняні": "Демі",
    }
    for value, season in expected.items():
        assert season_from_taxonomy_name(value) == season
        assert canonicalize_type_name(value) is None
        assert canonicalize_subtype_name(value) is None


def test_season_merge_is_stable_and_preserves_existing_values():
    assert merge_season_values("Літо", "Демі", "літо") == "Демі, Літо"
    assert merge_season_values("Єврозима", "Зима") == "Зима, Єврозима"


def test_cross_field_taxonomy_values_have_exact_destinations():
    assert style_from_taxonomy_name("Спорт") == "Спортивний"
    assert style_from_taxonomy_name("Святкові") == "Святковий"
    assert packaging_from_taxonomy_name("У футлярі") == "Футляр"
    assert canonicalize_type_name("Святкові") is None
    assert canonicalize_subtype_name("У футлярі") is None


def test_pair_normalization_promotes_real_type_and_clears_redundancy():
    assert normalize_taxonomy_pair("Взуття", "Кросівки") == (
        "Кросівки",
        None,
        (),
    )
    assert normalize_taxonomy_pair("Черевики", "Ботинки") == (
        "Ботинки",
        None,
        (),
    )
    assert normalize_taxonomy_pair("Ботинки", "Зимові") == (
        "Ботинки",
        None,
        ("Зима",),
    )


def test_service_text_is_blocked_from_type_and_subtype():
    assert canonicalize_type_name("Бренд не вказано") is None
    assert canonicalize_subtype_name("Бренд не вказано") is None

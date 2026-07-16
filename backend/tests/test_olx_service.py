from backend.services import olx_service as O


def test_category_mapping_women_men_kids():
    assert O.olx_category_for("Туфлі", "Жіноча") == 2619
    assert O.olx_category_for("Кросівки", "Жіноча") == 2623
    assert O.olx_category_for("Кросівки", "Чоловіча") == 2687
    assert O.olx_category_for("Черевики", "Дитяча") == 542
    # унісекс іде в жіноче дерево
    assert O.olx_category_for("Кеди", "Унісекс") == 2624


def test_category_shoe_fallback():
    # взуттєвий, але тип поза мапою → «інше взуття» відповідної статі
    assert O.olx_category_for("Челсі", "Жіноча") == O._OLX_CAT_WOMEN_OTHER
    assert O.olx_category_for("Бутси", "Чоловіча") == O._OLX_CAT_MEN_OTHER


def test_category_non_shoe_bags_accessories_clothing():
    # сумки/подорож/аксесуари (стать не важлива)
    assert O.olx_category_for("Сумка", "Жіноча") == 552
    assert O.olx_category_for("Рюкзак", "Унісекс") == 3142
    assert O.olx_category_for("Валіза", "Унісекс") == 3208
    assert O.olx_category_for("Гаманець", "Чоловіча") == 3143
    assert O.olx_category_for("Ремінь", "Чоловіча") == 3158
    assert O.olx_category_for("Парфуми", "Жіноча") == 2728
    # одяг — гендерний
    assert O.olx_category_for("Куртка", "Жіноча") == 2903
    assert O.olx_category_for("Куртка", "Чоловіча") == 2739
    assert O.olx_category_for("Сукня", "Жіноча") == 2891
    assert O.olx_category_for("Штани", "Чоловіча") == 2742


def test_category_typo_and_latin_aliases():
    assert O.olx_category_for("Cумка", "Жіноча") == 552          # латинська C
    assert O.olx_category_for("Ботінки", "Чоловіча") == 2689     # одрук ботінки→ботинки
    assert O.olx_category_for("Босоніжкиї", "Жіноча") == 2628


def test_category_unmapped_returns_none():
    assert O.olx_category_for("Рушник", "Унісекс") is None
    assert O.olx_category_for("", "Жіноча") is None


_DEFS = [
    {"code": "state", "values": [{"code": "used", "label": "Вживане"},
                                 {"code": "new", "label": "Нове"}]},
    {"code": "size", "values": [{"code": "38", "label": "38"},
                                {"code": "38_5", "label": "38.5"}]},
    {"code": "color", "values": [{"code": "black", "label": "Чорний"},
                                 {"code": "white", "label": "Білий"}]},
    {"code": "brand", "values": [{"code": "guess", "label": "GUESS"},
                                 {"code": "nike", "label": "Nike"}]},
]


def test_match_value_code_by_label_and_size_normalization():
    assert O._match_value_code(_DEFS, "color", "чорний") == "black"
    assert O._match_value_code(_DEFS, "brand", "guess") == "guess"
    assert O._match_value_code(_DEFS, "size", "38.5") == "38_5"   # dot→underscore
    assert O._match_value_code(_DEFS, "color", "неонів") is None


def test_build_attributes_state_required_and_single_size():
    prod = {"conditionname": "Новий", "sizes": ["38"], "colorname": "Чорний",
            "brandname": "GUESS"}
    attrs = {a["code"]: a["value"] for a in O._build_attributes(_DEFS, prod)}
    assert attrs["state"] == "new"
    assert attrs["size"] == "38"
    assert attrs["color"] == "black"
    assert attrs["brand"] == "guess"


def test_build_attributes_multisize_omits_size():
    prod = {"conditionname": "Хороший", "sizes": ["38", "39", "40"],
            "colorname": "Білий", "brandname": "Nike"}
    codes = {a["code"] for a in O._build_attributes(_DEFS, prod)}
    assert "size" not in codes          # ростовка → без атрибута розміру
    assert ("state", "used") in {(a["code"], a["value"]) for a in O._build_attributes(_DEFS, prod)}


def test_build_attributes_generic_prefixed_codes():
    # Категорія «Сумки» використовує префіксні коди bags_* — універсальний
    # заповнювач має підхопити їх за семантикою (color/brand), не за точним кодом.
    bag_defs = [
        {"code": "state", "values": [{"code": "used", "label": "Вживане"},
                                     {"code": "new", "label": "Нове"}]},
        {"code": "bags_brand", "values": [{"code": "guess", "label": "GUESS"}]},
        {"code": "bags_color", "values": [{"code": "black", "label": "Чорний"},
                                          {"code": "white", "label": "Білий"}]},
        {"code": "bags_material", "values": [{"code": "leather", "label": "Шкіра"}]},
    ]
    prod = {"conditionname": "Вживаний", "colorname": "Чорний", "brandname": "GUESS",
            "materials": {1: "Шкіра"}, "sizes": []}
    got = {a["code"]: a["value"] for a in O._build_attributes(bag_defs, prod)}
    assert got["state"] == "used"
    assert got["bags_brand"] == "guess"
    assert got["bags_color"] == "black"
    assert got["bags_material"] == "leather"


def test_build_attributes_always_has_state_even_without_defs():
    prod = {"conditionname": "Новий", "sizes": []}
    got = {a["code"]: a["value"] for a in O._build_attributes([], prod)}
    assert got == {"state": "new"}


def test_olx_description_is_plain_text():
    prod = {"brandname": "GUESS", "typename": "Кросівки", "colorname": "Білий",
            "gendername": "Жіноча", "conditionname": "Новий", "sizes": ["37"],
            "materials": {1: "текстиль"}, "description": "<p>html</p>"}
    d = O._build_olx_description(prod)
    assert "<" not in d                  # без HTML
    assert "GUESS" in d and "Розмір: 37" in d

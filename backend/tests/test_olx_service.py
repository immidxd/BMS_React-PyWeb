from backend.services import olx_service as O


def test_category_mapping_women_men_kids():
    assert O.olx_category_for("Туфлі", "Жіноча") == 2619
    assert O.olx_category_for("Кросівки", "Жіноча") == 2623
    assert O.olx_category_for("Кросівки", "Чоловіча") == 2687
    assert O.olx_category_for("Черевики", "Дитяча") == 542
    # унісекс іде в жіноче дерево
    assert O.olx_category_for("Кеди", "Унісекс") == 2624


def test_category_shoe_fallback_and_non_shoe():
    # взуттєвий, але тип поза мапою → «інше взуття» відповідної статі
    assert O.olx_category_for("Уги", "Жіноча") == O._OLX_CAT_WOMEN_OTHER
    # не взуття → None (категорію треба задати окремо)
    assert O.olx_category_for("Сумка", "Жіноча") is None
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


def test_olx_description_is_plain_text():
    prod = {"brandname": "GUESS", "typename": "Кросівки", "colorname": "Білий",
            "gendername": "Жіноча", "conditionname": "Новий", "sizes": ["37"],
            "materials": {1: "текстиль"}, "description": "<p>html</p>"}
    d = O._build_olx_description(prod)
    assert "<" not in d                  # без HTML
    assert "GUESS" in d and "Розмір: 37" in d

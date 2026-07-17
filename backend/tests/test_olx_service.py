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


def test_category_home_and_shoe_accessories():
    assert O.olx_category_for("Взуття", "Жіноча") == O._OLX_CAT_WOMEN_OTHER
    assert O.olx_category_for("Устілки", "Унісекс") == 3162
    assert O.olx_category_for("Рушник", "Унісекс") == 529
    assert O.olx_category_for("Плед", "Унісекс") == 529


def test_category_unmapped_returns_none():
    # товар без заданого типу / справжня екзотика — категорії немає (треба вручну)
    assert O.olx_category_for("Компрес", "Унісекс") is None
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
    prod = {"conditionname": "Вживаний", "sizes": ["38", "39", "40"],
            "colorname": "Білий", "brandname": "Nike"}
    codes = {a["code"] for a in O._build_attributes(_DEFS, prod)}
    assert "size" not in codes          # ростовка → без атрибута розміру
    assert ("state", "used") in {(a["code"], a["value"]) for a in O._build_attributes(_DEFS, prod)}


def test_state_newlike_matches_prom_grid():
    """Правило власника: Новий І Хороший = Нове; решта = Вживане."""
    def state(cond):
        prod = {"conditionname": cond, "sizes": [], "colorname": None, "brandname": None}
        return {a["code"]: a["value"] for a in O._build_attributes(_DEFS, prod)}["state"]
    assert state("Новий") == "new"
    assert state("Хороший") == "new"      # раніше хибно їхало як 'used'
    assert state("Вживаний") == "used"
    assert state("Легковживаний") == "used"
    assert state("Пошкоджений") == "used"


def test_condition_line_states_packaging_and_grammar():
    """Пакування вказується ЗАВЖДИ; «(Сток)» — ЛИШЕ для «Хороший» (не для «Новий»);
    рід/число — за типом товару."""
    def line(typ, cond, pack=None):
        return O._olx_condition_line({"typename": typ, "conditionname": cond,
                                      "packagingname": pack})
    # «Новий» — справді новий товар, БЕЗ «(Сток)»
    assert line("Кросівки", "Новий") == "Нові, без коробки"
    assert line("Кросівки", "Новий", "Коробка") == "Нові, в коробці"
    # «(Сток)» лише для «Хороший»
    assert line("Кросівки", "Хороший") == "Нові (Сток), без коробки"
    assert line("Рюкзак", "Хороший") == "Новий (Сток), без коробки"   # чол. рід
    assert line("Сумка", "Новий") == "Нова, без коробки"              # жін. рід
    assert line("Туфлі", "Вживаний") == "Вживаний"                    # чесно, як є


def test_attributes_autofill_from_bms():
    """Повна відповідність BMS→OLX: розмір рядка, ВЕРХНІЙ матеріал, відтінок→база."""
    defs = [
        {"code": "state", "values": [{"code": "used", "label": "Вживане"},
                                     {"code": "new", "label": "Нове"}]},
        {"code": "size", "values": [{"code": "38", "label": "38"}]},
        {"code": "color", "values": [{"code": "blue", "label": "Синій"},
                                     {"code": "other", "label": "Інший"}]},
        {"code": "material", "values": [{"code": "2", "label": "Натуральна шкіра"},
                                        {"code": "6", "label": "Текстиль"}]},
        {"code": "brand", "values": [{"code": "nike", "label": "Nike"},
                                     {"code": "other", "label": "Інший"}]},
    ]
    prod = {"conditionname": "Новий", "sizeeu": "38", "sizes": ["36", "37", "38"],
            "colorname": "яскраво-синій", "brandname": "HOKA",
            "materials": {"membrane": "gore-tex", "upper": "шкіра"}}
    a = {x["code"]: x["value"] for x in O._build_attributes(defs, prod)}
    assert a["size"] == "38"          # ВЛАСНИЙ розмір рядка, а не вся ростовка
    assert a["color"] == "blue"       # «яскраво-синій» → базовий «Синій»
    assert a["material"] == "2"       # ВЕРХ (шкіра), а не мембрана gore-tex
    assert a["brand"] == "other"      # HOKA немає в OLX → чесно «Інший»


def test_sole_short_form_maps_to_olx_label():
    """BMS зберігає підошву коротко («плоска»), OLX хоче «Плоска підошва»."""
    defs = [{"code": "sole", "values": [{"code": "1", "label": "Платформа"},
                                        {"code": "2", "label": "Плоска підошва"},
                                        {"code": "4", "label": "Каблук"},
                                        {"code": "5", "label": "Тракторна підошва"}]}]
    def sole(v):
        a = {x["code"]: x["value"] for x in O._build_attributes(defs, {"soletypename": v})}
        return a.get("sole")
    assert sole("плоска") == "2"
    assert sole("спортивна") == "2"
    assert sole("підбора") == "4"      # → Каблук
    assert sole("тракторна") == "5"
    assert sole("рифлена") == "5"


def test_kids_detected_by_size_not_only_gender():
    """Дитяче взуття «Унісекс» (розмір ≤34.5) НЕ має їхати в жіноче взуття."""
    assert O.olx_category_for("Шльопанці", "Унісекс", is_kids=True) == O._OLX_CAT_KIDS
    # без ознаки дитячого — звичайне жіноче дерево
    assert O.olx_category_for("Шльопанці", "Унісекс", is_kids=False) != O._OLX_CAT_KIDS


def test_free_text_attribute_passes_raw_value():
    """Атрибут без словника (Розмір у «Дитяче взуття») — вільний текст."""
    defs = [{"code": "size", "values": []}]
    a = {x["code"]: x["value"] for x in O._build_attributes(defs, {"sizeeu": "29-30"})}
    assert a["size"] == "29-30"


def test_description_has_article_dimensions_and_no_internal_notes():
    prod = {"brandname": "Herschel", "typename": "Рюкзак", "colorname": "темно-синій",
            "model": "Little America", "productnumber": "#А1256",
            "conditionname": "Хороший", "dimensions": "29x49x18",
            "materials": {1: "текстиль"}, "sizes": [],
            "description": "старі", "extranote": "внутрішня нотатка"}
    d = O._build_olx_description(prod)
    assert "(Внутрішній артикул: #А1256)" in d       # ідентифікує товар
    # Артикул — ОДРАЗУ під вступом, без порожнього рядка між ними.
    assert "темно-синій\n(Внутрішній артикул: #А1256)" in d
    assert "Габарити: 29x49x18 см" in d              # критично для сумок/рюкзаків
    assert "Стан: Новий (Сток), без коробки" in d
    assert "старі" not in d                          # внутрішня нотатка НЕ публікується
    assert "внутрішня нотатка" not in d
    assert "<" not in d                              # чистий текст
    # Живий продавець не ставить крапку в кінці кожного рядка.
    for line in d.split("\n"):
        assert not line.strip().endswith("."), f"trailing period: {line!r}"


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

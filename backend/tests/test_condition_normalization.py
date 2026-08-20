from backend.services.condition_normalization import normalize_condition_name


def test_condition_typo_is_canonicalized():
    assert normalize_condition_name("Пошкодженний") == "Пошкоджений"
    assert normalize_condition_name("  пошкоджені ") == "Пошкоджений"


def test_used_alias_is_canonicalized():
    assert normalize_condition_name("Б/У") == "Вживаний"


def test_free_text_note_is_not_a_condition():
    assert normalize_condition_name("тиснення бренду на шкірі нечитабельне") is None
    assert normalize_condition_name("2500") is None

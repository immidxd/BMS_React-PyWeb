"""Червоний номер: що вважається дублем, а що — законна ростовка/суфікс."""
from backend.services.duplicate_numbers import find_duplicates


def test_same_number_in_two_deliveries_is_a_duplicate():
    out = find_duplicates([("#Ф1971", 1, "01.05.2025(Андрій)", 5, 1),
                           ("#Ф1971", 2, "09.05.2025(Андрій)", 6, 1)])
    assert out["#Ф1971"]["reason"] == "deliveries"
    assert out["#Ф1971"]["deliveries"] == ["01.05.2025(Андрій)", "09.05.2025(Андрій)"]


def test_rostovka_in_one_delivery_is_not_a_duplicate():
    """Один номер, кілька розмірів того ж бренду й виду — норма."""
    assert find_duplicates([("#Ф4440", 9, "x", 5, 1), ("#Ф4440", 9, "x", 5, 1),
                            ("#Ф4440", 9, "x", None, 1)]) == {}


def test_conflicting_brand_or_type_in_one_delivery_is_a_duplicate():
    assert find_duplicates([("#А1124", 3, "x", 5, 1), ("#А1124", 3, "x", 6, 1)])["#А1124"]["reason"] == "conflict"
    assert find_duplicates([("#А1198", 3, "x", 5, 1), ("#А1198", 3, "x", 5, 2)])["#А1198"]["reason"] == "conflict"


def test_service_and_suffix_numbers_are_ignored():
    rows = [("#???", 1, "a", 1, 1), ("#???", 2, "b", 2, 2),
            ("__tmp_rename_1", 1, "a", 1, 1), ("__tmp_rename_1", 2, "b", 1, 1),
            ("#В37", 1, "a", 1, 1), ("#В37-2", 2, "b", 2, 2)]
    assert find_duplicates(rows) == {}


def test_legacy_number_without_hash_is_a_different_number():
    """4401 (імпорт 2025-06-01) і #4401 — свідомо лишені як є, не червоні."""
    assert find_duplicates([("4401", 1, "a", 1, 1), ("#4401", 2, "b", 2, 2)]) == {}


def test_record_without_delivery_does_not_make_a_duplicate_alone():
    assert find_duplicates([("#Ф1", None, None, 1, 1), ("#Ф1", 4, "x", 1, 1)]) == {}


# ── Ростовка з різних завозів (рішення власника 17.10.2026) ───────────────────
def test_size_run_across_deliveries_is_not_a_duplicate():
    """#Ф4350: 36–38 і 40 — завіз 20.08, 39 — 13.08; той самий ECCO Street 1."""
    rows = [("#Ф4350", 807, "20.08.2026(Лісоводи)", 109, 26, "Street 1", s) for s in ("36", "37", "38", "40")]
    rows.append(("#Ф4350", 805, "13.08.2026(Лісоводи)", 109, 26, "street 1 ", "39"))
    assert find_duplicates(rows) == {}


def test_repeated_size_across_deliveries_stays_a_duplicate():
    # 38⅔ і 38.6 — той самий розмір, тож це не ростовка
    rows = [("#Ф1", 1, "a", 5, 1, "M", "38⅔"), ("#Ф1", 2, "b", 5, 1, "M", "38.6")]
    assert find_duplicates(rows)["#Ф1"]["reason"] == "deliveries"


def test_different_model_or_missing_size_across_deliveries_stays_a_duplicate():
    assert find_duplicates([("#Ф2", 1, "a", 5, 1, "Street 1", "36"),
                            ("#Ф2", 2, "b", 5, 1, "Street 720", "37")])["#Ф2"]["reason"] == "deliveries"
    assert find_duplicates([("#Ф3", 1, "a", 5, 1, "M", "36"),
                            ("#Ф3", 2, "b", 5, 1, "M", None)])["#Ф3"]["reason"] == "deliveries"
    assert find_duplicates([("#Ф4", 1, "a", 5, 1, "M", "36"),
                            ("#Ф4", 2, "b", 6, 1, "M", "37")])["#Ф4"]["reason"] == "deliveries"

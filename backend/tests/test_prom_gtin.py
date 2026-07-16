from backend.services.prom_service import _feed_item, normalize_gtin


def test_normalize_gtin_accepts_valid_ean13_and_formatting():
    assert normalize_gtin("4006381333931") == "4006381333931"
    assert normalize_gtin("4006-3813 33931") == "4006381333931"


def test_normalize_gtin_rejects_wrong_length_and_check_digit():
    assert normalize_gtin("123") is None
    assert normalize_gtin("4006381333932") is None
    assert normalize_gtin("not-a-code") is None


def test_prom_feed_includes_only_valid_gtin():
    base = {"price": 1000, "typename": "Кросівки", "_qty": 1,
            "gtin": "4006381333931"}
    valid = _feed_item(base, "Ф1", True, [], 1)
    invalid = _feed_item({**base, "gtin": "4006381333932"}, "Ф2", True, [], 1)
    assert "<gtin>4006381333931</gtin>" in valid
    assert "<gtin>" not in invalid


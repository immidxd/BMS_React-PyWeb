from backend.services import shafa_reader


def test_listing_id_from_full_shafa_url():
    url = ("https://shafa.ua/uk/women/zhenskaya-obuv/tufli/"
           "211316208-tufli-zhinochi-klasichni-chovniki-guess-gavi")
    assert shafa_reader._listing_id(url, None) == 211316208


def test_listing_id_from_explicit_id_column():
    assert shafa_reader._listing_id(None, "211316208") == 211316208
    assert shafa_reader._listing_id(None, " 211316208 ") == 211316208


def test_listing_id_prefers_url_numeric_segment_not_random_digits():
    # slug містить рік 2024 у хвості — беремо саме ID-сегмент (перед дефісом).
    url = "https://shafa.ua/uk/men/obuv/188888888-krosivki-nike-2024-original"
    assert shafa_reader._listing_id(url, None) == 188888888


def test_listing_id_rejects_short_or_missing():
    assert shafa_reader._listing_id(None, None) is None
    assert shafa_reader._listing_id("https://shafa.ua/uk/women", None) is None
    assert shafa_reader._listing_id(None, "123") is None  # < 6 цифр — не ID


def test_available_status_constant():
    # Живий і в наявності саме за AVAILABLE; решта станів => не в наявності.
    assert shafa_reader._AVAILABLE_STATUS == "AVAILABLE"

from backend.services import monobazar as M


def test_status_reports_blocked_posting():
    st = M.get_status()
    assert st["available"] is False
    assert st["posting_ready"] is False
    assert st["pricing_ready"] is True
    assert st["blockers"]  # перелік вимог не порожній


def test_price_covers_commission_and_margin():
    e = M.price_economics(1000, "Кросівки")   # markup 1.35, комісія 1.9%
    assert e["net"] >= e["target_net"] - 0.01
    assert e["margin_safe"] is True
    assert e["commission"] > 0
    # чистими після комісії має бути ≥ база×націнка
    assert e["net"] >= 1350 - 0.01


def test_never_lowers_existing_higher_price():
    e = M.price_economics(1000, "Туфлі", current_price=5000)
    assert e["effective_price"] == 5000


def test_commission_configurable():
    e = M.price_economics(1000, "Кросівки", commission=0.001)  # 0.1% акційна
    assert e["commission_pct"] == 0.1
    assert e["net"] >= e["target_net"] - 0.01

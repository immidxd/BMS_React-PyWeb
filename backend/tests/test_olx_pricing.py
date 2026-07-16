from backend.services import olx_pricing as P


def test_delivery_commission_business_flat_plus_rate():
    # бізнес: 3% + 20 грн
    assert P.olx_delivery_commission(1000, is_business=True) == 50.0   # 30 + 20
    assert P.olx_delivery_commission(1000, is_business=False) == 40.0  # 20 + 20


def test_delivery_commission_capped_at_499():
    assert P.olx_delivery_commission(100000, is_business=True) == 499.0


def test_delivery_commission_branch_surcharge():
    base = P.olx_delivery_commission(400, is_business=True)                 # 12 + 20 = 32
    withbranch = P.olx_delivery_commission(400, is_business=True, branch_payment=True)
    assert round(withbranch - base, 2) == 15.0
    assert round(P.olx_delivery_commission(800, True, True)
                 - P.olx_delivery_commission(800, True), 2) == 20.0


def test_zero_price_no_commission():
    assert P.olx_delivery_commission(0) == 0.0


def test_price_covers_all_costs_and_margin():
    e = P.price_economics(1000, "Кросівки", packet_unit=30, ad_spend=50,
                          is_business=True, use_delivery=True)
    # чистими після комісії/пакета/реклами має лишитись ≥ target_net
    assert e["net"] >= e["target_net"] - 0.01
    assert e["margin_safe"] is True
    # ефективна ціна покриває базу+пакет+рекламу+комісію
    assert e["effective_price"] > e["target_net"]
    assert e["total_platform_cost"] == round(
        e["packet_unit"] + e["ad_spend"] + e["delivery_commission"], 2)


def test_markup_matches_prom_grid():
    assert P.markup_multiplier("Кросівки") == 1.35
    assert P.markup_multiplier("Туфлі") == 1.28
    assert P.markup_multiplier("невідомий") == 1.33


def test_never_lowers_existing_higher_price():
    e = P.price_economics(1000, "Туфлі", packet_unit=30, ad_spend=0,
                          current_olx_price=5000)
    assert e["effective_price"] == 5000
    assert e["price_will_change"] is False


def test_no_delivery_means_no_commission_component():
    e = P.price_economics(1000, "Кросівки", packet_unit=30, use_delivery=False)
    assert e["delivery_commission"] == 0.0
    assert e["net"] >= e["target_net"] - 0.01


def test_packet_unit_from_live_packets_prefers_mid_pack():
    packets = [
        {"size": 3, "price": 95},     # 31.7/шт
        {"size": 30, "price": 893},   # 29.77/шт
        {"size": 750, "price": 18369}  # 24.49/шт
    ]
    # середній пакет (20–50) → 30-pack = 29.77
    assert P.packet_unit_from_packets(packets) == 29.77

from backend.routers import publications


def test_manual_instagram_cleanup_targets_feed_and_whole_product_number():
    condition = publications._manual_cleanup_condition("instagram")

    assert "instagram_publications cleanup_ip" in condition
    assert "cleanup_ip.media_type = 'feed'" in condition
    assert "NOT EXISTS" in condition
    assert "available_p.productnumber" in condition
    assert "available_sold.sold_count" in condition


def test_manual_viber_cleanup_uses_same_whole_group_stock_guard():
    condition = publications._manual_cleanup_condition("viber")

    assert "viber_publications cleanup_vp" in condition
    assert "cleanup_vp.status = 'published'" in condition
    assert "available_p.productnumber" in condition
    assert "available_p.productnumber = p.productnumber" in condition


def test_telegram_cleanup_predicate_keeps_existing_post_level_workflow():
    condition = publications._telegram_cleanup_condition()

    assert "telegram_posts cleanup_tp" in condition
    assert "cleanup_tp.tg_status = 'published'" in condition
    assert "cleanup_p2.sizeeu" in condition


def test_manual_cleanup_rejects_unsupported_platform():
    try:
        publications._manual_cleanup_condition("telegram")
    except ValueError as exc:
        assert "Unsupported manual-cleanup platform" in str(exc)
    else:
        raise AssertionError("unsupported platform must be rejected")

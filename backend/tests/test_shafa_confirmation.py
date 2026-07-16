from backend.services import shafa_service


def test_shafa_url_must_be_real_shafa_host():
    assert shafa_service._valid_shafa_url("https://shafa.ua/uk/women/tufli/123")
    assert shafa_service._valid_shafa_url("https://www.shafa.ua/uk/women/tufli/123")
    assert not shafa_service._valid_shafa_url(None)
    assert not shafa_service._valid_shafa_url("https://example.com/shafa.ua/123")


def test_confirm_requires_listing_url(monkeypatch):
    monkeypatch.setattr(
        shafa_service,
        "_product_meta",
        lambda _db, _pid: {"product_id": 1, "productnumber": "Ф1"},
    )
    result = shafa_service.confirm_product(None, 1, None)
    assert result["ok"] is False
    assert "посилання" in result["error"]


def test_link_existing_requires_listing_url(monkeypatch):
    monkeypatch.setattr(
        shafa_service,
        "_product_meta",
        lambda _db, _pid: {"product_id": 1, "productnumber": "Ф1"},
    )
    result = shafa_service.link_existing(None, 1, "")
    assert result["ok"] is False
    assert "посилання" in result["error"]


def test_shafa_fashion_home_fee_tiers_and_cap():
    group = shafa_service.SHAFA_TARIFF_CORE
    assert shafa_service.shafa_commission_rate(500, group) == 0.20
    assert shafa_service.shafa_commission_rate(501, group) == 0.17
    assert shafa_service.shafa_commission_rate(1000, group) == 0.17
    assert shafa_service.shafa_commission_rate(1001, group) == 0.13
    assert shafa_service.shafa_fee(5000, group) == 500


def test_shafa_other_category_fee_tiers_and_minimum():
    group = shafa_service.SHAFA_TARIFF_OTHER
    assert shafa_service.shafa_commission_rate(500, group) == 0.15
    assert shafa_service.shafa_commission_rate(501, group) == 0.12
    assert shafa_service.shafa_commission_rate(1001, group) == 0.09
    assert shafa_service.shafa_fee(40, group) == 10
    assert shafa_service.shafa_fee(80, group) == 20
    assert shafa_service.shafa_fee(120, group) == 30


def test_shafa_tariff_group_maps_backpack_to_accessories():
    assert shafa_service.shafa_tariff_group("Рюкзак") == shafa_service.SHAFA_TARIFF_CORE
    assert shafa_service.shafa_tariff_group("Іграшка") == shafa_service.SHAFA_TARIFF_OTHER


def test_unified_price_protects_prom_and_shafa_margin():
    economics = shafa_service.price_economics(
        1619, "Туфлі", current_prom_price=1990,
    )
    assert economics["target_net"] == 2072.32
    assert economics["prom_safe_price"] == 2590
    assert economics["shafa_safe_price"] == 2390
    assert economics["effective_price"] == 2590
    assert economics["price_will_change"] is True
    assert economics["margin_safe"] is True
    assert economics["shafa_net"] >= economics["target_net"]
    assert economics["prom_net"] >= economics["base_price"] * 1.10


def test_shafa_preserves_existing_correct_prom_price_for_a1256():
    economics = shafa_service.price_economics(
        1500, "Рюкзак", current_prom_price=2490,
    )
    assert economics["prom_safe_price"] == 2490
    assert economics["shafa_safe_price"] == 2350
    assert economics["effective_price"] == 2490
    assert economics["price_will_change"] is False
    assert economics["shafa_fee"] == 323.70
    assert economics["shafa_margin"] == 666.30
    assert economics["margin_safe"] is True


def test_psychological_price_never_rounds_below_required_net():
    for target in (100, 450, 900, 2072.32, 5000):
        price = shafa_service._minimum_shafa_price(target, shafa_service.SHAFA_TARIFF_CORE)
        assert price - shafa_service.shafa_fee(price, shafa_service.SHAFA_TARIFF_CORE) >= target
        assert price % 100 in (50, 90)


def test_one_click_uses_effective_safe_price(monkeypatch):
    class FakeDb:
        def commit(self):
            pass

    meta = {"product_id": 1, "productnumber": "Ф1", "available_qty": 1,
            "price": 1000.0, "typename": "Кросівки"}
    pricing = shafa_service.price_economics(1000, "Кросівки")
    used = {}
    monkeypatch.setattr(shafa_service, "_product_meta", lambda _db, _pid: meta)
    monkeypatch.setattr(shafa_service, "_config", lambda _db: {
        "bridge_enabled": True,
    })
    monkeypatch.setattr(shafa_service, "_publication", lambda _db, _number: None)
    monkeypatch.setattr(shafa_service, "_upsert", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(shafa_service, "_product_pricing", lambda *_args: pricing)
    monkeypatch.setattr(
        shafa_service.prom_service, "prom_product_status",
        lambda _db, _pid: {"on_prom": False, "status": None, "price": None},
    )

    def ensure(_db, _pid, price):
        used["price"] = price
        return {"ok": True, "queued": True, "import_id": "imp-1", "skus": ["Ф1"]}

    monkeypatch.setattr(shafa_service.prom_service, "ensure_product_live", ensure)
    monkeypatch.setattr(shafa_service, "product_status", lambda _db, _pid: {"ok": True})

    result = shafa_service.publish_product(FakeDb(), 1)
    assert result["ok"] is True
    assert result["queued"] is True
    assert used["price"] == pricing["effective_price"]
    assert result["pricing"]["margin_safe"] is True


def _status_fixture(monkeypatch, prom_status):
    class FakeDb:
        def commit(self):
            pass

    meta = {
        "product_id": 1, "productnumber": "Ф1", "available_qty": 1,
        "image_count": 1, "gtins": [], "invalid_gtins": [],
        "price": 1000.0, "typename": "Кросівки", "variant_count": 1,
    }
    publication = {}

    monkeypatch.setattr(shafa_service, "_product_meta", lambda _db, _pid: meta)
    monkeypatch.setattr(shafa_service, "_config", lambda _db: {"bridge_enabled": True})
    monkeypatch.setattr(
        shafa_service.prom_service, "prom_product_status",
        lambda _db, _pid: prom_status,
    )
    monkeypatch.setattr(
        shafa_service, "_publication",
        lambda _db, _number: dict(publication) if publication else None,
    )

    def upsert(_db, _meta, status, **_kwargs):
        publication.update({
            "status": status, "source": "prom_bridge", "shafa_url": None,
            "shafa_listing_id": None, "created_at": None, "updated_at": None,
        })

    monkeypatch.setattr(shafa_service, "_upsert", upsert)
    monkeypatch.setattr(
        shafa_service, "_product_pricing",
        lambda *_args: shafa_service.price_economics(1000, "Кросівки"),
    )
    monkeypatch.setattr(shafa_service, "_tracked_count", lambda _db: 1)
    return FakeDb()


def test_normal_prom_publication_auto_registers_expected_shafa(monkeypatch):
    db = _status_fixture(monkeypatch, {
        "on_prom": True, "status": "on_display", "presence": "available",
        "price": 1690.0, "last_synced_at": None,
    })

    result = shafa_service.product_status(db, 1)

    assert result["state"] == "bridge_ready"
    assert result["tracked"] is True
    assert result["verified"] is False
    assert result["on_shafa"] is False


def test_pending_prom_publication_auto_registers_waiting_shafa(monkeypatch):
    db = _status_fixture(monkeypatch, {
        "on_prom": True, "status": "pending", "presence": None,
        "price": None, "last_synced_at": None,
    })

    result = shafa_service.product_status(db, 1)

    assert result["state"] == "waiting_prom"
    assert result["tracked"] is True
    assert result["verified"] is False

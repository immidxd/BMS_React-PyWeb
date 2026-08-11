from __future__ import annotations

from backend.services import instagram_publisher as ip


class _TgCaption:
    @staticmethod
    def _is_bag(_bms): return False

    @staticmethod
    def _normalize_dimensions(value): return value

    @staticmethod
    def default_tagline(_bms): return "чоловічі кросівки"

    @staticmethod
    def default_emoji(_bms): return "👟"

    @staticmethod
    def default_features(_bms): return ["Проміжна підошва eva, вставка tpu, abzorb"]

    @staticmethod
    def normalize_technology_abbreviations(value):
        return value.replace("eva", "EVA").replace("tpu", "TPU").replace("abzorb", "ABZORB")

    @staticmethod
    def _condition_line(_bms): return "Стан нових (Сток)"

    @staticmethod
    def _condition_icon(_bms): return "🆕"

    @staticmethod
    def _fmt_price(value): return str(value) if value else None


def test_caption_matches_current_instagram_product_style(monkeypatch):
    monkeypatch.setattr(ip, "_tg", lambda: _TgCaption)
    caption = ip.build_caption({
        "brandname": "HOKA",
        "model": "Kawana Mid",
        "price": 3500,
        "productnumber": "Ф3914",
    }, [{"size": "44.6", "measurementscm": "28.5"}])

    assert caption.startswith("👟 HOKA Kawana Mid • чоловічі кросівки")
    assert "📏 Розмір: 44.6 (на ніжку 28.5 см)" in caption
    assert "▪️ Проміжна підошва EVA, вставка TPU, ABZORB" in caption
    assert "🆕 Стан нових (Сток)" in caption
    assert "🛒 Ціна: 3500 грн" in caption
    assert "📲 Пиши #Ф3914 в приватні 👉 +380972337387" in caption
    assert len(caption) <= ip.CAPTION_LIMIT


def test_feed_presets_stay_inside_official_aspect_ratio_range():
    for preset in ip.FEED_PRESETS.values():
        ratio = preset["width"] / preset["height"]
        assert 4 / 5 <= ratio <= 1.91


def test_dry_run_validates_contract_without_external_calls(monkeypatch):
    monkeypatch.setattr(ip, "preview_post", lambda _db, _product_id: {
        "ok": True,
        "productnumber": "Ф42",
        "caption": "Підпис",
        "image_count": 3,
        "default_image_idx": [0, 1, 2],
        "default_feed_preset": "portrait",
    })

    result = ip.dry_run(None, 42, {"image_idx": [2, 0], "feed_preset": "square"})

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["external_calls"] == 0
    assert result["would_publish_as"] == "carousel"
    assert result["image_idx"] == [2, 0]


def test_dry_run_rejects_empty_or_oversized_media_selection(monkeypatch):
    monkeypatch.setattr(ip, "preview_post", lambda _db, _product_id: {
        "ok": True,
        "productnumber": "Ф42",
        "caption": "Підпис",
        "image_count": 12,
        "default_image_idx": list(range(10)),
        "default_feed_preset": "portrait",
    })

    assert ip.dry_run(None, 42, {"image_idx": []})["ok"] is False
    oversized = ip.dry_run(None, 42, {"image_idx": list(range(11))})
    assert oversized["ok"] is False
    assert "до 10" in oversized["error"]


def test_connection_status_never_claims_live_publish_is_ready(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_DISPATCHER_URL", "https://example.invalid")
    monkeypatch.setenv("INSTAGRAM_DISPATCHER_KEY", "not-a-real-secret")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "123")

    status = ip.connection_status()

    assert status["mode"] == "dry_run_only"
    assert status["configured"] is False
    assert status["live_publish_available"] is False
    assert status["schedule_available"] is False

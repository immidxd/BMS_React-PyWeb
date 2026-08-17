from __future__ import annotations

import asyncio
import io

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
    assert "📲 Пиши #Ф3914 нам в приватні" in caption
    assert "+380" not in caption
    assert len(caption) <= ip.CAPTION_LIMIT


def test_story_text_separates_model_tagline_and_plain_product_number(monkeypatch):
    monkeypatch.setattr(ip, "_tg", lambda: _TgCaption)

    value = ip.build_story_text({
        "brandname": "HOKA",
        "model": "Kawana Mid",
        "price": 3500,
        "productnumber": "Ф3914",
    }, [{"size": "44.6", "measurementscm": "28.5"}])

    assert value.splitlines()[0] == "HOKA Kawana Mid"
    assert value.splitlines()[1] == "чоловічі кросівки"
    assert value.splitlines()[-1] == "#Ф3914"
    assert "Пиши" not in value


def test_feed_presets_stay_inside_official_aspect_ratio_range():
    for preset in ip.FEED_PRESETS.values():
        ratio = preset["width"] / preset["height"]
        assert 4 / 5 <= ratio <= 1.91


def test_feed_defaults_to_square_format():
    spec = ip.normalize_media_spec({"publish_type": "feed", "image_idx": [0]}, 1)

    assert spec["feed_preset"] == "square"


def test_dry_run_validates_contract_without_external_calls(monkeypatch):
    monkeypatch.setattr(ip, "preview_post", lambda _db, _product_id: {
        "ok": True,
        "productnumber": "Ф42",
        "caption": "Підпис",
        "image_count": 3,
        "default_image_idx": [0, 1, 2],
        "default_feed_preset": "portrait",
    })
    monkeypatch.setattr(ip, "render_media_for_product", lambda _db, _product_id, _payload: {
        "spec": {"publish_type": "feed", "image_idx": [2, 0], "feed_preset": "square"},
        "assets": [
            {"type": "IMAGE", "bytes": b"one"},
            {"type": "IMAGE", "bytes": b"two"},
        ],
        "output": ip.FEED_PRESETS["square"],
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

    monkeypatch.setattr(ip, "render_media_for_product", lambda _db, _product_id, payload: (
        (_ for _ in ()).throw(ValueError("Для Instagram треба вибрати хоча б одне фото"))
        if payload["image_idx"] == []
        else (_ for _ in ()).throw(ValueError("У каруселі може бути до 10 фото"))
    ))
    assert ip.dry_run(None, 42, {"image_idx": []})["ok"] is False
    oversized = ip.dry_run(None, 42, {"image_idx": list(range(11))})
    assert oversized["ok"] is False
    assert "до 10" in oversized["error"]


def test_connection_status_requires_dispatcher_and_public_r2(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_DISPATCHER_URL", "https://example.invalid")
    monkeypatch.setenv("INSTAGRAM_DISPATCHER_KEY", "not-a-real-secret")
    class _R2:
        R2_PUBLIC_BASE_URL = ""
        @staticmethod
        def is_enabled(): return False
    monkeypatch.setattr(ip, "_r2", lambda: _R2)

    status = ip.connection_status()

    assert status["mode"] == "draft_ready"
    assert status["configured"] is False
    assert status["live_publish_available"] is False
    assert status["schedule_available"] is False


def test_normalize_media_spec_supports_story_and_reel_rules():
    story = ip.normalize_media_spec({
        "publish_type": "story", "image_idx": [3, 2, 1],
        "frames": [{"image_idx": 3, "zoom": 4, "x": -2, "y": 2}],
    }, 5)
    assert story["image_idx"] == [3]
    assert story["frames"][0] == {"image_idx": 3, "zoom": 3.0, "x": -1.0, "y": 1.0}
    reel = ip.normalize_media_spec({"publish_type": "reel", "image_idx": [2, 0]}, 3)
    assert reel["image_idx"] == [2, 0]


def test_default_zoom_matches_feed_and_vertical_formats():
    feed = ip.normalize_media_spec({"publish_type": "feed", "image_idx": [0, 1]}, 2)
    story = ip.normalize_media_spec({"publish_type": "story", "image_idx": [0]}, 2)
    reel = ip.normalize_media_spec({"publish_type": "reel", "image_idx": [0, 1]}, 2)

    assert [frame["zoom"] for frame in feed["frames"]] == [0.9, 0.9]
    assert story["frames"][0]["zoom"] == 0.6
    assert [frame["zoom"] for frame in reel["frames"]] == [0.6, 0.6]


def test_feed_auto_zoom_keeps_studio_default_when_subject_has_safe_margins():
    source = ip.Image.new("RGB", (800, 800), (255, 255, 255))
    ip.ImageDraw.Draw(source).rounded_rectangle((100, 145, 700, 655), radius=35, fill=(70, 90, 120))
    buffer = io.BytesIO()
    source.save(buffer, "PNG")

    zoom, adjusted = ip._recommended_feed_zoom(buffer.getvalue(), ip.FEED_PRESETS["portrait"])

    assert zoom == 0.9
    assert adjusted is False


def test_feed_auto_zoom_disables_shrinking_when_subject_touches_edges():
    source = ip.Image.new("RGB", (800, 800), (255, 255, 255))
    ip.ImageDraw.Draw(source).rectangle((260, 0, 799, 799), fill=(70, 90, 120))
    buffer = io.BytesIO()
    source.save(buffer, "PNG")

    zoom, adjusted = ip._recommended_feed_zoom(buffer.getvalue(), ip.FEED_PRESETS["portrait"])

    assert zoom == 1.0
    assert adjusted is True


def test_renderer_outputs_official_feed_jpeg(monkeypatch):
    class _TgPhotos:
        @staticmethod
        def _load_product(_db, _product_id): return {"productnumber": "Ф42"}
        @staticmethod
        def _photo_entries(_bms): return ([object()], "real")
    source = ip.Image.new("RGB", (800, 600), (230, 20, 30))
    buffer = ip.io.BytesIO()
    source.save(buffer, "PNG")
    monkeypatch.setattr(ip, "_tg", lambda: _TgPhotos)
    import services.product_images as product_images
    monkeypatch.setattr(product_images, "read_image_bytes", lambda _entry: buffer.getvalue())

    rendered = ip.render_media_for_product(None, 42, {
        "publish_type": "feed", "feed_preset": "portrait", "image_idx": [0],
    })

    assert rendered["output"] == ip.FEED_PRESETS["portrait"]
    assert rendered["spec"]["frames"][0]["zoom"] == 1.0
    assert rendered["assets"][0]["type"] == "IMAGE"
    assert len(rendered["assets"][0]["bytes"]) < ip.JPEG_MAX_BYTES
    with ip.Image.open(ip.io.BytesIO(rendered["assets"][0]["bytes"])) as result:
        assert result.size == (1080, 1350)


def test_renderer_respects_explicit_manual_feed_zoom(monkeypatch):
    class _TgPhotos:
        @staticmethod
        def _load_product(_db, _product_id): return {"productnumber": "Ф42"}
        @staticmethod
        def _photo_entries(_bms): return ([object()], "real")
    source = ip.Image.new("RGB", (800, 600), (230, 20, 30))
    buffer = ip.io.BytesIO()
    source.save(buffer, "PNG")
    monkeypatch.setattr(ip, "_tg", lambda: _TgPhotos)
    import services.product_images as product_images
    monkeypatch.setattr(product_images, "read_image_bytes", lambda _entry: buffer.getvalue())

    rendered = ip.render_media_for_product(None, 42, {
        "publish_type": "feed", "feed_preset": "portrait", "image_idx": [0],
        "frames": [{"image_idx": 0, "zoom": 0.9, "x": 0, "y": 0}],
    })

    assert rendered["spec"]["frames"][0]["zoom"] == 0.9


def test_story_renderer_bakes_editable_text_into_jpeg(monkeypatch):
    class _TgPhotos:
        @staticmethod
        def _load_product(_db, _product_id): return {"productnumber": "Ф42"}
        @staticmethod
        def _photo_entries(_bms): return ([object()], "real")
    source = ip.Image.new("RGB", (1080, 1920), (255, 255, 255))
    buffer = ip.io.BytesIO()
    source.save(buffer, "JPEG")
    monkeypatch.setattr(ip, "_tg", lambda: _TgPhotos)
    import services.product_images as product_images
    monkeypatch.setattr(product_images, "read_image_bytes", lambda _entry: buffer.getvalue())

    plain = ip.render_media_for_product(None, 42, {
        "publish_type": "story", "image_idx": [0], "story_text": "",
    })["assets"][0]["bytes"]
    with_text = ip.render_media_for_product(None, 42, {
        "publish_type": "story", "image_idx": [0],
        "story_text": "TEVA REFLIP\nРозмір: 42\nПиши #Ф42 нам в приватні",
    })["assets"][0]["bytes"]

    assert plain != with_text
    with ip.Image.open(ip.io.BytesIO(with_text)) as result:
        assert result.size == (1080, 1920)
        assert any(
            blue > red + 8 and red > 65 and green < 110
            for red, green, blue in result.convert("RGB").get_flattened_data()
        )


def test_story_frame_places_trimmed_studio_subject_only_in_product_zone():
    source = ip.Image.new("RGB", (800, 800), (255, 255, 255))
    ip.ImageDraw.Draw(source).rounded_rectangle((120, 220, 680, 640), radius=40,
                                                fill=(65, 95, 135))
    buffer = ip.io.BytesIO()
    source.save(buffer, "PNG")

    result = ip._render_story_frame(
        buffer.getvalue(), {"zoom": 0.6, "x": 0, "y": 0}, (255, 255, 255),
    )
    left, top, right, bottom = ip.STORY_PRODUCT_BOX

    assert result.size == (1080, 1920)
    assert result.getpixel((540, 300)) == (255, 255, 255)
    assert result.getpixel((540, 1550)) == (255, 255, 255)
    assert any(
        pixel != (255, 255, 255)
        for pixel in result.crop((left, top, right, bottom)).get_flattened_data()
    )
    assert top >= 650
    assert bottom <= 1520


def test_story_detail_summary_compacts_size_run_without_dangling_bullets():
    summary = ip._story_detail_summary([
        "Розміри:",
        "— 44.5 (на ніжку 28.5 см)",
        "— 45 (на ніжку 29 см)",
    ])

    assert summary == "Розміри: 44.5 / 28.5 см • 45 / 29 см"
    assert "• •" not in summary


def test_story_renderer_handles_long_or_partial_custom_text_without_overflow():
    source = ip.Image.new("RGB", (1080, 1920), (248, 248, 248))
    variants = [
        (
            "NEW BALANCE Fresh Foam X Hierro Premium Limited Edition • "
            "чоловічі кросівки для щоденних прогулянок\n"
            "Розміри:\n— 44.5 (на ніжку 28.5 см)\n— 45 (на ніжку 29 см)\n"
            "Ціна: 3890 грн\nПиши #Ф98765 нам в приватні"
        ),
        "GUESS Shaida\nЗаміри: 28 × 16 × 12\n#Ф4329",
        "TEVA ReFlip\nРозмір: 42\nЗамовити #Ф42 у Direct",
    ]

    for value in variants:
        result = ip._render_story_text(source, value)
        assert result.size == (1080, 1920)
        assert result.mode == "RGB"


def test_story_renderer_parses_emoji_price_and_keeps_number_out_of_details(monkeypatch):
    captured = {}

    def fake_fit(_draw, value, **_kwargs):
        captured.setdefault("values", []).append(value)
        return [value], ip._story_font(24, bold=True)

    monkeypatch.setattr(ip, "_fit_story_lines", fake_fit)
    source = ip.Image.new("RGB", (1080, 1920), (255, 255, 255))
    result = ip._render_story_text(
        source,
        "GUESS Shaida\n📐 Заміри: 28 × 16 × 12\n🛒 Ціна: 2100 грн\nПиши #Ф4329 нам в приватні",
    )

    assert result.size == (1080, 1920)
    assert ip.STORY_PRICE_RE.search("🛒 Ціна: 2100 грн")
    assert ip.STORY_CTA_RE.search("Пиши #Ф4329 нам в приватні")
    assert captured["values"] == ["GUESS Shaida", "📐 Заміри: 28 × 16 × 12"]


def test_story_article_label_is_right_aligned(monkeypatch):
    positions = {}
    original_draw = ip.ImageDraw.Draw

    class _RecordingDraw:
        def __init__(self, delegate):
            self._delegate = delegate

        def __getattr__(self, name):
            return getattr(self._delegate, name)

        def text(self, xy, value, *args, **kwargs):
            if value == "АРТИКУЛ":
                positions["label_x"] = xy[0]
                positions["label_width"] = self._delegate.textlength(
                    value, font=kwargs.get("font"),
                )
            return self._delegate.text(xy, value, *args, **kwargs)

    monkeypatch.setattr(
        ip.ImageDraw, "Draw",
        lambda image, *args, **kwargs: _RecordingDraw(original_draw(image, *args, **kwargs)),
    )
    source = ip.Image.new("RGB", (1080, 1920), (255, 255, 255))

    ip._render_story_text(source, "GUESS Shaida\nЦіна: 2100 грн\n#Ф4329")

    assert round(positions["label_x"] + positions["label_width"]) == 1002


def test_batch_dry_run_deduplicates_productnumber_and_never_calls_external(monkeypatch):
    monkeypatch.setattr(ip, "preview_posts_batch", lambda _db, _ids: {
        "ok": True,
        "selected_count": 2,
        "unique_count": 1,
        "merged_count": 1,
        "missing_ids": [],
        "items": [{
            "product_id": 42,
            "productnumber": "Ф42",
            "source_product_ids": [42, 43],
        }],
    })
    monkeypatch.setattr(ip, "dry_run", lambda _db, product_id, payload: {
        "ok": True,
        "product_id": product_id,
        "caption_len": len(payload["caption"]),
    })

    result = ip.dry_run_batch(None, [
        {"product_id": 42, "caption": "Перша чернетка"},
        {"product_id": 43, "caption": "Дублікат ростовки"},
    ])

    assert result["ok"] is True
    assert result["external_calls"] == 0
    assert result["counts"] == {"success": 1, "error": 0}
    assert result["results"][0]["result"]["caption_len"] == len("Перша чернетка")


def test_batch_dry_run_returns_per_item_validation_errors(monkeypatch):
    monkeypatch.setattr(ip, "preview_posts_batch", lambda _db, _ids: {
        "ok": True,
        "selected_count": 2,
        "unique_count": 2,
        "merged_count": 0,
        "missing_ids": [],
        "items": [
            {"product_id": 42, "productnumber": "Ф42", "source_product_ids": [42]},
            {"product_id": 44, "productnumber": "Ф44", "source_product_ids": [44]},
        ],
    })
    monkeypatch.setattr(ip, "dry_run", lambda _db, product_id, _payload: (
        {"ok": True, "product_id": product_id}
        if product_id == 42 else {"ok": False, "error": "Немає фото"}
    ))

    result = ip.dry_run_batch(None, [
        {"product_id": 42},
        {"product_id": 44},
    ])

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["counts"] == {"success": 1, "error": 1}
    assert result["results"][1]["error"] == "Немає фото"


def test_create_post_surfaces_immediate_terminal_worker_failure(monkeypatch):
    prepared = {
        "pnum": "#Ф42",
        "caption": "Підпис",
        "scheduled_at": None,
        "rendered": {"spec": {"publish_type": "feed"}},
    }
    recorded = []
    monkeypatch.setattr(ip, "_cached_result", lambda _db, _key: None)
    monkeypatch.setattr(ip, "dispatcher_status", lambda: asyncio.sleep(0, result={
        "live_publish_available": True,
        "oauth_connected": True,
    }))
    monkeypatch.setattr(ip, "_prepare", lambda _db, _product_id, _payload: prepared)
    monkeypatch.setattr(ip, "_upload_derivatives", lambda _ready: {
        "media": [{"type": "IMAGE", "url": "https://cdn.example.com/a.jpeg"}],
        "media_keys": ["a.jpeg"],
        "cover_url": None,
    })
    monkeypatch.setattr(ip, "_dispatcher_request", lambda *_args, **_kwargs: asyncio.sleep(0, result={
        "job_id": "job-failed",
        "status": "failed",
        "error": "Invalid parameter",
    }))
    monkeypatch.setattr(ip, "_record", lambda *_args, **kwargs: recorded.append(kwargs["dispatch"]))

    result = asyncio.run(ip.create_post(None, 42, {
        "idempotency_key": "terminal-failure",
    }))

    assert recorded == [{
        "job_id": "job-failed",
        "status": "failed",
        "error": "Invalid parameter",
    }]
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Invalid parameter"


# ─── Добова квота: чесна відмова ДО рендерингу ───────────────────────────────

def _capacity_status(used, limit=45, frees_at="2026-08-18T06:30:00+00:00"):
    return {
        "local_24h_limit": limit,
        "usage": [{
            "account_id": "17841450824246612",
            "used": used,
            "queued": 0,
            "limit": limit,
            "remaining": max(0, limit - used),
            "window_frees_at": frees_at,
        }],
    }


def test_daily_capacity_reports_the_remaining_window(monkeypatch):
    monkeypatch.setattr(ip, "_dispatcher_request",
                        lambda *_a, **_k: asyncio.sleep(0, result=_capacity_status(used=40)))
    capacity = asyncio.run(ip.daily_capacity())
    assert capacity["known"] is True
    assert capacity["remaining"] == 5
    assert capacity["used"] == 40
    assert capacity["limit"] == 45


def test_batch_bigger_than_the_remaining_quota_is_refused_before_rendering(monkeypatch):
    monkeypatch.setattr(ip, "_dispatcher_request",
                        lambda *_a, **_k: asyncio.sleep(0, result=_capacity_status(used=40)))
    # Рендер не має навіть початися: інакше людина чекала б хвилини заради відмови.
    def explode(*_args, **_kwargs):
        raise AssertionError("_prepare не повинен викликатися за вичерпаної квоти")
    monkeypatch.setattr(ip, "_prepare", explode)

    items = [{"product_id": index} for index in range(1, 11)]
    result = asyncio.run(ip.create_posts_batch(None, items, "batch-over-quota"))

    assert result["ok"] is False
    assert "лишилося 5 із 45" in result["error"]
    assert result["daily_capacity"]["remaining"] == 5


def test_unreachable_dispatcher_never_blocks_publishing(monkeypatch):
    """Не знаємо квоти — пробуємо публікувати. Вигаданий ліміт гірший за спробу."""
    def fail(*_args, **_kwargs):
        raise RuntimeError("Worker не відповів")
    monkeypatch.setattr(ip, "_dispatcher_request", fail)
    capacity = asyncio.run(ip.daily_capacity())
    assert capacity["known"] is False
    assert "Worker не відповів" in capacity["error"]


def test_capacity_label_speaks_kyiv_time(monkeypatch):
    label = ip._capacity_label("2026-08-18T06:30:00+00:00")
    assert "о 09:30" in label
    assert ip._capacity_label("не дата") == "згодом"

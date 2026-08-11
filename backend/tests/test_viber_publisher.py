from __future__ import annotations

import asyncio
import io
from datetime import datetime, timedelta, timezone

from PIL import Image

from backend.services import viber_publisher as vp


def _jpeg(size=(900, 700), color=(240, 80, 40)) -> bytes:
    image = Image.new("RGB", size, color)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


def test_collage_v1_is_square_jpeg_and_respects_viber_limits():
    images = [_jpeg(color=(30 * i, 90, 180)) for i in range(1, 6)]
    spec = vp.normalize_collage_spec({
        "image_idx": [4, 2, 0, 3, 1],
        "layout": "hero",
        "background": "soft",
        "gap": 16,
        "frames": [{"image_idx": 4, "zoom": 1.4, "x": 0.2, "y": -0.1}],
    }, 5)

    main, thumb = vp.render_collage(images, spec)

    assert main.startswith(b"\xff\xd8")
    assert thumb.startswith(b"\xff\xd8")
    assert len(main) <= vp.COLLAGE_MAX_BYTES
    assert len(thumb) <= vp.THUMB_MAX_BYTES
    with Image.open(io.BytesIO(main)) as image:
        assert image.size == (1080, 1080)
        assert image.mode == "RGB"
    with Image.open(io.BytesIO(thumb)) as image:
        assert image.size == (400, 400)


def test_collage_spec_is_bounded_deduplicated_and_keeps_manual_order():
    spec = vp.normalize_collage_spec({
        "image_idx": [3, 1, 3, 99, -1, 2, 0, 4, 5],
        "layout": "unknown",
        "background": "transparent",
        "gap": 999,
        "frames": [{"image_idx": 3, "zoom": 99, "x": -8, "y": 9}],
    }, 6)

    assert spec["image_idx"] == [3, 1, 2, 0, 4]
    assert spec["layout"] == "auto"
    assert spec["background"] == "white"
    assert spec["gap"] == 32
    assert spec["frames"][0] == {"image_idx": 3, "zoom": 3.0, "x": -1.0, "y": 1.0}


def test_collage_zoom_can_shrink_below_default_but_stays_bounded():
    spec = vp.normalize_collage_spec({
        "image_idx": [0, 1],
        "frames": [
            {"image_idx": 0, "zoom": 0.75, "x": 0, "y": 0},
            {"image_idx": 1, "zoom": 0.1, "x": 0, "y": 0},
        ],
    }, 2)

    assert spec["frames"][0]["zoom"] == 0.75
    assert spec["frames"][1]["zoom"] == vp.FRAME_ZOOM_MIN


def test_render_tile_zoom_below_one_adds_space_around_product():
    raw = _jpeg(size=(100, 100), color=(240, 80, 40))
    tile = vp._render_tile(
        raw,
        (200, 200),
        {"image_idx": 0, "zoom": 0.5, "x": 0, "y": 0},
        (255, 255, 255),
    )

    assert tile.getpixel((0, 0)) == (255, 255, 255)
    assert tile.getpixel((100, 100)) != (255, 255, 255)


def test_five_photo_smart_layout_matches_historical_viber_two_plus_three_grid():
    cells = vp._layout_cells(5, "auto", 4)

    assert len(cells) == 5
    left_top, left_bottom, right_top, right_middle, right_bottom = cells
    assert left_top[0] == left_bottom[0] == 0
    assert left_top[2] == left_bottom[2]
    assert left_top[2] > right_top[2]
    assert left_bottom[1] == left_top[3] + 4
    assert right_top[0] == right_middle[0] == right_bottom[0]
    assert right_top[2] == right_middle[2] == right_bottom[2]
    assert right_middle[1] == right_top[3] + 4
    assert right_bottom[1] == right_middle[1] + right_middle[3] + 4

    # Усі плитки лишаються всередині стабільного полотна 1080×1080.
    for x, y, width, height in cells:
        assert x >= 0 and y >= 0
        assert x + width <= vp.COLLAGE_SIZE
        assert y + height <= vp.COLLAGE_SIZE


def test_uniform_white_photo_margins_are_trimmed_around_product():
    image = Image.new("RGB", (1000, 800), "white")
    # Умовний товар із легкою тінню на білому офіційному фото.
    for x in range(260, 760):
        for y in range(250, 560):
            image.putpixel((x, y), (80, 110, 150))

    trimmed = vp._trim_uniform_photo_background(image)

    assert trimmed.width < 650
    assert trimmed.height < 430
    assert trimmed.width > 500
    assert trimmed.height > 310


def test_schedule_never_silently_turns_invalid_time_into_publish_now():
    too_soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    parsed, error = vp._validate_schedule(too_soon)
    assert parsed is None
    assert "2 хвилини" in error

    parsed, error = vp._validate_schedule("not-a-date")
    assert parsed is None
    assert "Некоректний" in error


def test_default_caption_never_exceeds_picture_description_limit(monkeypatch):
    class Tg:
        @staticmethod
        def _is_bag(_bms): return False
        @staticmethod
        def default_tagline(_bms): return "кросівки"
        @staticmethod
        def default_emoji(_bms): return "👟"
        @staticmethod
        def default_features(_bms): return ["Дуже довга перевага " * 50] * 6
        @staticmethod
        def _condition_line(_bms): return "Стан нових (Сток)"
        @staticmethod
        def _condition_icon(_bms): return "🆕"
        @staticmethod
        def _fmt_price(_value): return "1900"

    monkeypatch.setattr(vp, "_tg", lambda: Tg)
    caption = vp.build_caption({
        "brandname": "Brand", "model": "Model", "price": 1900,
        "productnumber": "Ф42",
    }, [{"size": "43", "measurementscm": "28"}])

    assert len(caption) <= vp.CAPTION_LIMIT
    assert "#Ф42" in caption
    assert "1900 грн" in caption
    assert "🚚 Доставка: 1–2 дні" in caption
    assert "📲 Пиши #Ф42 для замовлення 👉 +380972337387" in caption


def test_live_create_stops_before_render_or_upload_when_dispatcher_is_not_configured(monkeypatch):
    called = {"prepare": 0, "upload": 0}
    monkeypatch.setattr(vp, "_cached_result", lambda _db, _key: None)
    monkeypatch.setattr(vp, "connection_status", lambda: {
        "configured": False,
        "missing": ["VIBER_DISPATCHER_URL"],
    })

    def prepare(*_args, **_kwargs):
        called["prepare"] += 1
        raise AssertionError("live create must not render while dispatcher is unavailable")

    def upload(*_args, **_kwargs):
        called["upload"] += 1

    monkeypatch.setattr(vp, "_prepare", prepare)
    monkeypatch.setattr(vp, "_upload_derivatives", upload)

    result = asyncio.run(vp.create_post(object(), 42, {
        "caption": "test", "idempotency_key": "safe-unconfigured-test",
    }))

    assert result["ok"] is False
    assert "не підключений" in result["error"]
    assert called == {"prepare": 0, "upload": 0}


def test_dry_run_renders_but_never_uploads_records_or_dispatches(monkeypatch):
    called = {"upload": 0, "record": 0, "dispatch": 0}
    monkeypatch.setattr(vp, "_cached_result", lambda _db, _key: None)
    monkeypatch.setattr(vp, "_prepare", lambda *_args, **_kwargs: {
        "pnum": "Ф42",
        "main": b"main-jpeg",
        "thumb": b"thumb-jpeg",
        "spec": {"layout": "auto"},
    })

    def upload(*_args, **_kwargs):
        called["upload"] += 1

    async def dispatch(*_args, **_kwargs):
        called["dispatch"] += 1

    def record(*_args, **_kwargs):
        called["record"] += 1

    monkeypatch.setattr(vp, "_upload_derivatives", upload)
    monkeypatch.setattr(vp, "_dispatch", dispatch)
    monkeypatch.setattr(vp, "_record", record)

    result = asyncio.run(vp.create_post(object(), 42, {
        "caption": "test",
        "dry_run": True,
        "idempotency_key": "safe-dry-run",
    }))

    assert result == {
        "ok": True,
        "dry_run": True,
        "product_id": 42,
        "productnumber": "Ф42",
        "image_bytes": len(b"main-jpeg"),
        "thumbnail_bytes": len(b"thumb-jpeg"),
        "collage": {"layout": "auto"},
    }
    assert called == {"upload": 0, "record": 0, "dispatch": 0}

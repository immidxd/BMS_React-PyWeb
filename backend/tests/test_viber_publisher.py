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

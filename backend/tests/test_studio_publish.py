"""Відправлення постів майстерні: перевірки, що стоять ПЕРЕД мережею.

Сама відправка тут не виконується — вона йде в живі акаунти. Під тестом
натомість усе, що вирішує, чи взагалі можна відправляти: публікаційна похідна
(мережі приймають лише JPEG за HTTPS), ключі ідемпотентності й гарди, які
мають зупинити пост до першого запиту, а не після нього.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from backend.services import studio_publish as sp


def _png(size=(400, 500), color=(30, 90, 200), alpha=None) -> bytes:
    if alpha is None:
        image = Image.new("RGB", size, color)
    else:
        image = Image.new("RGBA", size, (*color, alpha))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


# ── Публікаційна похідна ────────────────────────────────────────────────────

def test_flatten_produces_jpeg():
    data = sp._flatten_to_jpeg(_png(), max_bytes=sp.JPEG_MAX_BYTES)
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_flatten_puts_white_under_transparency():
    """Прозорий PNG без підкладки став би чорним саме там, де в макеті нічого."""
    raw = _png(size=(50, 50), alpha=0)
    data = sp._flatten_to_jpeg(raw, max_bytes=sp.JPEG_MAX_BYTES)
    with Image.open(io.BytesIO(data)) as image:
        pixel = image.convert("RGB").getpixel((25, 25))
    assert min(pixel) > 240


def test_flatten_squeezes_into_limit():
    """Стеля розміру — не порада: мережа має отримати файл, який точно влізе."""
    noisy = Image.effect_noise((1200, 1200), 90).convert("RGB")
    buffer = io.BytesIO()
    noisy.save(buffer, "PNG")
    data = sp._flatten_to_jpeg(buffer.getvalue(), max_bytes=120_000)
    assert len(data) <= 120_000


def test_flatten_reports_impossible_limit():
    noisy = Image.effect_noise((1400, 1400), 110).convert("RGB")
    buffer = io.BytesIO()
    noisy.save(buffer, "PNG")
    with pytest.raises(sp.PublishError):
        sp._flatten_to_jpeg(buffer.getvalue(), max_bytes=2_000)


# ── Тип публікації та ключі ─────────────────────────────────────────────────

def test_story_format_maps_to_story_type():
    assert sp._publish_type("story") == "STORY"
    for other in ("square", "portrait", "landscape"):
        assert sp._publish_type(other) == "FEED"


def test_key_changes_with_frame_content():
    """Ключ містить відбиток кадру: виправлений макет — нова публікація, а
    незмінений упирається в кеш диспетчера замість дубля в стрічці."""
    first = sp._key(7, "instagram", "story", "aaaa")
    same = sp._key(7, "instagram", "story", "aaaa")
    edited = sp._key(7, "instagram", "story", "bbbb")
    assert first == same
    assert first != edited


def test_key_separates_facebook_pages():
    """Дві Сторінки — дві публікації; спільний ключ зробив би з них одну."""
    left = sp._key(7, "facebook", "story", "aaaa", "104772748941632")
    right = sp._key(7, "facebook", "story", "aaaa", "103970525795354")
    assert left != right
    assert len(left) <= 180


# ── Гарди перед відправкою ──────────────────────────────────────────────────

def _post(**patch) -> dict:
    base = {
        "id": 42, "title": "Анонс", "caption": "Текст",
        "targets": [{"platform": "instagram", "format": "story", "enabled": True}],
        "renders": {"story": {"key": "studio/posts/42/story-abc.png"}},
        "scheduled_at": None,
    }
    base.update(patch)
    return base


def test_publish_refuses_without_targets(monkeypatch):
    monkeypatch.setattr(sp.studio, "get_post", lambda db, post_id: _post(targets=[]))
    with pytest.raises(sp.PublishError) as exc:
        asyncio.run(sp.publish_post(None, 42))
    assert "мереж" in str(exc.value)


def test_publish_refuses_when_frame_missing(monkeypatch):
    """Найчастіша помилка людини: обрала Viber із квадратом, а кадр зібрала
    лише в Сторіс. Пост має спинитись тут, а не після відправки в Instagram."""
    monkeypatch.setattr(sp.studio, "get_post", lambda db, post_id: _post(
        targets=[{"platform": "viber", "format": "square", "enabled": True}],
    ))
    with pytest.raises(sp.PublishError) as exc:
        asyncio.run(sp.publish_post(None, 42))
    assert "не зібрано" in str(exc.value)


def test_publish_refuses_past_schedule(monkeypatch):
    monkeypatch.setattr(sp.studio, "get_post", lambda db, post_id: _post())
    with pytest.raises(sp.PublishError) as exc:
        asyncio.run(sp.publish_post(None, 42, {"publish_at": "2020-01-01T10:00:00+02:00"}))
    message = str(exc.value)
    assert "щонайменше" in message
    # Текст спільної перевірки не має рекламувати Instagram у пості для Viber.
    assert "Instagram" not in message


def test_publish_refuses_caption_over_platform_limit(monkeypatch):
    monkeypatch.setattr(sp.studio, "get_post", lambda db, post_id: _post(
        caption="я" * 2400,
        targets=[{"platform": "instagram", "format": "square", "enabled": True}],
        renders={"square": {"key": "studio/posts/42/square-abc.png"}},
    ))
    with pytest.raises(sp.PublishError) as exc:
        asyncio.run(sp.publish_post(None, 42))
    assert "2200" in str(exc.value)


def test_disabled_target_is_not_published(monkeypatch):
    """Знята галочка мережі — це не «опублікувати тихенько»."""
    monkeypatch.setattr(sp.studio, "get_post", lambda db, post_id: _post(
        targets=[{"platform": "instagram", "format": "story", "enabled": False}],
    ))
    with pytest.raises(sp.PublishError):
        asyncio.run(sp.publish_post(None, 42))


def test_story_caption_is_dropped_in_dry_run(monkeypatch):
    """Instagram ігнорує підпис Stories — репетиція має показувати саме нуль,
    інакше людина рахуватиме, що текст піде в кадр."""
    monkeypatch.setattr(sp.studio, "get_post", lambda db, post_id: _post())
    monkeypatch.setattr(sp.studio, "object_bytes", lambda key: _png())
    monkeypatch.setattr(sp, "publication_derivative", lambda *args, **kwargs: {
        "image_url": "https://example.test/studio/42/story-abc.jpeg",
        "image_key": "studio/publish/42/story-abc.jpeg",
        "bytes": 1234, "digest": "abc", "jpeg": b"",
    })
    result = asyncio.run(sp.publish_post(None, 42, {"dry_run": True}))
    assert result["ok"] is True
    assert result["results"][0]["caption_chars"] == 0
    assert result["results"][0]["image_url"].endswith(".jpeg")

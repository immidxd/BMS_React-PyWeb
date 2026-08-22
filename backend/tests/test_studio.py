"""Майстерня публікацій — правила, які не мають тихо зламатись.

Під тестом саме те, що псується непомітно: перетворення залитого файлу
(орієнтація, прозорість, стеля розміру), розбір накреслення шрифта й перевірка
цілей публікації. Растр збирає браузер, тож сюди він не потрапляє — тут
перевіряємо контракт, на який той растр спирається.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.services import studio


def _png(size=(400, 300), color=(200, 120, 60), alpha=False) -> bytes:
    image = Image.new("RGBA" if alpha else "RGB", size,
                      (*color, 128) if alpha else color)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


# ── Підготовка зображень ────────────────────────────────────────────────────

def test_prepare_image_makes_webp_master_and_thumb():
    # Джерело свідомо більше за стелю мініатюри: саме на таких фото видно, чи
    # справді сітка галереї отримує зменшений кадр, а не той самий файл.
    master, thumb, width, height, has_alpha = studio._prepare_image(
        _png(size=(1600, 1200)))
    assert width == 1600 and height == 1200
    assert has_alpha is False
    assert Image.open(io.BytesIO(master)).format == "WEBP"
    assert max(Image.open(io.BytesIO(thumb)).size) <= studio.THUMB_MAX_SIDE
    assert max(Image.open(io.BytesIO(master)).size) == 1600


def test_prepare_image_keeps_alpha():
    """Логотип із прозорим тлом не має отримати чорну підкладку."""
    master, _thumb, _w, _h, has_alpha = studio._prepare_image(_png(alpha=True))
    assert has_alpha is True
    assert Image.open(io.BytesIO(master)).mode in ("RGBA", "LA")


def test_prepare_image_downscales_huge_source():
    master, _thumb, width, height, _alpha = studio._prepare_image(
        _png(size=(6000, 4000)))
    assert max(width, height) == studio.MASTER_MAX_SIDE
    assert max(Image.open(io.BytesIO(master)).size) == studio.MASTER_MAX_SIDE


def test_prepare_image_rejects_garbage():
    with pytest.raises(studio.StudioError):
        studio._prepare_image(b"not an image at all")


# ── Шрифти ──────────────────────────────────────────────────────────────────

def test_font_meta_reads_weight_and_style_from_filename():
    """woff2 Pillow не відкриває — родина й накреслення мають узятися з імені."""
    family, weight, style, has_cyrillic = studio._font_meta(
        b"", "MyBrand-BoldItalic.woff2", "woff2")
    assert family == "MyBrand"
    assert weight == 700
    assert style == "italic"
    assert has_cyrillic is False


def test_font_meta_black_is_not_read_as_regular():
    _family, weight, _style, _cyr = studio._font_meta(b"", "Brand-Black.woff", "woff")
    assert weight == 900


# ── Цілі публікації ─────────────────────────────────────────────────────────

def test_validate_targets_drops_unknown_platform():
    targets = studio._validate_targets([
        {"platform": "instagram", "format": "story"},
        {"platform": "olx", "format": "story"},
        "не словник",
    ])
    assert [target["platform"] for target in targets] == ["instagram"]


def test_validate_targets_coerces_unsupported_format():
    """Viber не вміє Stories — формат мовчки замінюється на перший придатний,
    а не летить у мережу як невідомий."""
    targets = studio._validate_targets([{"platform": "viber", "format": "story"}])
    assert targets[0]["format"] in studio.PLATFORM_FORMATS["viber"]["formats"]
    assert targets[0]["format"] != "story"


def test_every_platform_format_is_a_known_canvas():
    for platform, info in studio.PLATFORM_FORMATS.items():
        assert info["formats"], f"{platform} без жодного формату"
        for key in info["formats"]:
            assert key in studio.CANVAS_FORMATS, f"{platform}: невідомий {key}"


# ── Хмара ───────────────────────────────────────────────────────────────────

def test_studio_requires_r2(monkeypatch):
    """Без налаштованого R2 майстерня має сказати це словами, а не впасти 500."""
    monkeypatch.setattr(studio.r2_storage, "is_enabled", lambda: False)
    with pytest.raises(studio.StudioError) as exc:
        studio._r2()
    assert "R2" in str(exc.value)

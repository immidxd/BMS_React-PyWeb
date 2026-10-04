"""Кадрування 1:1 і поворот: опис кадру → пікселі з оригіналу → квадратний майстер."""

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import photo_edit as pe  # noqa: E402
from services import photo_manager as pm  # noqa: E402


def _two_tone(w=200, h=100):
    """Ліва половина червона, права синя — видно, що саме вирізали."""
    im = Image.new("RGB", (w, h), (255, 0, 0))
    im.paste((0, 0, 255), (w // 2, 0, w, h))
    return im


# ── parse_edit ──────────────────────────────────────────────────────────────

def test_no_change_means_none():
    assert pe.parse_edit(None) is None
    assert pe.parse_edit({}) is None
    assert pe.parse_edit({"rotate": 0}) is None
    assert pe.parse_edit({"rotate": 360}) is None


@pytest.mark.parametrize("bad", [
    {"rotate": 45},
    {"crop": {"x": 0, "y": 0, "w": 0, "h": 1}},
    {"crop": {"x": 0, "y": 0, "w": 9, "h": 1}},
    {"crop": {"x": 1.2, "y": 0, "w": 0.5, "h": 1}},     # рамка повз знімок
    {"crop": {"x": 0, "y": 0, "w": "nan", "h": 1}},
    {"crop": {"x": 0, "y": 0, "w": 1}},
    "square",
])
def test_garbage_is_refused(bad):
    with pytest.raises(ValueError):
        pe.parse_edit(bad)


# ── apply_edit ──────────────────────────────────────────────────────────────

def test_center_square_of_wide_photo():
    out = pe.apply_edit(_two_tone(), pe.parse_edit({"crop": {"x": 0.25, "y": 0, "w": 0.5, "h": 1}}))
    assert out.size == (100, 100)
    assert out.getpixel((10, 50))[0] > 200 and out.getpixel((90, 50))[2] > 200


def test_frame_wider_than_photo_pads_with_white_not_black():
    """Ціле взуття в квадраті: поля білі, а не чорні від Image.crop."""
    out = pe.apply_edit(_two_tone(), pe.parse_edit({"crop": {"x": 0, "y": -0.5, "w": 1, "h": 2}}))
    assert out.size == (200, 200)
    assert out.getpixel((100, 10)) == (255, 255, 255)
    assert out.getpixel((10, 100))[0] > 200


def test_rotation_is_clockwise_and_happens_before_crop():
    out = pe.apply_edit(_two_tone(), pe.parse_edit({"rotate": 90}))
    assert out.size == (100, 200)
    # За годинниковою: ліва (червона) половина стає верхньою.
    assert out.getpixel((50, 10))[0] > 200 and out.getpixel((50, 190))[2] > 200
    sq = pe.apply_edit(_two_tone(), pe.parse_edit({"rotate": 90, "crop": {"x": 0, "y": 0, "w": 1, "h": 0.5}}))
    assert sq.size == (100, 100) and sq.getpixel((50, 50))[0] > 200


# ── У майстер і в R2 лягає вже квадрат ──────────────────────────────────────

def test_add_photos_stores_cropped_square_master(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path / "mirror")
    uploaded = []
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(pm.r2_storage, "upload_file", lambda path, key, **k: uploaded.append(key))
    monkeypatch.setattr(pm, "_invalidate_r2_index", lambda: None)
    src = tmp_path / "wide.jpg"
    _two_tone(2000, 1000).save(src, quality=95)
    plain = tmp_path / "plain.jpg"
    _two_tone(2000, 1000).save(plain, quality=95)

    edit = pe.parse_edit({"crop": {"x": 0.25, "y": 0, "w": 0.5, "h": 1}})
    res = pm.add_photos("Ф9001", "Сумки", [(str(src), "wide.jpg", edit), (str(plain), "plain.jpg")], kind="real")

    assert res == {"added": 2, "errors": []}
    with Image.open(tmp_path / "mirror" / "Сумки" / "Ф9001_001.webp") as m:
        assert m.size == (1000, 1000), "квадрат з оригіналу, до даунскейлу"
    with Image.open(tmp_path / "mirror" / "Сумки" / "Ф9001_002.webp") as m:
        assert m.size == (1512, 756), "без кадру — як і раніше"
    assert uploaded == ["Сумки/Ф9001_001.webp", "Сумки/Ф9001_002.webp"]


def test_edit_existing_photo_in_place(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: False)
    path = tmp_path / "Сумки" / "Ф9002_01.webp"
    path.parent.mkdir(parents=True)
    _two_tone(80, 40).save(path, "WEBP", lossless=True)

    res = pm.edit_photo("Ф9002", "Сумки", path.name,
                        pe.parse_edit({"crop": {"x": 0.5, "y": 0, "w": 0.5, "h": 1}}))

    assert res["filename"] == path.name and (res["width"], res["height"]) == (40, 40)
    with Image.open(path) as m:
        assert m.size == (40, 40) and m.getpixel((20, 20))[2] > 200
    assert list(path.parent.glob(".__bms_edit_*")) == []


def test_edit_refuses_foreign_file(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    with pytest.raises(ValueError):
        pm.edit_photo("Ф9002", "Сумки", "Ф9999_01.webp", {"rotate": 90, "crop": None})


# ── Перевʼязати фото до іншого товару ───────────────────────────────────────

def _real(root, pnum, idx, color="red"):
    p = root / "Сумки" / f"{pnum}_00{idx}.webp"
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), color).save(p, "WEBP")
    return p


def test_move_photo_to_other_product_takes_next_index_and_cleans_source(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    ops = []
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(pm.r2_storage, "upload_file", lambda path, key, **k: ops.append(("up", key)))
    monkeypatch.setattr(pm.r2_storage, "object_exists", lambda key: True)
    monkeypatch.setattr(pm.r2_storage, "delete", lambda key: ops.append(("del", key)))
    monkeypatch.setattr(pm, "_invalidate_r2_index", lambda: None)
    src = _real(tmp_path, "Ф1", 2, "blue")
    _real(tmp_path, "Ф2", 1)

    res = pm.move_photo_to_product("#Ф1", "Сумки", src.name, "#Ф2", "Сумки")

    assert res["moved"] == "Ф2_002.webp" and res["kind"] == "real"
    assert not src.exists()
    with Image.open(tmp_path / "Сумки" / "Ф2_002.webp") as m:
        assert m.getpixel((5, 5))[2] > 150
    # Спершу заливка нового, потім видалення старого — не навпаки.
    assert ops == [("up", "Сумки/Ф2_002.webp"), ("del", "Сумки/Ф1_002.webp")]


def test_move_photo_failure_of_upload_keeps_source(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: True)
    def boom(*a, **k): raise RuntimeError("R2 down")
    monkeypatch.setattr(pm.r2_storage, "upload_file", boom)
    src = _real(tmp_path, "Ф1", 1)
    with pytest.raises(RuntimeError):
        pm.move_photo_to_product("Ф1", "Сумки", src.name, "Ф2", "Сумки")
    assert src.exists() and not (tmp_path / "Сумки" / "Ф2_001.webp").exists()


def test_move_photo_can_change_kind_and_refuses_nonsense(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: False)
    src = _real(tmp_path, "Ф1", 1)
    assert pm.move_photo_to_product("Ф1", "Сумки", src.name, "Ф3", "Взуття", "official")["moved"] == "Ф3_01.webp"
    assert (tmp_path / "Взуття" / "Ф3_01.webp").exists()
    other = _real(tmp_path, "Ф1", 2)
    with pytest.raises(ValueError):
        pm.move_photo_to_product("Ф1", "Сумки", other.name, "#Ф1", "Сумки")   # той самий товар
    with pytest.raises(ValueError):
        pm.move_photo_to_product("Ф9", "Сумки", other.name, "Ф3", "Сумки")   # чужий файл


def test_destination_folder_comes_from_existing_photos_even_cloud_only(monkeypatch):
    """Хмара основна: фото цілі можуть бути лише в R2 — теку беремо з їхніх адрес."""
    from types import SimpleNamespace
    from routers import products as pr
    from services import product_images as pi
    monkeypatch.setattr(pi, "list_images", lambda pnum, include_hidden=False: [
        SimpleNamespace(url="/product-images-drive/abc"),
        SimpleNamespace(url="/product-images/%D0%A1%D1%83%D0%BC%D0%BA%D0%B8/%D0%A42_01.webp?v=1"),
    ])
    assert pr._existing_photo_category("#Ф2") == "Сумки"
    monkeypatch.setattr(pi, "list_images", lambda pnum, include_hidden=False: [])
    assert pr._existing_photo_category("#Ф2") is None

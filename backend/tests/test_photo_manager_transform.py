"""Безпека збережуваних поворотів/віддзеркалення фото товару."""

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import photo_manager as pm  # noqa: E402


def _master(root: Path, pnum: str = "Ф9000") -> Path:
    path = root / "Сумки" / f"{pnum}_01.webp"
    path.parent.mkdir(parents=True)
    image = Image.new("RGB", (80, 40), "red")
    for x in range(40, 80):
        for y in range(40):
            image.putpixel((x, y), (0, 0, 255))
    image.save(path, "WEBP", lossless=True)
    return path


def _is_red(pixel) -> bool:
    return pixel[0] > 180 and pixel[2] < 80


def _is_blue(pixel) -> bool:
    return pixel[2] > 180 and pixel[0] < 80


def test_rotate_right_replaces_same_master_and_keeps_orientation(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: False)
    path = _master(tmp_path)

    result = pm.transform_photo("Ф9000", "Сумки", path.name, "rotate_right")

    assert result["filename"] == path.name
    assert result["width"] == 40 and result["height"] == 80
    assert list(path.parent.glob(".__bms_transform_*")) == []
    with Image.open(path) as image:
        assert image.size == (40, 80)
        assert _is_red(image.getpixel((20, 20)))
        assert _is_blue(image.getpixel((20, 60)))


def test_horizontal_flip_moves_left_and_right_without_renaming(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: False)
    path = _master(tmp_path)

    pm.transform_photo("Ф9000", "Сумки", path.name, "flip_horizontal")

    with Image.open(path) as image:
        assert image.size == (80, 40)
        assert _is_blue(image.getpixel((20, 20)))
        assert _is_red(image.getpixel((60, 20)))


def test_r2_failure_leaves_local_master_byte_for_byte_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    path = _master(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: True)

    def fail_upload(*_args, **_kwargs):
        raise RuntimeError("temporary R2 failure")

    monkeypatch.setattr(pm.r2_storage, "upload_file", fail_upload)

    with pytest.raises(RuntimeError, match="R2 failure"):
        pm.transform_photo("Ф9000", "Сумки", path.name, "rotate_180")

    assert path.read_bytes() == before
    assert list(path.parent.glob(".__bms_transform_*")) == []


def test_r2_receives_exact_committed_bytes_under_same_key(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    path = _master(tmp_path)
    uploaded = {}
    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: True)

    def capture_upload(local_path, key, **kwargs):
        uploaded.update(data=Path(local_path).read_bytes(), key=key, kwargs=kwargs)

    monkeypatch.setattr(pm.r2_storage, "upload_file", capture_upload)

    pm.transform_photo("Ф9000", "Сумки", path.name, "rotate_left")

    assert uploaded["key"] == f"Сумки/{path.name}"
    assert uploaded["data"] == path.read_bytes()
    assert uploaded["kwargs"]["cache_control"] == pm._REPLACEMENT_CACHE_CONTROL

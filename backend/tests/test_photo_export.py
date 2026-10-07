"""Експорт фото: PNG-конвертація, вибір набору для пакета, архів."""
import io
import os
import sys
import zipfile
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import photo_export as pe  # noqa: E402


def _webp(color=(200, 10, 10), size=(40, 30), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "WEBP", lossless=True)
    return buf.getvalue()


def test_to_png_keeps_pixels_and_size():
    png = pe.to_png(_webp())
    with Image.open(io.BytesIO(png)) as im:
        assert im.format == "PNG"
        assert im.size == (40, 30)
        assert im.convert("RGB").getpixel((5, 5)) == (200, 10, 10)


def test_to_png_keeps_alpha():
    png = pe.to_png(_webp((0, 0, 0, 0), mode="RGBA"))
    with Image.open(io.BytesIO(png)) as im:
        assert "A" in im.getbands()


def test_export_name():
    assert pe.export_name("Ф4509_001.webp", "png") == "Ф4509_001.png"
    assert pe.export_name("Ф4509_001.webp", "original") == "Ф4509_001.webp"


def _img(fn, kind="official", hidden=False):
    return SimpleNamespace(filename=fn, kind=kind, hidden=hidden)


def test_pick_by_kind_skips_hidden_and_defects():
    imgs = [_img("a_01.webp"), _img("a_02.webp", hidden=True), _img("a_001.webp", "real"),
            _img("a_def1.webp", "defect")]
    assert [i.filename for i in pe.pick(imgs, "official")] == ["a_01.webp"]
    assert [i.filename for i in pe.pick(imgs, "real")] == ["a_001.webp"]
    assert [i.filename for i in pe.pick(imgs, "all")] == ["a_01.webp", "a_001.webp"]


def test_write_zip_order_failures_and_collisions():
    srcs = {"x1": _webp(), "x2": None, "x3": _webp((0, 200, 0))}
    items = [("Ф1/Ф1_01.png", "x1"), ("Ф1/Ф1_02.png", "x2"), ("Ф1/Ф1_01.png", "x3")]
    buf = io.BytesIO()
    packed, failed = pe.write_zip(buf, items, lambda k: srcs[k], "png")
    assert packed == 2
    assert failed == ["Ф1/Ф1_02.png"]
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert names == ["Ф1/Ф1_01.png", "Ф1/Ф1_01_2.png"]
        assert all(zf.getinfo(n).compress_type == zipfile.ZIP_STORED for n in names)
        with Image.open(io.BytesIO(zf.read("Ф1/Ф1_01_2.png"))) as im:
            assert im.format == "PNG"


def test_write_zip_survives_reader_exception():
    def boom(_k):
        raise RuntimeError("R2 недоступний")
    packed, failed = pe.write_zip(io.BytesIO(), [("a.png", "k")], boom, "png")
    assert (packed, failed) == (0, ["a.png"])


def test_folder_name_strips_hash_and_unsafe():
    assert pe.folder_name("#Ф4509", 1) == "Ф4509"
    assert pe.folder_name("", 7) == "product-7"
    assert "/" not in pe.folder_name("A/B", 2)

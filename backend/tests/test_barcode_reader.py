"""Детермінований шар: що вважаємо кодом товару, а що — сміттям.

Тут НЕ перевіряється саме декодування (це робота zxing-cpp і її гарантує
контрольна сума). Перевіряється наша частина: відбір форматів, визначення
роздрібного коду й обережність із артикулом.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import barcode_reader as br  # noqa: E402


def _hit(fmt, text, photo="Ф1_001.webp"):
    return br.BarcodeHit(fmt, text, photo)


# ── Що є роздрібним кодом ───────────────────────────────────────────────────

@pytest.mark.parametrize("fmt, text, ok", [
    ("EAN13", "4895245119084", True),
    ("EAN8", "12345670", True),
    ("UPCA", "012345678905", True),
    ("DataMatrix", "196432723249", False),   # цифри, але не роздрібний формат
    ("QRCode", "https://reebok.com/qr?x", False),
    ("Code128", "POP454928", False),
])
def test_is_gtin(fmt, text, ok):
    assert _hit(fmt, text).is_gtin is ok


def test_pick_gtin_ignores_qr_and_datamatrix():
    """У #Ф4128 разом із EAN13 лежить QR Reebok — у поле має піти лише код."""
    hits = [_hit("QRCode", "https://www.reebok.com/qr?xPBBRKSPQ4BMR7H"),
            _hit("EAN13", "2230059181797")]
    assert br.pick_gtin(hits).text == "2230059181797"


def test_pick_gtin_returns_none_when_absent():
    assert br.pick_gtin([_hit("QRCode", "https://x")]) is None


# ── Обережність із артикулом ────────────────────────────────────────────────

def test_article_candidates_from_real_datamatrix():
    """Реальний код із #Ф4132. Ми повертаємо ВСІ схожі фрагменти й свідомо не
    вгадуємо, який із них артикул: саме вгадування й породило «HQ8708»."""
    hits = [_hit("DataMatrix", "F2,0225,196432723249,POP454928,GR530AA")]
    assert br.article_candidates(hits) == ["0225", "196432723249",
                                           "POP454928", "GR530AA"]


def test_article_candidates_skip_gtin_and_links():
    """Роздрібний код — це `gtin`, а не артикул; посилання з QR — узагалі не код."""
    hits = [_hit("EAN13", "4895245119084"),
            _hit("QRCode", "https://www.reebok.com/qr?xPBBRKSPQ4BMR7H")]
    assert br.article_candidates(hits) == []


def test_article_candidates_need_a_digit():
    """«F2» коротке, «ADIDAS» без цифр — ні те, ні те не артикул."""
    hits = [_hit("Code128", "F2 ADIDAS ORIGINALS JQ8356")]
    assert br.article_candidates(hits) == ["JQ8356"]


def test_article_candidates_deduplicate():
    hits = [_hit("Code128", "JQ8356,JQ8356")]
    assert br.article_candidates(hits) == ["JQ8356"]


# ── Стійкість ───────────────────────────────────────────────────────────────

def test_unreadable_file_is_not_an_error(tmp_path):
    """Знімок без коду (і навіть не знімок) — порожній список, не виняток."""
    bad = tmp_path / "не-картинка.webp"
    bad.write_text("це не зображення", encoding="utf-8")
    assert br.read_photo(bad) == []


def test_missing_file_is_not_an_error(tmp_path):
    assert br.read_photo(tmp_path / "нема.webp") == []


def test_read_photos_deduplicates_across_frames(monkeypatch, tmp_path):
    """Один код часто видно на двох кадрах — у картку він має піти один раз."""
    monkeypatch.setattr(br, "read_photo",
                        lambda p: [_hit("EAN13", "4895245119084", p.name)])
    a, b = tmp_path / "a.webp", tmp_path / "b.webp"
    a.write_bytes(b""); b.write_bytes(b"")
    assert len(br.read_photos([a, b])) == 1


def test_upscale_is_skipped_for_large_frames():
    """Дороге збільшення не застосовується до великих кадрів.

    Прогін із збільшенням 1512-піксельних майстрів у 2× і 3× не вклався у дві
    хвилини на чотирьох товарах, не додавши жодного зчитування.
    """
    from PIL import Image
    big = Image.new("RGB", (1512, 2016))
    assert len(list(br._variants(big))) == 1
    small = Image.new("RGB", (800, 600))
    assert len(list(br._variants(small))) == 2

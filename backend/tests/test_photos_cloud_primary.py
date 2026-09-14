"""R2 — джерело правди для фото; локальна тека — лише робочий кеш.

Рішення власника 15.09.2026: «хмара має бути основною, локальна тека може
бути втрачена; локально не дублювати». Ці тести закріплюють три наслідки:
  1) список фото не залежить від наявності локального файла;
  2) читання з R2 нічого НЕ пише на диск;
  3) в індекс R2 не потрапляють похідні картинки (derived/social/studio).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.services import product_images as pi  # noqa: E402


class _R2:
    def __init__(self, keys, blobs=None):
        self.keys = keys; self.blobs = blobs or {}; self.downloads = 0
    def is_enabled(self): return True
    def list_keys_with_etag(self, prefix=""): return [(k, "etag") for k in self.keys]
    def download_bytes(self, key): self.downloads += 1; return self.blobs[key]
    def public_url(self, key): return f"https://r2.example/{key}"


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "get_images_dir", lambda: str(tmp_path))
    monkeypatch.setattr(pi, "_hidden_keys", lambda force=False: frozenset())
    monkeypatch.setattr(pi, "_list_drive_only", lambda t: [])
    monkeypatch.setattr(pi, "_publish_index_to_db", lambda rows: None)
    monkeypatch.setattr(pi, "_R2_INDEX", {"at": 0.0, "by_pnum": {}})
    r2 = _R2(keys=["Взуття/Ф4400_01.webp", "Взуття/Ф4400_02.webp",
                   "derived/Ф4400_prom.webp", "social/Ф4400_story.webp", "studio/Ф4400_x.webp"],
             blobs={"Взуття/Ф4400_02.webp": b"RIFF-webp-bytes"})
    monkeypatch.setattr(pi, "_r2", lambda: r2)
    pi.invalidate_image_list_cache()
    return tmp_path, r2


def test_list_does_not_depend_on_local_folder(cloud):
    """Тека порожня — а список повний, з тими самими URL, що й локальні."""
    tmp, _ = cloud
    imgs = pi.list_images("Ф4400")
    assert [i.filename for i in imgs] == ["Ф4400_01.webp", "Ф4400_02.webp"]
    assert all(i.url.startswith("/product-images/") for i in imgs)


def test_derived_and_social_never_enter_the_gallery(cloud):
    """`derived/`, `social/`, `studio/` названі тим самим номером — і без фільтра
    сухий прогін відновлення показав їх як «фото товару» (728 файлів)."""
    names = [i.filename for i in pi.list_images("Ф4400")]
    assert "Ф4400_prom.webp" not in names and "Ф4400_story.webp" not in names


def test_reading_from_r2_leaves_no_local_copy(cloud):
    """Локально не дублювати — байти йдуть у памʼять, на диск нічого."""
    tmp, r2 = cloud
    data = pi.image_bytes("Взуття/Ф4400_02.webp")
    assert data == b"RIFF-webp-bytes" and r2.downloads == 1
    assert not (tmp / "Взуття" / "Ф4400_02.webp").exists()


def test_local_wins_when_present(cloud):
    tmp, r2 = cloud
    (tmp / "Взуття").mkdir(); (tmp / "Взуття" / "Ф4400_02.webp").write_bytes(b"local")
    assert pi.image_bytes("Взуття/Ф4400_02.webp") == b"local" and r2.downloads == 0


def test_public_url_is_offered_for_redirect(cloud):
    assert pi.r2_public_url("Взуття/Ф4400_01.webp") == "https://r2.example/Взуття/Ф4400_01.webp"


def test_explicit_restore_is_the_only_thing_that_writes(cloud):
    """`ensure_local` — лише для restore_mirror_from_r2.py; штатна віддача ним
    не користується (перевіряється у test_serving_never_calls_ensure_local)."""
    tmp, _ = cloud
    p = pi.ensure_local("Взуття/Ф4400_02.webp")
    assert p and Path(p).read_bytes() == b"RIFF-webp-bytes"


def test_path_traversal_is_refused(cloud):
    assert pi.image_bytes("../../etc/passwd") is None
    assert pi.local_path_if_exists("../x.webp") is None


def test_serving_never_calls_ensure_local():
    """Роздача /product-images і мініатюри не мають тягнути файл на диск."""
    src = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    assert "ensure_local" not in src, "віддача знову пише копію на диск"
    assert "_ImgRedirect(url, status_code=302)" in src


def test_r2_failure_keeps_previous_index(cloud, monkeypatch):
    """Збій R2 не має «обнулити» фото — лишається попередній індекс."""
    pi._r2_index(force=True)
    class Boom(_R2):
        def list_keys_with_etag(self, prefix=""): raise RuntimeError("R2 down")
    monkeypatch.setattr(pi, "_r2", lambda: Boom([]))
    assert pi._r2_index(force=True).get("ф4400")

"""sync_photos_to_r2: сироти — лише фото товарів у категорійних папках.

Раніше віддалені ключі брались із ВСЬОГО бакета, тож `--delete-orphans`
видалив би рендери студії (studio/), варіанти для Prom (derived/) і логотипи
технологій (tech-logos/) — у «Товар» їх нема, отже вони «сироти».
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import sync_photos_to_r2 as sync  # noqa: E402

REMOTE = [
    "Взуття/Ф1000_01.webp",          # є локально
    "Взуття/Ф9999_01.webp",          # фото-сирота
    "studio/posts/abc/render.webp",
    "derived/prom-shafa-main/v1/deadbeef.webp",
    "tech-logos/manifest.json",
    "tech-logos/Мембрани/gore-tex.png",
]


def test_orphans_only_product_photos(tmp_path, monkeypatch):
    (tmp_path / "Взуття").mkdir()
    (tmp_path / "Взуття" / "Ф1000_01.webp").write_bytes(b"x")
    (tmp_path / "Сумки").mkdir()
    (tmp_path / "Сумки" / "С1_01.webp").write_bytes(b"x")
    # Локальний кеш варіантів Prom — не фото товару, у R2 не заливається.
    (tmp_path / ".derived" / "prom-shafa-main" / "v1").mkdir(parents=True)
    (tmp_path / ".derived" / "prom-shafa-main" / "v1" / "cafe.webp").write_bytes(b"x")

    uploaded, deleted = [], []
    monkeypatch.setattr(sync, "MIRROR_ROOT", tmp_path)
    monkeypatch.setattr(sync.r2_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(sync.r2_storage, "list_keys",
                        lambda prefix="": [k for k in REMOTE if k.startswith(prefix)])
    monkeypatch.setattr(sync.r2_storage, "upload_file", lambda path, key, **_k: uploaded.append(key))
    monkeypatch.setattr(sync.r2_storage, "delete", lambda key: deleted.append(key))
    monkeypatch.setattr(sys, "argv", ["sync_photos_to_r2.py", "--delete-orphans"])

    sync.main()

    assert deleted == ["Взуття/Ф9999_01.webp"]
    assert uploaded == ["Сумки/С1_01.webp"]

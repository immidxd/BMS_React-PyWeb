from backend.services import product_images as images


def test_list_images_reuses_recent_scan_and_explicit_invalidation(monkeypatch):
    calls = {"local": 0, "drive": 0}

    def local(_target):
        calls["local"] += 1
        return [images.ImageEntry("Ф42_01.webp", "/product-images/Ф42_01.webp?v=1", 0)]

    def drive(_target):
        calls["drive"] += 1
        return []

    monkeypatch.setattr(images, "_list_local_only", local)
    monkeypatch.setattr(images, "_list_drive_only", drive)
    images.invalidate_image_list_cache()

    assert images.list_images("#Ф42")[0].filename == "Ф42_01.webp"
    assert images.list_images("Ф42")[0].filename == "Ф42_01.webp"
    assert calls == {"local": 1, "drive": 1}

    images.invalidate_image_list_cache("Ф42")
    images.list_images("Ф42")
    assert calls == {"local": 2, "drive": 2}

    images.invalidate_image_list_cache()

from pathlib import Path

from PIL import Image, ImageDraw

from backend.services import prom_image_variants, prom_service


def _configure_storage(monkeypatch, tmp_path, upload):
    monkeypatch.setattr(prom_image_variants.product_images, "get_images_dir", lambda: str(tmp_path))
    monkeypatch.setattr(prom_image_variants.r2_storage, "R2_PUBLIC_BASE_URL", "https://img.example")
    monkeypatch.setattr(prom_image_variants.r2_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(
        prom_image_variants.r2_storage, "public_url",
        lambda key: f"https://img.example/{key}",
    )
    monkeypatch.setattr(prom_image_variants.r2_storage, "upload_file", upload)
    monkeypatch.setenv("PROM_IMAGE_VARIANT_CACHE_DIR", str(tmp_path / ".cache"))
    monkeypatch.setenv("PROM_SHAFA_SAFE_MAIN_IMAGE", "1")


def _wide_studio_photo(path: Path):
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 360, 955, 640), radius=60, fill=(30, 30, 30))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "WEBP", quality=95)


def test_derivative_keeps_subject_inside_shafa_safe_zone_and_is_cached(monkeypatch, tmp_path):
    source = tmp_path / "Взуття" / "Ф1_01.webp"
    _wide_studio_photo(source)
    uploads = []
    _configure_storage(monkeypatch, tmp_path, lambda path, key, **kwargs: uploads.append((path, key)))
    original = "https://img.example/%D0%92%D0%B7%D1%83%D1%82%D1%82%D1%8F/%D0%A41_01.webp"

    first = prom_image_variants.prepare_prom_main_image(original)
    second = prom_image_variants.prepare_prom_main_image(original)

    assert first.applied is True
    assert first.url == second.url
    assert second.reason == "cached"
    assert len(uploads) == 1
    output = Path(uploads[0][0])
    with Image.open(output) as image:
        analysis = prom_image_variants._analyze_content(image.convert("RGB"))
    assert analysis is not None
    _, (x0, _y0, x1, _y1) = analysis
    assert x0 >= prom_image_variants.SAFE_LEFT - 0.015
    assert x1 <= prom_image_variants.SAFE_RIGHT + 0.015


def test_upload_failure_falls_back_to_original(monkeypatch, tmp_path):
    source = tmp_path / "Взуття" / "Ф2_01.webp"
    _wide_studio_photo(source)

    def fail_upload(*_args, **_kwargs):
        raise RuntimeError("temporary R2 failure")

    _configure_storage(monkeypatch, tmp_path, fail_upload)
    original = "https://img.example/%D0%92%D0%B7%D1%83%D1%82%D1%82%D1%8F/%D0%A42_01.webp"
    result = prom_image_variants.prepare_prom_main_image(original)

    assert result.url == original
    assert result.applied is False
    assert result.reason == "processing-failed"


def test_legacy_prom_cdn_image_uses_full_size_and_safe_derivative(monkeypatch, tmp_path):
    source = tmp_path / "prom-source.webp"
    _wide_studio_photo(source)
    content = source.read_bytes()
    requested = []
    uploads = []
    _configure_storage(monkeypatch, tmp_path, lambda path, key, **kwargs: uploads.append((path, key)))

    class Response:
        def __init__(self):
            self.content = content

        @staticmethod
        def raise_for_status():
            return None

    def get(url, **_kwargs):
        requested.append(url)
        return Response()

    monkeypatch.setattr(prom_image_variants.requests, "get", get)
    result = prom_image_variants.prepare_prom_remote_main_image(
        "https://images.prom.ua/123_w200_h200_legacy-shoe.jpg"
    )

    assert requested == ["https://images.prom.ua/123_w0_h0_legacy-shoe.jpg"]
    assert result.applied is True
    assert len(uploads) == 1
    with Image.open(uploads[0][0]) as image:
        analysis = prom_image_variants._analyze_content(image.convert("RGB"))
    assert analysis is not None
    _, (x0, _y0, x1, _y1) = analysis
    assert x0 >= prom_image_variants.SAFE_LEFT - 0.015
    assert x1 <= prom_image_variants.SAFE_RIGHT + 0.015


def test_prom_adapter_is_opt_in_per_export_without_changing_other_channel_helper(monkeypatch):
    originals = ["https://img.example/main.webp", "https://img.example/second.webp"]
    monkeypatch.setattr(
        prom_service, "_prepare_prom_main_image",
        lambda _url: prom_image_variants.VariantResult("https://img.example/safe.webp", True, "cached"),
    )

    for image_kind in ("official", "real"):
        monkeypatch.setattr(
            prom_service,
            "_select_images",
            lambda *_args, kind=image_kind: (list(originals), kind),
        )
        assert prom_service._product_image_urls("Ф1") == originals
        adapted, kind, applied = prom_service._prom_export_images("Ф1", adapt_main_image=True)
        untouched, _, untouched_applied = prom_service._prom_export_images(
            "Ф1", adapt_main_image=False,
        )

        assert kind == image_kind
        assert adapted == ["https://img.example/safe.webp", originals[1]]
        assert applied is True
        assert untouched == originals
        assert untouched_applied is False

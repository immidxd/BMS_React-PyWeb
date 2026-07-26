import csv
import io
import json

from PIL import Image

from backend.services import prom_service
from backend.scripts import update_prom_main_images as refresh_script
from backend.scripts.update_prom_main_images import (
    _build_csv,
    _build_feed,
    _normalized_size,
    _size_from_prom_name,
)


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"id": 123}


def test_submit_feed_can_restrict_import_to_images(monkeypatch):
    sent = {}
    monkeypatch.setattr(prom_service, "_ensure_import_log", lambda _db: None)
    monkeypatch.setattr(prom_service, "_log_import", lambda *_args: None)
    monkeypatch.setattr(prom_service, "_prom_spawn_drainer", lambda *_args: None)

    def post(*_args, **kwargs):
        sent.update(kwargs)
        return _Response()

    monkeypatch.setattr(prom_service.requests, "post", post)
    result = prom_service._submit_feed(
        object(), "token", "<price/>", ["Ф1"], "photo-only",
        updated_fields=["images_urls"],
    )

    assert result["ok"] is True
    settings = json.loads(sent["data"]["data"])
    assert settings == {
        "mark_missing_product_as": "none",
        "force_update": True,
        "updated_fields": ["images_urls"],
    }


def test_legacy_prom_title_size_is_resolved_safely():
    assert _size_from_prom_name(
        "Женские босоножки Teva Flatform Universal Crochet 37 размер"
    ) == "37"
    assert _size_from_prom_name("Hoka Bondi 8, розмір 40,6 EU") == "40.6"
    assert _size_from_prom_name("Модель 2025 без указаного розміру") is None
    assert _normalized_size("40,60") == "40.6"


def test_photo_feed_preserves_both_prom_shoe_groups(monkeypatch):
    monkeypatch.setattr(
        prom_service,
        "_prom_export_images",
        lambda *_args, **_kwargs: (["https://img.example/safe.webp"], "official", True),
    )
    monkeypatch.setattr(
        prom_service,
        "_feed_item",
        lambda _row, sku, _available, _images, group_id: (
            f'<item id="{sku}"><categoryId>{group_id}</categoryId></item>'
        ),
    )
    records = [
        {
            "productnumber": "Ф1",
            "official_photos_from": None,
            "rows": [{"_sku": "Ф1", "_prom_group_id": 155060318}],
        },
        {
            "productnumber": "Ф2",
            "official_photos_from": None,
            "rows": [{"_sku": "Ф2", "_prom_group_id": 155371252}],
        },
    ]

    feed, skus = _build_feed(records)

    assert skus == ["Ф1", "Ф2"]
    assert '<category id="155060318">Обувь</category>' in feed
    assert '<category id="155371252">Обувь</category>' in feed
    assert "<categoryId>155060318</categoryId>" in feed
    assert "<categoryId>155371252</categoryId>" in feed


def test_csv_photo_update_targets_prom_internal_ids_and_keeps_image_order():
    records = [{
        "productnumber": "Ф1",
        "official_photos_from": None,
        "images_override": ["https://img.example/safe.webp", "https://img.example/detail.webp"],
        "rows": [{"_sku": "Ф1", "_prom_group_id": 155060318}],
    }]
    live = {
        "Ф1": [
            {"id": 101, "sku": "Ф1", "name": "Товар 1", "external_id": None},
            {"id": 102, "sku": "Ф1", "name": "Товар 1 дубль", "external_id": None},
        ],
    }

    content, skus, ids = _build_csv(records, live)
    decoded = content.decode("utf-8-sig")

    assert skus == ["Ф1", "Ф1"]
    assert ids == [101, 102]
    assert "Унікальний_ідентифікатор" in decoded
    assert "101" in decoded and "102" in decoded
    assert "https://img.example/safe.webp, https://img.example/detail.webp" in decoded
    parsed = list(csv.DictReader(io.StringIO(decoded)))
    assert [row["Ідентифікатор_товару"] for row in parsed] == ["", ""]


def test_submit_feed_accepts_csv_bytes_without_changing_settings(monkeypatch):
    sent = {}
    monkeypatch.setattr(prom_service, "_ensure_import_log", lambda _db: None)
    monkeypatch.setattr(prom_service, "_log_import", lambda *_args: None)
    monkeypatch.setattr(prom_service, "_prom_spawn_drainer", lambda *_args: None)

    def post(*_args, **kwargs):
        sent.update(kwargs)
        return _Response()

    monkeypatch.setattr(prom_service.requests, "post", post)
    result = prom_service._submit_feed(
        object(), "token", b"csv-bytes", ["Ф1"], "photo-csv",
        updated_fields=["images_urls"],
        file_name="photos.csv", content_type="text/csv",
    )

    assert result["ok"] is True
    assert sent["files"]["file"] == ("photos.csv", b"csv-bytes", "text/csv")
    assert json.loads(sent["data"]["data"])["updated_fields"] == ["images_urls"]


def test_verified_canary_state_is_persistent_and_versioned(monkeypatch, tmp_path):
    state_path = tmp_path / "photo-refresh-state.json"
    monkeypatch.setattr(refresh_script, "_refresh_state_path", lambda: state_path)
    state = {
        "format_version": refresh_script._REFRESH_FORMAT_VERSION,
        "plan_signature": "plan-a",
        "canary_prom_id": 101,
        "canary_verified": True,
        "canary": {"sku": "Ф1"},
    }

    refresh_script._write_refresh_state(state)

    restored = refresh_script._read_refresh_state()
    assert restored == state
    assert refresh_script._refresh_state_matches(restored, [101, 102]) is True
    assert refresh_script._refresh_state_matches(restored, [101, 102, 103]) is True
    assert refresh_script._refresh_state_matches(restored, [102]) is False
    assert refresh_script._refresh_state_matches({**restored, "format_version": "old"}, [101]) is False

    refresh_script._clear_refresh_state()
    assert refresh_script._read_refresh_state() == {}


def test_live_photo_verifier_uses_prom_master_instead_of_200px_thumbnail(monkeypatch):
    image = Image.new("RGB", (1000, 1000), "white")
    for x in range(220, 780):
        for y in range(400, 600):
            image.putpixel((x, y), (30, 30, 30))
    payload = io.BytesIO()
    image.save(payload, "JPEG", quality=95)
    requested = []

    class Response:
        content = payload.getvalue()

        @staticmethod
        def raise_for_status():
            return None

    def get(url, **_kwargs):
        requested.append(url)
        return Response()

    monkeypatch.setattr(refresh_script.requests, "get", get)
    prom_id, safe, reason = refresh_script._main_image_is_crop_safe({
        "id": 101,
        "main_image": "https://images.prom.ua/123_w200_h200_product.jpg",
    })

    assert requested == ["https://images.prom.ua/123_w0_h0_product.jpg"]
    assert (prom_id, safe, reason) == (101, True, "safe")

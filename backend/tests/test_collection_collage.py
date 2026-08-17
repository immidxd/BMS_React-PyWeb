from __future__ import annotations

import asyncio
import io

from PIL import Image

from backend.services import collection_collage as cc
from backend.services import facebook_publisher as fb
from backend.services import instagram_publisher as ig
from backend.services import viber_publisher as vp


def _jpeg(size=(900, 700), color=(240, 80, 40)) -> bytes:
    image = Image.new("RGB", size, color)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


def _items(count: int) -> list:
    return [{"product_id": index + 1} for index in range(count)]


def _fake_photos(monkeypatch):
    """Товар віддає власне фото, розміри й ціну — без БД і файлової системи.

    Підміняємо саме `_tg`, а не готовий підпис: так рендер підписів (шрифти,
    добір кегля, замір для ростовки) лишається під тестом.
    """
    def photo(_db, product_id, image_idx):
        color = ((product_id * 37) % 255, (product_id * 91) % 255, 120)
        return _jpeg(color=color), {
            "productnumber": f"#Ф{4000 + product_id}",
            "price": 1200 + product_id,
        }
    monkeypatch.setattr(cc, "_photo_for_item", photo)

    class Tg:
        @staticmethod
        def _is_bag(_bms):
            return False

        @staticmethod
        def _available_sizes(_db, _number):
            return [{"size": "38", "measurementscm": "24.5"},
                    {"size": "40", "measurementscm": "26"}]

        @staticmethod
        def _fmt_price(value):
            return f"{int(value)}" if value else None

    monkeypatch.setattr(cc, "_tg", lambda: Tg)


# ─── Специфікація ────────────────────────────────────────────────────────────

def test_spec_drops_duplicate_products_and_clamps_frames():
    spec = cc.normalize_spec({
        "platform": "viber",
        "items": [
            {"product_id": 7, "zoom": 9.0, "x": -4, "y": 0.5},
            {"product_id": 7, "zoom": 1.0},
            {"product_id": 8, "image_idx": "2"},
        ],
    })

    assert [item["product_id"] for item in spec["items"]] == [7, 8]
    assert spec["items"][0]["zoom"] == cc.FRAME_ZOOM_MAX
    assert spec["items"][0]["x"] == -1.0
    assert spec["items"][1]["image_idx"] == 2


def test_spec_picks_smallest_grid_that_fits_and_never_exceeds_it():
    assert cc.normalize_spec({"platform": "viber", "items": _items(9)})["layout"] == "grid9"
    assert cc.normalize_spec({"platform": "viber", "items": _items(10)})["layout"] == "grid16"

    # Явно обрана сітка 3×3 не може мовчки взяти 12 товарів.
    clamped = cc.normalize_spec({"platform": "viber", "layout": "grid9", "items": _items(12)})
    assert len(clamped["items"]) == 9


def test_spec_never_takes_more_than_the_hard_ceiling():
    spec = cc.normalize_spec({"platform": "facebook", "layout": "grid16", "items": _items(30)})
    assert len(spec["items"]) == cc.MAX_ITEMS


def test_unknown_platform_is_rejected_before_any_render():
    # Instagram теж має бути відхилений: підбірка живе лише у Viber і Facebook,
    # і мовчазний фолбек на чужий формат був би гіршим за помилку.
    for value in ("telegram", "instagram", "olx"):
        try:
            cc.platform_config(value)
        except ValueError:
            continue
        raise AssertionError(f"платформа {value!r} не мала пройти")


def test_missing_platform_falls_back_to_viber():
    assert cc.platform_config(None)["key"] == "viber"
    assert cc.platform_config("")["key"] == "viber"


# ─── Геометрія ───────────────────────────────────────────────────────────────

def test_cells_never_overlap_and_stay_inside_canvas():
    for count in range(2, cc.MAX_ITEMS + 1):
        layout = cc.layout_for_count(count)
        geometry = cc.grid_geometry(count, layout, 1080, 8)
        cells = geometry["cells"]
        assert len(cells) == count
        for x, y, width, height in cells:
            assert x >= 0 and y >= 0
            assert x + width <= geometry["width"]
            assert y + height <= geometry["height"]
        for first in range(count):
            for second in range(first + 1, count):
                ax, ay, aw, ah = cells[first]
                bx, by, bw, bh = cells[second]
                overlap = ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
                assert not overlap, f"комірки {first} і {second} перекриваються при {count} товарах"


def test_canvas_is_cropped_to_the_grid_instead_of_leaving_an_empty_band():
    # 6 товарів — це 3×2: квадратне полотно лишило б порожню смугу згори й знизу.
    six = cc.grid_geometry(6, "grid9", 1080, 8)
    assert six["cols"] == 3 and six["rows"] == 2
    assert six["height"] < six["width"]

    nine = cc.grid_geometry(9, "grid9", 1080, 8)
    assert nine["cols"] == 3 and nine["rows"] == 3
    assert abs(nine["height"] - nine["width"]) <= 4


def test_four_products_use_a_square_two_by_two_not_a_lonely_second_row():
    geometry = cc.grid_geometry(4, "grid9", 1080, 8)
    assert (geometry["cols"], geometry["rows"]) == (2, 2)


# ─── Підпис ──────────────────────────────────────────────────────────────────

def _caption_items(count: int) -> list:
    return [{
        "productnumber": f"Ф{4000 + index}",
        "brand": "Lauren Ralph Lauren",
        "model": f"Keaton Sport Loafer {index}",
        "sizes": ["36", "37", "38", "39", "40"],
        "price": "1 700",
    } for index in range(count)]


def test_caption_stays_within_the_viber_limit_even_for_a_full_grid():
    caption = cc.build_caption(_caption_items(16), "viber")
    assert len(caption) <= cc.PLATFORMS["viber"]["caption_limit"]
    # Ліміт тримаємо відкиданням деталей, а не обрізанням посеред слова.
    assert not caption.endswith("…")
    for item in _caption_items(16):
        assert f"#{item['productnumber']}" in caption


def test_caption_keeps_full_detail_when_it_fits():
    caption = cc.build_caption(_caption_items(3), "viber")
    assert "Lauren Ralph Lauren" in caption
    assert "36, 37" in caption
    assert "1 700 грн" in caption


def test_facebook_caption_has_no_viber_markdown():
    caption = cc.build_caption(_caption_items(4), "facebook")
    assert "*" not in caption


# ─── Рендер ──────────────────────────────────────────────────────────────────

def test_render_produces_a_jpeg_within_the_platform_limit(monkeypatch):
    _fake_photos(monkeypatch)
    rendered = cc.render(None, {"platform": "viber", "items": _items(9)})

    assert rendered["main"].startswith(b"\xff\xd8")
    assert len(rendered["main"]) <= cc.PLATFORMS["viber"]["max_bytes"]
    assert len(rendered["thumbnail"]) <= vp.THUMB_MAX_BYTES
    with Image.open(io.BytesIO(rendered["main"])) as image:
        assert image.size == (1080, rendered["spec"]["height"])
    assert rendered["product_numbers"] == [f"Ф{4000 + index}" for index in range(1, 10)]


def test_facebook_render_uses_a_bigger_canvas_and_skips_the_viber_thumbnail(monkeypatch):
    _fake_photos(monkeypatch)
    rendered = cc.render(None, {"platform": "facebook", "items": _items(16)})

    assert rendered["thumbnail"] == b""
    with Image.open(io.BytesIO(rendered["main"])) as image:
        assert image.width == cc.PLATFORMS["facebook"]["size"]


# ─── Підписи: артикул · замір · ціна ─────────────────────────────────────────

def test_measurement_shows_a_range_for_rostovka_and_one_value_for_a_single_size(monkeypatch):
    class Tg:
        @staticmethod
        def _is_bag(_bms):
            return False

        @staticmethod
        def _available_sizes(_db, number):
            if number == "#Ф4336":
                return [
                    {"size": "36", "measurementscm": "23"},
                    {"size": "40", "measurementscm": "26.0"},
                    {"size": "38", "measurementscm": "24,5"},
                ]
            return [{"size": "39", "measurementscm": "25"}]

    monkeypatch.setattr(cc, "_tg", lambda: Tg)
    assert cc.measurement_label(None, {"productnumber": "#Ф4336"}) == "23–26 см"
    assert cc.measurement_label(None, {"productnumber": "#Ф1681"}) == "25 см"


def test_bags_have_no_foot_measurement(monkeypatch):
    class Tg:
        @staticmethod
        def _is_bag(_bms):
            return True

    monkeypatch.setattr(cc, "_tg", lambda: Tg)
    assert cc.measurement_label(None, {"productnumber": "#Ф4329"}) is None


def test_measurement_falls_back_to_the_product_row_when_no_sizes_are_available(monkeypatch):
    class Tg:
        @staticmethod
        def _is_bag(_bms):
            return False

        @staticmethod
        def _available_sizes(_db, _number):
            return []

    monkeypatch.setattr(cc, "_tg", lambda: Tg)
    label = cc.measurement_label(None, {"productnumber": "#Ф1", "measurementscm": "27.5"})
    assert label == "27,5 см"
    assert cc.measurement_label(None, {"productnumber": "#Ф2", "measurementscm": 0}) is None


def test_labels_shrink_the_photo_but_stay_on_by_default(monkeypatch):
    _fake_photos(monkeypatch)
    spec = cc.normalize_spec({"platform": "viber", "items": _items(4)})
    assert spec["labels"] is True

    with_labels = cc.render(None, {"platform": "viber", "items": _items(4)})
    without = cc.render(None, {"platform": "viber", "items": _items(4), "labels": False})
    # Смуга з підписом забирає висоту в самого фото, а не в полотна: інакше
    # сітка «поповзла» б і перестала збігатися з прев'ю.
    assert with_labels["spec"]["height"] == without["spec"]["height"]
    assert with_labels["main"] != without["main"]


def test_render_refuses_a_single_product(monkeypatch):
    _fake_photos(monkeypatch)
    try:
        cc.render(None, {"platform": "viber", "items": _items(1)})
    except ValueError as exc:
        assert "щонайменше" in str(exc)
    else:
        raise AssertionError("одинарна «підбірка» не мала пройти")


# ─── Публікація ──────────────────────────────────────────────────────────────

def test_collection_never_touches_product_publication_tables(monkeypatch):
    """Головна обіцянка підбірки: статус опублікованості товарів не змінюється."""
    _fake_photos(monkeypatch)
    # `services.X` і `backend.services.X` — два різні обʼєкти модуля; без цього
    # рядка у повному прогоні патчі лягли б на іншу копію, ніж бачить publisher.
    monkeypatch.setattr(vp, "_collection", lambda: cc)
    monkeypatch.setattr(cc, "cached_post", lambda _db, _key: None)
    monkeypatch.setattr(vp, "connection_status", lambda: {"configured": True, "missing": []})
    monkeypatch.setattr(
        cc, "upload_derivatives",
        lambda _rendered, _caption: {
            "image_key": "k", "image_url": "https://r2/collection.jpeg",
            "thumbnail_key": "t", "thumbnail_url": "https://r2/collection.thumb.jpeg",
        },
    )
    dispatched = []

    async def dispatch(payload):
        dispatched.append(payload)
        return {"ok": True, "job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(vp, "_dispatch", dispatch)
    recorded = []
    monkeypatch.setattr(cc, "record_post", lambda *args, **kwargs: recorded.append(kwargs))

    # Будь-яке звернення до товарних таблиць = провал тесту.
    for guard in ("_existing_count", "_pending_count", "_record"):
        monkeypatch.setattr(vp, guard, lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"підбірка не має чіпати {guard}")
        ))

    result = asyncio.run(vp.create_collection_post(None, {
        "items": _items(6), "caption": "Свіжа підбірка", "platform": "viber",
        "idempotency_key": "collection-1",
    }))

    assert result["ok"] is True
    assert result["job_id"] == "job-1"
    assert dispatched[0]["type"] == "picture"
    assert dispatched[0]["media_url"].endswith(".jpeg")
    # Worker вимагає product_id — передаємо перший у сітці, суто для його журналу.
    assert dispatched[0]["product_id"] == 1
    assert recorded and recorded[0]["platform"] == "viber"


def test_empty_caption_is_refused_before_render_because_viber_rejects_it(monkeypatch):
    monkeypatch.setattr(vp, "_collection", lambda: cc)
    monkeypatch.setattr(cc, "cached_post", lambda _db, _key: None)
    monkeypatch.setattr(cc, "render", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("рендер не мав запускатися для порожнього підпису")
    ))
    result = asyncio.run(vp.create_collection_post(None, {"items": _items(4), "caption": "  "}))
    assert result["ok"] is False


# ─── Дзеркальна публікація Instagram ↔ Facebook ──────────────────────────────

def test_instagram_draft_mirrors_into_facebook_with_the_local_call_to_action():
    mirrored = fb.payload_from_instagram({
        "product_id": 12,
        "caption": f"Nike Air Max\n\n📲 Пиши #4336 {ig.DIRECT_CTA}",
        "publish_type": "reel",
        "image_idx": [0, 2],
        "feed_preset": "square",
        "frames": [{"image_idx": 0, "zoom": 1.1, "x": 0, "y": 0}],
        "publish_at": "2026-09-01T09:00:00+00:00",
        "collaborators": ["someone"],
        "share_to_feed": True,
        "idempotency_key": "ig-key",
    }, page_ids=["page-1", "page-2"])

    assert mirrored["caption"].endswith(fb.FACEBOOK_CTA)
    assert ig.DIRECT_CTA not in mirrored["caption"]
    # Композиція переїжджає без змін — інакше в мережах були б різні кадри.
    assert mirrored["publish_type"] == "reel"
    assert mirrored["image_idx"] == [0, 2]
    assert mirrored["frames"] == [{"image_idx": 0, "zoom": 1.1, "x": 0, "y": 0}]
    assert mirrored["publish_at"] == "2026-09-01T09:00:00+00:00"
    # Суто інстаграмівські поля у Facebook не їдуть.
    assert "collaborators" not in mirrored and "share_to_feed" not in mirrored
    assert mirrored["page_ids"] == ["page-1", "page-2"]
    assert mirrored["idempotency_key"] == "ig-key:mirror-fb"


def test_facebook_draft_mirrors_back_into_instagram():
    mirrored = ig.payload_from_facebook({
        "product_id": 12,
        "caption": f"Nike Air Max\n\n📲 Пиши #4336 {fb.FACEBOOK_CTA}",
        "publish_type": "story",
        "story_text": "Ціна 1700 грн",
        "page_ids": ["page-1"],
        "idempotency_key": "fb-key",
    })

    assert mirrored["caption"].endswith(ig.DIRECT_CTA)
    assert mirrored["story_text"] == "Ціна 1700 грн"
    assert "page_ids" not in mirrored
    assert mirrored["idempotency_key"] == "fb-key:mirror-ig"


def test_hand_written_caption_survives_the_mirror_untouched():
    caption = "Власний текст без службового заклику"
    assert fb.payload_from_instagram({"caption": caption})["caption"] == caption
    assert ig.payload_from_facebook({"caption": caption})["caption"] == caption

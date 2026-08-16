"""Facebook Page publisher.

Рендерер спільний з Instagram і покритий test_instagram_publisher. Тут
перевіряємо саме те, що у Facebook ІНШЕ, і те, що спільне не мусить розʼїхатися.
"""

from __future__ import annotations

import asyncio

from backend.services import facebook_publisher as fb
from backend.services import instagram_publisher as ip


class _TgCaption:
    @staticmethod
    def _is_bag(_bms): return False

    @staticmethod
    def _normalize_dimensions(value): return value

    @staticmethod
    def default_tagline(_bms): return "чоловічі кросівки"

    @staticmethod
    def default_emoji(_bms): return "👟"

    @staticmethod
    def default_features(_bms): return ["Проміжна підошва eva"]

    @staticmethod
    def normalize_technology_abbreviations(value): return value.replace("eva", "EVA")

    @staticmethod
    def _condition_line(_bms): return "Стан нових (Сток)"

    @staticmethod
    def _condition_icon(_bms): return "🆕"

    @staticmethod
    def _fmt_price(value): return str(value) if value else None


BMS = {"brandname": "HOKA", "model": "Kawana Mid", "price": 3500, "productnumber": "Ф3914"}
SIZES = [{"size": "44.6", "measurementscm": "28.5"}]


def test_caption_differs_from_instagram_only_in_the_call_to_action(monkeypatch):
    """Одна крамниця — один голос. Розходитись дозволено РІВНО в одному рядку:
    «в приватні» — інстаграмівське слово (Direct), у Facebook пишуть у Messenger.
    Будь-яка інша різниця = непомічений дрейф однієї з копій.

    ``_ig`` прибиваємо явно: подвійний імпорт (`services.` і `backend.services.`)
    дає ДВА різні обʼєкти модуля, і без цього патч ``ip._tg`` не дійшов би до тієї
    копії, якою користується facebook_publisher.
    """
    monkeypatch.setattr(fb, "_ig", lambda: ip)
    monkeypatch.setattr(ip, "_tg", lambda: _TgCaption)

    facebook = fb.build_caption(BMS, SIZES).splitlines()
    instagram = ip.build_caption(BMS, SIZES).splitlines()

    assert len(facebook) == len(instagram)
    differing = [i for i, (a, b) in enumerate(zip(facebook, instagram)) if a != b]
    assert len(differing) == 1, f"розійшлось більше ніж один рядок: {differing}"
    assert facebook[differing[0]] == "📲 Пиши #Ф3914 нам у повідомлення"
    assert instagram[differing[0]] == "📲 Пиши #Ф3914 нам в приватні"
    # Артикул лишається з «#» — користувач попросив не чіпати.
    assert "#Ф3914" in facebook[differing[0]]
    # Текст Story нанесений на кадр і CTA не містить — має лишатись спільним.
    assert fb.build_story_text(BMS, SIZES) == ip.build_story_text(BMS, SIZES)


def test_reel_slide_fits_the_whole_photo_at_the_default_zoom():
    """Головний баг живого Reel: кадр 9:16 різав фото по боках.

    `_render_frame` масштабує за max() («cover»): у 9:16 квадратне фото стає
    ширшим за кадр, і краї зникають. Слайд Reel на типовому значенні повзунка
    мусить показувати фото ЦІЛКОМ.
    """
    from PIL import Image
    import io as _io

    # Мітки біля самих країв фото — саме вони й зникали.
    source = Image.new("RGB", (1000, 1000), (255, 255, 255))
    for x in list(range(0, 20)) + list(range(980, 1000)):
        for y in range(400, 600):
            source.putpixel((x, y), (0, 0, 0))
    buffer = _io.BytesIO()
    source.save(buffer, "JPEG", quality=95)
    raw = buffer.getvalue()

    def has_black(image):
        pixels = image.load()
        return {x for x in range(image.width)
                for y in range(0, image.height, 4)
                if sum(pixels[x, y]) < 150}

    default = {"image_idx": 0, "zoom": 0.6, "x": 0.0, "y": 0.0}
    reel = ip._render_reel_frame(raw, default, (255, 255, 255))
    cover = ip._render_frame(raw, 1080, 1920, default, (255, 255, 255))

    assert reel.size == (1080, 1920)

    reel_marks = has_black(reel)
    # Обидві крайні мітки лишились → фото поміщається повністю.
    assert reel_marks, "фото зникло з кадру"
    assert min(reel_marks) > 0 and max(reel_marks) < 1079, "товар торкається країв"
    left = [x for x in reel_marks if x < 540]
    right = [x for x in reel_marks if x >= 540]
    assert left and right, "одну з країв фото втрачено"

    # Старий «cover» на тому самому типовому zoom обидві мітки зрізав.
    assert not has_black(cover), "cover мав відрізати краї фото — тест втратив сенс"


def test_reel_zoom_above_default_crops_on_purpose():
    """Збільшення понад «вміститись» ріже — і має різати.

    Це не той баг, що вище: повзунок навмисно дозволяє наблизити кадр, рівно
    як у Story. Тест фіксує, що поведінка усвідомлена, а не випадкова."""
    from PIL import Image
    import io as _io

    source = Image.new("RGB", (1000, 1000), (255, 255, 255))
    for x in range(0, 1000):
        for y in range(400, 600):
            source.putpixel((x, y), (0, 0, 0))
    buffer = _io.BytesIO()
    source.save(buffer, "JPEG", quality=95)
    raw = buffer.getvalue()

    zoomed = ip._render_reel_frame(raw, {"image_idx": 0, "zoom": 1.2, "x": 0.0, "y": 0.0},
                                   (255, 255, 255))
    pixels = zoomed.load()
    touches_edge = any(sum(pixels[0, y]) < 150 for y in range(0, 1920, 4))
    assert touches_edge


def test_reel_preview_renders_the_reel_slide_not_the_story_layout(monkeypatch):
    """Прев'ю мусить показувати ТОЙ кадр, що піде у відео.

    Раніше прев'ю Reel підмінялося на Story, щоб не ганяти FFmpeg. Відколи
    слайд Reel рендериться інакше, така підміна показувала б не те."""
    captured = {}

    def fake_render(_db, _pid, payload):
        captured.update(payload)
        return {"assets": [{"bytes": b"jpeg"}]}

    monkeypatch.setattr(ip, "render_media_for_product", fake_render)
    ip.render_preview_jpeg(None, 42, {"publish_type": "reel", "image_idx": [0, 1]})

    assert captured["publish_type"] == "reel"
    assert captured["_frames_only"] is True


def test_page_message_limit_is_facebook_own_not_instagram_2200():
    long_text = "a" * 3000
    # Той самий текст: у стрічку Сторінки проходить, у Reel — ні.
    assert fb.validate_caption(long_text, "feed") is None
    assert ip.validate_caption(long_text) is not None
    assert "2200" in fb.validate_caption(long_text, "reel")
    assert "63206" in fb.validate_caption("a" * (fb.MESSAGE_LIMIT + 1), "feed")


def test_story_needs_no_message_because_text_is_burned_into_the_image():
    assert fb.validate_caption("", "story") is None
    assert fb.validate_caption("", "feed") == "Текст допису Facebook порожній"


def test_publish_types_cover_posts_stories_and_reels():
    assert set(fb.PUBLISH_TYPES) == {"feed", "story", "reel"}
    assert fb.PUBLISH_TYPES["story"]["max_media"] == 1
    assert fb.PUBLISH_TYPES["feed"]["max_media"] == 10


def test_media_spec_clamps_each_type_to_its_own_media_limit():
    story = fb.normalize_media_spec({"publish_type": "story", "image_idx": [0, 1, 2]}, 5)
    assert story["image_idx"] == [0]
    assert len(story["frames"]) == 1

    album = fb.normalize_media_spec({"publish_type": "feed", "image_idx": list(range(12))}, 12)
    assert len(album["image_idx"]) == 10
    assert len(album["frames"]) == 10
    assert {frame["image_idx"] for frame in album["frames"]} == set(album["image_idx"])


def test_unknown_publish_type_falls_back_to_feed():
    assert fb.normalize_media_spec({"publish_type": "carousel"}, 3)["publish_type"] == "feed"


def test_dry_run_names_facebook_shapes_and_calls_nothing_external(monkeypatch):
    monkeypatch.setattr(fb, "preview_post", lambda _db, _pid: {
        "ok": True, "productnumber": "Ф42", "caption": "Підпис",
    })
    monkeypatch.setattr(fb, "render_media_for_product", lambda _db, _pid, _payload: {
        "spec": {"publish_type": "feed", "image_idx": [0, 1], "feed_preset": "square"},
        "assets": [{"type": "IMAGE", "bytes": b"one"}, {"type": "IMAGE", "bytes": b"two"}],
        "output": {"width": 1080, "height": 1080},
    })

    result = fb.dry_run(None, 42, {"publish_type": "feed"})

    assert result["ok"] is True
    assert result["external_calls"] == 0
    # У Facebook це «альбом», а не «карусель» — назва має збігатися з тим, що
    # людина реально побачить у Сторінці.
    assert result["would_publish_as"] == "album"
    assert result["media_count"] == 2


def test_dry_run_single_photo_is_a_photo_post(monkeypatch):
    monkeypatch.setattr(fb, "preview_post", lambda _db, _pid: {
        "ok": True, "productnumber": "Ф42", "caption": "Підпис",
    })
    monkeypatch.setattr(fb, "render_media_for_product", lambda _db, _pid, _payload: {
        "spec": {"publish_type": "feed", "image_idx": [0], "feed_preset": "square"},
        "assets": [{"type": "IMAGE", "bytes": b"one"}],
        "output": {"width": 1080, "height": 1080},
    })

    assert fb.dry_run(None, 42, {})["would_publish_as"] == "photo"


def test_dry_run_rejects_a_reel_description_over_the_limit(monkeypatch):
    monkeypatch.setattr(fb, "preview_post", lambda _db, _pid: {
        "ok": True, "productnumber": "Ф42", "caption": "Підпис",
    })

    result = fb.dry_run(None, 42, {"publish_type": "reel", "caption": "a" * 2201})

    assert result["ok"] is False
    assert "2200" in result["error"]


def test_schedule_errors_speak_about_facebook_not_instagram(monkeypatch):
    monkeypatch.setattr(fb, "preview_post", lambda _db, _pid: {
        "ok": True, "productnumber": "Ф42", "caption": "Підпис",
    })

    result = fb.dry_run(None, 42, {"publish_at": "2020-01-01T10:00:00"})

    assert result["ok"] is False
    assert "Facebook" in result["error"]
    assert "Instagram" not in result["error"]


def test_r2_prefix_is_separate_from_instagram(monkeypatch):
    """Спільний ключ означав би, що перекадрування для одного майданчика
    мовчки підміняє медіа другого — Meta тягне файл за URL у момент публікації."""
    uploaded = {}

    class _R2:
        R2_PUBLIC_BASE_URL = "https://cdn.example.com"

        @staticmethod
        def is_enabled(): return True

        @staticmethod
        def upload_bytes(_data, key, content_type=None): uploaded[key] = content_type

        @staticmethod
        def public_url(key): return f"https://cdn.example.com/{key}"

    monkeypatch.setattr(fb, "_r2", lambda: _R2)

    result = fb._upload_derivatives({
        "caption": "Підпис",
        "pnum": "#Ф42",
        "rendered": {"assets": [
            {"bytes": b"one", "extension": "jpeg", "content_type": "image/jpeg", "type": "IMAGE"},
        ], "cover": None},
    })

    assert all(key.startswith("social/facebook/Ф42/") for key in uploaded)
    assert not any(key.startswith("social/instagram/") for key in uploaded)
    assert result["media"][0]["url"].startswith("https://cdn.example.com/social/facebook/")
    # Facebook Pages API не приймає alt_text цим шляхом — не вигадуємо поле,
    # якого воркер не надішле.
    assert "alt_text" not in result["media"][0]


def test_connection_status_never_returns_secrets(monkeypatch):
    monkeypatch.setenv("FACEBOOK_DISPATCHER_URL", "https://worker.example.com")
    monkeypatch.setenv("FACEBOOK_DISPATCHER_KEY", "k" * 40)

    status = fb.connection_status()
    flat = repr(status)

    assert status["dispatcher_configured"] is True
    assert "k" * 40 not in flat
    assert "worker.example.com" not in flat
    assert status["oauth_method"] == "facebook_login"
    assert status["page_required"] is True


def test_batch_refuses_more_than_the_safe_number_of_products():
    result = asyncio.run(fb.create_posts_batch(
        None, [{"product_id": index} for index in range(fb.BATCH_MAX_PRODUCTS + 1)], "batch-1",
    ))

    assert result["ok"] is False
    assert str(fb.BATCH_MAX_PRODUCTS) in result["error"]


def test_batch_without_id_is_refused():
    assert asyncio.run(fb.create_posts_batch(None, [{"product_id": 1}], ""))["ok"] is False
    assert asyncio.run(fb.create_posts_batch(None, [], "batch-1"))["ok"] is False


def test_dry_run_batch_reports_each_card_separately(monkeypatch):
    monkeypatch.setattr(fb, "preview_posts_batch", lambda _db, ids: {
        "ok": True, "selected_count": len(ids), "unique_count": len(ids), "merged_count": 0,
        "missing_ids": [], "items": [
            {"product_id": pid, "productnumber": f"Ф{pid}", "source_product_ids": [pid]}
            for pid in ids
        ],
    })
    monkeypatch.setattr(fb, "dry_run", lambda _db, pid, _payload: (
        {"ok": True, "media_count": 1} if pid == 1 else {"ok": False, "error": "немає фото"}
    ))

    result = fb.dry_run_batch(None, [{"product_id": 1}, {"product_id": 2}])

    assert result["ok"] is False
    assert result["counts"] == {"success": 1, "error": 1}
    assert result["external_calls"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Дві Сторінки. Найризикованіше місце: одна чернетка мусить розійтись у кілька
# Сторінок, і ЖОДНА з них не має отримати чужий ключ ідемпотентності.
# ─────────────────────────────────────────────────────────────────────────────

PAGES = [{"id": "111", "name": "b.randstoreua"}, {"id": "222", "name": "Lyudmila"}]


def test_empty_selection_means_every_connected_page():
    assert fb._target_pages({}, {"pages": PAGES}) == PAGES
    assert fb._target_pages({"page_ids": []}, {"pages": PAGES}) == PAGES


def test_explicit_selection_is_honoured_and_ordered():
    assert fb._target_pages({"page_ids": ["222"]}, {"pages": PAGES}) == [PAGES[1]]
    assert fb._target_pages({"page_ids": ["222", "111"]}, {"pages": PAGES}) == [PAGES[1], PAGES[0]]


def test_unknown_page_is_loud_not_silently_dropped():
    """Мовчки проігнорувати чужий id = «нічого не опубліковано», і людина
    дізнається про це лише з порожньої стрічки."""
    try:
        fb._target_pages({"page_ids": ["111", "999"]}, {"pages": PAGES})
    except ValueError as exc:
        assert "999" in str(exc)
    else:
        raise AssertionError("невідома Сторінка мала б дати помилку")


class _FakeSession:
    """Мінімальна сесія: create_post відкочує транзакцію на невдалій Сторінці."""

    def __init__(self): self.rollbacks = 0

    def rollback(self): self.rollbacks += 1


def _stub_publish(monkeypatch, *, dispatch_results):
    """Готує create_post так, щоб він не торкався ані рендера, ані мережі."""
    sent: list[dict] = []
    recorded: list[dict] = []

    async def fake_status():
        return {"live_publish_available": True, "oauth_connected": True, "pages": PAGES}

    async def fake_dispatch(_method, _path, *, payload=None):
        sent.append(payload)
        result = dispatch_results[len(sent) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(fb, "dispatcher_status", fake_status)
    monkeypatch.setattr(fb, "_dispatcher_request", fake_dispatch)
    monkeypatch.setattr(fb, "_cached_result", lambda _db, _key: None)
    monkeypatch.setattr(fb, "_prepare", lambda _db, _pid, _payload: {
        "pnum": "#Ф42", "caption": "Підпис", "scheduled_at": None,
        "rendered": {"spec": {"publish_type": "feed", "image_idx": [0]}, "assets": [], "cover": None},
    })
    monkeypatch.setattr(fb, "_upload_derivatives", lambda _ready: {
        "media": [{"type": "IMAGE", "url": "https://cdn.example.com/a.jpeg"}],
        "media_keys": ["k"], "cover_url": None, "digest": "d",
    })
    monkeypatch.setattr(fb, "_record", lambda db, **kwargs: recorded.append(kwargs))
    return sent, recorded


def test_one_draft_becomes_one_job_per_page(monkeypatch):
    sent, recorded = _stub_publish(monkeypatch, dispatch_results=[
        {"ok": True, "job_id": "job-1", "status": "queued"},
        {"ok": True, "job_id": "job-2", "status": "queued"},
    ])

    result = asyncio.run(fb.create_post(_FakeSession(), 42, {"idempotency_key": "draft-7"}))

    assert result["ok"] is True
    assert [job["account_id"] for job in sent] == ["111", "222"]
    # Ключі мусять РІЗНИТИСЬ, інакше друга Сторінка отримає кешовану відповідь
    # першої й мовчки лишиться без публікації.
    assert [job["idempotency_key"] for job in sent] == ["draft-7:111", "draft-7:222"]
    assert len(set(job["idempotency_key"] for job in sent)) == 2
    # Медіа готується ОДИН раз на обидві Сторінки.
    assert sent[0]["media"] == sent[1]["media"]
    assert [row["dispatch"]["account_id"] for row in recorded] == ["111", "222"]
    assert [page["name"] for page in result["pages"]] == ["b.randstoreua", "Lyudmila"]


def test_selected_page_only_publishes_there(monkeypatch):
    sent, _ = _stub_publish(monkeypatch, dispatch_results=[
        {"ok": True, "job_id": "job-1", "status": "queued"},
    ])

    result = asyncio.run(fb.create_post(_FakeSession(), 42, {"idempotency_key": "d", "page_ids": ["222"]}))

    assert result["ok"] is True
    assert len(sent) == 1
    assert sent[0]["account_id"] == "222"


def test_one_failing_page_does_not_hide_the_other(monkeypatch):
    """Часткова невдача — штатний результат, а не «все впало»: другу Сторінку
    вже опубліковано, і мовчати про це не можна."""
    sent, _ = _stub_publish(monkeypatch, dispatch_results=[
        RuntimeError("Meta відхилила"),
        {"ok": True, "job_id": "job-2", "status": "queued"},
    ])

    session = _FakeSession()
    result = asyncio.run(fb.create_post(session, 42, {"idempotency_key": "d"}))

    assert result["ok"] is False
    assert session.rollbacks == 1
    assert len(sent) == 2
    assert [page["ok"] for page in result["pages"]] == [False, True]
    assert "b.randstoreua" in result["error"]
    assert "Meta відхилила" in result["error"]


def test_publishing_without_any_connected_page_is_refused(monkeypatch):
    async def fake_status():
        return {"live_publish_available": True, "oauth_connected": True, "pages": []}

    monkeypatch.setattr(fb, "dispatcher_status", fake_status)

    result = asyncio.run(fb.create_post(_FakeSession(), 42, {}))

    assert result["ok"] is False
    assert "Сторінк" in result["error"]

"""Тести генератора постів Telegram.

Покривають саме те, що ламалось під час розробки й що не видно з тестів
маркетплейсів: узгодження роду/числа в українській, телеграмний (а НЕ промівський)
рядок стану, добудова обірваної розмітки при переписуванні з історії та правила
автопідбору гілок форуму.

Усе тут — чисті функції, без БД і без мережі.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import telegram_publisher as tp  # noqa: E402


def _p(**kw):
    """Товар із мінімально потрібними полями."""
    base = {
        "id": 1, "productnumber": "#Ф1000", "brandname": "Teva", "model": "ReFlip",
        "marking": "1124051", "typename": "Шльопанці", "gendername": "Жіноча",
        "season": "Літо", "sizeeu": "38", "measurementscm": "24",
        "conditionname": "Новий", "packagingname": "", "materials": {},
        "sizes": ["38"], "price": 1200, "description": "", "extranote": "",
    }
    base.update(kw)
    return base


# ── Рід і число ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("typename,expected", [
    ("Кросівки", "pl"), ("Босоніжки", "pl"), ("Туфлі", "pl"),
    ("Сумка", "f"), ("Валіза", "f"), ("Куртка", "f"),
    ("Рюкзак", "m"), ("Гаманець", "m"), ("Ремінь", "m"),
])
def test_type_form(typename, expected):
    assert tp._type_form(typename) == expected


def test_gender_adjective_agrees_with_type():
    """Регресія: «жіночі сумка» — саме так виглядав хвіст заголовка,
    поки прикметник статі був завжди у множині."""
    assert tp._gender_adj(_p(typename="Сумка", gendername="Жіноча")) == "жіноча"
    assert tp._gender_adj(_p(typename="Рюкзак", gendername="Жіноча")) == "жіночий"
    assert tp._gender_adj(_p(typename="Кросівки", gendername="Чоловіча")) == "чоловічі"
    assert tp._gender_adj(_p(typename="Кросівки", gendername="Унісекс")) == ""


# ── Рядок стану ──────────────────────────────────────────────────────────────

def test_condition_line_is_telegram_style_not_prom():
    """Справді новий товар не називаємо стоком; пакування лишається видимим."""
    assert tp._condition_line(_p(packagingname="")) == "Нові"
    assert tp._condition_line(_p(packagingname="Без коробки")) == "Нові"
    assert tp._condition_line(_p(packagingname="Коробка")) == "Нові (в коробці)"
    assert "Сток" not in (tp._condition_line(_p(packagingname="")) or "")


def test_condition_line_gender_number():
    assert tp._condition_line(_p(typename="Сумка")) == "Нова"
    assert tp._condition_line(_p(typename="Рюкзак")) == "Новий"


def test_condition_line_used_is_honest():
    assert tp._condition_line(_p(conditionname="Легковживаний")) == "Легковживані"
    assert tp._condition_line(_p(conditionname="Вживаний")) == "Вживані"
    assert tp._condition_line(_p(conditionname="Пошкоджений")) == "Пошкоджені"
    assert tp._condition_line(_p(typename="Сумка", conditionname="Легковживаний")) == "Легковживана"
    assert tp._condition_line(_p(typename="Рюкзак", conditionname="Пошкоджений")) == "Пошкоджений"


def test_good_condition_counts_as_new():
    """«Хороший» — це сток у стані нового, але не стан «Новий»."""
    assert tp._condition_line(_p(conditionname="Хороший")) == "Стан нових (Сток)"
    assert tp._condition_line(_p(typename="Сумка", conditionname="Хороший")) == "Стан нової (Сток)"
    assert tp._condition_line(_p(typename="Рюкзак", conditionname="Хороший")) == "Стан нового (Сток)"
    assert tp._condition_icon(_p(conditionname="Хороший")) == "🆕"


def test_condition_confirmation_is_only_for_risky_used_states():
    assert tp._condition_requires_confirmation(_p(conditionname="Вживаний")) is True
    assert tp._condition_requires_confirmation(_p(conditionname="Пошкоджений")) is True
    assert tp._condition_requires_confirmation(_p(conditionname="Легковживаний")) is False
    assert tp._condition_requires_confirmation(_p(conditionname="Хороший")) is False
    assert tp._condition_icon(_p(conditionname="Легковживаний")) == "🆗"


# ── Блок розмірів / замірів ──────────────────────────────────────────────────

def test_single_size_block():
    block = tp._size_block(_p(), [{"size": "42", "measurementscm": "27"}])
    assert block == "👣 **Розмір**: 42 (на ніжку 27 см)"


def test_multi_size_block_is_bullet_list():
    block = tp._size_block(_p(), [
        {"size": "39", "measurementscm": "25-25.5"},
        {"size": "40", "measurementscm": "25.5-26"},
    ])
    assert block.startswith("👣 **Розміри**: \n")
    assert "— 39 (на ніжку 25-25.5 см)" in block
    assert "— 40 (на ніжку 25.5-26 см)" in block


def test_bag_uses_dimensions_not_sizes():
    block = tp._size_block(_p(typename="Сумка", dimensions="25x14x7"), [])
    assert block == "📏 **Заміри**: 25 × 14 × 7 см"


@pytest.mark.parametrize("raw,expected", [
    ("25x14x7", "25 × 14 × 7"),
    ("23х17х6", "23 × 17 × 6"),      # кирилична «х» — у базі трапляється впереміш
    ("26 × 15 × 8", "26 × 15 × 8"),
    ("", None),
])
def test_normalize_dimensions(raw, expected):
    assert tp._normalize_dimensions(raw) == expected


# ── Розмітка ─────────────────────────────────────────────────────────────────

def test_balance_markdown_closes_broken_markers():
    """Регресія: у реальних постах закривальні «**» часто стоять на наступному
    рядку, тож витягнутий рядок «▪️» лишався з непарними маркерами і зʼїдав
    половину поста."""
    assert tp._balance_markdown("Цільнолитий анатомічний **__EVA__") == "Цільнолитий анатомічний **__EVA__**"
    assert tp._balance_markdown("Натуральна шкіра") == "Натуральна шкіра"
    assert tp._balance_markdown("Текстиль **__Crochet__**") == "Текстиль **__Crochet__**"


def test_parse_existing_post_extracts_parts():
    post = (
        "**🏖 ECCO **[**Cozmo Beige**](https://www.google.com/search?q=Ecco-Cozmo-52390400104)"
        "** • легкі анатомічні клоги**\n\n"
        "👣 **Розміри**: \n— 39 (на ніжку 25 см)\n\n"
        "▪️ Цільнолитий анатомічний **__EVA__\n**\n\n"
        "✅ Нові\n"
    )
    parsed = tp.parse_existing_post(post)
    assert parsed["emoji"] == "🏖"
    assert parsed["tagline"] == "легкі анатомічні клоги"
    assert parsed["features"] == ["Цільнолитий анатомічний **__EVA__**"]
    assert parsed["search_q"] == "Ecco-Cozmo-52390400104"


def test_validate_caption_rejects_overlong():
    problem = tp.validate_caption("я" * (tp.CAPTION_LIMIT + 1))
    assert problem and "ліміт" in problem


def _sample_caption(**kw):
    args = dict(
        emoji="👟", brand="HOKA", model="Kawana Mid", search_q="HOKA-Kawana-Mid-1169270",
        tagline="стабільні хайтопи", size_block="👣 **Розмір**: 44 (на ніжку 28 см)",
        features=["Дихаючий текстиль", "**__MetaRocker™__** для плавного перекату"],
        condition="Нові (в коробці)", price="3500", productnumber="#Ф3977",
    )
    args.update(kw)
    return tp.build_caption(**args)


def test_validate_caption_accepts_real_template():
    caption = _sample_caption()
    assert tp.validate_caption(caption) is None
    assert "🛒 Ціна: 3500 грн" in caption
    assert "📲 Пиши `#Ф3977` менеджеру" in caption


def test_technology_abbreviations_are_uppercase_in_default_and_edited_features():
    assert tp.normalize_technology_abbreviations(
        "Проміжна підошва — eva, вставка tpu, abzorb, блискавка sbs, c cap"
    ) == "Проміжна підошва — EVA, вставка TPU, ABZORB, блискавка SBS, C-CAP"

    caption = _sample_caption(features=["підошва eva + pu", "амортизація abzorb"])
    assert "▪️ підошва EVA + PU" in caption
    assert "▪️ амортизація ABZORB" in caption


def test_extended_technology_abbreviations_are_always_uppercase():
    assert tp.normalize_technology_abbreviations(
        "cmeva, cvema, svs, sps, absorb, eva, pu і tpu"
    ) == "CMEVA, CVEMA, SVS, SPS, ABSORB, EVA, PU і TPU"


def test_upper_leather_suede_and_nubuck_are_marked_natural_without_touching_synthetics():
    assert tp.normalize_upper_material("шкіра") == "Натуральна шкіра"
    assert tp.normalize_upper_material("замша") == "Натуральна замша"
    assert tp.normalize_upper_material("нубук") == "Натуральний нубук"
    assert tp.normalize_upper_material("нубукова шкіра") == "Натуральна нубукова шкіра"
    assert tp.normalize_upper_material("шкіра-замша") == "Натуральна шкіра замша"
    assert tp.normalize_upper_material("гладка шкіра") == "Натуральна гладка шкіра"
    assert tp.normalize_upper_material("зерниста шкіра") == "Натуральна зерниста шкіра"
    assert tp.normalize_upper_material("еко-шкіра") == "Еко-шкіра"
    assert tp.normalize_upper_material("Штучна замша") == "Штучна замша"


def test_caption_uses_condition_specific_icon():
    caption = _sample_caption(condition="Стан нових (Сток)", condition_icon="🆕")
    assert "🆕 Стан нових (Сток)" in caption
    assert "✅ Стан нових (Сток)" not in caption


def test_manual_caption_supports_strike_and_monospace_entities():
    from telethon.tl.types import MessageEntityCode, MessageEntityStrike

    plain, entities = _parsed("~~стара ціна~~ і `#Ф3977`")

    assert plain == "стара ціна і #Ф3977"
    assert any(isinstance(entity, MessageEntityStrike) and _seg(plain, entity) == "стара ціна" for entity in entities)
    assert any(isinstance(entity, MessageEntityCode) and _seg(plain, entity) == "#Ф3977" for entity in entities)


# ── Найважливіше: те, що ДІЙСНО побачить підписник ───────────────────────────
#
# Прев'ю в діалозі малює свій рендер і легко бреше. Єдина чесна перевірка —
# прогнати підпис ЧЕРЕЗ ТОЙ САМИЙ парсер, яким його розбере Telegram, і
# подивитись на голий текст та сутності.

def _parsed(caption):
    """Розібрати підпис ТОЧНО так, як його розбере Telegram: через HTML, у який
    його конвертує публікатор. Прогін через markdown.parse тут був би самообманом
    — саме цей парсер і ламався на посиланні всередині жирного."""
    from telethon.extensions import html as tg_html
    return tg_html.parse(tp.md_to_html(caption))


def _seg(plain, ent):
    """Текст під сутністю.

    ⚠️ Зсуви сутностей Telegram рахуються в одиницях UTF-16, а не в символах
    Python: кожне емодзі поза BMP займає ДВІ одиниці. Наївний зріз
    `plain[offset:offset+length]` на підписі, який починається з 👟, з'їжджає
    і показує сміття на кшталт «7 мене».
    """
    from telethon.helpers import add_surrogate, del_surrogate
    s = add_surrogate(plain)
    return del_surrogate(s[ent.offset:ent.offset + ent.length])


@pytest.mark.parametrize(("size_block", "label"), [
    ("👣 **Розмір**: 44 (на ніжку 28 см)", "Розмір"),
    ("👣 **Розміри**: \n— 43 (на ніжку 27.5 см)\n— 44 (на ніжку 28 см)", "Розміри"),
    ("📏 **Заміри**: 28 × 16 × 12 см", "Заміри"),
])
def test_size_heading_is_a_real_telegram_bold_entity(size_block, label):
    """Перевіряємо не зірочки у чернетці, а сутність у фактичному Telegram HTML."""
    from telethon.tl.types import MessageEntityBold

    plain, entities = _parsed(_sample_caption(size_block=size_block))

    assert any(
        isinstance(entity, MessageEntityBold) and _seg(plain, entity) == label
        for entity in entities
    )


def test_no_markdown_leaks_into_visible_text():
    """Регресія: заголовок будувався як `[**Модель**](url)`, і Telethon лишав
    зірочки видимим текстом — у каналі було «Teva **ReFlip**»."""
    plain, _ = _parsed(_sample_caption())
    assert "**" not in plain
    assert "__" not in plain
    assert "Kawana Mid" in plain


def test_headline_is_bold_and_model_is_a_link():
    from telethon.tl.types import MessageEntityBold, MessageEntityTextUrl
    plain, ents = _parsed(_sample_caption())
    headline = plain.split("\n", 1)[0]

    bold = [e for e in ents if isinstance(e, MessageEntityBold)]
    assert any(_seg(plain, e) == headline for e in bold), \
        "увесь заголовок має бути жирним одним відрізком"

    urls = [e for e in ents if isinstance(e, MessageEntityTextUrl)]
    model_url = [e for e in urls if _seg(plain, e) == "Kawana Mid"]
    assert model_url, "модель має лишитись клікабельною"
    assert "HOKA-Kawana-Mid-1169270" in model_url[0].url


def test_bold_does_not_leak_past_the_headline():
    """Регресія: з Markdown-парсером жирний із заголовка тягнувся ПОЗА нього —
    накривав і блок розмірів, і першу перевагу (Bold на 93 одиниці замість 37),
    бо посилання всередині `**…**` збиває пару зірочок."""
    from telethon.tl.types import MessageEntityBold
    plain, ents = _parsed(_sample_caption())
    headline = plain.split("\n", 1)[0]
    for e in ents:
        if isinstance(e, MessageEntityBold):
            seg = _seg(plain, e)
            assert "\n" not in seg, f"жирний перетік через рядки: {seg!r}"
    assert any(_seg(plain, e) == headline
               for e in ents if isinstance(e, MessageEntityBold))


def test_html_conversion_escapes_raw_angle_brackets():
    """Людина може написати «<» у перевагах — це має лишитись символом, а не
    поламати розмітку."""
    plain, _ = _parsed(_sample_caption(features=["Вага < 300 г & міцний"]))
    assert "Вага < 300 г & міцний" in plain


def test_feature_line_keeps_bold_italic_nesting():
    """`**__X__**` Telethon розбирає правильно — на відміну від лінка."""
    from telethon.tl.types import MessageEntityBold, MessageEntityItalic
    plain, ents = _parsed(_sample_caption())
    assert "MetaRocker™" in plain and "**" not in plain
    spans = {(type(e).__name__, _seg(plain, e)) for e in ents}
    assert ("MessageEntityBold", "MetaRocker™") in spans
    assert ("MessageEntityItalic", "MetaRocker™") in spans


def test_product_number_stays_monospace():
    from telethon.tl.types import MessageEntityCode
    plain, ents = _parsed(_sample_caption())
    codes = [_seg(plain, e) for e in ents if isinstance(e, MessageEntityCode)]
    assert codes == ["#Ф3977"]


@pytest.mark.parametrize("kw", [
    {"model": ""},                    # товар без моделі
    {"brand": ""},                    # без бренду
    {"tagline": ""},                  # без хвоста заголовка
    {"search_q": ""},                 # немає що шукати — модель лишається текстом
    {"brand": "", "model": "", "tagline": ""},
    {"tagline": "**зайві** зірочки"},  # людина вставила розмітку в поле
])
def test_headline_never_leaks_markdown(kw):
    plain, _ = _parsed(_sample_caption(**kw))
    assert "**" not in plain


def test_caption_never_leaks_internal_notes():
    """description/extranote — внутрішні нотатки складу; їхня публікація вже
    одного разу нашкодила на OLX, тож у чернетку переваг вони не входять."""
    bms = _p(description="старі, лежали на складі", extranote="пляма на носку",
             materials={"upper": "Натуральна шкіра"})
    features = tp.default_features(bms)
    assert features == ["Натуральна шкіра"]
    assert not any("старі" in f or "пляма" in f for f in features)


# ── Автопідбір гілок ─────────────────────────────────────────────────────────

THREADS = [
    {"thread_id": 1, "thread_title": "ВСІ ПРОПОЗИЦІЇ", "auto_suggest": False},
    {"thread_id": 219, "thread_title": "ЛІТО | ЖІНОЧІ", "auto_suggest": True},
    {"thread_id": 245, "thread_title": "ЛІТО | ЧОЛОВІЧІ", "auto_suggest": True},
    {"thread_id": 8633, "thread_title": "ЛІТО | ДИТЯЧІ", "auto_suggest": True},
    {"thread_id": 2, "thread_title": "КРОСІВКИ | ЧОЛОВІЧІ", "auto_suggest": True},
    {"thread_id": 749, "thread_title": "ДЕМІ | ЧОЛОВІЧІ", "auto_suggest": True},
    {"thread_id": 2924, "thread_title": "СУМКИ ТА РЮКЗАКИ", "auto_suggest": True},
    {"thread_id": 6039, "thread_title": "HOKA", "auto_suggest": True},
    {"thread_id": 599, "thread_title": "УЦІНКА | РОЗПРОДАЖ", "auto_suggest": True},
]


def _sizes(*vals):
    return [{"product_id": i, "size": v, "measurementscm": ""} for i, v in enumerate(vals, 1)]


def test_root_topic_and_sale_thread_never_suggested():
    picked = tp.suggest_threads(_p(), THREADS, _sizes("38"))
    assert 1 not in picked        # «ВСІ ПРОПОЗИЦІЇ» публікується завжди, окремо
    assert 599 not in picked      # уцінку вирішує людина


def test_unisex_goes_to_both_gender_threads():
    """Регресія: унісекс не потрапляв нікуди, хоча власник кладе його в обидві
    гілки (так пішли клоги ECCO Cozmo)."""
    picked = tp.suggest_threads(_p(gendername="Унісекс"), THREADS, _sizes("39", "44"))
    assert 219 in picked and 245 in picked


def test_rostovka_spanning_kids_and_adults_hits_both():
    """Ростовка 29–45 має пропонувати і дитячу гілку, і дорослі: оцінювати
    «дитячість» по одному рядку товару не можна."""
    picked = tp.suggest_threads(_p(gendername="Унісекс"), THREADS, _sizes("29", "30", "42"))
    assert 8633 in picked
    assert 219 in picked and 245 in picked


def test_all_season_sandals_still_go_to_summer():
    """«Всесезон» — значення за замовчуванням у 8181 товару; для босоніжок
    сезон визначає тип, інакше найлітніший товар не отримував пропозицій."""
    picked = tp.suggest_threads(
        _p(typename="Босоніжки", season="Всесезон"), THREADS, _sizes("38"))
    assert 219 in picked


def test_sneakers_marked_summer_do_not_go_to_summer_thread():
    """«ЛІТО | …» — гілка ВІДКРИТОГО взуття: з 57 постів там 55 шльопанці й
    босоніжки. Кросівки з сезоном «Літо» власник кладе в «КРОСІВКИ | …»."""
    picked = tp.suggest_threads(
        _p(typename="Кросівки", gendername="Чоловіча", season="Літо, Всесезон"),
        THREADS, _sizes("44"))
    assert 245 not in picked
    assert 2 in picked


def test_first_listed_season_wins():
    """«Демі, Літо, Всесезон» → ДЕМІ: власник кладе в перший названий сезон."""
    picked = tp.suggest_threads(
        _p(typename="Кросівки", gendername="Чоловіча", season="Демі, Літо, Всесезон"),
        THREADS, _sizes("44"))
    assert 749 in picked and 245 not in picked


def test_brand_thread_matches_by_brand():
    picked = tp.suggest_threads(
        _p(brandname="HOKA", typename="Кросівки", gendername="Чоловіча"),
        THREADS, _sizes("44"))
    assert 6039 in picked


def test_bag_goes_to_bags_thread_only():
    picked = tp.suggest_threads(
        _p(typename="Сумка", gendername="Жіноча", season="Всесезон", sizeeu=None),
        THREADS, [])
    assert picked == [2924]


# ── Наскрізний прапорець «усе без звуку» ────────────────────────────────────

def test_live_publish_rejects_risky_condition_without_confirmation(monkeypatch):
    monkeypatch.setattr(
        tp, "_load_product",
        lambda _db, _pid: _p(conditionname="Вживаний"),
    )

    result = asyncio.run(tp.create_post(object(), 1, {"caption": _sample_caption()}))

    assert result["ok"] is False
    assert result["confirmation_required"] is True
    assert "Вживаний" in result["error"]
    assert "підтвердж" in (tp._preflight_batch_item(object(), 1, {}) or "")


def test_create_post_passes_silent_to_original_threads_and_channel(monkeypatch):
    """Одна галочка має дійти до КОЖНОГО Telegram API-виклику альбому."""
    class FakeResult:
        def scalar(self):
            return 0

    class FakeDb:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

        def commit(self):
            pass

        def rollback(self):
            pass

    class FakeClient:
        def __init__(self):
            self.sent = []
            self.forwarded = []
            self.next_id = 100

        async def get_entity(self, entity):
            return entity

        async def send_file(self, entity, files, **kwargs):
            self.sent.append(kwargs)
            self.next_id += 1
            return [SimpleNamespace(id=self.next_id, photo=object(), grouped_id=77)]

        async def forward_messages(self, **kwargs):
            self.forwarded.append(kwargs)
            return []

    class FakeScanner:
        def __init__(self):
            self.client = FakeClient()

        async def disconnect(self):
            pass

        async def _resolve_entity(self, entity):
            return entity

    scanner = FakeScanner()

    async def fake_connect():
        return scanner, None

    monkeypatch.setattr(tp, "_connect", fake_connect)
    monkeypatch.setattr(tp, "_load_product", lambda _db, _pid: _p())
    monkeypatch.setattr(tp, "_photo_entries", lambda _bms: ([object()], "official"))
    monkeypatch.setattr(tp, "_read_photo_bytes", lambda _photos: [object()])
    monkeypatch.setattr(tp, "_available_sizes", lambda _db, _pnum: [])
    monkeypatch.setattr(tp, "get_threads", lambda _db: [
        {"thread_id": 219, "thread_title": "ЛІТО | ЖІНОЧІ"},
    ])
    monkeypatch.setattr(tp, "_record_post", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tp, "save_template", lambda *_args, **_kwargs: None)

    result = asyncio.run(tp.create_post(FakeDb(), 1, {
        "caption": _sample_caption(),
        "thread_ids": [219],
        "to_channel": True,
        "channel_at": None,
        "silent": True,
    }))

    assert result["ok"] is True and result["silent"] is True
    assert len(scanner.client.sent) == 2  # оригінал + одна тематична копія
    assert all(call["silent"] is True for call in scanner.client.sent)
    assert len(scanner.client.forwarded) == 1
    assert scanner.client.forwarded[0]["silent"] is True

    monkeypatch.setattr(tp, "ARCHIVE_CHAT", "workshop")
    test_result = asyncio.run(tp.create_post(FakeDb(), 1, {
        "caption": _sample_caption(),
        "test_mode": True,
        "silent": True,
    }))
    assert test_result["ok"] is True and test_result["test_mode"] is True
    assert scanner.client.sent[-1]["silent"] is True


# ── Безпечна пакетна публікація з «Товарів» ─────────────────────────────────

def test_batch_preview_merges_size_rows_by_productnumber(monkeypatch):
    products = {
        10: _p(id=10, productnumber="#Ф500"),
        11: _p(id=11, productnumber="#Ф500"),
        20: _p(id=20, productnumber="#Ф501"),
    }
    monkeypatch.setattr(tp, "_load_product", lambda _db, pid: products.get(pid))
    monkeypatch.setattr(tp, "preview_post", lambda _db, pid: {
        "ok": True, "product_id": pid, "productnumber": products[pid]["productnumber"],
    })

    result = tp.preview_posts_batch(object(), [10, 11, 20])

    assert result["ok"] is True
    assert result["selected_count"] == 3
    assert result["unique_count"] == 2
    assert result["merged_count"] == 1
    assert result["items"][0]["product_id"] == 10
    assert result["items"][0]["source_product_ids"] == [10, 11]


def test_invalid_schedule_never_silently_becomes_publish_now():
    now, error = tp._validate_when(None)
    assert now is None and error is None

    invalid, error = tp._validate_when("")
    assert invalid is None and error

    past, error = tp._validate_when("2020-01-01T08:00:00+03:00")
    assert past is None and "2 хвилини" in error


def test_batch_reuses_connection_stops_after_flood_and_is_idempotent(monkeypatch):
    class FakeScanner:
        def __init__(self):
            self.disconnected = 0

        async def disconnect(self):
            self.disconnected += 1

    scanner = FakeScanner()
    connects = []
    published = []

    async def fake_connect():
        connects.append(True)
        return scanner, None

    async def fake_create(_db, pid, _payload, **kwargs):
        published.append((pid, kwargs.get("_scanner")))
        if pid == 2:
            return {"ok": False, "error": "FLOOD_WAIT_35"}
        return {"ok": True, "failed": []}

    products = {
        1: _p(id=1, productnumber="#Ф1"),
        2: _p(id=2, productnumber="#Ф2"),
        3: _p(id=3, productnumber="#Ф3"),
    }
    monkeypatch.setattr(tp, "_connect", fake_connect)
    monkeypatch.setattr(tp, "create_post", fake_create)
    monkeypatch.setattr(tp, "_load_product", lambda _db, pid: products.get(pid))
    monkeypatch.setattr(tp, "_preflight_batch_item", lambda *_args: None)
    monkeypatch.setattr(tp, "BATCH_POST_GAP_SEC", 0)
    tp._BATCH_CACHE.clear()
    request = [
        {"product_id": 1, "payload": {"to_channel": False}},
        {"product_id": 2, "payload": {"to_channel": False}},
        {"product_id": 3, "payload": {"to_channel": False}},
    ]

    first = asyncio.run(tp.create_posts_batch(object(), request, "test-flood-batch"))
    second = asyncio.run(tp.create_posts_batch(object(), request, "test-flood-batch"))

    assert [r["status"] for r in first["results"]] == ["success", "error", "skipped"]
    assert first["status"] == "partial"
    assert published == [(1, scanner), (2, scanner)]
    assert len(connects) == 1 and scanner.disconnected == 1
    assert second["replayed"] is True

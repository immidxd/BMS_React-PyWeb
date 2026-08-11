"""
Створення постів у Telegram — запис (Фаза 3 вкладки «Публікації»).

`telegram_service.py` вміє читати канали й ЗНІМАТИ товар з публікації.
Цей модуль робить протилежне — СТВОРЮЄ пост, точно повторюючи ручний флоу
власника, розібраний по живих повідомленнях каналу:

  1. Оригінальний альбом фото + підпис → топік «ВСІ ПРОПОЗИЦІЇ» (topic_id=1,
     службовий General форуму «КАТАЛОГ ТОВАРУ»). Це ЄДИНА копія, яку Telegram
     дозволяє редагувати, тому вона завжди головна.
  2. Копії в тематичні гілки («ЛІТО | ЖІНОЧІ», «HOKA», «СУМКИ ТА РЮКЗАКИ», …).
     Саме КОПІЇ, а не форварди: форвард відредагувати неможливо, і через це
     ростовки доводилось правити руками (`needs_manual_edit`). Фото при цьому
     НЕ перезаливаються — переюзаємо вже завантажені в Telegram обʼєкти.
  3. Форвард у канал BrandStore — тут форвард доречний: підпис «Переслано з
     КАТАЛОГ ТОВАРУ» веде підписників у каталог. Штатно планується засобами
     Telegram на 08:00 (власник роками публікує саме о 08:00–08:05).

Шаблон підпису — уніфікований, знятий з реальних постів. Автоматично
підставляється все, крім двох рядків: «• короткий опис» і «▪️ переваги».
Їх BMS чесно вигадати не може, а `description`/`extranote` — ВНУТРІШНІ
нотатки, які не публікуються ніколи. Тому: чернетка з полів товару +
переписування з минулого поста тієї ж моделі + редагування людиною.
"""

from __future__ import annotations

import json
import logging
import os
import re
import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Куди публікуємо ──────────────────────────────────────────────────────────
# Ті самі chat_id, що в TelegramScanner.KNOWN_CHANNELS (raw ID без префікса -100).
FORUM_CHAT_ID = int(os.getenv("TELEGRAM_FORUM_CHAT_ID", "2373506200"))
FORUM_TITLE = "КАТАЛОГ ТОВАРУ"
CHANNEL_CHAT_ID = int(os.getenv("TELEGRAM_CHANNEL_CHAT_ID", "1201323714"))
CHANNEL_TITLE = "BrandStore 👟 │ Брендове взуття"
# «ВСІ ПРОПОЗИЦІЇ» — це General-топік форуму. Повідомлення в ньому не мають
# reply_to, тому в telegram_posts вони лежать із thread_id = NULL.
ROOT_TOPIC_ID = 1
ROOT_TOPIC_TITLE = "ВСІ ПРОПОЗИЦІЇ"

MANAGER_HANDLE = os.getenv("TELEGRAM_MANAGER", "@branstore_manager")
CATALOG_URL = os.getenv("TELEGRAM_CATALOG_URL", "https://t.me/brandstore_catalog")
REVIEWS_URL = os.getenv("TELEGRAM_REVIEWS_URL", "https://t.me/brandstore_reviews")
DELIVERY_LINE = os.getenv("TELEGRAM_DELIVERY_LINE", "1–2 дні")

# Ліміт підпису до медіа в Telegram — 1024 символи (не 4096, як у тексту).
CAPTION_LIMIT = 1024
# Telegram пускає в альбом до 10 медіа, але канон каналу — РІВНО 5: із 116
# альбомів форуму 115 мають по 5 фото. Тому ліміт тут наш, а не платформи;
# у діалозі можна вибрати, які саме 5 із наявних піде в пост.
ALBUM_LIMIT = 5
ALBUM_HARD_LIMIT = 10          # межа самого Telegram — вище неї не пускаємо

# Пакет навмисно невеликий: один товар може породити оригінал, до шести копій
# у гілках і форвард у канал. MTProto не має сталої «квоти» — Telegram повертає
# FLOOD_WAIT динамічно, тому BMS посилає послідовно й зупиняє хвіст черги при
# першому rate-limit. Це суттєво безпечніше за паралельні Promise/корутини.
BATCH_MAX_PRODUCTS = int(os.getenv("TELEGRAM_BATCH_MAX_PRODUCTS", "10"))
MAX_THREADS_PER_POST = int(os.getenv("TELEGRAM_MAX_THREADS_PER_POST", "6"))
BATCH_POST_GAP_SEC = float(os.getenv("TELEGRAM_BATCH_POST_GAP_SEC", "1.25"))
BATCH_DESTINATION_GAP_SEC = float(os.getenv("TELEGRAM_BATCH_DESTINATION_GAP_SEC", "0.45"))
BATCH_CACHE_TTL_SEC = 6 * 60 * 60
_PUBLISH_LOCK = asyncio.Lock()
_BATCH_CACHE: "OrderedDict[str, Tuple[float, dict]]" = OrderedDict()
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# WORKSHOP — приватний архівний канал власника (той самий, куди відлітають
# резервні копії при знятті з продажу). Використовується як полігон: тестова
# публікація йде ТІЛЬКИ туди, її ніхто, крім власника, не бачить, і вона НЕ
# записується в telegram_posts — інакше товар вважався б опублікованим.
ARCHIVE_CHAT = os.getenv("TELEGRAM_ARCHIVE_CHAT", "")
ARCHIVE_TITLE = "WORKSHOP (архів)"


# ─────────────────────────────────────────────────────────────────────────────
# Дані товару
# ─────────────────────────────────────────────────────────────────────────────

def _prom():
    """Логіка стану/пакування («Нові, без коробки») уже вивірена в Prom —
    не дублюємо її, а переюзуємо."""
    try:
        from services import prom_service
    except ImportError:
        from backend.services import prom_service
    return prom_service


def _order_logic():
    try:
        from utils.order_status_logic import CONFIRMED_SOLD, sql_in_list
    except ImportError:
        from backend.utils.order_status_logic import CONFIRMED_SOLD, sql_in_list
    return CONFIRMED_SOLD, sql_in_list


_PRODUCT_SQL = """
    SELECT p.id, p.productnumber, p.model, p.marking, p.price, p.sizeeu,
           p.measurementscm, p.dimensions, p.season, p.quantity, p.year,
           p.official_photos_from, p.width,
           p.measurements_sole_thickness_min, p.measurements_sole_thickness_max,
           p.measurements_heel_min, p.measurements_heel_max,
           b.id AS brand_id, b.brandname,
           t.id AS type_id, t.typename,
           st.id AS subtype_id, st.subtypename,
           g.id AS gender_id, g.gendername,
           c.colorname,
           cond.conditionname, pk.packagingname,
           so.soletypename, ln.liningname,
           s.statusname
    FROM products p
    LEFT JOIN brands b ON b.id = p.brandid
    LEFT JOIN types t ON t.id = p.typeid
    LEFT JOIN subtypes st ON st.id = p.subtypeid
    LEFT JOIN genders g ON g.id = p.genderid
    LEFT JOIN colors c ON c.id = p.colorid
    LEFT JOIN conditions cond ON cond.id = COALESCE(p.current_conditionid, p.conditionid)
    LEFT JOIN packaging_types pk ON pk.id = p.packagingid
    LEFT JOIN sole_types so ON so.id = p.soletypeid
    LEFT JOIN linings ln ON ln.id = p.liningid
    LEFT JOIN statuses s ON s.id = p.statusid
    WHERE p.id = :pid
"""


def _load_product(db: Session, product_id: int) -> Optional[dict]:
    row = db.execute(text(_PRODUCT_SQL), {"pid": product_id}).mappings().first()
    if not row:
        return None
    d = dict(row)
    mats = db.execute(text("""
        SELECT pm.position, string_agg(m.materialname, ', ' ORDER BY m.materialname) AS names
        FROM product_materials pm JOIN materials m ON m.id = pm.material_id
        WHERE pm.product_id = :pid GROUP BY pm.position
    """), {"pid": product_id}).fetchall()
    d["materials"] = {p: n for p, n in mats}
    # `_condition_line` з prom_service читає саме ці ключі.
    d["sizes"] = [d.get("sizeeu")] if d.get("sizeeu") else []
    return d


def _available_sizes(db: Session, productnumber: str) -> List[dict]:
    """Розміри цього номера, які РЕАЛЬНО є в наявності, кожен зі своїм заміром.

    Ростовка в BMS — це один рядок products на розмір (див. унікальний індекс
    номер+розмір+колір), тому «скільки лишилось» рахується через quantity мінус
    підтверджено продані, а не через кількість рядків.
    """
    confirmed_sold, sql_in_list = _order_logic()
    rows = db.execute(text(f"""
        SELECT p.id, COALESCE(p.sizeeu, '') AS sz, p.measurementscm,
               GREATEST(COALESCE(p.quantity, 1) - COALESCE((
                   SELECT COUNT(*) FROM order_items oi
                   JOIN orders o ON o.id = oi.order_id
                   WHERE oi.product_id = p.id
                     AND o.order_status_id IN {sql_in_list(confirmed_sold)}
               ), 0), 0) AS avail
        FROM products p
        LEFT JOIN statuses s ON s.id = p.statusid
        WHERE p.productnumber = :pn
          AND COALESCE(s.statusname, '') <> 'Продано'
        ORDER BY NULLIF(regexp_replace(COALESCE(p.sizeeu, ''), '[^0-9.]', '', 'g'), '')::numeric
                 NULLS FIRST, p.id
    """), {"pn": productnumber}).fetchall()
    out = []
    for pid, sz, meas, avail in rows:
        if int(avail or 0) <= 0:
            continue
        out.append({
            "product_id": pid,
            "size": (sz or "").strip(),
            "measurementscm": (meas or "").strip(),
            "available": int(avail),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Складові шаблону
# ─────────────────────────────────────────────────────────────────────────────

# Емодзі за типом товару. Зняті з реальних постів; у самих постах трапляється
# й творчий вибір (🐊 для Crocs, 🏖 для клогів, 🐈‍⬛ для котячого принту) —
# тому це саме ДЕФОЛТ, який людина міняє в діалозі одним кліком.
_TYPE_EMOJI: Dict[str, str] = {
    "кросівки": "👟", "кеди": "👟", "сліпони": "👟", "мокасини": "👞",
    "ботинки": "🥾", "напівсапоги": "🥾", "сапоги": "🥾", "черевики": "🥾",
    "босоніжки": "👡", "сандалі": "🩴", "сандалії": "🩴",
    "шльопанці": "🩴", "тапки": "🩴", "сабо": "🩴",
    "туфлі": "👞", "балетки": "🥿",
    "сумка": "👜", "cумка": "👜",          # друга — з латинською C, легасі BMS
    "рюкзак": "🎒", "валіза": "🧳", "гаманець": "👛", "ремінь": "🎗",
    "окуляри": "🕶", "куртка": "🧥", "штани": "👖", "піжама": "🩳",
    "білизна": "🩲", "шапка": "🧢", "рушник": "🧺",
}
# Бренд перебиває тип: у постах Crocs завжди йде під 🐊.
_BRAND_EMOJI: Dict[str, str] = {"crocs": "🐊"}
_EMOJI_FALLBACK = "🛍"

# Гілка «ТУФЛІ | ЖІНОЧІ» ловить і балетки; «КРОСІВКИ» — кеди й сліпони.
_TYPE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "кросівки": ("кросівки", "кеди", "сліпони"),
    "туфлі": ("туфлі", "балетки", "мокасини"),
    "сумки": ("сумка", "cумка", "рюкзак", "валіза", "гаманець"),
    "верхній одяг": ("куртка", "пальто", "пуховик", "вітровка", "жилет"),
}

_BAG_TYPES = {"сумка", "cумка", "рюкзак", "валіза", "гаманець", "клатч", "барсетка"}


def _low(v: Any) -> str:
    return str(v or "").strip().lower()


def _is_bag(bms: dict) -> bool:
    return _low(bms.get("typename")) in _BAG_TYPES


def default_emoji(bms: dict) -> str:
    brand = _low(bms.get("brandname"))
    for key, emo in _BRAND_EMOJI.items():
        if key in brand:
            return emo
    return _TYPE_EMOJI.get(_low(bms.get("typename")), _EMOJI_FALLBACK)


def default_search_q(bms: dict) -> str:
    """Те, що підставляється в google.com/search?q=.

    У живих постах переважає «Бренд-Модель-Маркування» через дефіси
    (`Teva-ReFlip-1124051`, `Ecco-Cozmo-52390400104`), рідше — саме маркування
    (`1169270-BLTR`). Беремо переважний варіант; рядок редагований.
    """
    parts = [str(bms.get(k) or "").strip() for k in ("brandname", "model", "marking")]
    joined = " ".join(p for p in parts if p)
    return re.sub(r"[\s/]+", "-", joined).strip("-")


# Рід і число назви типу — потрібні, щоб узгодити і прикметник статі, і слово
# «Нові/Нова/Новий». Взуття в українській — множина («кросівки», «туфлі»),
# сумка — жіноча однина, рюкзак — чоловіча. Явні винятки + евристика за
# закінченням, щоб новий тип у довіднику не ламав граматику мовчки.
_TYPE_FORM_OVERRIDE: Dict[str, str] = {
    "сумка": "f", "cумка": "f", "валіза": "f", "куртка": "f", "шапка": "f",
    "піжама": "f", "білизна": "f", "сукня": "f", "кофта": "f", "футболка": "f",
    "рюкзак": "m", "гаманець": "m", "ремінь": "m", "костюм": "m", "шарф": "m",
    "взуття": "n",
}


def _type_form(typename: str) -> str:
    """'pl' | 'f' | 'm' | 'n' — число/рід назви типу товару."""
    t = _low(typename)
    if not t:
        return "pl"
    if t in _TYPE_FORM_OVERRIDE:
        return _TYPE_FORM_OVERRIDE[t]
    if t.endswith(("и", "і", "ї")):
        return "pl"
    if t.endswith(("а", "я")):
        return "f"
    if t.endswith(("о", "е")):
        return "n"
    return "m"


_GENDER_ADJ: Dict[str, Dict[str, str]] = {
    "ж": {"pl": "жіночі", "f": "жіноча", "m": "жіночий", "n": "жіноче"},
    "ч": {"pl": "чоловічі", "f": "чоловіча", "m": "чоловічий", "n": "чоловіче"},
    "д": {"pl": "дитячі", "f": "дитяча", "m": "дитячий", "n": "дитяче"},
}


def _gender_adj(bms: dict) -> str:
    """«жіночі» / «жіноча» / «чоловічий» — узгоджено з родом і числом типу."""
    form = _type_form(bms.get("typename"))
    if _is_kids(bms):
        return _GENDER_ADJ["д"][form]
    g = _low(bms.get("gendername"))
    if g.startswith("жін"):
        return _GENDER_ADJ["ж"][form]
    if g.startswith("чол"):
        return _GENDER_ADJ["ч"][form]
    return ""


def _is_kids(bms: dict) -> bool:
    """Дитяче не вгадуємо з голови — та сама ознака, що в Prom/OLX."""
    try:
        return bool(_prom()._is_kids({**bms, "sizes": bms.get("sizes") or []}))
    except Exception:
        return False


def default_tagline(bms: dict) -> str:
    """Чернетка хвоста заголовка після «• ». Коротка іменна фраза, як у постах:
    «жіночі босоніжки на платформі», «легкі анатомічні клоги»."""
    typename = _low(bms.get("typename"))
    if not typename or typename in {"невизначено", "???"}:
        return ""
    base = typename          # у довіднику типи вже в потрібній формі: «кросівки», «сумка»
    adj = _gender_adj(bms)
    tail = ""
    sole = _low(bms.get("soletypename"))
    if sole == "платформа":
        tail = " на платформі"
    elif sole == "танкетка":
        tail = " на танкетці"
    elif sole == "підбора":
        tail = " на каблуку"
    return " ".join(x for x in [adj, base] if x) + tail


# Матеріал верху йде окремим рядком як є; для решти позицій потрібен префікс,
# інакше виходить неграматичне «Підошва гума».
_POSITION_PREFIX: Dict[str, str] = {
    "sole": "Підошва — ",
    "midsole": "Проміжна підошва — ",
    "insole": "Устілка — ",
    "membrane": "Мембрана — ",
    "middle": "Середній шар — ",
}


def _fmt_cm(lo: Any, hi: Any) -> Optional[str]:
    def num(v):
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)
        except (TypeError, ValueError):
            return None
    a, b = num(lo), num(hi)
    if a and b and a != b:
        return f"{a}–{b}"
    return a or b


def default_features(bms: dict) -> List[str]:
    """Чернетка рядків «▪️» з полів BMS — матеріали, підошва, висота платформи.

    Свідомо НЕ чіпає `description`/`extranote`: це внутрішні нотатки складу
    («старі», «пляма на носку»), і їх публікація вже одного разу зашкодила
    на OLX. Рядки тут — сировина, яку людина доводить у діалозі.
    """
    out: List[str] = []
    mats: Dict[str, str] = bms.get("materials") or {}

    upper = (mats.get("upper") or "").strip()
    if upper:
        out.append(_cap(upper))
    for pos in ("membrane", "midsole", "sole", "insole"):
        val = (mats.get(pos) or "").strip()
        if val:
            out.append(f"{_POSITION_PREFIX[pos]}{val.lower()}")

    sole = _low(bms.get("soletypename"))
    thick = _fmt_cm(bms.get("measurements_sole_thickness_min"),
                    bms.get("measurements_sole_thickness_max"))
    heel = _fmt_cm(bms.get("measurements_heel_min"), bms.get("measurements_heel_max"))
    if sole == "платформа" and thick:
        out.append(f"Платформа {thick} см")
    elif heel:
        out.append(f"Каблук {heel} см")
    elif sole and sole not in {"плоска"}:
        out.append(f"{_cap(sole)} підошва")

    lining = (bms.get("liningname") or "").strip()
    season = _low(bms.get("season"))
    if lining and ("зима" in season or "єврозима" in season):
        out.append(f"Утеплена підкладка — {lining.lower()}")

    # Дублікати трапляються, коли верх і підкладка з одного матеріалу.
    seen, uniq = set(), []
    for line in out:
        k = line.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(line)
    return uniq[:4]


def _cap(s: Any) -> str:
    s = str(s or "").strip()
    return s[:1].upper() + s[1:] if s else s


def _normalize_dimensions(raw: Any) -> Optional[str]:
    """«25x14x7» / «23х17х6» → «25 × 14 × 7» (у постах саме через ×, з пробілами).
    Латинська x і кирилична х трапляються в базі впереміш."""
    s = str(raw or "").strip()
    if not s:
        return None
    parts = [p.strip().replace(",", ".") for p in re.split(r"[xXхХ×*]", s) if p.strip()]
    return " × ".join(parts) if parts else None


def _size_block(bms: dict, sizes: List[dict]) -> Optional[str]:
    """«👣 Розмір/Розміри» для взуття або «📏 Заміри» для сумок."""
    if _is_bag(bms) or not sizes:
        dims = _normalize_dimensions(bms.get("dimensions"))
        if dims:
            return f"📏 **Заміри**: {dims} см"
        if not sizes:
            return None

    def one(s: dict) -> str:
        meas = (s.get("measurementscm") or "").strip()
        return f"{s['size']} (на ніжку {meas} см)" if meas else s["size"]

    real = [s for s in sizes if s.get("size")]
    if not real:
        return None
    if len(real) == 1:
        return f"👣 **Розмір**: {one(real[0])}"
    lines = "\n".join(f"— {one(s)}" for s in real)
    return f"👣 **Розміри**: \n{lines}"


_NEW_WORD: Dict[str, str] = {"pl": "Нові", "f": "Нова", "m": "Новий", "n": "Нове"}


def _condition_line(bms: dict) -> Optional[str]:
    """Рядок «✅» у телеграмному вигляді.

    ⚠️ Свідомо НЕ переюзуємо `prom_service._condition_line`: там конвенція
    маркетплейсів — «Нові (Сток), без коробки». У Telegram власник роками пише
    інакше — просто «Нові», а дужки зʼявляються ЛИШЕ коли коробка є:
    «Нові (в коробці)». «Сток» у канал не йде взагалі. Стан вживаного
    показуємо чесно, як він записаний у BMS.
    """
    prom = _prom()
    cond = _low(bms.get("conditionname"))
    pack = _low(bms.get("packagingname"))
    if cond in prom._COND_NEWLIKE:
        word = _NEW_WORD[_type_form(bms.get("typename"))]
        has_box = "коробк" in pack and "без" not in pack
        return f"{word} (в коробці)" if has_box else word
    if cond in prom._COND_USED:
        return _cap(bms.get("conditionname"))
    return None


def _fmt_price(price: Any) -> Optional[str]:
    try:
        f = float(price)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")


def build_caption(
    *,
    emoji: str,
    brand: str,
    model: str,
    search_q: str,
    tagline: str,
    size_block: Optional[str],
    features: List[str],
    condition: Optional[str],
    price: Optional[str],
    productnumber: str,
) -> str:
    """Складання підпису за уніфікованим шаблоном (Telegram Markdown).

    Порядок і пунктуація дзеркалять реальні пости каналу — навмисно, щоб
    згенерований пост не відрізнявся від сотні вже опублікованих вручну.
    """
    # ⚠️ Заголовок — ОДИН жирний блок, у якому вже лежить посилання, а не
    # «жирний усередині посилання» (`[**Модель**](url)`). У постах, написаних
    # у клієнті Telegram, друга форма зустрічається, але парсер Telethon її НЕ
    # розуміє: зірочки лишаються видимим текстом — «Teva **ReFlip**». Тому
    # збираємо `**Бренд [Модель](url) • опис**`: Telegram кладе Bold на весь
    # відрізок і TextUrl на модель — візуально те саме, що в ручних постах.
    def _no_bold(s: str) -> str:
        return str(s or "").replace("**", "").strip()

    head_bits: List[str] = []
    if brand:
        head_bits.append(_no_bold(brand))
    if model:
        link = f"https://www.google.com/search?q={search_q}" if search_q else ""
        head_bits.append(f"[{_no_bold(model)}]({link})" if link else _no_bold(model))
    head = " ".join([emoji] + head_bits) if head_bits else emoji
    if tagline:
        head += f" • {_no_bold(tagline)}"
    blocks: List[str] = [f"**{head}**"]

    if size_block:
        blocks.append(size_block)
    feat = [f"▪️ {f}" for f in features if str(f).strip()]
    if feat:
        blocks.append("\n".join(feat))
    if condition:
        blocks.append(f"✅ {condition}")
    if price:
        blocks.append(f"🛒 Ціна: {price} грн")
    blocks.append(f"🚚 Доставка: {DELIVERY_LINE}")
    pnum = productnumber if productnumber.startswith("#") else f"#{productnumber}"
    blocks.append(f"📲 Пиши `{pnum}` менеджеру 👉 {MANAGER_HANDLE}")
    blocks.append(f"[КАТАЛОГ ТОВАРІВ]({CATALOG_URL}) | [ВІДГУКИ]({REVIEWS_URL})")
    return "\n\n".join(blocks)


# ─────────────────────────────────────────────────────────────────────────────
# Памʼять тексту за брендом+моделлю
# ─────────────────────────────────────────────────────────────────────────────

def _template_key(bms: dict) -> Tuple[str, str]:
    return _low(bms.get("brandname")), _low(bms.get("model"))


def load_template(db: Session, bms: dict) -> Optional[dict]:
    """Збережений маркетинговий текст для цієї моделі (якщо вже публікували)."""
    bk, mk = _template_key(bms)
    if not mk:
        return None
    row = db.execute(text("""
        SELECT emoji, tagline, features, search_q FROM telegram_post_templates
        WHERE brand_key = :bk AND model_key = :mk
    """), {"bk": bk, "mk": mk}).mappings().first()
    if not row:
        return None
    try:
        feats = json.loads(row["features"]) if row["features"] else []
    except (ValueError, TypeError):
        feats = []
    return {
        "emoji": row["emoji"], "tagline": row["tagline"],
        "features": [str(f) for f in feats if str(f).strip()],
        "search_q": row["search_q"], "source": "template",
    }


def save_template(db: Session, bms: dict, *, emoji: str, tagline: str,
                  features: List[str], search_q: str) -> None:
    bk, mk = _template_key(bms)
    if not mk:
        return
    db.execute(text("""
        INSERT INTO telegram_post_templates (brand_key, model_key, emoji, tagline, features, search_q)
        VALUES (:bk, :mk, :emoji, :tagline, :features, :q)
        ON CONFLICT (brand_key, model_key) DO UPDATE SET
            emoji = EXCLUDED.emoji, tagline = EXCLUDED.tagline,
            features = EXCLUDED.features, search_q = EXCLUDED.search_q,
            use_count = telegram_post_templates.use_count + 1,
            updated_at = now()
    """), {"bk": bk, "mk": mk, "emoji": emoji, "tagline": tagline,
           "features": json.dumps(features, ensure_ascii=False), "q": search_q})


# ── Розбір історичного поста (коли памʼяті ще нема, а пост уже був) ──────────

_HEAD_RE = re.compile(r"^\s*\**\s*(?P<emoji>[^\w\s*\[]{1,6})", re.UNICODE)
_TAGLINE_RE = re.compile(r"•\s*(?P<tag>[^*\n]+)", re.UNICODE)
_FEATURE_RE = re.compile(r"^▪️\s*(?P<f>.+?)\s*$", re.MULTILINE | re.UNICODE)
_SEARCH_RE = re.compile(r"google\.com/search\?q=(?P<q>[^)\s]+)", re.UNICODE)


def _balance_markdown(s: str) -> str:
    """Закрити розмітку, обірвану на межі рядка.

    У реальних постах закривальні `**` часто стоять на НАСТУПНОМУ рядку
    (`**__EVA__\\n**`). Витягуючи один рядок «▪️», ми відрізаємо цей хвіст —
    і Telegram отримує непарні маркери, які зʼїдають половину тексту.
    """
    s = str(s or "").rstrip()
    s = re.sub(r"(?<!\*)\*(?!\*)$", "", s).rstrip()   # самотня зірочка в кінці
    for marker in ("**", "__"):
        if s.count(marker) % 2 == 1:
            s += marker
    return s.strip()


def parse_existing_post(message_text: str) -> dict:
    """Витягти емодзі / хвіст заголовка / «▪️» рядки з уже опублікованого поста.

    Дає переписати текст для товару тієї ж моделі, навіть якщо памʼять
    (`telegram_post_templates`) ще не заповнена — історія в `telegram_posts`
    накопичена за роки й це найточніше джерело «як власник це формулює».

    `search_q` витягуємо лише для довідки: підставляти його іншому товару НЕ
    можна — це артикул конкретної речі, а не моделі (у «Teva ReFlip» різні
    забарвлення мають різні маркування).
    """
    out: dict = {"emoji": None, "tagline": None, "features": [], "search_q": None}
    if not message_text:
        return out
    first = message_text.split("\n", 1)[0]
    m = _HEAD_RE.match(first)
    if m:
        out["emoji"] = m.group("emoji").strip()
    m = _TAGLINE_RE.search(first)
    if m:
        out["tagline"] = m.group("tag").strip().rstrip("*").strip()
    out["features"] = [
        f for f in (_balance_markdown(x) for x in _FEATURE_RE.findall(message_text)) if f
    ]
    m = _SEARCH_RE.search(message_text)
    if m:
        out["search_q"] = m.group("q")
    return out


def _from_history(db: Session, bms: dict) -> Optional[dict]:
    """Найсвіжіший опублікований пост товару тієї ж моделі того ж бренду."""
    bk, mk = _template_key(bms)
    if not mk:
        return None
    row = db.execute(text("""
        SELECT tp.message_text
        FROM telegram_posts tp
        JOIN products p ON p.id = tp.product_id
        LEFT JOIN brands b ON b.id = p.brandid
        WHERE lower(trim(COALESCE(b.brandname, ''))) = :bk
          AND lower(trim(COALESCE(p.model, ''))) = :mk
          AND tp.message_text IS NOT NULL AND tp.message_text <> ''
          AND p.id <> :pid
        ORDER BY tp.message_date DESC NULLS LAST
        LIMIT 1
    """), {"bk": bk, "mk": mk, "pid": bms["id"]}).fetchone()
    if not row:
        return None
    parsed = parse_existing_post(row[0])
    if not (parsed.get("tagline") or parsed.get("features")):
        return None
    parsed["source"] = "history"
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Гілки форуму
# ─────────────────────────────────────────────────────────────────────────────

_SEASON_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ЛІТО": ("літо",),
    "ДЕМІ": ("демі",),
    "ЗИМА": ("зима", "єврозима"),
}
_GENDER_WORDS: Dict[str, Tuple[str, ...]] = {
    "ЖІНОЧ": ("жін",),
    "ЧОЛОВІЧ": ("чол",),
    "ДИТЯЧ": (),          # визначається не статтю, а _is_kids
}


def _thread_rule(title: str) -> dict:
    """Правило автопідбору, прочитане з НАЗВИ гілки.

    Назви в форумі самоописові («ЛІТО | ЖІНОЧІ», «КРОСІВКИ | ДИТЯЧІ», «HOKA»),
    тому мапа виводиться з них, а не задається руками. Ручні перевизначення
    все одно можливі через telegram_thread_mapping.
    """
    up = (title or "").upper()
    rule: dict = {"season": None, "gender": None, "types": (), "brand": None, "kids": False}
    for season, _ in _SEASON_ALIASES.items():
        if season in up:
            rule["season"] = season
    for gword in _GENDER_WORDS:
        if gword in up:
            rule["gender"] = gword
    if "ДИТЯЧ" in up:
        rule["kids"] = True
    if "КРОСІВКИ" in up:
        rule["types"] = _TYPE_GROUPS["кросівки"]
    elif "ТУФЛІ" in up:
        rule["types"] = _TYPE_GROUPS["туфлі"]
    elif "СУМКИ" in up or "РЮКЗАК" in up:
        rule["types"] = _TYPE_GROUPS["сумки"]
    elif "ВЕРХНІЙ ОДЯГ" in up:
        rule["types"] = _TYPE_GROUPS["верхній одяг"]
    # Гілка-бренд: назва без роздільника «|» і без сезону/типу вище.
    if "|" not in up and not rule["season"] and not rule["types"] and up.strip():
        rule["brand"] = up.strip()
    return rule


# Тип сам по собі визначає сезон, коли поле `season` цього не каже. Босоніжки
# й шльопанці масово стоять як «Всесезон» (8181 товар — це значення за
# замовчуванням), але власник кладе їх у «ЛІТО | …». Без цього правила
# найлітніший товар не отримував жодної пропозиції.
_TYPE_SEASON: Dict[str, str] = {
    "босоніжки": "ЛІТО", "шльопанці": "ЛІТО", "сандалі": "ЛІТО",
    "сандалії": "ЛІТО", "сабо": "ЛІТО", "тапки": "ЛІТО",
    "сапоги": "ЗИМА", "напівсапоги": "ЗИМА",
}
_NEUTRAL_SEASONS = {"всесезон", ""}


def _effective_seasons(bms: dict) -> List[str]:
    """Сезон гілки, до якої пасує товар.

    Поле `season` часто перелічує кілька («Демі, Літо, Всесезон»), але власник
    кладе товар у ПЕРШИЙ названий сезон — саме тому HOKA з «Демі, Літо…» пішла
    в «ДЕМІ | ЧОЛОВІЧІ», а не ще й у «ЛІТО». Беремо перший за порядком у полі.
    Якщо поле нейтральне — сезон підказує тип (босоніжки = літо).
    """
    raw = _low(bms.get("season"))
    typename = _low(bms.get("typename"))
    ordered: List[str] = []
    for part in [p.strip() for p in raw.split(",")]:
        for key, words in _SEASON_ALIASES.items():
            if key not in ordered and any(w in part for w in words):
                ordered.append(key)
    # «ЛІТО | …» — гілка ВІДКРИТОГО літнього взуття: з 57 постів там 55 це
    # шльопанці й босоніжки. Кросівки з сезоном «Літо» власник туди не кладе,
    # вони йдуть у «КРОСІВКИ | …». Тому літо за полем сезону визнаємо лише
    # для відкритих типів; закриті — за типом (ДЕМІ/ЗИМА лишаються як є.)
    if ordered[:1] == ["ЛІТО"] and _TYPE_SEASON.get(typename) != "ЛІТО":
        ordered = ordered[1:]
    if ordered:
        return ordered[:1]
    if all(part.strip() in _NEUTRAL_SEASONS for part in raw.split(",")):
        by_type = _TYPE_SEASON.get(_low(bms.get("typename")))
        return [by_type] if by_type else []
    return []


def _size_num(value: Any) -> Optional[float]:
    m = re.match(r"\d+(?:[.,]\d+)?", str(value or "").strip())
    return float(m.group(0).replace(",", ".")) if m else None


def suggest_threads(bms: dict, threads: List[dict],
                    sizes: Optional[List[dict]] = None) -> List[int]:
    """Які гілки запропонувати для цього товару (галочки в діалозі за замовч.).

    Пропозиція — не автоматизм: у діалозі все видно й перемикається. Уцінку та
    приховані/закриті гілки не пропонуємо ніколи.

    `sizes` — розміри, які підуть у пост. Ростовка часто перекриває і дитячу, і
    дорослу сітку (Crocs 29–45): такий пост власник кладе І в «ЛІТО | ДИТЯЧІ»,
    І в дорослі гілки. Оцінювати «дитячість» по одному рядку товару не можна.
    """
    typename = _low(bms.get("typename"))
    gender = _low(bms.get("gendername"))
    brand = _low(bms.get("brandname"))
    seasons = _effective_seasons(bms)
    is_female = gender.startswith("жін")
    is_male = gender.startswith("чол")
    # «Унісекс» (3714 товарів) власник кладе В ОБИДВІ статеві гілки — саме так
    # пішли, наприклад, клоги ECCO Cozmo. Тому унісекс підходить і туди, і туди.
    unisex = not is_female and not is_male

    nums = [n for n in (_size_num(s.get("size")) for s in (sizes or [])) if n is not None]
    if nums:
        has_kids_sizes = any(n <= 34.5 for n in nums)
        has_adult_sizes = any(n > 34.5 for n in nums)
    else:
        # Без розмірів (сумки) або без даних — покладаємось на ознаку товару.
        has_kids_sizes = _is_kids(bms)
        has_adult_sizes = not has_kids_sizes
    picked: List[int] = []

    for th in threads:
        if th.get("thread_id") == ROOT_TOPIC_ID or not th.get("auto_suggest", True):
            continue
        title_up = (th.get("thread_title") or "").upper()
        if "УЦІНКА" in title_up or "РОЗПРОДАЖ" in title_up:
            continue
        rule = _thread_rule(th.get("thread_title") or "")

        if rule["brand"]:
            if brand and rule["brand"].lower() == brand:
                picked.append(th["thread_id"])
            continue

        # Дитяча гілка — лише якщо в пості є дитячі розміри; доросла статева —
        # лише якщо є дорослі.
        if rule["kids"]:
            if not has_kids_sizes:
                continue
        elif rule["gender"]:
            if not has_adult_sizes:
                continue
            if rule["gender"] == "ЖІНОЧ" and not (is_female or unisex):
                continue
            if rule["gender"] == "ЧОЛОВІЧ" and not (is_male or unisex):
                continue

        if rule["types"]:
            if typename in rule["types"]:
                picked.append(th["thread_id"])
            continue
        if rule["season"] and rule["season"] in seasons:
            picked.append(th["thread_id"])
    return picked


def get_threads(db: Session) -> List[dict]:
    """Гілки форуму з локального кешу (telegram_thread_mapping)."""
    rows = db.execute(text("""
        SELECT thread_id, thread_title, auto_suggest, sort_order
        FROM telegram_thread_mapping
        WHERE chat_id = :cid AND thread_id IS NOT NULL
        ORDER BY sort_order, thread_title
    """), {"cid": FORUM_CHAT_ID}).mappings().all()
    return [dict(r) for r in rows]


def _save_threads(db: Session, topics: List[Tuple[int, str, bool]]) -> int:
    """Оновити кеш гілок. `topics` = [(id, title, hidden_or_closed)]."""
    saved = 0
    for order, (tid, title, skip) in enumerate(topics):
        db.execute(text("""
            INSERT INTO telegram_thread_mapping (chat_id, thread_id, thread_title, auto_suggest, sort_order, updated_at)
            VALUES (:cid, :tid, :title, :auto, :ord, now())
            ON CONFLICT (chat_id, thread_id) DO UPDATE SET
                thread_title = EXCLUDED.thread_title,
                sort_order = EXCLUDED.sort_order,
                updated_at = now()
        """), {"cid": FORUM_CHAT_ID, "tid": tid, "title": title,
               "auto": not skip, "ord": order})
        saved += 1
    db.commit()
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# Фото
# ─────────────────────────────────────────────────────────────────────────────

def _photo_entries(bms: dict, limit: Optional[int] = None) -> Tuple[List[Any], str]:
    """Фото товару для альбому: офіційні мають абсолютний пріоритет над реальними
    (те саме табу, що в Prom — не змішуємо студійні з «як є»). Дефектні не йдуть
    у пост ніколи. Порядок — натуральний за номером у назві файлу, тож перший
    кадр збігається з головним фото картки.

    `limit=None` означає «віддати ВСІ придатні» — прев'ю показує повний набір,
    щоб людина обрала, які саме поїдуть; обрізає вже публікація.
    """
    try:
        from services.product_images import list_images
    except ImportError:
        from backend.services.product_images import list_images
    pnum = bms.get("productnumber") or ""
    imgs = list_images(pnum)
    cut = (lambda xs: xs[:limit] if limit else xs)

    official = [i for i in imgs if getattr(i, "kind", "") == "official"]
    if official:
        return cut(official), "official"
    donor = (bms.get("official_photos_from") or "").strip()
    if donor and donor.lstrip("#").lower() != pnum.lstrip("#").lower():
        donor_official = [i for i in list_images(donor) if getattr(i, "kind", "") == "official"]
        if donor_official:
            return cut(donor_official), "official"
    real = [i for i in imgs if getattr(i, "kind", "") != "defect"]
    return cut(real), ("real" if real else "none")


# ─────────────────────────────────────────────────────────────────────────────
# Прев'ю (нічого не створює)
# ─────────────────────────────────────────────────────────────────────────────

def preview_post(db: Session, product_id: int) -> dict:
    bms = _load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}

    pnum = bms.get("productnumber") or ""
    sizes = _available_sizes(db, pnum)
    photos, image_kind = _photo_entries(bms)          # усі придатні — вибір за людиною

    seed = load_template(db, bms) or _from_history(db, bms) or {}
    emoji = seed.get("emoji") or default_emoji(bms)
    tagline = seed.get("tagline") or default_tagline(bms)
    features = seed.get("features") or default_features(bms)
    # search_q НІКОЛИ не беремо з памʼяті/історії: там артикул іншої речі тієї ж
    # моделі («ReFlip Canvas 1134375» замість власного 1124051) — посилання вело б
    # покупця на чуже забарвлення.
    search_q = default_search_q(bms)

    threads = get_threads(db)
    suggested = suggest_threads(bms, threads, sizes)

    warnings: List[str] = []
    if image_kind == "none":
        warnings.append("У товару немає фото — пост без альбому Telegram не публікує.")
    elif image_kind == "real":
        warnings.append("Немає офіційних фото — пост піде з РЕАЛЬНИМИ.")
    if len(photos) < ALBUM_LIMIT and image_kind != "none":
        warnings.append(
            f"Фото лише {len(photos)}, а у твоїх постах їх зазвичай {ALBUM_LIMIT}."
        )
    if not features:
        warnings.append("Переваги («▪️») порожні — напиши хоча б один рядок.")
    if not tagline:
        warnings.append("Порожній хвіст заголовка після «• ».")
    if not _fmt_price(bms.get("price")):
        warnings.append("У товару не вказана ціна.")
    if not sizes and not _is_bag(bms):
        warnings.append("Немає доступних розмірів у наявності.")
    if not threads:
        warnings.append("Список гілок форуму порожній — натисни «Оновити гілки».")

    # Один Telegram-пост представляє весь номер/ростовку. Тому живі пости,
    # прив'язані до будь-якого рядка того самого productnumber, мають блокувати
    # повтор і показуватись у прев'ю кожного розміру.
    already = db.execute(text("""
        SELECT COUNT(*) FROM telegram_posts tp
        JOIN products sibling ON sibling.id = tp.product_id
        WHERE sibling.productnumber = :pnum AND tp.tg_status = 'published'
    """), {"pnum": pnum}).scalar() or 0

    caption = build_caption(
        emoji=emoji, brand=bms.get("brandname") or "", model=bms.get("model") or "",
        search_q=search_q, tagline=tagline, size_block=_size_block(bms, sizes),
        features=features, condition=_condition_line(bms),
        price=_fmt_price(bms.get("price")), productnumber=pnum,
    )

    return {
        "ok": True,
        "product_id": product_id,
        "productnumber": pnum.lstrip("#"),
        "brand": bms.get("brandname"),
        "model": bms.get("model"),
        "type": bms.get("typename"),
        "emoji": emoji,
        "tagline": tagline,
        "features": features,
        "search_q": search_q,
        "condition": _condition_line(bms),
        "price": _fmt_price(bms.get("price")),
        "sizes": sizes,
        "is_bag": _is_bag(bms),
        "dimensions": _normalize_dimensions(bms.get("dimensions")),
        "caption": caption,
        "caption_len": len(caption),
        "caption_limit": CAPTION_LIMIT,
        "image_count": len(photos),
        "image_kind": image_kind,
        "image_urls": [getattr(p, "url", "") for p in photos],
        "image_names": [getattr(p, "filename", "") for p in photos],
        "album_limit": ALBUM_LIMIT,
        "album_hard_limit": ALBUM_HARD_LIMIT,
        "max_threads_per_post": MAX_THREADS_PER_POST,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        # Перші ALBUM_LIMIT у натуральному порядку — рівно те, що пішло б без
        # втручання; решту людина може підмінити кліком.
        "default_image_idx": list(range(min(len(photos), ALBUM_LIMIT))),
        "archive": {"configured": bool(ARCHIVE_CHAT), "title": ARCHIVE_TITLE},
        "threads": threads,
        "suggested_threads": suggested,
        "root_topic": {"thread_id": ROOT_TOPIC_ID, "thread_title": ROOT_TOPIC_TITLE},
        "channel": {"chat_id": CHANNEL_CHAT_ID, "chat_title": CHANNEL_TITLE},
        "default_channel_at": _next_morning().isoformat(),
        "already_published": int(already),
        "seed_source": seed.get("source"),
        "warnings": warnings,
    }


def preview_posts_batch(db: Session, product_ids: List[int]) -> dict:
    """Прев'ю виділення з «Товарів», згруповане за номером товару.

    Ростовка може містити кілька рядків/розмірів, але це один пост. Повертаємо
    representative product_id плюс source_product_ids — фронт чесно показує,
    скільки виділених рядків було об'єднано.
    """
    if len(product_ids) > 200:
        return {"ok": False, "error": "За один раз можна обробити до 200 виділених рядків"}
    clean_ids: List[int] = []
    seen_ids = set()
    for raw in product_ids:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in seen_ids:
            seen_ids.add(pid)
            clean_ids.append(pid)

    grouped: "OrderedDict[str, dict]" = OrderedDict()
    missing: List[int] = []
    for pid in clean_ids:
        bms = _load_product(db, pid)
        if not bms:
            missing.append(pid)
            continue
        pnum = str(bms.get("productnumber") or "").strip()
        key = pnum.lstrip("#").casefold() or f"id:{pid}"
        if key not in grouped:
            grouped[key] = {"product_id": pid, "source_product_ids": [], "productnumber": pnum}
        grouped[key]["source_product_ids"].append(pid)

    if not grouped:
        return {"ok": False, "error": "Серед виділених рядків не знайдено жодного товару"}

    if len(grouped) > BATCH_MAX_PRODUCTS:
        return {
            "ok": False,
            "error": (
                f"За один безпечний пакет можна опублікувати до {BATCH_MAX_PRODUCTS} "
                f"унікальних товарів. Зараз після об'єднання ростовок — {len(grouped)}."
            ),
        }

    items = []
    for group in grouped.values():
        preview = preview_post(db, group["product_id"])
        items.append({
            **group,
            "ok": bool(preview.get("ok")),
            "preview": preview if preview.get("ok") else None,
            "error": preview.get("error") if not preview.get("ok") else None,
        })
    return {
        "ok": True,
        "selected_count": len(clean_ids),
        "unique_count": len(items),
        "merged_count": max(0, len(clean_ids) - len(items)),
        "missing_ids": missing,
        "batch_max_products": BATCH_MAX_PRODUCTS,
        "items": items,
    }


def rebuild_caption(db: Session, product_id: int, parts: dict) -> dict:
    """Перезібрати підпис із відредагованих частин.

    Живе прев'ю в діалозі мусить показувати РІВНО той текст, що піде в Telegram,
    тому шаблон складається на бекенді — єдиному місці, де він описаний.
    `size_ids` — які розміри ростовки лишити в пості (порожньо = всі доступні).
    """
    bms = _load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}
    pnum = bms.get("productnumber") or ""

    sizes = _available_sizes(db, pnum)
    keep = parts.get("size_ids")
    if isinstance(keep, list) and keep:
        keep_set = {int(x) for x in keep}
        sizes = [s for s in sizes if s["product_id"] in keep_set]

    caption = build_caption(
        emoji=str(parts.get("emoji") or default_emoji(bms)),
        brand=bms.get("brandname") or "",
        model=bms.get("model") or "",
        search_q=str(parts.get("search_q") or default_search_q(bms)),
        tagline=str(parts.get("tagline") or ""),
        size_block=_size_block(bms, sizes),
        features=[str(f) for f in (parts.get("features") or []) if str(f).strip()],
        condition=_condition_line(bms),
        price=_fmt_price(parts.get("price") if parts.get("price") not in (None, "") else bms.get("price")),
        productnumber=pnum,
    )
    return {
        "ok": True,
        "caption": caption,
        "caption_len": len(caption),
        "caption_limit": CAPTION_LIMIT,
        "problem": validate_caption(caption),
    }


def _next_morning(hour: int = 8, minute: int = 0) -> datetime:
    """Найближчі 08:00 за київським часом — ритм, у якому канал наповнюється роками."""
    now = datetime.now(KYIV_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now + timedelta(minutes=5):
        target += timedelta(days=1)
    return target


def product_status(db: Session, product_id: int) -> dict:
    """Де товар уже є в Telegram — для чіпа в картці й кнопки в таблиці."""
    rows = db.execute(text("""
        SELECT tp.chat_id, tp.chat_title, tp.thread_id, tp.thread_title,
               tp.message_id, tp.message_date
        FROM telegram_posts tp
        JOIN products linked ON linked.id = tp.product_id
        JOIN products requested ON requested.id = :pid
        WHERE linked.productnumber = requested.productnumber
          AND tp.tg_status = 'published'
        ORDER BY tp.message_date DESC NULLS LAST
    """), {"pid": product_id}).mappings().all()
    sched = db.execute(text("""
        SELECT tsp.chat_title, tsp.scheduled_at FROM telegram_scheduled_posts tsp
        JOIN products linked ON linked.id = tsp.product_id
        JOIN products requested ON requested.id = :pid
        WHERE linked.productnumber = requested.productnumber
          AND tsp.state = 'scheduled'
        ORDER BY tsp.scheduled_at
    """), {"pid": product_id}).mappings().all()
    return {
        "ok": True,
        "on_telegram": len(rows) > 0,
        "posts": [dict(r, message_date=r["message_date"].isoformat() if r["message_date"] else None)
                  for r in rows],
        "scheduled": [dict(s, scheduled_at=s["scheduled_at"].isoformat()) for s in sched],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Публікація (ЗАПИС у Telegram)
# ─────────────────────────────────────────────────────────────────────────────

# ── Markdown → HTML ──────────────────────────────────────────────────────────
#
# ⚠️ Підпис НЕ можна віддавати Telegram із `parse_mode="md"`. Парсер Markdown у
# Telethon ламається на посиланні всередині жирного:
#
#   `**A [L](url) • t**\n\nдалі **жирне**`  →  Bold накриває 20 символів,
#   тобто тягнеться ПОЗА заголовок, аж до наступних зірочок нижче в пості.
#
# А якщо винести жирний усередину підпису лінка (`[**Модель**](url)`, саме так
# пишуть у клієнті Telegram), парсер лишає зірочки ВИДИМИМ текстом — у каналі
# виходить «Teva **ReFlip**».
#
# HTML-парсер Telethon вкладеність тримає коректно, тому конвертуємо самі:
# граматика тут наша й обмежена, тож перетворення однозначне. Для людини в
# діалозі синтаксис лишається звичним Markdown.
_MD_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_ITALIC_RE = re.compile(r"__(.+?)__", re.DOTALL)


def md_to_html(text: str) -> str:
    """Наш підмножинний Markdown → HTML, який розуміє Telegram."""
    out = (str(text or "")
           .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    out = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    # Посилання — ДО жирного: тоді `**` усередині підпису стануть <b> усередині
    # <a>, а `**`, що обгортають лінк, — <b> навколо <a>. Обидва варіанти валідні.
    out = _MD_LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>' if m.group(2) else m.group(1),
        out)
    out = _MD_BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _MD_ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", out)
    return out


def validate_caption(caption: str) -> Optional[str]:
    """Чи переживе текст розмітку Telegram. Помилка тут краща за покалічений
    пост у каналі з тисячею підписників.

    Перевіряємо саме ТИМ парсером, яким підпис піде в Telegram (HTML), і
    звіряємо, що жодні маркери не лишились видимим текстом.
    """
    if not caption.strip():
        return "Порожній текст поста"
    if len(caption) > CAPTION_LIMIT:
        return f"Текст задовгий: {len(caption)} символів, ліміт Telegram — {CAPTION_LIMIT}"
    try:
        from telethon.extensions import html as tg_html
        plain, _ = tg_html.parse(md_to_html(caption))
    except ImportError:
        return None
    except Exception as exc:
        return f"Розмітка не розбирається: {exc}"
    if not plain.strip():
        return "Після розбору розмітки текст порожній — перевір зірочки й дужки"
    if "**" in plain or "__" in plain:
        return "Непарні зірочки або підкреслення — вони покажуться в пості як текст"
    return None


# Скільки максимум чекати на вільну сесію Telegram, перш ніж здатися.
# Штатне сканування каналів (фоновий цикл кожні 30 хв) триває ~1.5 хв і весь
# цей час тримає файл сесії — тож чекати менше означає падати саме тоді, коли
# людина натиснула «Опублікувати» в невдалу хвилину.
SESSION_WAIT_SEC = int(os.getenv("TELEGRAM_SESSION_WAIT_SEC", "210"))


async def _connect():
    """Підключення до Telegram, з очікуванням вільної сесії.

    Сесія Telethon — файл SQLite, один на машину, і фонові цикли синхронізації
    тримають його поки скануються канали. Тоді connect() падає з
    «database is locked». Це не поломка, а черга, тому чекаємо до
    SESSION_WAIT_SEC — публікація довша за секунду в будь-якому разі
    (заливка альбому), і чекати тут чесніше, ніж повертати помилку.
    """
    import asyncio
    import time

    try:
        from services.telegram_service import TelegramScanner
    except ImportError:
        from backend.services.telegram_service import TelegramScanner
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    if not all([api_id, api_hash, phone]):
        return None, "Telegram не налаштовано (TELEGRAM_API_ID / API_HASH / PHONE у .env)"

    deadline = time.monotonic() + SESSION_WAIT_SEC
    delay, attempt = 1.0, 0
    while True:
        attempt += 1
        scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
        try:
            if await scanner.connect():
                if attempt > 1:
                    logger.info("Telegram: сесія звільнилась із %s-ї спроби", attempt)
                return scanner, None
        except Exception as exc:
            logger.warning("Telegram connect спроба %s: %s", attempt, exc)
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 1.5, 8.0)
    return None, (
        f"Telegram зайнятий понад {SESSION_WAIT_SEC // 60} хв: файл сесії тримає інший процес "
        "(фонова синхронізація каналів або другий запущений застосунок). Спробуй ще раз."
    )


async def refresh_threads(db: Session) -> dict:
    """Перечитати живий список гілок форуму. Read-only щодо Telegram."""
    scanner, err = await _connect()
    if err:
        return {"ok": False, "error": err}
    try:
        from telethon.tl.types import PeerChannel
        from telethon.tl.functions.channels import GetForumTopicsRequest
        entity = await scanner.client.get_entity(PeerChannel(FORUM_CHAT_ID))
        topics: List[Tuple[int, str, bool]] = []
        off_d = off_i = off_t = 0
        while True:
            res = await scanner.client(GetForumTopicsRequest(
                channel=entity, offset_date=off_d, offset_id=off_i,
                offset_topic=off_t, limit=100, q=""))
            if not res.topics:
                break
            for t in res.topics:
                hidden = bool(getattr(t, "hidden", False) or getattr(t, "closed", False))
                topics.append((t.id, t.title, hidden))
            if len(res.topics) < 100:
                break
            last = res.topics[-1]
            off_i, off_t, off_d = last.top_message, last.id, getattr(last, "date", 0)
        saved = _save_threads(db, topics)
        return {"ok": True, "threads": saved, "items": get_threads(db)}
    except Exception as exc:
        logger.error("refresh_threads failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    finally:
        await scanner.disconnect()


def _record_post(db: Session, *, product_id: int, pnum: str, chat_id: int,
                 chat_title: str, chat_type: str, thread_id: Optional[int],
                 thread_title: Optional[str], message_id: int, caption: str,
                 grouped_id: Optional[int], sizes: List[str]) -> None:
    """Одразу писати створений пост у telegram_posts, не чекаючи скану.

    Інакше товар до наступної синхронізації виглядав би «не опублікованим» —
    і його легко було б опублікувати вдруге.
    """
    db.execute(text("""
        INSERT INTO telegram_posts (
            product_id, product_number_raw, chat_id, chat_title, chat_type,
            thread_id, thread_title, message_id, message_text, message_date,
            sizes_in_post, is_multi_size, grouped_id, tg_status
        ) VALUES (
            :pid, :pnum, :cid, :ctitle, :ctype, :tid, :ttitle, :mid, :txt, :dt,
            :sizes, :multi, :gid, 'published'
        )
        ON CONFLICT (chat_id, message_id) DO UPDATE SET
            product_id = EXCLUDED.product_id,
            message_text = EXCLUDED.message_text,
            sizes_in_post = EXCLUDED.sizes_in_post,
            is_multi_size = EXCLUDED.is_multi_size,
            tg_status = 'published'
    """), {
        "pid": product_id, "pnum": (pnum or "").lstrip("#").lstrip("Ф").lstrip("ф") or pnum,
        "cid": chat_id, "ctitle": chat_title, "ctype": chat_type,
        "tid": thread_id, "ttitle": thread_title, "mid": message_id,
        "txt": caption[:2000], "dt": datetime.utcnow(),
        "sizes": json.dumps(sizes, ensure_ascii=False), "multi": len(sizes) > 1,
        "gid": None,
    })


async def create_post(db: Session, product_id: int, payload: dict, *,
                      _scanner: Any = None, _lock_held: bool = False) -> dict:
    """Опублікувати товар у Telegram.

    payload: caption, thread_ids[], to_channel, channel_at (ISO або null = зараз),
             image_idx[] (які фото і в якому порядку), test_mode (репетиція
             у WORKSHOP), emoji/tagline/features/search_q (щоб запамʼятати текст
             моделі), silent (все без звуку), force (публікувати попри вже
             наявні пости).
    """
    # Одна Telegram user-session не повинна одночасно виконувати дві ручні
    # публікації (single або batch). Внутрішні виклики batch уже тримають lock.
    if not _lock_held:
        async with _PUBLISH_LOCK:
            return await create_post(
                db, product_id, payload, _scanner=_scanner, _lock_held=True,
            )

    bms = _load_product(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}
    pnum = bms.get("productnumber") or ""

    caption = (payload.get("caption") or "").strip()
    problem = validate_caption(caption)
    if problem:
        return {"ok": False, "error": problem}

    # Підпис іде в Telegram у HTML — Markdown-парсер Telethon ламає посилання
    # всередині жирного (див. md_to_html). У діалозі людина далі бачить Markdown.
    caption_html = md_to_html(caption)

    # ── Репетиція: усе йде в приватний WORKSHOP і нікуди більше ──────────────
    test_mode = bool(payload.get("test_mode"))
    silent = bool(payload.get("silent"))
    if test_mode and not ARCHIVE_CHAT:
        return {"ok": False,
                "error": "TELEGRAM_ARCHIVE_CHAT не заданий у .env — немає куди слати тестовий пост"}

    # У тестовому режимі перевірка «вже опубліковано» не має сенсу: ми нічого
    # не публікуємо в каталог і нічого не записуємо в telegram_posts.
    if not test_mode and not payload.get("force"):
        live = db.execute(text("""
            SELECT COUNT(*) FROM telegram_posts tp
            JOIN products sibling ON sibling.id = tp.product_id
            WHERE sibling.productnumber = :pnum AND tp.tg_status = 'published'
        """), {"pnum": pnum}).scalar() or 0
        if live:
            return {"ok": False, "already_published": True,
                    "error": f"Товар уже має {live} живих постів у Telegram"}

    all_photos, image_kind = _photo_entries(bms)
    # Вибір людини (індекси у порядку, як їх обрали) — інакше перші ALBUM_LIMIT.
    idx = payload.get("image_idx")
    if isinstance(idx, list) and idx:
        photos = [all_photos[i] for i in (int(x) for x in idx) if 0 <= i < len(all_photos)]
    else:
        photos = all_photos[:ALBUM_LIMIT]
    photos = photos[:ALBUM_HARD_LIMIT]
    if not photos:
        return {"ok": False, "error": "У товару немає фото — Telegram не прийме пост без альбому"}

    try:
        thread_ids = list(dict.fromkeys(
            int(t) for t in (payload.get("thread_ids") or []) if int(t) != ROOT_TOPIC_ID
        ))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Некоректний ідентифікатор гілки Telegram"}
    if len(thread_ids) > MAX_THREADS_PER_POST:
        return {
            "ok": False,
            "error": f"Для одного поста можна обрати до {MAX_THREADS_PER_POST} тематичних гілок",
        }
    thread_titles = {t["thread_id"]: t["thread_title"] for t in get_threads(db)}
    unknown_threads = [tid for tid in thread_ids if tid not in thread_titles]
    if unknown_threads:
        return {
            "ok": False,
            "error": "Одна або кілька гілок уже не існують. Онови список гілок в Інтеграціях.",
        }

    channel_when: Optional[datetime] = None
    if payload.get("to_channel"):
        channel_when, when_error = _validate_when(payload.get("channel_at"))
        if when_error:
            return {"ok": False, "error": when_error}
    # Розміри, записані в telegram_posts, мусять збігтися з тими, що в тексті:
    # саме за ними знімалка потім вирішує, редагувати пост чи видаляти.
    avail = _available_sizes(db, pnum)
    keep = payload.get("size_ids")
    if isinstance(keep, list) and keep:
        keep_set = {int(x) for x in keep}
        avail = [s for s in avail if s["product_id"] in keep_set]
    sizes = [s["size"] for s in avail if s.get("size")]

    scanner = _scanner
    owns_scanner = scanner is None
    if owns_scanner:
        scanner, err = await _connect()
        if err:
            return {"ok": False, "error": err}

    result: Dict[str, Any] = {
        "ok": True, "product_id": product_id, "productnumber": pnum.lstrip("#"),
        "root_message_id": None, "threads_posted": [], "channel": None,
        "image_count": len(photos), "image_kind": image_kind, "failed": [],
        "test_mode": test_mode, "silent": silent,
    }

    try:
        from telethon.tl.types import PeerChannel
        from starlette.concurrency import run_in_threadpool

        # Читання з диска + перекодування 5 кадрів у JPEG — це Pillow і сотні
        # мілісекунд CPU. У корутині воно б заморозило ВЕСЬ бекенд (uvicorn —
        # один цикл подій), тому йде в пул потоків.
        files = await run_in_threadpool(_read_photo_bytes, photos)
        if not files:
            return {"ok": False, "error": "Не вдалося прочитати файли фото товару"}

        # ── Репетиція: один альбом у WORKSHOP, і на цьому все ────────────────
        if test_mode:
            archive = await scanner._resolve_entity(ARCHIVE_CHAT)
            msgs = await scanner.client.send_file(
                archive, files, caption=caption_html, parse_mode="html", album=True,
                silent=silent,
            )
            msgs = msgs if isinstance(msgs, list) else [msgs]
            result.update({
                "root_message_id": msgs[0].id,
                "archive_title": ARCHIVE_TITLE,
                "note": "Тестовий пост. У каталог і канал НЕ пішов, у базі не записаний.",
            })
            return result

        forum = await scanner.client.get_entity(PeerChannel(FORUM_CHAT_ID))

        # ── 1. Оригінал у «ВСІ ПРОПОЗИЦІЇ» ──────────────────────────────────
        root_msgs = await scanner.client.send_file(
            forum, files, caption=caption_html, parse_mode="html", album=True,
            silent=silent,
        )
        root_msgs = root_msgs if isinstance(root_msgs, list) else [root_msgs]
        head = root_msgs[0]
        result["root_message_id"] = head.id
        _record_post(db, product_id=product_id, pnum=pnum, chat_id=FORUM_CHAT_ID,
                     chat_title=FORUM_TITLE, chat_type="forum", thread_id=None,
                     thread_title=None, message_id=head.id, caption=caption,
                     grouped_id=getattr(head, "grouped_id", None), sizes=sizes)
        db.commit()

        # ── 2. Копії в тематичні гілки ──────────────────────────────────────
        # Фото вже в Telegram — передаємо готові обʼєкти, повторної заливки нема.
        media = [m.photo for m in root_msgs if getattr(m, "photo", None)]
        rate_limited = False
        for tid in thread_ids:
            try:
                if _scanner is not None:
                    await asyncio.sleep(BATCH_DESTINATION_GAP_SEC)
                msgs = await scanner.client.send_file(
                    forum, media or files, caption=caption_html, parse_mode="html",
                    album=True, reply_to=tid, silent=silent,
                )
                msgs = msgs if isinstance(msgs, list) else [msgs]
                first = msgs[0]
                _record_post(db, product_id=product_id, pnum=pnum, chat_id=FORUM_CHAT_ID,
                             chat_title=FORUM_TITLE, chat_type="forum", thread_id=tid,
                             thread_title=thread_titles.get(tid), message_id=first.id,
                             caption=caption, grouped_id=getattr(first, "grouped_id", None),
                             sizes=sizes)
                db.commit()
                result["threads_posted"].append(
                    {"thread_id": tid, "thread_title": thread_titles.get(tid),
                     "message_id": first.id})
            except Exception as exc:
                logger.warning("Не вдалось опублікувати в гілку %s: %s", tid, exc)
                result["failed"].append({"thread_id": tid,
                                         "thread_title": thread_titles.get(tid),
                                         "error": str(exc)})
                if _is_rate_limit_error(exc):
                    rate_limited = True
                    break

        # ── 3. Канал BrandStore — форвардом (атрибуція веде в каталог) ───────
        if payload.get("to_channel") and rate_limited:
            result["failed"].append({
                "channel": CHANNEL_TITLE,
                "error": "Не надсилали після FloodWait/SlowMode у попередній гілці",
            })
        elif payload.get("to_channel"):
            if _scanner is not None:
                await asyncio.sleep(BATCH_DESTINATION_GAP_SEC)
            channel = await scanner.client.get_entity(PeerChannel(CHANNEL_CHAT_ID))
            when = channel_when
            album_ids = [m.id for m in root_msgs]
            try:
                await scanner.client.forward_messages(
                    entity=channel, messages=album_ids, from_peer=forum, schedule=when,
                    silent=silent,
                )
                if when:
                    # Заплановане повідомлення живе в окремому просторі id і
                    # отримає справжній лише після відправки — тому в
                    # telegram_posts не пишемо, чекаємо на штатний скан.
                    db.execute(text("""
                        INSERT INTO telegram_scheduled_posts
                            (product_id, product_number, chat_id, chat_title,
                             scheduled_at, source_chat_id, source_message_id)
                        VALUES (:pid, :pnum, :cid, :ctitle, :at, :scid, :smid)
                    """), {"pid": product_id, "pnum": pnum, "cid": CHANNEL_CHAT_ID,
                           "ctitle": CHANNEL_TITLE, "at": when,
                           "scid": FORUM_CHAT_ID, "smid": head.id})
                    db.commit()
                    result["channel"] = {"scheduled_at": when.isoformat()}
                else:
                    result["channel"] = {"scheduled_at": None, "sent": True}
            except Exception as exc:
                logger.warning("Форвард у канал не вдався: %s", exc)
                result["failed"].append({"channel": CHANNEL_TITLE, "error": str(exc)})

        # ── 4. Запамʼятати формулювання для наступного товару цієї моделі ───
        try:
            save_template(
                db, bms,
                emoji=str(payload.get("emoji") or default_emoji(bms)),
                tagline=str(payload.get("tagline") or ""),
                features=[str(f) for f in (payload.get("features") or []) if str(f).strip()],
                search_q=str(payload.get("search_q") or default_search_q(bms)),
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Не вдалося зберегти шаблон моделі: %s", exc)

        return result
    except Exception as exc:
        logger.error("create_post failed for %s: %s", product_id, exc)
        return {"ok": False, "error": str(exc), "partial": result}
    finally:
        if owns_scanner:
            await scanner.disconnect()


def _is_rate_limit_error(value: Any) -> bool:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    up = raw.upper()
    return any(token in up for token in (
        "FLOOD_WAIT", "FLOODWAIT", "SLOWMODE_WAIT", "TOO MANY REQUESTS",
    ))


def _clean_batch_cache() -> None:
    cutoff = time.monotonic() - BATCH_CACHE_TTL_SEC
    for key, (created, _value) in list(_BATCH_CACHE.items()):
        if created < cutoff:
            _BATCH_CACHE.pop(key, None)
    while len(_BATCH_CACHE) > 50:
        _BATCH_CACHE.popitem(last=False)


def _preflight_batch_item(db: Session, product_id: int, payload: dict) -> Optional[str]:
    bms = _load_product(db, product_id)
    if not bms:
        return "Товар не знайдено"
    problem = validate_caption(str(payload.get("caption") or "").strip())
    if problem:
        return problem
    photos, _kind = _photo_entries(bms)
    idx = payload.get("image_idx")
    if isinstance(idx, list) and idx:
        valid_count = len({int(x) for x in idx if str(x).lstrip("-").isdigit() and 0 <= int(x) < len(photos)})
    else:
        valid_count = min(len(photos), ALBUM_LIMIT)
    if valid_count < 1:
        return "У товару немає вибраних фото"
    try:
        tids = list(dict.fromkeys(int(x) for x in (payload.get("thread_ids") or []) if int(x) != ROOT_TOPIC_ID))
    except (TypeError, ValueError):
        return "Некоректний ідентифікатор гілки Telegram"
    if len(tids) > MAX_THREADS_PER_POST:
        return f"Обрано понад {MAX_THREADS_PER_POST} тематичних гілок"
    known = {int(t["thread_id"]) for t in get_threads(db)}
    if any(t not in known for t in tids):
        return "Одна або кілька гілок уже не існують — онови їх в Інтеграціях"
    if payload.get("to_channel"):
        _when, error = _validate_when(payload.get("channel_at"))
        if error:
            return error
    return None


async def create_posts_batch(db: Session, items: List[dict], batch_id: str) -> dict:
    """Безпечна послідовна черга кількох відредагованих Telegram-постів.

    Увесь пакет спочатку валідовується, а потім використовує одне з'єднання.
    `batch_id` робить подвійний клік/повтор HTTP-відповіді ідемпотентним у межах
    процесу. Після FLOOD_WAIT хвіст позначається skipped і не надсилається.
    """
    batch_id = str(batch_id or "").strip()
    if not batch_id or len(batch_id) > 100:
        return {"ok": False, "error": "Некоректний batch_id"}
    _clean_batch_cache()
    cached = _BATCH_CACHE.get(batch_id)
    if cached:
        return {**cached[1], "replayed": True}
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Пакет порожній"}
    if len(items) > BATCH_MAX_PRODUCTS:
        return {
            "ok": False,
            "error": f"За один пакет можна опублікувати до {BATCH_MAX_PRODUCTS} унікальних товарів",
        }

    normalized: List[Tuple[int, dict, str]] = []
    seen_numbers = set()
    scheduled: List[Tuple[int, datetime]] = []
    for pos, item in enumerate(items):
        try:
            pid = int(item.get("product_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": f"Позиція {pos + 1}: немає product_id"}
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        bms = _load_product(db, pid)
        if not bms:
            return {"ok": False, "error": f"Позиція {pos + 1}: товар не знайдено"}
        pnum = str(bms.get("productnumber") or "").strip()
        key = pnum.lstrip("#").casefold() or f"id:{pid}"
        if key in seen_numbers:
            return {"ok": False, "error": f"Товар {pnum or pid} повторюється у пакеті"}
        seen_numbers.add(key)
        error = _preflight_batch_item(db, pid, payload)
        if error:
            return {"ok": False, "error": f"#{pnum.lstrip('#') or pid}: {error}"}
        if payload.get("to_channel") and payload.get("channel_at") is not None:
            when, _ = _validate_when(payload.get("channel_at"))
            if when:
                scheduled.append((pos, when))
        normalized.append((pid, payload, pnum))

    # Однаковий/майже однаковий час для кількох форвардів створює некерований
    # сплеск. Інтерфейс розставляє +2 хв, а бекенд не допускає випадкове
    # затирання цього кроку загальними налаштуваннями.
    for i, (left_pos, left) in enumerate(scheduled):
        for right_pos, right in scheduled[i + 1:]:
            if abs((right - left).total_seconds()) < 60:
                return {
                    "ok": False,
                    "error": (
                        f"Час каналу в постах {left_pos + 1} і {right_pos + 1} надто близький. "
                        "Залиши щонайменше 1 хвилину між ними."
                    ),
                }

    async with _PUBLISH_LOCK:
        # Повторний запит міг чекати lock, поки перший уже завершив цей batch.
        _clean_batch_cache()
        cached = _BATCH_CACHE.get(batch_id)
        if cached:
            return {**cached[1], "replayed": True}

        scanner, err = await _connect()
        if err:
            return {"ok": False, "error": err}
        results: List[dict] = []
        stopped_reason: Optional[str] = None
        try:
            for index, (pid, payload, pnum) in enumerate(normalized):
                if stopped_reason:
                    results.append({
                        "product_id": pid, "productnumber": pnum.lstrip("#"),
                        "status": "skipped", "error": stopped_reason,
                    })
                    continue
                result = await create_post(
                    db, pid, payload, _scanner=scanner, _lock_held=True,
                )
                has_partial_root = bool((result.get("partial") or {}).get("root_message_id"))
                if result.get("ok") and result.get("failed"):
                    status = "partial"
                elif result.get("ok"):
                    status = "success"
                elif has_partial_root:
                    status = "partial"
                else:
                    status = "error"
                results.append({
                    "product_id": pid, "productnumber": pnum.lstrip("#"),
                    "status": status, "error": result.get("error"), "result": result,
                })
                if _is_rate_limit_error(result):
                    stopped_reason = (
                        "Telegram увімкнув тимчасовий ліміт (FloodWait/SlowMode). "
                        "Решту пакета не надсилали — її можна повторити пізніше."
                    )
                elif index + 1 < len(normalized):
                    await asyncio.sleep(BATCH_POST_GAP_SEC)
        finally:
            await scanner.disconnect()

        counts = {
            name: sum(1 for item in results if item["status"] == name)
            for name in ("success", "partial", "error", "skipped")
        }
        if counts["error"] == counts["skipped"] == counts["partial"] == 0:
            status = "success"
        elif counts["success"] or counts["partial"]:
            status = "partial"
        else:
            status = "error"
        response = {
            "ok": True, "batch_id": batch_id, "status": status,
            "counts": counts, "results": results,
        }
        _BATCH_CACHE[batch_id] = (time.monotonic(), response)
        _clean_batch_cache()
        return response


# Telegram приймає в альбом фото JPEG/PNG. Сторона з довжиною понад ~2560 px
# сенсу не має (клієнт усе одно стисне), а сума сторін > 10000 відхиляється.
_TG_MAX_SIDE = 2560
_TG_JPEG_QUALITY = 90


def _read_photo_bytes(entries: List[Any]) -> List[Any]:
    """Фото у вигляді, придатному для `send_file(album=True)`.

    ⚠️ Фото товарів у BMS лежать у **WebP** — і Telegram вважає webp СТІКЕРОМ,
    а стікер в альбом покласти не можна: SendMultiMediaRequest відповідає
    «The provided media object is invalid». Тому кожен кадр перекодовується
    в JPEG. Оригінали на диску не чіпаються — конвертація лише в памʼяті.
    """
    try:
        from services.product_images import read_image_bytes
    except ImportError:
        from backend.services.product_images import read_image_bytes
    import io

    out = []
    for e in entries:
        data = read_image_bytes(e)
        if not data:
            continue
        name = getattr(e, "filename", "photo.jpg")
        buf = _to_jpeg(data, io)
        buf.name = re.sub(r"\.\w+$", "", name) + ".jpg"
        out.append(buf)
    return out


def _to_jpeg(data: bytes, io_mod) -> Any:
    """Байти будь-якого підтримуваного формату → JPEG у памʼяті."""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow недоступний — фото піде як є")
        buf = io_mod.BytesIO(data)
        return buf
    try:
        img = Image.open(io_mod.BytesIO(data))
        # Прозорість (webp/png) на JPEG треба покласти на білий фон, інакше
        # альфа стане чорною плямою на студійному фото.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.size) > _TG_MAX_SIDE:
            img.thumbnail((_TG_MAX_SIDE, _TG_MAX_SIDE), Image.LANCZOS)
        out = io_mod.BytesIO()
        img.save(out, format="JPEG", quality=_TG_JPEG_QUALITY, optimize=True)
        out.seek(0)
        return out
    except Exception as exc:
        logger.warning("Не вдалося перекодувати фото в JPEG (%s) — шлю як є", exc)
        return io_mod.BytesIO(data)


def _validate_when(raw: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """ISO-рядок → (aware datetime, error). Лише `None` означає «зараз».

    Невалідний/минулий рядок більше ніколи не деградує мовчки до негайної
    публікації — це найнебезпечніший можливий fallback для каналу.
    """
    if raw is None:
        return None, None
    if not str(raw).strip():
        return None, "Не вибрано час публікації в канал"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, "Некоректна дата публікації в канал"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KYIV_TZ)
    now = datetime.now(timezone.utc)
    if dt <= now + timedelta(seconds=90):
        return None, "Час публікації в канал має бути щонайменше через 2 хвилини"
    if dt > now + timedelta(days=365):
        return None, "Telegram не приймає розклад більш ніж на рік наперед"
    return dt, None


def _parse_when(raw: Any) -> Optional[datetime]:
    """Back-compat helper для старих викликів; новий код читає й помилку."""
    return _validate_when(raw)[0]

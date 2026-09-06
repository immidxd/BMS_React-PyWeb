"""Розпізнавання товару з фотографій → пропозиції для картки.

Тут зібрані всі три рубежі захисту від сміття в довідниках:

  1. `build_schema()` — перелік значень збирається з БД на льоту й іде в схему
     відповіді, тож модель ФІЗИЧНО не поверне значення, якого в нас немає;
  2. приймання йде через `field_proposals`, а звідти в картку — лише звичайним
     `update_product` зі строгим резолвером (без CREATE);
  3. поріг певності: нижче нього пропозиція взагалі не створюється.

Модуль НЕ пише в products. Він кладе пропозиції, і на цьому його роль
закінчується — рішення завжди за людиною.

ДВА ШАРИ, НЕ ОДИН. Крім моделі тут працює `barcode_reader` — детерміноване
зчитування штрихкоду. Він безкоштовний, не помиляється (контрольна сума) і
працює навіть коли модель вимкнена, без ключа або за вичерпаною стелею. Коли
обидва шари сходяться на артикулі — це підтвердження, якого сама модель дати не
може; коли розходяться — людина бачить обидві версії. Спрацьовує шар приблизно
на кожному десятому товарі, тож він саме ДОДАТКОВИЙ, а не заміна.

ТРЕТІЙ ШАР — `model_profile`: що каже наша власна база про цю саму модель.
Теж безкоштовний, теж не залежить від провайдера, і єдиний, чиї дані ввела
людина. Він озивається лише там, де минулі записи бренда+моделі СХОДЯТЬСЯ
повністю, — інакше мовчить.

Усі три шари сходяться в одному місці — `extract_and_propose`. Збіг двох шарів
підіймає певність; розбіжність нічого не приховує, а показує людині обидві
версії поруч.

ЄДИНЕ ДЖЕРЕЛО СХЕМИ. Побудова переліків живе саме тут, а скрипт виміру
(`scripts/autofill_eval.py`) імпортує її звідси. Якби кожен будував свою, вимір
показував би якість на одній схемі, а бойовий шлях працював на іншій — і
цифрам не можна було б вірити.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from services import ai_budget, barcode_reader, field_proposals, model_profile
    from services.shoe_attribute_normalization import is_dead_value
    from services.brand_normalization import canonicalize_brand_name as _canonicalize_brand_name
except ImportError:  # pragma: no cover
    from backend.services import ai_budget, barcode_reader, field_proposals, model_profile
    from backend.services.shoe_attribute_normalization import is_dead_value
    from backend.services.brand_normalization import canonicalize_brand_name as _canonicalize_brand_name

# поле схеми → (таблиця, колонка назви, FK у products, підпис, поле ProductUpdate)
# ⚠️ Останній елемент — ім'я, яке приймає ProductUpdate. Без нього довелося б
# перекладати імена при прийнятті, і зʼявився б ще один список, що розійдеться.
CLOSED_FIELDS: Dict[str, Tuple[str, str, str, str, str]] = {
    "sole_type":      ("sole_types",      "soletypename",      "soletypeid",
                       "тип підошви (профіль)", "sole_type_name"),
    "tread_type":     ("tread_types",     "treadtypename",     "treadtypeid",
                       "протектор", "tread_type_name"),
    "fastening_type": ("fastening_types", "fasteningtypename", "fasteningtypeid",
                       "застібка", "fastening_type_name"),
    "toe_shape":      ("toe_shapes",      "toeshapename",      "toeshapeid",
                       "форма носка", "toe_shape_name"),
    "lining":         ("linings",         "liningname",        "liningid",
                       "підкладка", "lining_name"),
    "heel_type":      ("heel_types",      "heeltypename",      "heeltypeid",
                       "тип каблука", "heel_type_name"),
}

# Значення, що означають ВІДСУТНІСТЬ ознаки. У цій базі відсутність = ПОРОЖНЄ
# поле, а не запис: 12111 товарів із 12177 мають порожній тип каблука, і лише
# 32 кросівки з 4175 позначені «без каблука». Пропонувати такі значення означало
# б засмічувати картку записом там, де конвенція — тиша.
ABSENCE_VALUES: Dict[str, set] = {
    "heel_type":      {"без каблука", "плоский"},
    "fastening_type": {"без застібки"},
    "lining":         {"без підкладки"},
}

# Визначення значень для моделі. Без них модель має лише слово й тяжіє до
# найчастішого: «рифлена» проти «рельєфна» без пояснення не розрізняються ніяк.
VALUE_HINTS: Dict[str, Dict[str, str]] = {
    "tread_type": {
        "рифлена":   "дрібні паралельні рівчаки або смужки, як на рифлених чіпсах; малюнок неглибокий",
        "рельєфна":  "виражений об'ємний малюнок різної форми, але НЕ глибокі шашки",
        "тракторна": "глибокі масивні шашки з широкими проміжками, як у протектора трактора",
        "гладка":    "рівна поверхня без малюнка взагалі",
    },
}

PROMPT = (
    "Ти оцінюєш вживане брендове взуття за фотографіями для картки товару.\n"
    "Заповни лише те, що ВИДНО НА ЗНІМКАХ. Якщо ознака не видна однозначно — "
    "постав null. Порожнє значення коштує кілька секунд ручної роботи, а "
    "неправильне псує дані у двох системах, тож null завжди краще за здогад.\n"
    "Текстові поля (бренд, артикул, модель) читай ДОСЛІВНО з бирки або лого, "
    "нічого не додумуючи."
)

DEFAULT_MODEL = os.getenv("AUTOFILL_MODEL", "gemini-3.5-flash")
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"


# ── Схема відповіді ─────────────────────────────────────────────────────────

def build_schema(db: Session) -> Dict[str, Any]:
    """JSON Schema із ЗАКРИТИМИ переліками з живих довідників.

    У перелік потрапляють лише значення, за якими Є товари. Мертві
    («goodyear welt», «wingtip», «хутро») виключені навмисно: подати їх моделі
    означає запросити відповідь, якої в наших даних не існує.
    """
    props: Dict[str, Any] = {}
    for field, (table, col, fk, label, _upd) in CLOSED_FIELDS.items():
        rows = db.execute(text(
            f"SELECT l.{col}, count(p.id) FROM {table} l "
            f"LEFT JOIN products p ON p.{fk} = l.id GROUP BY l.{col} ORDER BY l.{col}"
        )).fetchall()
        values = [(n or "").strip() for n, k in rows
                  if (n or "").strip() and k > 0 and not is_dead_value(field, n)]
        hints = VALUE_HINTS.get(field, {})
        detail = "; ".join(f"«{v}» — {hints[v]}" for v in values if v in hints)
        props[field] = {
            "type": ["string", "null"],
            # null у переліку — це і є «чесна відмова». Без нього модель мусить вгадувати.
            "enum": values + [None],
            "description": (f"{label}; null, якщо на знімках не видно однозначно"
                            + (f". Значення: {detail}" if detail else "")),
        }
        props[f"{field}_confidence"] = {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": f"певність щодо «{label}» від 0 до 1",
        }

    # Технології — many-to-many, тож масив. Перелік ВІДКРИТИЙ: назви власні й нові
    # зʼявляються постійно, закритий список відсікав би реальні.
    known = [r[0] for r in db.execute(text(
        "SELECT DISTINCT t.technologyname FROM technologies t "
        "JOIN product_technologies pt ON pt.technology_id = t.id ORDER BY 1"
    )).fetchall()]
    props["technologies"] = {
        "type": "array", "items": {"type": "string"},
        "description": ("технології, читані з бирки; порожній масив, якщо не видно. "
                        f"Відомі нам: {', '.join(known[:40])}"),
    }
    # ⚠️ ТЕКСТОВІ ПОЛЯ НЕ МАЮТЬ ЗАХИСТУ ENUM. Для полів із закритим переліком
    # модель фізично не поверне значення поза списком; тут вона просто читає — і
    # може ВИГАДАТИ. Реальний випадок 06.09.2026: на чіткому фото бирки Adidas
    # написано «JQ8356», а модель видала «HQ8708».
    # Тому для кожного текстового поля вимагаємо ДВА додаткові: власну оцінку
    # певності (а не наше припущення) і ЯКІР — дослівний рядок, у якому вона це
    # побачила. Вигадка такого контексту не має, а людині досить глянути.
    props["brand_text"] = {"type": ["string", "null"],
                           "description": "бренд як НАПИСАНО на бирці/лого, дослівно"}
    props["brand_text_confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                                      "description": "певність щодо бренда"}
    props["article_text"] = {"type": ["string", "null"],
                             "description": ("артикул виробника з бирки, дослівно (напр. CW2288-111). "
                                             "Якщо бирки НЕ ВИДНО або текст нерозбірливий — null. "
                                             "НЕ вгадуй код за брендом.")}
    props["article_text_confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                                        "description": "певність щодо артикула"}
    props["article_source_text"] = {
        "type": ["string", "null"],
        "description": ("ДОСЛІВНО весь рядок бирки, у якому стоїть артикул, разом із "
                        "сусідніми символами. Порожньо, якщо артикул не прочитано."),
    }
    props["model_text"] = {"type": ["string", "null"],
                           "description": "назва моделі як написано на бирці"}
    props["model_text_confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                                      "description": "певність щодо назви моделі"}
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


def to_gemini_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Загальний JSON Schema → діалект Gemini.

    Gemini НЕ розуміє ані union-типів (`["string","null"]`), ані `null` у enum:
    у нього для цього окреме поле `nullable`. Саме через такі розбіжності
    адаптер і потрібен — «одна схема на всіх» не працює навіть на цій задачі.
    """
    def conv(node: dict) -> dict:
        t = node.get("type")
        nullable = False
        if isinstance(t, list):
            nullable = "null" in t
            t = next((x for x in t if x != "null"), "string")
        out: dict = {"type": (t or "string").upper()}
        if nullable:
            out["nullable"] = True
        if "description" in node:
            out["description"] = node["description"]
        if "enum" in node:
            vals = [v for v in node["enum"] if v is not None]
            if vals:
                out["enum"] = vals
                out["type"] = "STRING"
        if t == "array":
            out["items"] = conv(node.get("items", {"type": "string"}))
        if "properties" in node:
            out["properties"] = {k: conv(v) for k, v in node["properties"].items()}
            if node.get("required"):
                out["required"] = list(node["required"])
        return out
    return conv(schema)


# ── Виклик провайдера ───────────────────────────────────────────────────────

def call_gemini(model: str, api_key: str, photos: List[pathlib.Path],
                schema: Dict[str, Any]) -> Dict[str, Any]:
    """Один виклик. Повертає розібрану відповідь або {'_error': ...}.

    Повторюємо лише 5xx — тимчасове перевантаження минає саме. 429 НЕ
    повторюємо: вимір показав, що це вичерпана ДОБОВА квота, і повтори там
    лише палять час (43 провали зʼїли одинадцять хвилин чистого сну).
    """
    import requests

    parts: List[Dict[str, Any]] = [{"text": PROMPT}]
    for p in photos:
        parts.append({"inline_data": {
            "mime_type": "image/webp",
            "data": base64.standard_b64encode(p.read_bytes()).decode("ascii"),
        }})
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": to_gemini_schema(schema),
            "temperature": 0,
        },
    }
    r = None
    for attempt in range(4):
        r = requests.post(_ENDPOINT.format(m=model),
                          headers={"x-goog-api-key": api_key,
                                   "Content-Type": "application/json"},
                          json=body, timeout=180)
        if r.status_code not in (500, 502, 503, 504):
            break
        time.sleep(2 ** attempt)
    if r is None or r.status_code != 200:
        return {"_error": f"HTTP {getattr(r, 'status_code', '?')}: "
                          f"{(r.text[:200] if r is not None else '')}"}
    data = r.json()
    try:
        out = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return {"_error": f"{type(e).__name__}: {str(e)[:120]}"}
    out["_usage"] = data.get("usageMetadata", {}) or {}
    return out


# ── Оркестрація ─────────────────────────────────────────────────────────────

def _current_values(db: Session, product_id: int) -> Dict[str, Optional[str]]:
    """Що вже стоїть у картці — щоб не пропонувати вже правильне.

    Пропозиція, яка повторює наявне значення, це чистий шум: вона просить
    підтвердити те, що людина колись уже й вписала. Саме через це «Hey Dude»
    отримував пропозицію «HEY DUDE» — модель читає лого дослівно.
    """
    sel = ", ".join(f"{t}.{c} AS {upd}" for _f, (t, c, _fk, _l, upd) in CLOSED_FIELDS.items())
    joins = " ".join(f"LEFT JOIN {t} ON {t}.id = p.{fk}"
                     for _f, (t, _c, fk, _l, _u) in CLOSED_FIELDS.items())
    row = db.execute(text(
        f"SELECT {sel}, b.brandname AS brand_name, p.marking, p.gtin, p.model "
        f"FROM products p {joins} LEFT JOIN brands b ON b.id = p.brandid "
        f"WHERE p.id = :pid"
    ), {"pid": product_id}).mappings().fetchone()
    return dict(row) if row else {}


def _norm_code(v: Optional[str]) -> str:
    """Артикул до порівнянного вигляду: лише літери й цифри, у верхньому.

    «CW2288-111», «cw2288 111» і «CW2288111» — той самий код, надрукований
    по-різному на бирці, у штрихкоді й у нашій картці.
    """
    return "".join(ch for ch in (v or "") if ch.isalnum()).upper()


def _norm_gtin(v: Optional[str]) -> str:
    """GTIN до порівнянного вигляду: цифри без провідних нулів.

    ⚠️ GTIN-8, -12, -13 і -14 — це ОДИН І ТОЙ САМИЙ номер, доповнений нулями до
    різної довжини. Реальний випадок #Ф2523: у картці стоїть «197002067565»
    (дванадцять цифр, UPC-A), а сканер віддає «0197002067565» (тринадцять,
    EAN-13). Порівняння рядків вважає їх різними — і шар пропонує «виправити»
    правильне значення на нього ж. Це рівно той шум, який ми прибирали з решти
    полів, тільки тут його не видно оком.
    """
    return "".join(ch for ch in (v or "") if ch.isdigit()).lstrip("0")


def _read_barcodes(photos: List[pathlib.Path]):
    """Зчитування кодів у ФОНІ, паралельно з викликом моделі.

    Це не передчасна оптимізація, а точний збіг характеру двох робіт: виклик
    моделі — це чекання мережі, а декодування — процесор у C++-розширенні, яке
    відпускає GIL. Послідовно вони дали б 10–16 секунд зверху на кожен товар;
    паралельно шар штрихкодів не коштує майже нічого за часом.
    """
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(barcode_reader.read_photos, photos)
    finally:
        pool.shutdown(wait=False)


def _propose_gtin(db: Session, product_id: int, hits, current: Dict[str, Any],
                  proposed: list, already: list) -> None:
    """Роздрібний штрихкод → поле `gtin`. Певність 1.0, і це не перебільшення.

    EAN-13 несе контрольну суму: він або сходиться, або не декодується взагалі.
    Тут нема чого «оцінювати» — на відміну від прочитаного моделлю тексту.

    Поле важливе саме тому, що досі порожнє майже всюди (0.2%): жоден інший шар
    його не заповнює, бо вручну переписувати тринадцять цифр ніхто не буде.
    """
    g = barcode_reader.pick_gtin(hits)
    if not g:
        return
    if _norm_gtin(current.get("gtin")) == _norm_gtin(g.text):
        already.append(("gtin", g.text))
        return
    if field_proposals.propose(db, product_id, "gtin", g.text, 1.0,
                               model=None, source="barcode",
                               source_photos=g.photo,
                               note=f"{g.format} зчитано з {g.photo}"):
        proposed.append(("gtin", g.text, 1.0))


def _profile_layer(db: Session, product_id: int, pred: Dict[str, Any],
                   current: Dict[str, Any], proposed: list, already: list,
                   confirmed: list, made: Dict[str, tuple]) -> None:
    """Третій шар: що каже наша власна база про цю саму модель.

    КЛЮЧ ПОШУКУ. Спершу беремо бренд і модель із самої картки. Якщо їх там ще
    немає — а для нового товару їх зазвичай і немає, — беремо прочитане
    моделлю. Це не робить шар залежним від здогаду: сам ФАКТ, що прочитана
    назва знайшлась у нашому каталозі, є її перевіркою. Вигадана назва просто
    ні на що не натрапить, і шар промовчить.

    ЩО РОБИМО ПРИ РОЗБІЖНОСТІ. Пропозицію моделі не затираємо: вона дивилась на
    ЦЮ пару, а профіль — на інші пари тієї ж моделі. Натомість дописуємо
    альтернативу в якір, щоб людина бачила обидві версії поруч і не мусила
    відкривати минулі записи руками.
    """
    brand = (current.get("brand_name") or pred.get("brand_text") or "").strip()
    model_name = (current.get("model") or pred.get("model_text") or "").strip()
    if not brand or len(model_name) < 2:
        return
    prof = model_profile.profile_for(db, brand, model_name, exclude_id=product_id)
    if not prof.get("records"):
        return
    agreed = model_profile.unanimous(prof)
    if not agreed:
        return

    now = model_profile.current_values(db, product_id)
    src = f"{brand} {model_name}"
    for field, (value, n) in agreed.items():
        if _same_as_current(now.get(field), value):
            # Картка вже містить це значення — минулі записи його підтвердили.
            confirmed.append((field, value))
            continue
        conf = model_profile.confidence_for(n)
        note = f"{n} минулих записів «{src}» сходяться на «{value}»"

        if field in made:
            ai_value, ai_conf, ai_note = made[field]
            if _same_as_current(ai_value, value):
                # ДВА НЕЗАЛЕЖНІ ШАРИ ЗІЙШЛИСЬ: модель побачила на фото те саме,
                # що люди вписували в минулі пари цієї моделі. Підіймаємо
                # певність до кращої з двох і кажемо людині, чому.
                best = max(conf, float(ai_conf or 0))
                field_proposals.propose(db, product_id, field, value, best,
                                        source="photo+profile",
                                        note=f"{ai_note + ' · ' if ai_note else ''}{note}")
                proposed[:] = [(f, v, best) if f == field else (f, v, c)
                               for f, v, c in proposed]
                made[field] = (value, best, note)
            else:
                # Розбіжність. Рішення лишаємо за моделлю (вона бачила саме цю
                # пару), але альтернативу показуємо поруч.
                merged = f"{ai_note + ' · ' if ai_note else ''}у минулих записах «{src}»: {value}"
                field_proposals.propose(db, product_id, field, ai_value, ai_conf,
                                        source="photo", note=merged)
            continue

        # Модель тут нічого не сказала — пропонує профіль, і це безкоштовно.
        if field_proposals.propose(db, product_id, field, value, conf,
                                   source="profile", note=note):
            proposed.append((field, value, conf))
            made[field] = (value, conf, note)


def _same_as_current(current: Optional[str], proposed: Optional[str]) -> bool:
    """Порівняння без урахування регістру й країв — «HEY DUDE» = «Hey Dude»."""
    a = (current or "").strip().casefold()
    b = (proposed or "").strip().casefold()
    return bool(a) and a == b


def extract_and_propose(db: Session, product_id: int, photos: List[pathlib.Path],
                        *, model: str = None, purpose: str = "autofill",
                        api_key: Optional[str] = None) -> Dict[str, Any]:
    """Розпізнати товар і скласти пропозиції. У products НЕ пише.

    Повертає звіт: чи дозволив бюджет, скільки коштувало, які поля запропоновано
    й скільки відсіяв поріг певності.
    """
    model = model or DEFAULT_MODEL
    photos = [p for p in photos if p.exists()]
    if not photos:
        return {"ok": False, "reason": "немає знімків"}

    current = _current_values(db, product_id)
    proposed, below_threshold, already, confirmed = [], [], [], []
    # ⚠️ Що саме запропонувала модель: поле → (значення, певність, якір).
    # Потрібне шару профілю: `propose()` робить upsert по (товар, поле), тож
    # без цього третій шар сліпо затирав би пропозицію другого.
    made: Dict[str, tuple] = {}

    # ⚠️ ПОРЯДОК ТУТ ЗМІСТОВНИЙ. Шар штрихкодів іде ПЕРШИМ і не залежить від
    # моделі ні в чому: він безкоштовний і працює навіть без ключа та за
    # вичерпаною стелею. Тому обидві відмови нижче повертають те, що він
    # устиг знайти, — інакше вимкнена модель гасила б і безкоштовний шар.
    barcode_future = _read_barcodes(photos)
    _box: list = []
    # Те, що прочитала модель, — щоб шар профілю міг це врахувати. Порожньо,
    # якщо модель не викликалась узагалі (немає ключа, вичерпана стеля).
    pred_box: Dict[str, Any] = {}

    def _hits():
        """Дочекатись фонового читання. Результат беремо один раз."""
        if not _box:
            _box.append(barcode_future.result())
        return _box[0]

    def _finish(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Спільний вихід: чим би не скінчилась модель, штрихкоди зберігаються."""
        hits = _hits()
        _propose_gtin(db, product_id, hits, current, proposed, already)
        # ⚠️ Підтвердження рахуємо САМЕ ТУТ, а не поруч із перехресною
        # перевіркою нижче: це факт чистого шару штрихкодів, і він не має
        # зникати через те, що модель вимкнена або стеля вичерпана.
        # Реальний випадок #Ф4132: DataMatrix New Balance містить «GR530AA» —
        # рівно той артикул, що вписаний руками. Пропонувати нічого, але знати,
        # що значення перевірене машиною, корисно.
        marking_now = current.get("marking")
        if marking_now and _norm_code(marking_now) in {
                _norm_code(c) for c in barcode_reader.article_candidates(hits)}:
            confirmed.append(("marking", marking_now))
        _profile_layer(db, product_id, pred_box, current,
                       proposed, already, confirmed, made)
        payload.setdefault("proposed", proposed)
        payload.setdefault("already_correct", already)
        payload["barcodes"] = [{"format": h.format, "text": h.text, "photo": h.photo}
                               for h in hits]
        payload["confirmed_by_barcode"] = confirmed
        return payload

    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _finish({"ok": False, "reason": "немає GEMINI_API_KEY"})

    verdict = ai_budget.guard(db, purpose=purpose)
    if not verdict.allowed:
        # Відмова бюджету — НЕ помилка. Автозаповнення просто вимикається.
        return _finish({"ok": False, "reason": verdict.reason, "budget_blocked": True,
                        "spent_usd": verdict.spent_usd})

    schema = build_schema(db)
    pred = call_gemini(model, api_key, photos, schema)
    usage = pred.pop("_usage", {}) or {}
    err = pred.get("_error")

    # Записуємо ЗАВЖДИ: провайдер тарифікує вхід навіть на провалі.
    cost = ai_budget.record(
        db, model=model, purpose=purpose, product_id=product_id,
        prompt_tokens=int(usage.get("promptTokenCount", 0)),
        output_tokens=int(usage.get("candidatesTokenCount", 0)),
        ok=not err, error=err,
    )
    if err:
        return _finish({"ok": False, "reason": err, "cost_usd": cost})

    pred_box.update(pred)
    photo_names = ",".join(p.name for p in photos)
    for field, (_t, _c, _fk, _label, upd_field) in CLOSED_FIELDS.items():
        value = pred.get(field)
        conf = pred.get(f"{field}_confidence")
        if not value:
            continue
        # Відсутність ознаки в нас позначається порожнім полем, а не записом.
        if value in ABSENCE_VALUES.get(field, ()):
            continue
        if _same_as_current(current.get(upd_field), value):
            already.append((upd_field, value))
            continue
        if field_proposals.propose(db, product_id, upd_field, value, conf,
                                   model=model, source_photos=photo_names):
            proposed.append((upd_field, value, conf))
            made[upd_field] = (value, conf, None)
        else:
            below_threshold.append((upd_field, value, conf))

    # Технології — масив; у картку йдуть рядком через кому, як і зберігаються.
    techs = pred.get("technologies") or []
    if techs:
        csv = ", ".join(t.strip() for t in techs if t and t.strip())
        if csv and field_proposals.propose(db, product_id, "technology_name", csv,
                                           None, model=model, source_photos=photo_names):
            proposed.append(("technology_name", csv, None))
            made["technology_name"] = (csv, None, None)

    # Другий шар зустрічається з першим. Тут і тільки тут ми чекаємо на фонове
    # читання — далі йде єдине місце, де його результат щось вирішує.
    codes = barcode_reader.article_candidates(_hits())
    norm_codes = {_norm_code(c) for c in codes}

    # Текст із бирки. Артикул має найвищий поріг — помилка там найдорожча.
    for src, upd_field in (("article_text", "marking"), ("brand_text", "brand_name")):
        val = pred.get(src)
        if not val:
            continue
        # ⚠️ Раніше тут стояло жорстке 0.9 — тобто НАШЕ припущення подавалось як
        # оцінка моделі, і вигаданий артикул виглядав майже впевненим. Тепер
        # беремо те, що сказала вона сама; немає оцінки — вважаємо ненадійним.
        conf = pred.get(f"{src}_confidence")
        anchor = pred.get("article_source_text") if src == "article_text" else None
        if src == "article_text":
            if _norm_code(val) in norm_codes:
                # ДВА НЕЗАЛЕЖНІ ШАРИ ЗІЙШЛИСЬ. Модель прочитала очима те саме,
                # що машина витягла зі штрихкоду з контрольною сумою. Вигадка
                # так збігтися не може — саме заради цієї миті шар і будувався.
                conf = 1.0
                anchor = f"{anchor or ''} · підтверджено штрихкодом".strip(" ·")
            elif not (anchor or "").strip():
                # Немає ані якоря, ані підтвердження кодом — немає підстав
                # вірити. Артикул без процитованого контексту не пропонуємо.
                below_threshold.append((upd_field, val, conf))
                continue
            elif codes:
                # Розбіжність САМА ПО СОБІ не викриває вигадку: артикул часто
                # надрукований на бирці, але у штрихкод не вкладений. Тому не
                # відхиляємо, а показуємо людині обидві версії поруч.
                anchor = f"{anchor} · у штрихкоді: {', '.join(codes)}"
        if upd_field == "brand_name":
            # Модель читає лого ДОСЛІВНО, тож бачить «HEY DUDE». Проводимо через
            # той самий нормалізатор, що й ручне введення, — інакше пропозиція
            # «виправляла» б правильний «Hey Dude» на крик із коробки.
            val = _canonicalize_brand_name(val) or val
        if _same_as_current(current.get(upd_field), val):
            already.append((upd_field, val))
            continue
        if field_proposals.propose(db, product_id, upd_field, val, conf,
                                   model=model, source_photos=photo_names, note=anchor):
            proposed.append((upd_field, val, conf))
            made[upd_field] = (val, conf, anchor)
        else:
            below_threshold.append((upd_field, val, conf))

    return _finish({"ok": True, "cost_usd": cost, "model": model,
                    "below_threshold": below_threshold, "photos": len(photos)})

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
    from services.shoe_attribute_normalization import is_dead_value, is_absence_value, is_misplaced_value
    from services.brand_normalization import canonicalize_brand_name as _canonicalize_brand_name
except ImportError:  # pragma: no cover
    from backend.services import ai_budget, barcode_reader, field_proposals, model_profile
    from backend.services.shoe_attribute_normalization import is_dead_value, is_absence_value, is_misplaced_value
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
    # Країна виробництва — читається з бирки «Made in …». Довідник закритий
    # (100 країн), тож вигадати країну модель не може; ризик інший — сплутати
    # країну ВИРОБНИЦТВА зі штаб-квартирою бренда («Made in Bangladesh under
    # quality control of Caprice Germany» → Бангладеш, не Німеччина). Про це —
    # у визначенні поля нижче.
    "manufacturer_country": ("countries", "countryname", "manufacturercountryid",
                       "країна виробництва", "manufacturer_country_name"),
    "style":          ("styles",          "stylename",         "styleid",
                       "стиль", "style_name"),
    # Підвид залежить від виду: «Челсі» — для ботинок, «Без рукавів» — для блуз.
    # Перелік звужується до підвидів, що трапляються з видом ЦЬОГО товару
    # (див. build_schema(type_id=…)), інакше модель обирала б із 133 чужих.
    "subtype":        ("subtypes",        "subtypename",       "subtypeid",
                       "підвид", "subtype_name"),
}

# ── Сезон: багатозначний, ДОПОВНЮЄТЬСЯ, не замінюється ──────────────────────
# У базі сезон — рядок через «, » з пʼяти канонічних значень у сталому порядку
# (той самий, що в парсері: SEASON_CANONICAL_ORDER). Рішення власника
# 15.09.2026: стоїть «Єврозима» і це правильно, але хай модель ДОДАЄ «Демі»
# чи «Зима», якщо бачить. Тому пропозиція — обʼєднання наявного з побаченим;
# нічого не прибирається ніколи, лише додається.
SEASONS: Tuple[str, ...] = ("Зима", "Єврозима", "Демі", "Літо", "Всесезон")
SEASON_HINTS: Dict[str, str] = {
    "Зима":     "утеплене взуття на сильний мороз: густе хутро, високий чобіт, товста підошва",
    "Єврозима": "утеплене на мʼяку зиму: тонке хутро чи фліс, невисока халява",
    "Демі":     "без утеплення, закрите — на весну й осінь",
    "Літо":     "відкрите чи легке дихаюче: босоніжки, сандалі, сітчасті кросівки",
    "Всесезон": "нейтральне, без явних ознак сезону",
}


def merge_seasons(current: Optional[str], seen: List[str]) -> Optional[str]:
    """Обʼєднати наявні сезони з побаченими у канонічному порядку.

    Повертає None, якщо додавати нічого (усе побачене вже стоїть).
    """
    cur = {t.strip() for t in (current or "").split(",") if t.strip()}
    new = {t.strip() for t in seen if t and t.strip() in SEASONS}
    if not new or new <= cur:
        return None
    return ", ".join(t for t in SEASONS if t in cur | new)


# ── Матеріали з піктограм ЄС на бирці ──────────────────────────────────────
# Директива 94/11/EC: три рядки (верх / підкладка й устілка / підошва), чотири
# символи (шкіра — силует шкури; шкіра з покриттям — шкура з ромбом; текстиль —
# плетіння; інше — ромб). Це стандарт, і він є майже на всьому європейському
# взутті — але його ніхто не читав, бо схема не питала. Наші позиції
# `upper / middle / sole` лягають на рядки один в один.
PICTOGRAM_ROWS: Dict[str, str] = {"upper": "upper", "lining": "middle", "outsole": "sole"}
PICTOGRAM_SYMBOLS: Tuple[str, ...] = ("шкіра", "шкіра з покриттям", "текстиль", "інше")
# Символ → назва у НАШОМУ словнику матеріалів (лише ті, що там є).
PICTOGRAM_TO_MATERIAL: Dict[str, str] = {
    "шкіра": "шкіра",
    "шкіра з покриттям": "шкіра",       # це справжня шкіра з покриттям, не екошкіра
    "текстиль": "текстиль",
    "інше": "синтетика",
}


def _current_materials(db: Session, product_id: int) -> Dict[str, str]:
    """{позиція: 'назва, назва'} — щоб не пропонувати вже вписане."""
    rows = db.execute(text("""
        SELECT pm.position, string_agg(m.materialname, ', ' ORDER BY pm.ord)
        FROM product_materials pm JOIN materials m ON m.id = pm.material_id
        WHERE pm.product_id = :pid GROUP BY pm.position
    """), {"pid": product_id}).fetchall()
    return {r[0]: (r[1] or "") for r in rows}


def _material_proposals(db, product_id, pred, photo_names, model,
                        proposed, below_threshold, already) -> Dict[str, Any]:
    pic = pred.get("materials_pictogram") or {}
    if not isinstance(pic, dict) or not any(pic.get(k) for k in PICTOGRAM_ROWS):
        return {"present": False}
    conf = pred.get("materials_pictogram_confidence")
    current = _current_materials(db, product_id)
    out: Dict[str, str] = {}
    for row, pos in PICTOGRAM_ROWS.items():
        sym = (pic.get(row) or "").strip().lower()
        if sym not in PICTOGRAM_TO_MATERIAL:
            continue
        name = PICTOGRAM_TO_MATERIAL[sym]
        field = f"material:{pos}"
        cur = current.get(pos, "")
        if name in {t.strip().lower() for t in cur.split(",") if t.strip()}:
            already.append((field, cur)); continue
        note = f"піктограма ЄС: {sym}" + (" (у нас — шкіра)" if sym == "шкіра з покриттям" else "")
        if field_proposals.propose(db, product_id, field, name, conf, model=model,
                                   source_photos=photo_names, note=note):
            proposed.append((field, name, conf)); out[pos] = name
        else:
            below_threshold.append((field, name, conf))
    return {"present": True, "proposed": out}


# ── Стікер із ціною/розміром/заміром ────────────────────────────────────────
# Власник пише на стікері від руки: ціну, розмір, замір устілки в см і НОМЕР
# товару. Це per-item поля, і помилка в них найдорожча: ціна їде в Журнал і на
# маркетплейси. Тому стікер має ВЛАСНИЙ запобіжник — номер на ньому мусить
# збігатися з номером картки; інакше він або чужий, або прочитаний невірно, і
# все з нього відкидається. Межі — від реальних значень бази.
STICKER_FIELDS: Dict[str, Tuple[str, float, float, float]] = {
    # ключ у відповіді: (поле ProductUpdate, поріг, мін, макс)
    "sticker_price": ("price",          0.90,   50.0, 50000.0),
    "sticker_size":  ("sizeeu",         0.90,   15.0,    52.0),
    "sticker_cm":    ("measurementscm", 0.85,   10.0,    36.0),
}

# ⚠️ Правило «відсутність = порожнє поле» живе в
# `shoe_attribute_normalization`, спільно з шаром профілю. Тримати тут власну
# копію вже коштувало 25 пропозицій «без каблука» від шару, який тієї копії не
# бачив. Псевдонім лишено заради тестів і читабельності викликів нижче.

# Визначення значень для моделі. Без них модель має лише слово й тяжіє до
# найчастішого: «рифлена» проти «рельєфна» без пояснення не розрізняються ніяк.
VALUE_HINTS: Dict[str, Dict[str, str]] = {
    # Підошва: модель бачить «товсту» і каже «платформа», хоча в нас платформа
    # — це суцільна товста підошва БЕЗ вирізу під склепінням. Туфлі з окремим
    # блоком ззаду люди позначають «каблук» (17 із 21), а вже тип каблука —
    # блок / низький / шпилька. Реальний випадок: лофери DeeZee з тракторною
    # підошвою і вирізом отримали «платформа» замість «каблук».
    "sole_type": {
        "платформа": "суцільна потовщена підошва однакової товщини спереду і ззаду, БЕЗ вирізу під склепінням і БЕЗ окремого каблука",
        "танкетка":  "суцільна підошва, що плавно товщає до пʼяти, без вирізу під склепінням",
        "каблук":    "є ОКРЕМИЙ каблук ззаду і виріз під склепінням між ним і передньою частиною — будь-якої висоти, включно з низьким блоком",
        "плоска":    "тонка рівна підошва без потовщення і без каблука",
        "спортивна": "кросівкова підошва з амортизацією, типово з піни",
    },
    "manufacturer_country": {
        "__field__": ("країна, де ВИГОТОВЛЕНО — з напису «Made in …» на бирці чи устілці. "
                      "НЕ країна бренда чи контролю якості: «Made in Bangladesh under quality "
                      "control of Caprice Germany» — це Бангладеш, а не Німеччина. "
                      "Немає напису «Made in» — null."),
    },
    # Підвиди взуття. Без визначень модель тяжіє до двох-трьох знайомих назв
    # і не користується рештою з 28 підвидів ботинок. Визначення — за силуетом
    # і застібкою, бо саме це видно на знімку; висота халяви — головна вісь.
    "subtype": {
        "__field__": "підвид за силуетом, висотою халяви й застібкою; null, якщо не видно однозначно",
        "Челсі":        "по кісточку, БЕЗ шнурівки й блискавки — з еластичними вставками з боків і петлею ззаду",
        "Ботильйони":   "жіночі по кісточку НА КАБЛУЦІ (блок, низький, шпилька), зазвичай з блискавкою",
        "Напівботинки": "низькі по кісточку на ПЛОСКІЙ підошві або без каблука, шнурівка чи блискавка",
        "Напівсапоги":  "халява вище кісточки, але нижче середини гомілки",
        "Чоботи":       "халява до середини гомілки або вище",
        "Уггі":         "мʼякі без каблука, овчина чи хутро назовні по краю халяви",
        "Дутики":       "стьобані «надуті» з синтетичної тканини, зимові",
        "Снігоходи":    "зимові на товстій рифленій підошві з високою утепленою халявою",
        "Хайтопи":      "спортивний силует вище кісточки на шнурівці, як високі кеди",
        "Козачки":      "вестерн-силует: загострений носок, скошений каблук, без шнурівки",
        "Тактичні":     "високі мілітарі-черевики на шнурівці з грубою підошвою",
        "Берці":        "армійський високий чобіт на шнурівці до середини гомілки",
        "Низькі":       "найнижчий силует, ледь прикриває кісточку",
        "Кросівки":     "спортивний утеплений силует кросівка з високою пʼятою",
        "Панчохи":      "щільно облягає ногу, як панчоха, еластична халява без застібок",
    },
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

def build_schema(db: Session, type_id: Optional[int] = None) -> Dict[str, Any]:
    """JSON Schema із ЗАКРИТИМИ переліками з живих довідників.

    У перелік потрапляють лише значення, за якими Є товари. Мертві
    («goodyear welt», «wingtip», «хутро») виключені навмисно: подати їх моделі
    означає запросити відповідь, якої в наших даних не існує.
    """
    props: Dict[str, Any] = {}
    for field, (table, col, fk, label, _upd) in CLOSED_FIELDS.items():
        if field == "subtype" and type_id:
            # Лише підвиди, що трапляються з видом цього товару.
            rows = db.execute(text(
                f"SELECT l.{col}, count(p.id) FROM {table} l "
                f"JOIN products p ON p.{fk} = l.id AND p.typeid = :tid GROUP BY l.{col} ORDER BY l.{col}"
            ), {"tid": type_id}).fetchall()
        else:
            rows = db.execute(text(
                f"SELECT l.{col}, count(p.id) FROM {table} l "
                f"LEFT JOIN products p ON p.{fk} = l.id GROUP BY l.{col} ORDER BY l.{col}"
            )).fetchall()
        # У перелік не потрапляють ані мертві значення, ані ті, що лежать не в
        # тому довіднику: «платформа» в типах каблука — це підошва, і модель
        # пропонувала її як каблук лише тому, що бачила в списку.
        # ⚠️ Значення з ВИЗНАЧЕННЯМ у VALUE_HINTS канонічне за побудовою — воно
        # входить у перелік навіть без товарів. Інакше «гладка» (0 товарів) не
        # потрапляла в перелік протектора, і на кожній гладкій підошві модель
        # МУСИЛА обирати з трьох, що лишились, — звідси «рифлена» на класичних
        # черевиках, відхилена вже чотири рази. Фільтр `k > 0` — проти сміття
        # в довідниках, а не проти справжніх категорій, які ще не заповнювали.
        defined = set(VALUE_HINTS.get(field, {}))
        values = [(n or "").strip() for n, k in rows
                  if (n or "").strip() and (k > 0 or (n or "").strip() in defined)
                  and not is_dead_value(field, n) and not is_misplaced_value(_upd, n)
                  and not is_absence_value(_upd, n)]
        hints = VALUE_HINTS.get(field, {})
        field_note = hints.get("__field__", "")
        detail = "; ".join(f"«{v}» — {hints[v]}" for v in values if v in hints and v != "__field__")
        props[field] = {
            "type": ["string", "null"],
            # null у переліку — це і є «чесна відмова». Без нього модель мусить вгадувати.
            "enum": values + [None],
            "description": (f"{label}; null, якщо на знімках не видно однозначно"
                            + (f". {field_note}" if field_note else "")
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
    # Сезон — масив: одне чи кілька значень. Пропозиція ДОПОВНЮЄ наявне.
    props["season"] = {
        "type": "array", "items": {"type": "string", "enum": list(SEASONS)},
        "description": ("сезон(и) за ознаками взуття; можна кілька. Значення: "
                        + "; ".join(f"«{k}» — {v}" for k, v in SEASON_HINTS.items())),
    }
    props["season_confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                                  "description": "певність щодо сезону"}
    # Піктограми ЄС на бирці: три рядки × чотири символи.
    props["materials_pictogram"] = {
        "type": "object",
        "description": ("стандартні піктограми матеріалів на бирці (ЄС): рядок ВЕРХ, рядок "
                        "ПІДКЛАДКА/УСТІЛКА, рядок ПІДОШВА. Символи: силует шкури — «шкіра»; "
                        "шкура з ромбом — «шкіра з покриттям»; плетіння — «текстиль»; ромб — «інше». "
                        "Немає піктограм на знімках — усі null"),
        "properties": {
            "upper":   {"type": ["string", "null"], "enum": list(PICTOGRAM_SYMBOLS) + [None]},
            "lining":  {"type": ["string", "null"], "enum": list(PICTOGRAM_SYMBOLS) + [None]},
            "outsole": {"type": ["string", "null"], "enum": list(PICTOGRAM_SYMBOLS) + [None]},
        },
        "required": ["upper", "lining", "outsole"],
    }
    props["materials_pictogram_confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                                               "description": "певність щодо піктограм"}
    # Стікер від руки (зазвичай зелений папірець): ціна, розмір, замір, номер.
    props["sticker_text"] = {"type": ["string", "null"],
                             "description": ("ДОСЛІВНО весь рукописний текст зі стікера/цінника, "
                                             "якщо він є на знімках; інакше null")}
    props["sticker_number"] = {"type": ["string", "null"],
                               "description": "номер товару зі стікера, як написано (напр. ф4419)"}
    props["sticker_price"] = {"type": ["number", "null"], "description": "ціна зі стікера, число в гривнях"}
    props["sticker_size"] = {"type": ["number", "null"], "description": "розмір EU зі стікера, напр. 36 або 45.3"}
    props["sticker_cm"] = {"type": ["number", "null"], "description": "замір устілки в см зі стікера, напр. 23.5"}
    for k in ("sticker_price", "sticker_size", "sticker_cm"):
        props[f"{k}_confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                                    "description": f"певність щодо {k}"}
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
        out: Dict[str, Any] = {"_error": f"HTTP {getattr(r, 'status_code', '?')}: "
                                         f"{(r.text[:200] if r is not None else '')}"}
        # 429 тут — це вичерпана ДОБОВА квота безкоштовного рівня (8 викликів),
        # а не перевантаження. Позначаємо окремо: викликач запропонує людині
        # повторити платним ключем, замість того щоб показати сиру помилку.
        if r is not None and r.status_code == 429:
            out["_quota_exhausted"] = True
        return out
    data = r.json()
    try:
        out = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return {"_error": f"{type(e).__name__}: {str(e)[:120]}"}
    out["_usage"] = data.get("usageMetadata", {}) or {}
    return out


# ── Перечитування артикула ──────────────────────────────────────────────────

# Схема з ОДНИМ питанням. Це не економія, а суть прийому: та сама модель на тих
# самих знімках #Ф4372 при повній схемі з двадцяти полів видала «HQ8708», потім
# «JQ8716», а при цій схемі — двічі правильний «JQ8356». Розпорошена увага і є
# джерелом вигадки.
ARTICLE_SCHEMA: Dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["article_text", "article_source_text"],
    "properties": {
        "article_text": {
            "type": ["string", "null"],
            "description": ("артикул виробника з бирки, ДОСЛІВНО (напр. CW2288-111). "
                            "Якщо бирки не видно або текст нерозбірливий — null. "
                            "НЕ вгадуй код за брендом."),
        },
        "article_source_text": {
            "type": ["string", "null"],
            "description": "дослівно весь рядок бирки, у якому стоїть артикул",
        },
    },
}


def verify_article(db: Session, model: str, api_key: str,
                   photos: List[pathlib.Path], value: str,
                   *, purpose: str, product_id: int,
                   anchor: Optional[str] = None) -> bool:
    """Друге, незалежне прочитання артикула. True — обидва читання збіглись.

    ⚠️ ЗАКРИТА ВІДМОВА. Помилка виклику (429, 5xx, розбір відповіді) означає
    «не підтверджено», а не «нехай іде». Артикул — єдине поле без захисту
    enum'ом, і ціна помилки в ньому найвища: він їде в аркуш і на маркетплейси.

    Вибірка, на якій це перевірялось, мала лише чотири чисті точки (решту
    зіпсували 429): три правильні прочитання збіглись, одне вигадане —
    розійшлось. Правило свідомо схиляє до зайвого питання людині, а не до
    зайвої довіри.
    """
    pred = call_gemini(model, api_key, photos, ARTICLE_SCHEMA)
    usage = pred.pop("_usage", {}) or {}
    err = pred.get("_error")
    ai_budget.record(db, model=model, purpose=purpose, product_id=product_id,
                     prompt_tokens=int(usage.get("promptTokenCount", 0)),
                     output_tokens=int(usage.get("candidatesTokenCount", 0)),
                     ok=not err, error=err)
    if err:
        return False
    second = pred.get("article_text")
    return _article_reads_agree(value, second, anchor)


def _code_tokens(text_: Optional[str]) -> set:
    """Нормалізовані коди з рядка: «9-26201-25-170/9-26201-43-170» → два коди.

    Увесь рядок цілком — теж код: «CW2288 111» на бирці Nike пишуть із
    пробілом, і розрізати його на «CW2288» + «111» означало б загубити збіг
    із «CW2288-111», який раніше давав _norm_code.
    """
    import re as _re
    parts = {_norm_code(t) for t in _re.split(r"[\s/,;|]+", text_ or "")}
    parts.add(_norm_code(text_))
    return {t for t in parts if len(t) >= 4}


def _article_reads_agree(first: str, second: Optional[str], anchor: Optional[str]) -> bool:
    """Чи два незалежні читання підтверджують артикул.

    Точний збіг — очевидно. Але на бирках Caprice поруч стоять ДВА коди
    («9-26201-25-170 / 9-26201-43-170»), і один прохід бере один, другий —
    інший: точне порівняння відкидало читабельну бирку. Тому згодою вважаємо
    й те, що перший код є серед кодів другого читання, і те, що ОБИДВА коди
    є в дослівному рядку бирки з першого читання: тоді розбіжність не про
    існування коду, а про те, який із двох обрати.
    """
    if not second:
        return False
    f = _norm_code(first)
    sec = _code_tokens(second)
    if f in sec:
        return True
    anc = _code_tokens(anchor)
    return bool(anc) and f in anc and bool(sec & anc)


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
        f"SELECT {sel}, b.brandname AS brand_name, p.marking, p.gtin, p.model, "
        f"p.price, p.sizeeu, p.measurementscm, p.typeid, p.productnumber, p.season "
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
            confirmed.append((field, value, "profile"))
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


def _number_matches(card_number: Optional[str], sticker_number: Optional[str]) -> bool:
    """Чи стікер належить ЦЬОМУ товару.

    Порівнюємо цифри й, якщо є, літеру-префікс (ф/f/Ф → Ф). Цифри мусять
    збігатися завжди: у цій базі '#4419' і '#Ф4419' — різні товари, але серед
    знімків ОДНІЄЇ картки стікер із тими самими цифрами й є її стікером.
    """
    def parts(v):
        v = (v or "").strip().lstrip("#")
        digits = "".join(ch for ch in v if ch.isdigit())
        letters = "".join(ch for ch in v if ch.isalpha()).upper().replace("F", "Ф")
        return digits, letters
    cd, cl = parts(card_number)
    sd, sl = parts(sticker_number)
    if not cd or not sd or cd != sd:
        return False
    return (not cl or not sl) or cl == sl


def _sticker_proposals(db, product_id, pred, current, photo_names, model,
                       proposed, below_threshold, already) -> Dict[str, Any]:
    """Ціна, розмір і замір зі стікера — лише коли номер на ньому наш."""
    text_ = (pred.get("sticker_text") or "").strip()
    number = (pred.get("sticker_number") or "").strip()
    card = current.get("productnumber") or ""
    if not text_ and not number:
        return {"present": False}
    if not _number_matches(card, number):
        # Чужий або неправильно прочитаний стікер — усе з нього відкидаємо.
        return {"present": True, "matched": False, "sticker_number": number, "text": text_}

    out = {"present": True, "matched": True, "text": text_}
    for key, (upd_field, threshold, lo, hi) in STICKER_FIELDS.items():
        raw = pred.get(key)
        conf = pred.get(f"{key}_confidence")
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if not (lo <= val <= hi):
            below_threshold.append((upd_field, raw, conf)); continue
        # Формат бази: розмір і замір — рядки без зайвих нулів, ціна — ціле.
        text_val = str(int(val)) if upd_field == "price" or val == int(val) else f"{val:g}"
        cur = current.get(upd_field)
        cur_s = f"{float(cur):g}" if cur not in (None, "") and str(cur).replace(".", "", 1).isdigit() else (cur or "")
        if _same_as_current(str(cur_s), text_val):
            already.append((upd_field, text_val)); continue
        if conf is not None and float(conf) >= threshold and field_proposals.propose(
                db, product_id, upd_field, text_val, conf, model=model,
                source_photos=photo_names, note=f"зі стікера: «{text_}»"[:200]):
            proposed.append((upd_field, text_val, conf))
        else:
            below_threshold.append((upd_field, text_val, conf))
    return out


def _same_as_current(current: Optional[str], proposed: Optional[str]) -> bool:
    """Порівняння без урахування регістру й країв — «HEY DUDE» = «Hey Dude»."""
    a = (current or "").strip().casefold()
    b = (proposed or "").strip().casefold()
    return bool(a) and a == b


def paid_key_available() -> bool:
    """Чи є другий ключ — із проєкту з увімкненим білінгом."""
    return bool((os.getenv("GEMINI_API_KEY_PAID") or "").strip())


def extract_and_propose(db: Session, product_id: int, photos: List[pathlib.Path],
                        *, model: str = None, purpose: str = "autofill",
                        api_key: Optional[str] = None, use_paid: bool = False) -> Dict[str, Any]:
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
            confirmed.append(("marking", marking_now, "barcode"))
        _profile_layer(db, product_id, pred_box, current,
                       proposed, already, confirmed, made)
        payload.setdefault("proposed", proposed)
        payload.setdefault("already_correct", already)
        payload["barcodes"] = [{"format": h.format, "text": h.text, "photo": h.photo}
                               for h in hits]
        # ⚠️ Ключ саме `confirmed`, а не `confirmed_by_barcode`: сюди пише й
        # шар профілю. Третій елемент кортежу називає шар — інакше звіт
        # приписував би штрихкоду те, що сказала власна база.
        payload["confirmed"] = confirmed
        return payload

    # ⚠️ ДВА КЛЮЧІ, НЕ ПЕРЕМИКАЧ. У Google рівень визначається ключем: одним і
    # тим самим ключем перемкнутись між безкоштовним і платним посеред роботи
    # неможливо. Тому за замовчуванням іде безкоштовний GEMINI_API_KEY (8
    # викликів на добу), а GEMINI_API_KEY_PAID — лише коли людина явно
    # підтвердила це в діалозі (use_paid=True). Стеля AI_MONTHLY_CAP_USD діє
    # на обидва.
    if use_paid and not api_key:
        api_key = os.getenv("GEMINI_API_KEY_PAID")
        if not api_key:
            return _finish({"ok": False, "reason": "немає GEMINI_API_KEY_PAID"})
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _finish({"ok": False, "reason": "немає GEMINI_API_KEY"})

    verdict = ai_budget.guard(db, purpose=purpose)
    if not verdict.allowed:
        # Відмова бюджету — НЕ помилка. Автозаповнення просто вимикається.
        return _finish({"ok": False, "reason": verdict.reason, "budget_blocked": True,
                        "spent_usd": verdict.spent_usd})

    schema = build_schema(db, type_id=current.get("typeid"))
    pred = call_gemini(model, api_key, photos, schema)
    usage = pred.pop("_usage", {}) or {}
    err = pred.get("_error")

    # Записуємо ЗАВЖДИ: провайдер тарифікує вхід навіть на провалі.
    cost = ai_budget.record(
        db, model=model, purpose=(purpose + ":paid" if use_paid else purpose),
        product_id=product_id,
        prompt_tokens=int(usage.get("promptTokenCount", 0)),
        output_tokens=int(usage.get("candidatesTokenCount", 0)),
        ok=not err, error=err,
    )
    if err:
        payload: Dict[str, Any] = {"ok": False, "reason": err, "cost_usd": cost}
        if pred.get("_quota_exhausted") and not use_paid:
            # Безкоштовна квота вичерпана. Це не помилка для людини, а вибір:
            # інтерфейс покаже діалог і, якщо вона погодиться, повторить запит
            # платним ключем.
            payload.update({"quota_exhausted": True,
                            "paid_available": paid_key_available(),
                            "estimate_usd": 0.003})
        return _finish(payload)

    pred_box.update(pred)
    photo_names = ",".join(p.name for p in photos)
    for field, (_t, _c, _fk, _label, upd_field) in CLOSED_FIELDS.items():
        value = pred.get(field)
        conf = pred.get(f"{field}_confidence")
        if not value:
            continue
        # Відсутність ознаки в нас позначається порожнім полем, а не записом.
        if is_absence_value(upd_field, value):
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

    # Сезон — обʼєднання наявного з побаченим; порожня різниця = уже правильно.
    seen_seasons = [t for t in (pred.get("season") or []) if isinstance(t, str)]
    if seen_seasons:
        merged = merge_seasons(current.get("season"), seen_seasons)
        conf = pred.get("season_confidence")
        if merged is None:
            already.append(("season", current.get("season") or ""))
        elif field_proposals.propose(db, product_id, "season", merged, conf, model=model,
                                     source_photos=photo_names,
                                     note=f"додано: {', '.join(t for t in SEASONS if t in set(seen_seasons) - set((current.get('season') or '').split(', ')))}"):
            proposed.append(("season", merged, conf))
            made["season"] = (merged, conf, None)
        else:
            below_threshold.append(("season", merged, conf))

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
            # ⚠️ АРТИКУЛ НЕ ПРОХОДИТЬ З ОДНОГО СВІДКА. Це єдине поле без захисту
            # enum'ом: решту модель ОБИРАЄ зі списку, а артикул ЧИТАЄ — і може
            # вигадати. Зафіксовано на живих товарах: #Ф4372 отримав «HQ8708»,
            # потім «JQ8716» при правді «JQ8356»; #Ф4128 — «1000200018-100070332»
            # при правді «100033704». Обидві вигадки мали певність 0.95 і
            # процитований якір, бо модель вигадує і якір теж.
            if _norm_code(val) in norm_codes:
                # СВІДОК ПЕРШИЙ І НАЙКРАЩИЙ: штрихкод із контрольною сумою.
                # Вигадка так збігтися не може, і додаткових питань не треба.
                conf = 1.0
                anchor = f"{anchor or ''} · підтверджено штрихкодом".strip(" ·")
            elif verify_article(db, model, api_key, photos, val,
                                purpose=purpose, product_id=product_id, anchor=anchor):
                # СВІДОК ДРУГИЙ: незалежне перечитування вузькою схемою. Певність
                # беремо власну, не 1.0 — це та сама модель, лише зосереджена.
                conf = max(float(conf or 0), 0.90)
                anchor = f"{anchor or ''} · підтверджено повторним прочитанням".strip(" ·")
                if codes:
                    anchor = f"{anchor} · у штрихкоді: {', '.join(codes)}"
            else:
                # Свідка немає. Пропозицію не створюємо взагалі: порожнє поле
                # коштує людині кілька секунд, а впевнено неправильний артикул
                # їде в аркуш і на маркетплейси.
                below_threshold.append((upd_field, val, conf))
                continue
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

    # ── Стікер: ціна / розмір / замір, лише якщо номер на ньому — цей товар ──
    sticker = _sticker_proposals(db, product_id, pred, current, photo_names, model,
                                 proposed, below_threshold, already)
    materials = _material_proposals(db, product_id, pred, photo_names, model,
                                    proposed, below_threshold, already)

    return _finish({"ok": True, "cost_usd": cost, "model": model,
                    "below_threshold": below_threshold, "photos": len(photos),
                    "sticker": sticker, "materials": materials})

"""Розпізнавання товару з фотографій → пропозиції для картки.

Тут зібрані всі три рубежі захисту від сміття в довідниках:

  1. `build_schema()` — перелік значень збирається з БД на льоту й іде в схему
     відповіді, тож модель ФІЗИЧНО не поверне значення, якого в нас немає;
  2. приймання йде через `field_proposals`, а звідти в картку — лише звичайним
     `update_product` зі строгим резолвером (без CREATE);
  3. поріг певності: нижче нього пропозиція взагалі не створюється.

Модуль НЕ пише в products. Він кладе пропозиції, і на цьому його роль
закінчується — рішення завжди за людиною.

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
    from services import ai_budget, field_proposals
    from services.shoe_attribute_normalization import is_dead_value
except ImportError:  # pragma: no cover
    from backend.services import ai_budget, field_proposals
    from backend.services.shoe_attribute_normalization import is_dead_value

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
        props[field] = {
            "type": ["string", "null"],
            # null у переліку — це і є «чесна відмова». Без нього модель мусить вгадувати.
            "enum": values + [None],
            "description": f"{label}; null, якщо на знімках не видно однозначно",
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
    props["brand_text"] = {"type": ["string", "null"],
                           "description": "бренд як НАПИСАНО на бирці/лого, дослівно"}
    props["article_text"] = {"type": ["string", "null"],
                             "description": "артикул виробника з бирки, дослівно (напр. CW2288-111)"}
    props["model_text"] = {"type": ["string", "null"],
                           "description": "назва моделі як написано на бирці"}
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

def extract_and_propose(db: Session, product_id: int, photos: List[pathlib.Path],
                        *, model: str = None, purpose: str = "autofill",
                        api_key: Optional[str] = None) -> Dict[str, Any]:
    """Розпізнати товар і скласти пропозиції. У products НЕ пише.

    Повертає звіт: чи дозволив бюджет, скільки коштувало, які поля запропоновано
    й скільки відсіяв поріг певності.
    """
    model = model or DEFAULT_MODEL
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "немає GEMINI_API_KEY"}
    photos = [p for p in photos if p.exists()]
    if not photos:
        return {"ok": False, "reason": "немає знімків"}

    verdict = ai_budget.guard(db, purpose=purpose)
    if not verdict.allowed:
        # Відмова бюджету — НЕ помилка. Автозаповнення просто вимикається.
        return {"ok": False, "reason": verdict.reason, "budget_blocked": True,
                "spent_usd": verdict.spent_usd}

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
        return {"ok": False, "reason": err, "cost_usd": cost}

    photo_names = ",".join(p.name for p in photos)
    proposed, below_threshold = [], []
    for field, (_t, _c, _fk, _label, upd_field) in CLOSED_FIELDS.items():
        value = pred.get(field)
        conf = pred.get(f"{field}_confidence")
        if not value:
            continue
        if field_proposals.propose(db, product_id, upd_field, value, conf,
                                   model=model, source_photos=photo_names):
            proposed.append((upd_field, value, conf))
        else:
            below_threshold.append((upd_field, value, conf))

    # Технології — масив; у картку йдуть рядком через кому, як і зберігаються.
    techs = pred.get("technologies") or []
    if techs:
        csv = ", ".join(t.strip() for t in techs if t and t.strip())
        if csv and field_proposals.propose(db, product_id, "technology_name", csv,
                                           None, model=model, source_photos=photo_names):
            proposed.append(("technology_name", csv, None))

    # Текст із бирки. Артикул має найвищий поріг — помилка там найдорожча.
    for src, upd_field in (("article_text", "marking"), ("brand_text", "brand_name")):
        val = pred.get(src)
        if val and field_proposals.propose(db, product_id, upd_field, val, 0.9,
                                           model=model, source_photos=photo_names):
            proposed.append((upd_field, val, 0.9))

    return {"ok": True, "cost_usd": cost, "model": model,
            "proposed": proposed, "below_threshold": below_threshold,
            "photos": len(photos)}

# -*- coding: utf-8 -*-
"""Четвертий шар: сторінка виробника за АРТИКУЛОМ.

Навіщо
──────
Три наявні шари дивляться всередину: модель читає знімки, сканер — штрихкод,
профіль — наші минулі записи. Жоден не знає того, що виробник САМ опублікував
про цю пару. А на сторінці Caprice за артикулом `9-25404-45-855` прямо
написано: «Ширина взуття: G-ширина», «Висота вала: 13 см», «Висота каблука:
3,2 см», «Матеріал верху: шкіра», «Носок взуття: загострений». Це рівно той
довгий хвіст, заради якого все автозаповнення й починалось, — і його не видно
на жодному знімку.

Чому це НЕ суперечить рішенню «зворотного пошуку зображень не робимо»
──────────────────────────────────────────────────────────────────────
Там ішлося про пошук ЗА КАРТИНКОЮ: офіційного API немає, а скрейпери проти
ToS. Тут ключ точний і текстовий — артикул виробника, — а пошук іде офіційним
інструментом `google_search` самої моделі. Немає артикула → шар мовчить, як
профіль мовчить без моделі.

Межі, ті самі, що й у решти шарів
─────────────────────────────────
* НЕ пише в картку — лише пропозиції (`field_proposals`), рішення за людиною;
* закриті переліки беруться з наших довідників (`closed_enum_values`), тож
  вигадати значення поза словником модель не може;
* кожне значення мусить мати ДЖЕРЕЛО: без посилання пропозиція не створюється.
  Це головний запобіжник саме тут — модель «знає» багато брендів напамʼять і
  залюбки перекаже характеристики, яких на сторінці немає;
* витрата пишеться в `ai_budget`, стеля спільна.

⚠️ Інструмент `google_search` на БЕЗКОШТОВНОМУ ключі недоступний: 29.09.2026
звичайний виклик повертав 200, а той самий виклик із `tools=[{google_search}]`
— 429 RESOURCE_EXHAUSTED. Тому шар вимагає платного ключа
(`GEMINI_API_KEY_PAID`) і чесно каже про це, якщо його немає чи на ньому нема
коштів (402).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from services import ai_budget, field_proposals, photo_autofill
except ImportError:  # dual-import trap
    from backend.services import ai_budget, field_proposals, photo_autofill

logger = logging.getLogger(__name__)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

# Поля закритого переліку, які виробники реально публікують на сторінці товару.
# Підвид/стиль/сезон свідомо НЕ питаємо: це наша торгова таксономія, а не дані
# виробника, і збіг назв тут був би випадковим.
WEB_CLOSED_FIELDS = ("toe_shape", "fastening_type", "lining", "heel_type", "sole_type")

# Заміри зі сторінки → поле пропозиції. Значення в сантиметрах.
WEB_MEASUREMENTS: Dict[str, tuple] = {
    "heel_height_cm":  ("meas:heel",   0.5, 20.0),
    "shaft_height_cm": ("meas:height", 3.0, 60.0),
    "sole_thickness_cm": ("meas:sole_thickness", 0.3, 12.0),
}

# Матеріали за позиціями (ті самі позиції, що й у піктограмах ЄС).
WEB_MATERIALS = {"upper": "material:upper", "lining": "material:middle", "sole": "material:sole"}

PROMPT = (
    "Ти шукаєш ОФІЦІЙНІ характеристики конкретної пари взуття за артикулом виробника.\n"
    "Знайди сторінку цього артикула на сайті бренда або у великого рітейлера й випиши "
    "ЛИШЕ те, що там написано.\n"
    "⚠️ Головне правило: кожне значення мусить стояти на знайденій сторінці. Якщо ти "
    "не знайшов сторінки саме цього артикула — постав усюди null і вкажи found=false. "
    "НЕ переказуй те, що памʼятаєш про бренд, і не описуй схожі моделі: порожнє поле "
    "коштує кілька секунд ручної роботи, а вигадана характеристика псує картку товару "
    "й поїде на маркетплейси.\n"
    "Ширину колодки пиши літерою, як на сайті (G, W, F, H); «G-ширина» → «G».\n"
    "Заміри — числом у сантиметрах."
)


def _build_schema(db: Session) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "found": {"type": "boolean",
                  "description": "чи знайдено сторінку САМЕ цього артикула"},
        "source_url": {"type": ["string", "null"],
                       "description": "посилання на сторінку, з якої взято характеристики"},
        "model_name": {"type": ["string", "null"],
                       "description": "назва моделі, як її називає виробник (напр. «Melissa»)"},
        "width": {"type": ["string", "null"],
                  "description": "ширина колодки ЛІТЕРОЮ: G, W, F, H, D. «G-ширина» → «G». "
                                 "Немає на сторінці — null"},
        "technologies": {"type": "array", "items": {"type": "string"},
                         "description": "фірмові технології зі сторінки (напр. «CAPRICE AIRMOTION»); "
                                        "порожній масив, якщо не вказано"},
    }
    for field in WEB_CLOSED_FIELDS:
        values = photo_autofill.closed_enum_values(db, field)
        if not values:
            # Порожній перелік у діалекті Gemini = «будь-який рядок» (#Ф4420).
            continue
        label = photo_autofill.CLOSED_FIELDS[field][3]
        props[field] = {"type": ["string", "null"], "enum": values + [None],
                        "description": f"{label} за сторінкою виробника; null, якщо не вказано"}
    for key in WEB_MEASUREMENTS:
        props[key] = {"type": ["number", "null"],
                      "description": f"{key} — сантиметри зі сторінки; null, якщо не вказано"}
    for pos in WEB_MATERIALS:
        props[f"material_{pos}"] = {
            "type": ["string", "null"],
            "description": f"матеріал ({pos}) словами зі сторінки: шкіра, замша, текстиль, синтетика…",
        }
    props["confidence"] = {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "наскільки впевнено сторінка відповідає саме цьому артикулу"}
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


def _call(model: str, api_key: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Виклик із увімкненим пошуком Google.

    ⚠️ `google_search` і `responseSchema` разом Gemini не приймає, тому схему
    передаємо ТЕКСТОМ у промпті й розбираємо JSON з відповіді самі. Це послаблює
    перший рубіж, тож нижче кожне значення все одно проходить через наш
    резолвер закритих переліків.
    """
    import requests

    body = {
        "contents": [{"parts": [{"text": prompt + "\n\nПоверни СУВОРО один JSON-обʼєкт за схемою:\n"
                                         + json.dumps(schema, ensure_ascii=False)}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0},
    }
    try:
        r = requests.post(_ENDPOINT.format(m=model),
                          headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                          json=body, timeout=photo_autofill.REQUEST_TIMEOUT_S)
    except requests.RequestException as e:
        return {"_error": f"{type(e).__name__}: {str(e)[:200]}", "_network": True}
    if r.status_code != 200:
        return {"_error": f"HTTP {r.status_code}: {r.text[:800]}",
                "_status": r.status_code}
    data = r.json()
    try:
        cand = data["candidates"][0]
        txt = "".join(p.get("text", "") for p in cand["content"]["parts"])
    except (KeyError, IndexError):
        return {"_error": "порожня відповідь моделі"}
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return {"_error": "у відповіді немає JSON"}
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"_error": f"JSONDecodeError: {str(e)[:120]}"}
    # Джерела беремо з grounding-метаданих, а не лише зі слів моделі: посилання,
    # яке вона назвала сама, може бути таким самим вигаданим, як і значення.
    gm = cand.get("groundingMetadata") or {}
    out["_sources"] = [c.get("web", {}).get("uri") for c in (gm.get("groundingChunks") or [])
                       if c.get("web", {}).get("uri")]
    out["_queries"] = gm.get("webSearchQueries") or []
    out["_usage"] = data.get("usageMetadata", {}) or {}
    return out


def _norm_material(value: Optional[str]) -> Optional[str]:
    """Слова сторінки → наш словник матеріалів (відкритий, тож лише чистимо)."""
    v = (value or "").strip().lower()
    if not v:
        return None
    v = re.sub(r"\s*\(.*?\)\s*", " ", v).strip(" .,;")
    return v or None


def available(cur: Dict[str, Any]) -> bool:
    """Чи є з чим і чим шукати: артикул у картці + платний ключ.

    Потрібно, щоб спільна дія «Розпізнати» могла мовчки пропустити цей шар, а
    не падати з помилкою на кожному товарі без артикула.
    """
    return bool((cur.get("marking") or "").strip()) and bool(os.getenv("GEMINI_API_KEY_PAID"))


def layer(db: Session, product_id: int, cur: Dict[str, Any], *,
          proposed: List[Any], already: List[Any], confirmed: List[Any],
          made: Dict[str, tuple], model: Optional[str] = None,
          api_key: Optional[str] = None) -> Dict[str, Any]:
    """Шар у спільному прогоні: пише в ті самі списки, що й решта.

    Зустріч шарів — за тим самим правилом, що й у профілю: збіг ПІДІЙМАЄ
    певність, а розбіжність нічого не ховає — рішення лишається за шаром, що
    бачив саме цю пару (модель на знімках), а альтернатива зі сторінки
    виробника йде в підпис поруч. Для звірки це й потрібно: «на фото носок
    здався заокругленим, а виробник пише „загострений“» — видно обидва.
    """
    return _run(db, product_id, cur, proposed, already, confirmed, made,
                model=model, api_key=api_key)


def enrich_by_article(db: Session, product_id: int, *,
                      model: Optional[str] = None,
                      api_key: Optional[str] = None) -> Dict[str, Any]:
    """Окремий прогін лише цього шару (ендпоїнт /enrich-web, скрипти)."""
    cur = photo_autofill._current_values(db, product_id)
    proposed: List[Any] = []
    already: List[Any] = []
    confirmed: List[Any] = []
    made: Dict[str, tuple] = {}
    out = _run(db, product_id, cur, proposed, already, confirmed, made,
               model=model, api_key=api_key)
    out.setdefault("proposed", proposed)
    out.setdefault("already_correct", already)
    return out


def _run(db: Session, product_id: int, cur: Dict[str, Any],
         proposed: List[Any], already: List[Any], confirmed: List[Any],
         made: Dict[str, tuple], *, model: Optional[str] = None,
         api_key: Optional[str] = None) -> Dict[str, Any]:
    article = (cur.get("marking") or "").strip()
    brand = (cur.get("brand_name") or "").strip()
    if not article:
        return {"ok": False, "reason": "у картці немає артикула (поле «Маркування») — "
                                       "шукати нема за чим"}

    # Пошук доступний лише на платному ключі (на безкоштовному — 429).
    api_key = api_key or os.getenv("GEMINI_API_KEY_PAID")
    if not api_key:
        return {"ok": False, "reason": "пошук в інтернеті потребує платного ключа "
                                       "(GEMINI_API_KEY_PAID) — на безкоштовному Google його не дає",
                "needs_paid": True}

    verdict = ai_budget.guard(db, purpose="web_enrich")
    if not verdict.allowed:
        return {"ok": False, "reason": verdict.reason, "budget_blocked": True,
                "spent_usd": verdict.spent_usd}

    model = model or photo_autofill.DEFAULT_MODEL
    schema = _build_schema(db)
    photo_autofill._release_db(db)   # пошук у Google теж довгий — див. _release_db
    prompt = (f"{PROMPT}\n\nБренд: {brand or 'невідомий'}\nАртикул: {article}\n"
              f"Тип товару: {cur.get('type_name') or '—'}")
    pred = _call(model, api_key, prompt, schema)
    err = pred.get("_error")
    usage = pred.pop("_usage", {}) or {}
    cost = ai_budget.record(db, model=model, purpose="web_enrich", product_id=product_id,
                            prompt_tokens=int(usage.get("promptTokenCount", 0)),
                            output_tokens=int(usage.get("candidatesTokenCount", 0)),
                            ok=not err, error=err)
    if err:
        payload = {"ok": False, "reason": err, "cost_usd": cost, "article": article}
        if pred.get("_status") in (402, 429):
            payload["reason"] = ("Платний ключ недоступний (немає коштів або вичерпано ліміт) — "
                                 "поповни проєкт у Google AI Studio")
            payload["needs_paid"] = True
        return payload

    sources: List[str] = pred.get("_sources") or []
    if not pred.get("found") or not sources:
        # ⚠️ Немає сторінки — немає розмови. Саме тут модель найбільше схильна
        # «пригадати» характеристики бренда: без джерела нічого не приймаємо.
        return {"ok": True, "found": False, "cost_usd": cost, "article": article,
                "queries": pred.get("_queries") or [],
                "reason": "сторінку саме цього артикула не знайдено"}

    conf = float(pred.get("confidence") or 0)
    src_note = f"виробник за артикулом {article}: {sources[0]}"
    below: List[Any] = []

    def _try(field: str, value: Any) -> None:
        if value in (None, "", []):
            return
        val = str(value).strip()
        if not val:
            return
        if photo_autofill._same_as_current(cur.get(field), val):
            already.append((field, val))
            return
        if conf < field_proposals.threshold_for(field):
            below.append((field, val, conf))
            return

        prev = made.get(field)
        if prev:
            # Поле вже озвучив інший шар — це зустріч, а не перезапис.
            prev_value, prev_conf, prev_note = prev
            if photo_autofill._same_as_current(prev_value, val):
                # ДВА НЕЗАЛЕЖНІ ДЖЕРЕЛА ЗІЙШЛИСЬ: знімок і сторінка виробника.
                best = max(conf, float(prev_conf or 0))
                field_proposals.propose(db, product_id, field, val, best, model=model,
                                        source="photo+web",
                                        note=f"{prev_note + ' · ' if prev_note else ''}{src_note}"[:200])
                proposed[:] = [(f, v, best) if f == field else (f, v, c) for f, v, c in proposed]
                made[field] = (val, best, src_note)
            else:
                # Розбіжність. Рішення лишається за шаром, що бачив САМЕ цю пару,
                # але альтернативу показуємо поруч — заради звірки це й робилось.
                field_proposals.propose(db, product_id, field, prev_value, prev_conf, model=model,
                                        source="photo",
                                        note=f"{prev_note + ' · ' if prev_note else ''}"
                                             f"у виробника: {val} ({sources[0]})"[:200])
            return

        if field_proposals.propose(db, product_id, field, val, conf, model=model,
                                   source="web", note=src_note[:200]):
            proposed.append((field, val, conf))
            made[field] = (val, conf, src_note)
        else:
            below.append((field, val, conf))

    # Ширина — через наш нормалізатор: «G-ширина» → «G», сміття → нічого.
    try:
        from services.width_normalization import normalize_width
    except ImportError:
        from backend.services.width_normalization import normalize_width
    _try("width", normalize_width(pred.get("width")))

    _try("model", (pred.get("model_name") or "").strip() or None)

    for field in WEB_CLOSED_FIELDS:
        if field in photo_autofill.CLOSED_FIELDS:
            _try(photo_autofill.CLOSED_FIELDS[field][4], pred.get(field))

    for key, (upd_field, lo, hi) in WEB_MEASUREMENTS.items():
        raw = pred.get(key)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if not (lo <= v <= hi):
            below.append((upd_field, raw, conf))
            continue
        _try(upd_field, str(int(v)) if v == int(v) else f"{v:g}")

    for pos, upd_field in WEB_MATERIALS.items():
        _try(upd_field, _norm_material(pred.get(f"material_{pos}")))

    techs = [t.strip() for t in (pred.get("technologies") or []) if str(t).strip()]
    if techs:
        _try("technology_name", ", ".join(techs))

    photo_autofill._record_run(db, product_id, "web_enrich", model, [], pred,
                               {"ok": True, "proposed": proposed, "sources": sources})
    return {"ok": True, "found": True, "cost_usd": cost, "article": article,
            "model": model, "sources": sources, "confidence": conf,
            "below_threshold": below}

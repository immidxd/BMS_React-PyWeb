"""OLX-інтеграція (read-only v1).

Тягне власні оголошення продавця через офіційний OLX API (OAuth2), витягує
#Ф-номер з опису/заголовка (тим самим регексом, що й Telegram), апсертить у
`olx_adverts` і лінкує до products. Маркер `published_olx` у рядку товару =
є active-оголошення на цей номер (аналог telegram).

OAuth2-флоу (одноразова авторизація продавця):
  1. Фронт відкриває build_authorize_url() у браузері → продавець логіниться в OLX.
  2. OLX редіректить на OLX_REDIRECT_URI з ?code=... → /olx/oauth/callback.
  3. exchange_code() міняє code на access+refresh токени → olx_oauth (id=1).
  4. sync_adverts() далі сам оновлює access через refresh_token.

Конфіг (.env):
  OLX_CLIENT_ID, OLX_CLIENT_SECRET   — з developer.olx.ua (реєстрація застосунку)
  OLX_REDIRECT_URI                   — напр. http://localhost:8000/api/publications/olx/oauth/callback
  OLX_API_BASE                       — за замовч. https://www.olx.ua
  OLX_SCOPE                          — за замовч. "v2 read write"
"""

from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
OLX_API_BASE = os.getenv("OLX_API_BASE", "https://www.olx.ua").rstrip("/")
OLX_SCOPE = os.getenv("OLX_SCOPE", "v2 read write")
OLX_API_VERSION = os.getenv("OLX_API_VERSION", "2.0")
# active/limited вважаємо «опубліковано» (видиме покупцям). Решта — ні.
OLX_PUBLISHED_STATUSES = {"active", "limited"}
_REFRESH_SKEW_SEC = 120  # оновлюємо access за 2хв до протухання


def _client_id() -> str:
    return os.getenv("OLX_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.getenv("OLX_CLIENT_SECRET", "").strip()


def _redirect_uri() -> str:
    return os.getenv(
        "OLX_REDIRECT_URI",
        "http://localhost:8000/api/publications/olx/oauth/callback",
    ).strip()


def is_configured() -> bool:
    """Чи задані client_id/secret — без них OAuth неможливий."""
    return bool(_client_id() and _client_secret())


# ── Token storage (olx_oauth, single-row id=1) ───────────────────────────────
def _load_tokens(db: Session) -> Optional[dict]:
    row = db.execute(text(
        "SELECT access_token, refresh_token, token_type, scope, expires_at "
        "FROM olx_oauth WHERE id = 1"
    )).fetchone()
    if not row:
        return None
    return {
        "access_token": row[0],
        "refresh_token": row[1],
        "token_type": row[2],
        "scope": row[3],
        "expires_at": row[4],
    }


def _save_tokens(db: Session, tok: dict) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(tok.get("expires_in", 3600))
    )
    db.execute(text("""
        INSERT INTO olx_oauth (id, access_token, refresh_token, token_type, scope, expires_at, updated_at)
        VALUES (1, :at, :rt, :tt, :sc, :exp, now())
        ON CONFLICT (id) DO UPDATE SET
            access_token  = EXCLUDED.access_token,
            -- OLX не завжди повертає новий refresh_token при refresh — лишаємо старий
            refresh_token = COALESCE(EXCLUDED.refresh_token, olx_oauth.refresh_token),
            token_type    = EXCLUDED.token_type,
            scope         = EXCLUDED.scope,
            expires_at    = EXCLUDED.expires_at,
            updated_at    = now()
    """), {
        "at": tok.get("access_token"),
        "rt": tok.get("refresh_token"),
        "tt": tok.get("token_type"),
        "sc": tok.get("scope"),
        "exp": expires_at,
    })
    db.commit()


def is_authorized(db: Session) -> bool:
    tok = _load_tokens(db)
    return bool(tok and (tok.get("access_token") or tok.get("refresh_token")))


# ── OAuth2 flow ───────────────────────────────────────────────────────────────
def build_authorize_url() -> str:
    """URL для одноразової авторизації продавця в браузері."""
    params = {
        "client_id": _client_id(),
        "response_type": "code",
        "scope": OLX_SCOPE,
        "redirect_uri": _redirect_uri(),
    }
    return f"{OLX_API_BASE}/oauth/authorize/?{urlencode(params)}"


def _token_request(payload: dict) -> dict:
    """POST на token endpoint, повертає dict токенів або кидає."""
    resp = requests.post(
        f"{OLX_API_BASE}/api/open/oauth/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OLX token request failed [{resp.status_code}]: {resp.text[:300]}")
    return resp.json()


def exchange_code(db: Session, code: str) -> dict:
    """authorization_code → access+refresh, зберігає в olx_oauth."""
    tok = _token_request({
        "grant_type": "authorization_code",
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "code": code,
        "redirect_uri": _redirect_uri(),
        "scope": OLX_SCOPE,
    })
    _save_tokens(db, tok)
    logger.info("OLX OAuth: code exchanged, tokens stored")
    return tok


def _refresh_access(db: Session, refresh_token: str) -> dict:
    tok = _token_request({
        "grant_type": "refresh_token",
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "refresh_token": refresh_token,
    })
    _save_tokens(db, tok)
    logger.info("OLX OAuth: access token refreshed")
    return tok


def get_access_token(db: Session) -> Optional[str]:
    """Валідний access_token (оновлює через refresh за потреби) або None."""
    tok = _load_tokens(db)
    if not tok:
        return None
    expires_at = tok.get("expires_at")
    now = datetime.now(timezone.utc)
    # expires_at з БД tz-aware; запас _REFRESH_SKEW_SEC
    still_valid = (
        tok.get("access_token")
        and expires_at is not None
        and expires_at > now + timedelta(seconds=_REFRESH_SKEW_SEC)
    )
    if still_valid:
        return tok["access_token"]
    # Протух/нема — пробуємо refresh
    if tok.get("refresh_token"):
        try:
            new = _refresh_access(db, tok["refresh_token"])
            return new.get("access_token")
        except Exception as e:
            logger.error(f"OLX token refresh failed: {e}")
            return None
    return tok.get("access_token")  # може бути None


# ── Adverts ───────────────────────────────────────────────────────────────────
def _api_get(access_token: str, path: str, params: dict) -> dict:
    resp = requests.get(
        f"{OLX_API_BASE}{path}",
        params=params,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Version": OLX_API_VERSION,
            "Accept": "application/json",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OLX API {path} failed [{resp.status_code}]: {resp.text[:300]}")
    return resp.json()


def _fetch_all_adverts(access_token: str, page_limit: int = 50, max_pages: int = 200) -> List[dict]:
    """Усі власні оголошення (пагінація limit/offset)."""
    out: List[dict] = []
    offset = 0
    for _ in range(max_pages):
        data = _api_get(access_token, "/api/partner/adverts", {"limit": page_limit, "offset": offset})
        items = data.get("data") or []
        if not items:
            break
        out.extend(items)
        if len(items) < page_limit:
            break
        offset += page_limit
    return out


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
    return None


# Номери товарів НЕ лише `#Ф…`: у БД ~38% Ф, ~40% інші літери (`#Р1`,`#У889`,
# `#Т3219`), ~30% голі цифри (`#1155`). Майже всі з `#`-префіксом. Тому ловимо
# БУДЬ-ЯКИЙ `#`-токен (опц. 1-3 літери + цифри + опц. `-N`), а також Ф-номер
# навіть без `#`. Capture зберігає літеру (на відміну від telegram-регексу).
# Хибні кандидати (сторонні хештеги типу Funko `#961`, модельні коди) відсіює
# _resolve_product_id — лінкуємо ЛИШЕ те, що збігається з реальним товаром.
_OLX_HASH_RE = re.compile(
    r'#\s*([A-Za-zА-Яа-яЁёІіЇїЄєҐґ]{0,3}\d{1,6}(?:-\d+)?)', re.UNICODE)
_OLX_F_RE = re.compile(
    r'(?<![0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ])([Фф]\d{1,6}(?:-\d+)?)', re.UNICODE)


def _extract_candidates(advert: dict) -> List[str]:
    """Усі ймовірні номери з external_id+title+description. Літерні — раніше за
    голі цифри (менше хибних збігів зі сторонніми #цифрами). Дедуп, порядок збережено."""
    text_all = " ".join(
        p for p in (advert.get("external_id"), advert.get("title"), advert.get("description")) if p
    )
    cands: List[str] = [m.group(1) for m in _OLX_HASH_RE.finditer(text_all)]
    cands += [m.group(1) for m in _OLX_F_RE.finditer(text_all)]
    seen, uniq = set(), []
    for c in cands:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    uniq.sort(key=lambda c: c[:1].isdigit())  # літерні (False) перші
    return uniq


def _resolve_product_id(db: Session, raw: Optional[str]) -> Optional[int]:
    """Лінк до products за номером (усі форми #Ф/Ф/#/raw). Ф-форми лише для
    голих цифр (бо опис міг мати `#4111`, а товар = `#Ф4111`)."""
    if not raw:
        return None
    forms = {raw, f"#{raw}"}
    if raw[:1].isdigit():               # голі цифри → спробувати і Ф-форми
        forms |= {f"Ф{raw}", f"#Ф{raw}"}
    row = db.execute(text("""
        SELECT id FROM products
        WHERE productnumber = ANY(:forms)
        ORDER BY id LIMIT 1
    """), {"forms": list(forms)}).fetchone()
    return row[0] if row else None


def _upsert_advert(db: Session, advert: dict) -> Tuple[bool, bool]:
    """Апсерт одного оголошення. Повертає (created, linked)."""
    olx_id = advert.get("id")
    if olx_id is None:
        return (False, False)

    # Перебираємо кандидатів (літерні перші) — лінкуємо до ПЕРШОГО, що збігся
    # з реальним товаром. Якщо жоден — лишаємо перший кандидат як raw (unlinked).
    cands = _extract_candidates(advert)
    raw, product_id = (cands[0] if cands else None), None
    for cand in cands:
        pid = _resolve_product_id(db, cand)
        if pid:
            raw, product_id = cand, pid
            break
    # BMS-створені оголошення несуть external_id = чистий номер (напр. «А1256»),
    # який регекс кандидатів не ловить (без # чи Ф). Лінкуємо за ним напряму.
    if not product_id:
        ext_id = (advert.get("external_id") or "").strip()
        pid = _resolve_product_id(db, ext_id) if ext_id else None
        if pid:
            raw, product_id = ext_id, pid

    price_obj = advert.get("price") or {}
    price = price_obj.get("value") if isinstance(price_obj, dict) else None
    currency = price_obj.get("currency") if isinstance(price_obj, dict) else None
    url = advert.get("url")
    if isinstance(advert.get("url"), dict):  # деякі версії віддають {href:...}
        url = advert["url"].get("href")

    res = db.execute(text("""
        INSERT INTO olx_adverts (
            olx_id, product_id, product_number_raw, title, description, status,
            url, external_id, category_id, price, currency, posted_at, valid_to, last_synced_at
        ) VALUES (
            :olx_id, :pid, :raw, :title, :descr, :status,
            :url, :ext, :cat, :price, :cur, :posted, :valid, now()
        )
        ON CONFLICT (olx_id) DO UPDATE SET
            -- Не втрачаємо вже наявний лінк, якщо sync цього разу не визначив номер.
            product_id         = COALESCE(EXCLUDED.product_id, olx_adverts.product_id),
            product_number_raw = COALESCE(EXCLUDED.product_number_raw, olx_adverts.product_number_raw),
            title              = EXCLUDED.title,
            description        = EXCLUDED.description,
            status             = EXCLUDED.status,
            url                = EXCLUDED.url,
            external_id        = COALESCE(EXCLUDED.external_id, olx_adverts.external_id),
            category_id        = EXCLUDED.category_id,
            price              = EXCLUDED.price,
            currency           = EXCLUDED.currency,
            valid_to           = EXCLUDED.valid_to,
            last_synced_at     = now()
        RETURNING (xmax = 0) AS inserted
    """), {
        "olx_id": olx_id,
        "pid": product_id,
        "raw": raw,
        "title": (advert.get("title") or "")[:300],
        "descr": advert.get("description"),
        "status": advert.get("status"),
        "url": (url or "")[:500] or None,
        "ext": (advert.get("external_id") or "")[:120] or None,
        "cat": advert.get("category_id"),
        "price": price,
        "cur": (currency or "")[:8] or None,
        "posted": _parse_dt(advert.get("created_at")),
        "valid": _parse_dt(advert.get("valid_to")),
    })
    row = res.fetchone()
    created = bool(row[0]) if row else False
    return (created, product_id is not None)


def sync_adverts(db: Session) -> Dict:
    """Головний вхід: тягне всі оголошення, апсертить, лінкує. Повертає статистику."""
    if not is_configured():
        return {"ok": False, "error": "OLX not configured (OLX_CLIENT_ID/OLX_CLIENT_SECRET)"}
    access = get_access_token(db)
    if not access:
        return {"ok": False, "error": "OLX not authorized — пройдіть OAuth (olx/oauth/start)"}

    try:
        adverts = _fetch_all_adverts(access)
    except Exception as e:
        logger.error(f"OLX sync: fetch failed: {e}")
        return {"ok": False, "error": str(e)}

    created = updated = linked = 0
    seen_ids: List[int] = []
    for adv in adverts:
        try:
            was_created, was_linked = _upsert_advert(db, adv)
            created += 1 if was_created else 0
            updated += 0 if was_created else 1
            linked += 1 if was_linked else 0
            if adv.get("id") is not None:
                seen_ids.append(int(adv["id"]))
        except Exception as e:
            logger.warning(f"OLX sync: upsert advert {adv.get('id')} failed: {e}")
    db.commit()

    # Прибрати оголошення, яких більше нема у видачі (зняті/видалені на OLX).
    removed = 0
    if seen_ids:
        r = db.execute(text(
            "DELETE FROM olx_adverts WHERE olx_id <> ALL(:ids)"
        ), {"ids": seen_ids})
        removed = r.rowcount or 0
        db.commit()

    logger.info(f"OLX sync: total={len(adverts)} created={created} updated={updated} linked={linked} removed={removed}")
    return {
        "ok": True,
        "total": len(adverts),
        "created": created,
        "updated": updated,
        "linked": linked,
        "removed": removed,
    }


def get_status(db: Session) -> Dict:
    """Статус інтеграції для UI."""
    tok = _load_tokens(db)
    return {
        "configured": is_configured(),
        "authorized": bool(tok and (tok.get("access_token") or tok.get("refresh_token"))),
        "expires_at": tok["expires_at"].isoformat() if (tok and tok.get("expires_at")) else None,
        "advert_count": db.execute(text("SELECT COUNT(*) FROM olx_adverts")).scalar() or 0,
        "active_count": db.execute(text(
            "SELECT COUNT(*) FROM olx_adverts WHERE status = ANY(:s)"
        ), {"s": list(OLX_PUBLISHED_STATUSES)}).scalar() or 0,
        "config": _load_config(db),
    }


# ══════════════════════════════════════════════════════════════════════════════
# OLX WRITE v2: створення оголошень (пост на сайт)
# ══════════════════════════════════════════════════════════════════════════════
OLX_TITLE_MAX = 70          # OLX обрізає довгі заголовки
OLX_MAX_IMAGES = 8


def _prom():
    try:
        from services import prom_service, olx_pricing
    except ImportError:
        from backend.services import prom_service, olx_pricing
    return prom_service, olx_pricing


def _api_post(access_token: str, path: str, body: dict) -> Tuple[int, dict]:
    """POST на OLX partner API. Повертає (status_code, json|{})."""
    resp = requests.post(
        f"{OLX_API_BASE}{path}", json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Version": OLX_API_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }, timeout=40,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:400]}
    return resp.status_code, data


# ── Мапа типів BMS → категорії OLX (усі товари: взуття/сумки/одяг/аксесуари) ──
# Ідентифікатори взято з живого дерева категорій OLX. Стать: Жіноче взуття 2015,
# Чоловіче 2014, Дитяче 542; одяг — Жіночий 2016 / Чоловічий 2107.
_OLX_CAT_WOMEN = {
    "туфлі": 2619, "балетки": 2619, "мокасини": 2619, "лофери": 2619,
    "кросівки": 2623, "кеди": 2624, "сліпони": 2624,
    "черевики": 2618, "чоботи": 2618, "ботинки": 2618, "напівботинки": 2618,
    "напівсапоги": 2618, "сапоги": 2618, "уги": 2618, "валянки": 2618, "дутики": 2618,
    "босоніжки": 2628, "сандалі": 2628, "шльопанці": 2628, "сабо": 2628,
    "в'єтнамки": 2628, "тапки": 2665, "тапочки": 2665,
}
_OLX_CAT_MEN = {
    "туфлі": 2695, "мокасини": 2695, "балетки": 2695, "лофери": 2695, "топсайдери": 2695,
    "кросівки": 2687, "кеди": 2688, "сліпони": 2688,
    "черевики": 2689, "чоботи": 2689, "ботинки": 2689, "напівботинки": 2689,
    "напівсапоги": 2689, "сапоги": 2689, "уги": 2689, "валянки": 2689, "дутики": 2689,
    "босоніжки": 2695, "сандалі": 2695, "шльопанці": 2695, "сабо": 2695,
    "в'єтнамки": 2695, "тапки": 2727, "тапочки": 2727,
}
_OLX_CAT_WOMEN_OTHER = 2665
_OLX_CAT_MEN_OTHER = 2727
_OLX_CAT_KIDS = 542

# Не-взуття. Значення: int (стать не важлива) або {ч,ж} (гендерна категорія).
_OLX_MISC: Dict[str, Any] = {
    # Сумки / подорож / переноска
    "сумка": 552, "рюкзак": 3142, "валіза": 3208, "гаманець": 3143,
    "портмоне": 3143, "косметичка": 3143, "пенал": 3143, "парасолька": 3144,
    "набір": 552,
    # Аксесуари
    "ремінь": 3158, "пасок": 3158, "окуляри": 3148, "шарф": 3147,
    "рукавиці": 3147, "рукавички": 3147, "краватка": 3149,
    "шапка": 2141, "кепка": 2131, "капелюх": 2144, "тюрбан": 2141, "бандана": 2141,
    # Парфумерія
    "парфуми": 2728, "духи": 2728,
    # Верх (гендерний)
    "футболка": {"ч": 2736, "ж": 2838}, "майка": {"ч": 2736, "ж": 2838},
    "сорочка": {"ч": 2737, "ж": 2868}, "блуза": {"ж": 2839}, "туніка": {"ж": 2839},
    "светр": {"ч": 2738, "ж": 2855}, "кофта": {"ч": 2738, "ж": 2840},
    "джемпер": {"ч": 2738, "ж": 2840}, "кардиган": {"ч": 2738, "ж": 2869},
    "худі": {"ч": 2738, "ж": 2880}, "толстовка": {"ч": 2738, "ж": 2900},
    # Верхній одяг
    "куртка": {"ч": 2739, "ж": 2903}, "пальто": {"ч": 2760, "ж": 2883},
    "пуховик": {"ч": 2797, "ж": 2739}, "плащ": {"ч": 2739, "ж": 2858},
    "вітровка": {"ч": 2739, "ж": 2893}, "жилетка": {"ч": 2803, "ж": 2739},
    "жилет": {"ч": 2803, "ж": 2739},
    # Низ
    "штани": {"ч": 2742, "ж": 2845}, "брюки": {"ч": 2742, "ж": 2845},
    "джинси": {"ч": 2741, "ж": 2844}, "легінси": {"ж": 2913}, "лосини": {"ж": 2913},
    "шорти": {"ч": 2740, "ж": 2951}, "спідниця": {"ж": 2842},
    # Сукні / костюми / комбінезони
    "сукня": {"ж": 2891}, "плаття": {"ж": 2891},
    "костюм": {"ч": 2743, "ж": 2847}, "піджак": {"ч": 2743},
    "комбінезон": {"ч": 2744, "ж": 2846},
    # Домашній одяг / сон
    "піжама": {"ч": 2745, "ж": 2864}, "халат": {"ч": 2745, "ж": 2876},
    "нічна_сорочка": {"ж": 2897},
    # Білизна
    "білизна": {"ч": 2010, "ж": 2011}, "труси": {"ч": 2010, "ж": 2300},
    "підштанники": {"ч": 2010}, "купальник": {"ж": 2299}, "бюстгальтер": {"ж": 2011},
    "носки": {"ж": 2314}, "шкарпетки": {"ж": 2314}, "колготи": {"ж": 2310},
    # Аксесуари для взуття
    "устілки": 3162, "устілка": 3162,
    # Домашній текстиль (Дім і сад / Предмети інтер'єру / Текстиль)
    "рушник": 529, "постіль": 529, "плед": 529, "ковдра": 529,
    "простирадло": 529, "покривало": 529,
}

# Одруки/латинські двійники → канонічний тип.
_TYPE_ALIASES = {
    "cумка": "сумка", "босоніжкиї": "босоніжки", "шльпанці": "шльопанці",
    "шльлопанці": "шльопанці", "ботінки": "ботинки", "ботинок": "ботинки",
    "напісапоги": "напівсапоги", "комбенізон": "комбінезон", "комбінізон": "комбінезон",
    "сандалії": "сандалі", "блузка": "блуза", "тапчки": "тапки",
    "уггі": "уги", "угі": "уги", "еспадрилії": "еспадрильї", "еспадрильї": "еспадрильї",
    "нічна сорочка": "нічна_сорочка", "брюки": "брюки",
}

_SHOE_PREFIXES = (
    "череви", "чобо", "боти", "боті", "напівбот", "напівсап", "напісап", "сапог",
    "кросів", "кед", "туфл", "босон", "санда", "мокас", "балет", "шльоп", "шльп",
    "сліп", "лофер", "уг", "тапк", "тапоч", "сабо", "в'єтнам", "валян", "дутик",
    "еспадр", "челсі", "батфор", "ботильй", "бутс", "футзал", "сороконож", "чешк",
    "галош", "топсайд", "трекінг", "сліпон", "взутт")   # «Взуття» без підтипу → інше взуття


def _norm_type(typename: Optional[str]) -> str:
    t = str(typename or "").strip().lower()
    return _TYPE_ALIASES.get(t, t)


def _is_shoe_type(t: str) -> bool:
    return any(t.startswith(p) for p in _SHOE_PREFIXES)


def olx_category_for(typename: Optional[str], gendername: Optional[str],
                     is_kids: Optional[bool] = None) -> Optional[int]:
    """Категорія OLX за типом+статтю BMS (статична мапа). None — якщо тип не
    розпізнано (тоді викликач пробує навчання з наявних оголошень).

    `is_kids` передає викликач (resolve_category) з prom_service._is_kids — там
    дитяче визначається за РОЗМІРОМ (≤34.5) та описом, а не лише за назвою
    статі. Інакше дитячі «Унісекс» (напр. Crocs 29-30) їхали в жіноче взуття."""
    t = _norm_type(typename)
    g = str(gendername or "").strip().lower()
    if not t:
        return None
    if is_kids is None:
        is_kids = any(k in g for k in ("діт", "дит", "дів", "хлоп"))
    is_men = g.startswith("чол")
    # 1) Взуття
    if _is_shoe_type(t):
        if is_kids:
            return _OLX_CAT_KIDS
        table = _OLX_CAT_MEN if is_men else _OLX_CAT_WOMEN
        return table.get(t, _OLX_CAT_MEN_OTHER if is_men else _OLX_CAT_WOMEN_OTHER)
    # 2) Не-взуття (сумки/одяг/аксесуари)
    v = _OLX_MISC.get(t)
    if v is None:
        return None
    if isinstance(v, int):
        return v
    key = "ч" if is_men else "ж"          # унісекс/жіноче → жіноче
    return v.get(key) or v.get("ж") or v.get("ч") or next(iter(v.values()), None)


def _learn_category(db: Session, product: dict) -> Optional[int]:
    """Найчастіша OLX-категорія серед НАЯВНИХ оголошень товарів того ж підтипу/типу
    (як робить Prom). Дає точну категорію навіть для типів поза статичною мапою."""
    tid, sid = product.get("typeid"), product.get("subtypeid")
    if not (tid or sid):
        return None
    # Спершу шукаємо збіг за ПІДТИПОМ (точніше), потім за типом.
    for by_subtype in (True, False):
        if by_subtype and not sid:
            continue
        cond = "p.subtypeid = :sid" if by_subtype else "p.typeid = :tid"
        row = db.execute(text(f"""
            SELECT oa.category_id, COUNT(*) c
            FROM olx_adverts oa JOIN products p ON p.id = oa.product_id
            WHERE oa.category_id IS NOT NULL AND {cond}
            GROUP BY oa.category_id ORDER BY c DESC LIMIT 1
        """), {"tid": tid, "sid": sid}).first()
        if row:
            return int(row[0])
    return None


# ── Дерево категорій + спуск до ЛИСТА (OLX постить лише в листові) ────────────
_CATEGORY_TREE_CACHE: dict = {}


def _category_tree(access: str) -> dict:
    """{byid, children} усього дерева категорій OLX (кеш у пам'яті)."""
    if _CATEGORY_TREE_CACHE:
        return _CATEGORY_TREE_CACHE
    try:
        cats = (_api_get(access, "/api/partner/categories", {}) or {}).get("data") or []
    except Exception:
        return {"byid": {}, "children": {}}
    byid = {c["id"]: c for c in cats}
    children: Dict[Optional[int], list] = {}
    for c in cats:
        children.setdefault(c.get("parent_id"), []).append(c)
    if byid:
        _CATEGORY_TREE_CACHE.update({"byid": byid, "children": children})
    return {"byid": byid, "children": children}


def _descend_to_leaf(access: str, cat_id: int, product: dict) -> int:
    """Спустити (можливо батьківську) категорію до ЛИСТА. Матч дитини за
    підтипом/типом BMS; інакше «Інше…»; інакше перша дитина."""
    tree = _category_tree(access)
    byid, children = tree.get("byid", {}), tree.get("children", {})
    if not byid or cat_id not in byid:
        return cat_id
    def _base(s: str) -> str:
        # нормалізація для толерантності до множини/відмінка: балетки↔балетк
        return str(s or "").strip().lower().rstrip("иіяьа")
    want = [w for w in (_base(product.get("subtypename")), _base(product.get("typename"))) if w]
    cur, seen = cat_id, set()
    for _ in range(6):
        kids = children.get(cur, [])
        if not kids:
            return cur  # лист
        pick = None
        # 1) збіг за ПОВНОЮ назвою дитини (не префіксом!) — щоб «Кросівки» не
        #    впіймали «Кросівки-шкарпетки», а «Штани» — «Штани чінос».
        for w in want:
            for ch in kids:
                if _base(ch.get("name")) == w:
                    pick = ch
                    break
            if pick:
                break
        if not pick:                                     # 2) «Інше/Інша/Інший»
            pick = next((ch for ch in kids
                         if str(ch.get("name") or "").strip().lower().startswith("інш")), None)
        if not pick:                                     # 3) перша дитина
            pick = kids[0]
        if pick["id"] in seen:
            return pick["id"]
        seen.add(pick["id"])
        cur = pick["id"]
    return cur


def resolve_category(db: Session, product: dict) -> Optional[int]:
    """Категорія OLX (ЗАВЖДИ листова): статична мапа → навчання → спуск до листа."""
    prom_service, _ = _prom()
    try:
        kids = prom_service._is_kids(product)   # за розміром (≤34.5) та описом
    except Exception:
        kids = None
    base = olx_category_for(product.get("typename"), product.get("gendername"), is_kids=kids) \
        or _learn_category(db, product)
    if not base:
        return None
    access = get_access_token(db)
    if not access:
        return base
    return _descend_to_leaf(access, base, product)


# ── Конфіг ────────────────────────────────────────────────────────────────────
def _load_config(db: Session) -> dict:
    row = db.execute(text("""
        SELECT ad_spend, advertiser_type, use_delivery, branch_payment,
               default_city_id, default_district_id, default_lat, default_lon,
               contact_name, contact_phone, updated_at
        FROM olx_config WHERE id = 1
    """)).mappings().first()
    if not row:
        return {"ad_spend": 0, "advertiser_type": "business", "use_delivery": True,
                "branch_payment": False}
    d = dict(row)
    d["ad_spend"] = float(d.get("ad_spend") or 0)
    return d


def save_config(db: Session, **fields) -> dict:
    allowed = {"ad_spend", "advertiser_type", "use_delivery", "branch_payment",
               "default_city_id", "default_district_id", "default_lat", "default_lon",
               "contact_name", "contact_phone"}
    sets, params = [], {}
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = :{k}")
            params[k] = v
    if sets:
        db.execute(text(
            f"UPDATE olx_config SET {', '.join(sets)}, updated_at = now() WHERE id = 1"), params)
        db.commit()
    return _load_config(db)


def _ensure_defaults(db: Session, access: str, cfg: dict) -> dict:
    """Заповнити контакт/локацію з акаунта OLX, якщо в конфізі порожньо."""
    if cfg.get("contact_phone") and cfg.get("default_city_id"):
        return cfg
    patch = {}
    try:
        me = (_api_get(access, "/api/partner/users/me", {}) or {}).get("data") or {}
        if not cfg.get("contact_name") and me.get("name"):
            patch["contact_name"] = me["name"][:120]
        if not cfg.get("contact_phone") and me.get("phone"):
            patch["contact_phone"] = me["phone"][:40]
    except Exception:
        pass
    if not cfg.get("default_city_id"):
        # Взяти локацію з будь-якого наявного оголошення акаунта.
        try:
            advs = _fetch_all_adverts(access, page_limit=1, max_pages=1)
            if advs:
                l = advs[0].get("location") or {}
                if l.get("city_id"):
                    patch.update({"default_city_id": l.get("city_id"),
                                  "default_district_id": l.get("district_id"),
                                  "default_lat": str(l.get("latitude") or "") or None,
                                  "default_lon": str(l.get("longitude") or "") or None})
        except Exception:
            pass
    if patch:
        cfg = save_config(db, **patch)
    return cfg


# ── Пакети (LISTING_FEE) та атрибути категорії — з кешем у пам'яті ────────────
_PACKETS_CACHE: Dict[int, dict] = {}
_ATTR_DEFS_CACHE: Dict[int, list] = {}


def get_packets(db: Session, category_id: int) -> dict:
    """Живі пакети публікацій для категорії + обрана вартість 1 оголошення."""
    _, olx_pricing = _prom()
    if category_id in _PACKETS_CACHE:
        return _PACKETS_CACHE[category_id]
    access = get_access_token(db)
    if not access:
        return {"ok": False, "error": "OLX не авторизовано", "packets": [],
                "unit_cost": olx_pricing.DEFAULT_PACKET_UNIT_UAH}
    try:
        data = _api_get(access, "/api/partner/packets", {"category_id": category_id})
        packets = data.get("data") or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "packets": [],
                "unit_cost": olx_pricing.DEFAULT_PACKET_UNIT_UAH}
    base = [p for p in packets if p.get("type") == "base"] or packets
    unit = olx_pricing.packet_unit_from_packets(base) or olx_pricing.DEFAULT_PACKET_UNIT_UAH
    result = {"ok": True, "category_id": category_id, "packets": base, "unit_cost": unit}
    _PACKETS_CACHE[category_id] = result
    return result


def _attr_defs(access: str, category_id: int) -> list:
    if category_id in _ATTR_DEFS_CACHE:
        return _ATTR_DEFS_CACHE[category_id]
    try:
        data = _api_get(access, f"/api/partner/categories/{category_id}/attributes", {})
        defs = data.get("data") or []
    except Exception:
        defs = []
    _ATTR_DEFS_CACHE[category_id] = defs
    return defs


def _match_value_code(defs: list, code: str, label: Optional[str]) -> Optional[str]:
    """Знайти OLX-код значення атрибута за label BMS (регістронезалежно)."""
    if not label:
        return None
    want = str(label).strip().lower()
    for d in defs:
        if d.get("code") != code:
            continue
        for v in d.get("values") or []:
            if str(v.get("label", "")).strip().lower() == want:
                return v.get("code")
        # м'який збіг для розмірів «38.5» → «38_5»
        if code == "size":
            norm = want.replace(".", "_").replace(",", "_")
            for v in d.get("values") or []:
                if str(v.get("code", "")).lower() == norm:
                    return v.get("code")
    return None


def _match_in_def(d: dict, label: Optional[str], is_size: bool = False) -> Optional[str]:
    """Код значення В МЕЖАХ одного атрибута за label BMS (не за фіксованим code —
    щоб працювало для будь-якої категорії: color / bags_color / material_ch_o…).

    Якщо атрибут БЕЗ словника значень (напр. «Розмір» у «Дитяче взуття») — це
    поле вільного тексту, тож віддаємо значення BMS як є."""
    if not label:
        return None
    if not (d.get("values") or []):
        return str(label).strip() or None
    want = str(label).strip().lower()
    for v in d.get("values") or []:
        if str(v.get("label", "")).strip().lower() == want:
            return v.get("code")
    if is_size:
        norm = want.replace(".", "_").replace(",", "_")
        for v in d.get("values") or []:
            if str(v.get("code", "")).lower() == norm:
                return v.get("code")
    return None


# Сезон BMS → типова OLX-мітка (додається лише якщо категорія має такий label).
_SEASON_MAP = {
    "літо": "Літо", "зима": "Зима", "демі": "Демісезон", "демісезон": "Демісезон",
    "весна": "Весна", "осінь": "Осінь", "всесезон": "Всесезонний",
}

# ── Відповідність характеристик BMS → OLX ────────────────────────────────────
# BMS зберігає матеріали ПО ПОЗИЦІЯХ (upper/sole/lining/…). Для OLX головний —
# матеріал ВЕРХУ; раніше брався довільний перший (напр. мембрана «gore-tex»).
_MATERIAL_POS_PRIORITY = ("upper", "middle", "lining", "insole", "sole", "midsole", "membrane")
_MATERIAL_MAP = {
    "шкіра": "Натуральна шкіра", "натуральна шкіра": "Натуральна шкіра", "нат. шкіра": "Натуральна шкіра",
    "екошкіра": "Шкірзам", "шкірзам": "Шкірзам", "штучна шкіра": "Шкірзам", "кожзам": "Шкірзам",
    "замша": "Замша", "нубук": "Нубук",
    "текстиль": "Текстиль", "сітчастий текстиль": "Текстиль", "плотний текстиль": "Текстиль",
    "сітка": "Текстиль", "трикотаж": "Текстиль", "тканина": "Тканина",
    "гума": "Гума", "резина": "Гума", "каучук": "Гума", "силікон": "Силікон",
    "вовна": "Вовна", "овчина": "Овчина", "хутро": "Овчина", "плюш": "Плюш", "повсть": "Повсть",
    "ажур": "Ажурні", "ажурні": "Ажурні",
    # Матеріали, яким немає прямого відповідника в OLX → «Інший».
    "eva": "Інший", "піна": "Інший", "синтетика": "Інший", "croslite": "Інший",
    "поліуретан": "Інший", "пластик": "Інший", "vibram": "Гума",
}

# BMS має відтінки (темно-синій, молочний, яскраво-синій), OLX — базову палітру.
_COLOR_PREFIXES = ("темно-", "світло-", "яскраво-", "ніжно-", "насичено-", "блідо-", "ясно-")
_COLOR_MAP = {
    "молочний": "Білий", "айворі": "Білий", "кремовий": "Білий", "кремовий/білий": "Білий",
    "срібний": "Сірий", "срібло": "Сірий", "графітовий": "Сірий", "сталевий": "Сірий",
    "рудий": "Коричневий", "карамельний": "Коричневий", "шоколадний": "Коричневий",
    "капучиновий": "Бежевий", "тілесний": "Бежевий", "пудровий": "Бежевий", "пісочний": "Бежевий",
    "оливковий": "Хакі", "болотний": "Хакі",
    "персиковий": "Помаранчевий", "кораловий": "Червоний", "коралловий": "Червоний",
    "малиновий": "Червоний", "вишневий": "Бордовий", "марсала": "Бордовий",
    "фуксія": "Рожевий", "ліловий": "Фіолетовий", "бузковий": "Фіолетовий", "фіалковий": "Фіолетовий",
    "золотистий": "Золотий", "мультиколор": "Різнокольоровий", "різнокольоровий": "Різнокольоровий",
    "джинсовий": "Синій", "індиго": "Синій", "м'ятний": "Бірюзовий", "мятний": "Бірюзовий",
    "лимонний": "Жовтий", "гірчичний": "Жовтий", "салатовий": "Салатовий",
}


# BMS зберігає підошву коротко («плоска»), OLX — повними назвами.
_SOLE_MAP = {
    "плоска": "Плоска підошва", "спортивна": "Плоска підошва", "шкіра": "Плоска підошва",
    "гума": "Плоска підошва",
    "платформа": "Платформа", "танкетка": "Танкетка",
    "підбора": "Каблук", "підбор": "Каблук", "каблук": "Каблук", "шпилька": "Каблук",
    "тракторна": "Тракторна підошва", "рифлена": "Тракторна підошва",
    "рельєфна": "Тракторна підошва", "протектор": "Тракторна підошва",
}


def _sole_candidates(product: dict) -> List[str]:
    """Тип підошви для OLX: з soletypename/heeltypename BMS через мапу повних назв."""
    out: List[str] = []
    for raw in (product.get("soletypename"), product.get("heeltypename")):
        v = str(raw or "").strip().lower()
        if not v:
            continue
        for cand in (v, _SOLE_MAP.get(v)):
            if cand and cand not in out:
                out.append(cand)
    return out


def _color_candidates(name: Optional[str]) -> List[str]:
    """Кандидати OLX-кольору для BMS-кольору, у порядку пріоритету.
    «темно-синій» → синій; «білий/молочний» → білий; «молочний» → Білий."""
    raw = str(name or "").strip().lower()
    if not raw:
        return []
    out: List[str] = [raw]

    def _add(v: Optional[str]):
        if v and v not in out:
            out.append(v)

    _add(_COLOR_MAP.get(raw))
    # складений колір «білий/молочний» → перша частина
    first = re.split(r"[/,]", raw)[0].strip()
    if first != raw:
        _add(first)
        _add(_COLOR_MAP.get(first))
    # відтінок «темно-синій» → «синій»
    for part in (raw, first):
        for pref in _COLOR_PREFIXES:
            if part.startswith(pref):
                base = part[len(pref):].strip()
                _add(base)
                _add(_COLOR_MAP.get(base))
    out.append("Різнокольоровий" if "/" in raw else "Інший")   # чесний фолбек
    return out


def _material_candidates(product: dict) -> List[str]:
    """Матеріал для OLX: спершу ВЕРХ (головний), далі інші позиції; з мапою назв."""
    mats = {str(k).lower(): str(v).strip().lower()
            for k, v in (product.get("materials") or {}).items() if v}
    ordered = [mats[p] for p in _MATERIAL_POS_PRIORITY if p in mats]
    ordered += [v for k, v in mats.items() if k not in _MATERIAL_POS_PRIORITY]
    out: List[str] = []
    for m in ordered:
        for cand in (m, _MATERIAL_MAP.get(m)):
            if cand and cand not in out:
                out.append(cand)
    return out


def _attach_own_size(db: Session, product: dict, product_id: int) -> None:
    """Додати ВЛАСНИЙ розмір рядка товару. `_bms_product_for_export` віддає лише
    агрегований `sizes` (усі розміри номера-ростовки), а оголошення OLX — це
    завжди ОДИН розмір, тобто один рядок BMS."""
    try:
        product["sizeeu"] = db.execute(text(
            "SELECT NULLIF(BTRIM(sizeeu), '') FROM products WHERE id = :i"),
            {"i": int(product_id)}).scalar()
    except Exception:
        product.setdefault("sizeeu", None)


def _size_candidates(product: dict) -> List[str]:
    """Розмір ЦЬОГО рядка товару. Ростовка в BMS — окремий рядок на кожен розмір,
    а оголошення OLX має рівно один розмір → беремо власний sizeeu рядка.
    Діапазон «29-30» → пробуємо обидва числа."""
    own = str(product.get("sizeeu") or "").strip()
    sizes = [str(s).strip() for s in (product.get("sizes") or []) if s]
    if not own and len(sizes) == 1:
        own = sizes[0]
    if not own:
        return []
    out = [own]
    for part in re.split(r"[-–/]", own):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _build_attributes(defs: list, product: dict) -> list:
    """Універсальне автозаповнення атрибутів OLX із даних BMS для БУДЬ-ЯКОЇ
    категорії: кожен атрибут класифікуємо за семантикою його коду й підбираємо
    значення за збігом label. `state` (Нове/Вживане) — завжди.

    ВАЖЛИВО: «нове» для OLX = та сама сітка, що й у Prom (`_COND_NEWLIKE`):
    Новий/Нове/Хороший → Нове; Вживаний/Легковживаний/Пошкоджений → Вживане.
    (Раніше хибно: будь-що без «нов» у назві ставало «Вживане» — і «Хороший»
    їхав як вживане.)"""
    prom_service, _ = _prom()
    cond = str(product.get("conditionname") or "").strip().lower()
    is_new = cond in prom_service._COND_NEWLIKE
    season_raw = str(product.get("season") or "").strip().lower()
    season_key = re.split(r"[\s,/]+", season_raw)[0] if season_raw else ""

    def _first(d: dict, cands: List[str], is_size: bool = False) -> Optional[str]:
        """Перший кандидат, що має відповідник у списку значень атрибута."""
        for c in cands:
            v = _match_in_def(d, c, is_size=is_size)
            if v:
                return v
        return None

    attrs: List[dict] = []
    for d in defs:
        code = (d.get("code") or "").lower()
        val = None
        if code == "state":
            val = _match_in_def(d, "Нове" if is_new else "Вживане") or ("new" if is_new else "used")
        elif "size" in code:
            val = _first(d, _size_candidates(product), is_size=True)
        elif "color" in code or "colour" in code:
            val = _first(d, _color_candidates(product.get("colorname")))
        elif "brand" in code:
            # Бренда може не бути в списку OLX (напр. HOKA) → чесно «Інший».
            val = _match_in_def(d, product.get("brandname")) or _match_in_def(d, "Інший")
        elif "material" in code:
            val = _first(d, _material_candidates(product))
        elif "sole" in code or "heel" in code:
            val = _first(d, _sole_candidates(product))
        elif "season" in code:
            val = _match_in_def(d, _SEASON_MAP.get(season_key))
        if val:
            attrs.append({"code": d.get("code"), "value": val})
    # Гарантія: state присутній навіть якщо defs не вдалося прочитати.
    if not any(a.get("code") == "state" for a in attrs):
        attrs.insert(0, {"code": "state", "value": "new" if is_new else "used"})
    return attrs


def _pricing_for(db: Session, product: dict, cfg: dict, category_id: int,
                 current_price: Optional[float] = None) -> dict:
    prom_service, olx_pricing = _prom()
    try:
        kids = prom_service._is_kids(product)
    except Exception:
        kids = False
    unit = get_packets(db, category_id).get("unit_cost") if category_id else None
    return olx_pricing.price_economics(
        product.get("price"), product.get("typename") or "",
        packet_unit=unit, ad_spend=cfg.get("ad_spend", 0),
        is_business=(cfg.get("advertiser_type", "business") == "business"),
        use_delivery=bool(cfg.get("use_delivery", True)),
        branch_payment=bool(cfg.get("branch_payment", False)),
        current_olx_price=current_price,
    )


# Рід/число для слова «новий» у рядку «Стан». Взуття — множина («Нові»), решта
# — за родом іменника, щоб не було «Рюкзак … Стан: Нові».
_NEW_WORD_F = {"сумка", "валіза", "куртка", "сукня", "шапка", "кепка", "футболка",
               "блуза", "спідниця", "піжама", "сорочка", "майка", "кофта", "жилетка",
               "парасолька", "бейсболка", "панама", "краватка", "туніка", "білизна",
               "нічна сорочка", "устілка", "ковдра", "постіль"}
_NEW_WORD_M = {"рюкзак", "гаманець", "ремінь", "шарф", "костюм", "комбінезон",
               "светр", "портфель", "набір", "пенал", "рушник", "плед", "капелюх",
               "купальник", "халат"}
_NEW_WORD_N = {"портмоне", "взуття", "пальто"}


def _new_word(product: dict) -> str:
    t = _norm_type(product.get("typename"))
    if t in _NEW_WORD_F:
        return "Нова"
    if t in _NEW_WORD_M:
        return "Новий"
    if t in _NEW_WORD_N:
        return "Нове"
    return "Нові"          # взуття та інша множина


def _olx_condition_line(product: dict) -> Optional[str]:
    """«Стан» для OLX за правилом власника:
      • «Новий»   → «Нові, без коробки»        (БЕЗ «(Сток)» — товар справді новий);
      • «Хороший» → «Нові (Сток), без коробки» («(Сток)» ЛИШЕ тут);
      • решта (Вживаний/Легковживаний/Пошкоджений) → чесний реальний стан.
    Пакування вказується ЗАВЖДИ; рід/число — за типом товару."""
    prom_service, _ = _prom()
    cond = str(product.get("conditionname") or "").strip().lower()
    pack = str(product.get("packagingname") or "").strip().lower()
    if cond in prom_service._COND_NEWLIKE:
        w = _new_word(product)
        stock = " (Сток)" if cond == "хороший" else ""
        if not pack or ("без" in pack and "коробк" in pack):
            return f"{w}{stock}, без коробки"
        if "коробк" in pack:
            return f"{w}{stock}, в коробці"
        return f"{w}{stock}, {pack}"
    return (product.get("conditionname") or "").strip() or None


def _insole_cm(product: dict) -> Optional[str]:
    """Замір «на стопу» (см) для цього розміру — з measurementscm рядка товару."""
    lo, hi = product.get("measurementscm_min"), product.get("measurementscm_max")
    def _f(v):
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else f"{f:g}"
        except (TypeError, ValueError):
            return None
    a, b = _f(lo), _f(hi)
    if a and b and a != b:
        return f"{a}–{b}"
    return a or b


def _build_olx_description(product: dict) -> str:
    """Опис OLX — ЧИСТИЙ ТЕКСТ у стилі ручних постів власника:
    вступ → (Внутрішній артикул) → ХАРАКТЕРИСТИКИ → ДОСТАВКА.

    Свідомо НЕ публікуємо `description`/`extranote` — це ВНУТРІШНІ нотатки BMS
    (напр. «старі»), не для покупця. Рядок «Стан» будує prom_service.
    _condition_line → «Нові (Сток), без коробки» / «Нові, в коробці» / чесний
    реальний стан вживаного (Новий і Хороший = нові, решта = вживані).
    """
    prom_service, _ = _prom()
    brand = (product.get("brandname") or "").strip()
    typ = (product.get("typename") or "Товар").strip()
    color = (product.get("colorname") or "").strip()
    model = (product.get("model") or "").strip()
    number = str(product.get("productnumber") or "").strip()
    sizes = [str(s) for s in (product.get("sizes") or []) if s]
    # Матеріали — у змістовному порядку (верх → підкладка → підошва), а не як у dict.
    _m = {str(k).lower(): str(v).strip() for k, v in (product.get("materials") or {}).items() if v}
    mats = ", ".join(dict.fromkeys(
        [_m[p] for p in _MATERIAL_POS_PRIORITY if p in _m]
        + [v for k, v in _m.items() if k not in _MATERIAL_POS_PRIORITY]))

    # ── Вступ (без крапки в кінці — так пише живий продавець, не бот) ─────────
    title_bits = " ".join(x for x in (typ, brand, model) if x)
    intro = title_bits + (f", {color.lower()}" if color else "")
    lines = [intro]
    if number:
        # Артикул — ОДРАЗУ під вступом, без порожнього рядка (як у ручних постах).
        lines.append(f"(Внутрішній артикул: {number if number.startswith('#') else '#' + number})")

    # ── ХАРАКТЕРИСТИКИ (порожній рядок між пунктами — як у ручних постах;
    # БЕЗ крапки в кінці кожного рядка) ────────────────────────────────────
    facts: List[str] = ["100% оригінал"]
    if brand:
        facts.append(f"Бренд: {prom_service._brand_with_country(brand, 'uk')}")
    if model:
        facts.append(f"Модель: {model}")
    if color:
        facts.append(f"Колір: {color}")
    # Оголошення OLX = ОДИН розмір (ростовка в BMS — окремий рядок на розмір).
    own_size = (_size_candidates(product) or [None])[0]
    if own_size:
        ins = _insole_cm(product)
        facts.append(f"Розмір: {own_size}"
                     + (f" (на стопу — {ins} см)" if ins else ""))
    # Габарити — критично для сумок/рюкзаків/валіз.
    dims = str(product.get("dimensions") or "").strip()
    if dims:
        facts.append(f"Габарити: {dims} см")
    # Стан + пакування («Новий (Сток), без коробки» / «Нові, в коробці»).
    cond_line = _olx_condition_line(product)
    if cond_line:
        facts.append(f"Стан: {cond_line}")
    if mats:
        facts.append(f"Матеріал: {mats}")
    if product.get("season"):
        facts.append(f"Сезон: {product.get('season')}")
    lines += ["", "ХАРАКТЕРИСТИКИ:", "", *_spaced(facts)]

    # ── ДОСТАВКА (без крапок у кінці — стиль живого продавця) ─────────────────
    lines += ["", "ДОСТАВКА:", "",
              "Швидка відправка Новою поштою або Укрпоштою наступного дня", "",
              "Пишіть — відповім на всі питання"]
    return "\n".join(lines).strip()[:8000]


def _spaced(items: List[str]) -> List[str]:
    """Пункти через порожній рядок (стиль ручних оголошень власника)."""
    out: List[str] = []
    for i, it in enumerate(items):
        out.append(it)
        if i < len(items) - 1:
            out.append("")
    return out


def build_advert_payload(product: dict, category_id: int, price: int, cfg: dict,
                         defs: list, image_urls: list) -> dict:
    prom_service, _ = _prom()
    title = (prom_service._build_name(product, "uk") or product.get("productnumber") or "Товар")[:OLX_TITLE_MAX]
    description = _build_olx_description(product)
    payload = {
        "title": title,
        "description": description,
        "category_id": int(category_id),
        "advertiser_type": cfg.get("advertiser_type", "business"),
        "contact": {"name": cfg.get("contact_name") or "Продавець",
                    "phone": cfg.get("contact_phone") or ""},
        "location": {"city_id": cfg.get("default_city_id")},
        "price": {"value": int(price), "currency": "UAH", "negotiable": False},
        "images": [{"url": u} for u in (image_urls or [])[:OLX_MAX_IMAGES]],
        "attributes": _build_attributes(defs, product),
        # Наш ref для зворотного лінка при sync (external_id = чистий номер).
        "external_id": str(product.get("productnumber") or "").lstrip("#")[:120] or None,
    }
    loc = payload["location"]
    if cfg.get("default_district_id"):
        loc["district_id"] = cfg["default_district_id"]
    if cfg.get("default_lat") and cfg.get("default_lon"):
        loc["latitude"] = cfg["default_lat"]
        loc["longitude"] = cfg["default_lon"]
    return payload


def _upsert_created(db: Session, product_id: int, number: str, advert: dict,
                    needs_package: bool, last_error: Optional[str]) -> None:
    price_obj = advert.get("price") or {}
    url = advert.get("url")
    if isinstance(url, dict):
        url = url.get("href")
    db.execute(text("""
        INSERT INTO olx_adverts (
            olx_id, product_id, product_number_raw, title, description, status,
            url, external_id, category_id, price, currency, created_by_bms,
            needs_package, last_error, last_synced_at
        ) VALUES (
            :olx_id, :pid, :raw, :title, :descr, :status,
            :url, :ext, :cat, :price, :cur, TRUE, :needs, :err, now()
        )
        ON CONFLICT (olx_id) DO UPDATE SET
            product_id=EXCLUDED.product_id, product_number_raw=EXCLUDED.product_number_raw,
            title=EXCLUDED.title, status=EXCLUDED.status, url=EXCLUDED.url,
            external_id=EXCLUDED.external_id, category_id=EXCLUDED.category_id,
            price=EXCLUDED.price, currency=EXCLUDED.currency, created_by_bms=TRUE,
            needs_package=EXCLUDED.needs_package, last_error=EXCLUDED.last_error,
            last_synced_at=now()
    """), {
        "olx_id": advert.get("id"), "pid": product_id,
        "raw": str(number).lstrip("#")[:50],
        "title": (advert.get("title") or "")[:300], "descr": advert.get("description"),
        "status": advert.get("status"), "url": (url or "")[:500] or None,
        "ext": (advert.get("external_id") or "")[:120] or None,
        "cat": advert.get("category_id"),
        "price": price_obj.get("value"), "cur": (price_obj.get("currency") or "UAH")[:8],
        "needs": needs_package, "err": last_error,
    })
    db.commit()


# Статуси OLX, що означають «оголошення живе/видиме». Решта після створення —
# ознака, що бракує активного пакета або триває модерація.
# ЛИШЕ 'active' = повністю ПУБЛІЧНЕ (перевірено: anonymous GET → 200).
# 'limited' = створене й активоване, але з ОБМЕЖЕНОЮ видимістю (anonymous → 404):
# вичерпано безкоштовний ліміт → потрібен активний пакет публікацій.
_LIVE_STATUSES = {"active"}
_LIMITED_STATUS = "limited"
_DRAFT_STATUSES = {"new", "unactivated", "draft"}
_PACKAGE_STATUSES = {"limited", "unpaid", "payment_waiting", "outdated", "disabled", "new"}


def _prepare_advert(db: Session, product_id: int) -> Tuple[Optional[dict], Optional[dict]]:
    """Спільна підготовка для preview і create. Повертає (ctx, error)."""
    if not is_configured():
        return None, {"ok": False, "error": "OLX не налаштовано (OLX_CLIENT_ID/SECRET)"}
    access = get_access_token(db)
    if not access:
        return None, {"ok": False, "error": "OLX не авторизовано — пройдіть OAuth"}
    prom_service, _ = _prom()
    product = prom_service._bms_product_for_export(db, int(product_id))
    if not product:
        return None, {"ok": False, "error": "Товар не знайдено"}
    _attach_own_size(db, product, int(product_id))
    number = str(product.get("productnumber") or "").lstrip("#")
    category_id = resolve_category(db, product)
    if not category_id:
        return None, {"ok": False, "need_category": True,
                      "error": f"Категорію OLX для типу «{product.get('typename')}» не визначено"}
    images = prom_service._product_image_urls(product.get("productnumber"),
                                              product.get("official_photos_from"))
    if not images:
        return None, {"ok": False, "error": f"{number}: немає фото — OLX відхилить оголошення"}
    cfg = _ensure_defaults(db, access, _load_config(db))
    if not cfg.get("contact_phone"):
        return None, {"ok": False, "error": "Не задано контактний телефон OLX (Налаштування)"}
    if not cfg.get("default_city_id"):
        return None, {"ok": False, "error": "Не задано місто OLX (Налаштування)"}
    pricing = _pricing_for(db, product, cfg, category_id)
    if not pricing.get("margin_safe") or not pricing.get("effective_price"):
        return None, {"ok": False, "error": "Не вдалося порахувати ціну із захищеною маржею",
                      "pricing": pricing}
    return {"access": access, "product": product, "number": number,
            "category_id": category_id, "images": images, "cfg": cfg,
            "pricing": pricing, "defs": _attr_defs(access, category_id)}, None


def _existing_live(db: Session, product_id: int, number: str) -> Optional[dict]:
    row = db.execute(text("""
        SELECT olx_id, status, url FROM olx_adverts
        WHERE (product_id = :pid OR product_number_raw = :num)
          AND status = ANY(:live) LIMIT 1
    """), {"pid": int(product_id), "num": number,
           "live": list(_LIVE_STATUSES)}).mappings().first()
    return dict(row) if row else None


def preview_advert(db: Session, product_id: int) -> dict:
    """Прев'ю ПЕРЕД публікацією (нічого не створює) — для діалогу редагування,
    як у Prom: назва, опис, ціна, характеристики зі списками допустимих значень."""
    ctx, err = _prepare_advert(db, product_id)
    if err:
        return err
    payload = build_advert_payload(ctx["product"], ctx["category_id"],
                                   ctx["pricing"]["effective_price"], ctx["cfg"],
                                   ctx["defs"], ctx["images"])
    chosen = {a["code"]: a["value"] for a in payload.get("attributes") or []}
    attributes = []
    for d in ctx["defs"]:
        code = d.get("code")
        attributes.append({
            "code": code,
            "label": d.get("label") or code,
            "required": bool((d.get("validation") or {}).get("required")),
            "value": chosen.get(code),
            "options": [{"code": v.get("code"), "label": v.get("label")}
                        for v in (d.get("values") or [])],
        })
    tree = _category_tree(ctx["access"])
    byid = tree.get("byid", {})
    path, cur = [], byid.get(ctx["category_id"])
    for _ in range(6):
        if not cur:
            break
        path.append(cur.get("name"))
        cur = byid.get(cur.get("parent_id"))
    existing = _existing_live(db, product_id, ctx["number"])
    warnings: List[str] = []
    pk = ctx["pricing"]
    warnings.append(f"OLX бере плату за ПУБЛІКАЦІЮ (пакет ~{pk['packet_unit']:.0f} грн), "
                    f"а не % з продажу. Без активного пакета оголошення буде «limited» "
                    f"(не в публічному пошуку).")
    if existing:
        warnings.append("Товар уже має активне оголошення на OLX — публікація створить друге.")
    return {
        "ok": True, "product_id": int(product_id), "productnumber": ctx["number"],
        "category_id": ctx["category_id"], "category_name": (path[0] if path else None),
        "category_path": " / ".join(reversed(path)) if path else None,
        "title": payload["title"], "description": payload["description"],
        "price": int(payload["price"]["value"]), "pricing": pk,
        "attributes": attributes, "image_count": len(ctx["images"]),
        "packet_unit": pk.get("packet_unit"),
        "already_on_olx": bool(existing), "olx_id": (existing or {}).get("olx_id"),
        "olx_url": (existing or {}).get("url"),
        "title_max": OLX_TITLE_MAX, "warnings": warnings,
    }


def create_advert(db: Session, product_id: int, price: Optional[float] = None,
                  force: bool = False, overrides: Optional[dict] = None) -> dict:
    """Створити (опублікувати) оголошення OLX. `overrides` — правки з діалогу
    (title/description/price/attributes), як у Prom-флоу."""
    ctx, err = _prepare_advert(db, product_id)
    if err:
        return err
    access, product, number = ctx["access"], ctx["product"], ctx["number"]
    category_id, images, cfg, pricing, defs = (ctx["category_id"], ctx["images"],
                                               ctx["cfg"], ctx["pricing"], ctx["defs"])
    existing = _existing_live(db, product_id, number)
    if existing and not force:
        return {"ok": False, "already_on_olx": True, "olx_id": existing["olx_id"],
                "url": existing["url"], "error": "Товар уже опубліковано на OLX"}

    ov = overrides or {}
    final_price = int(ov.get("price") or price or pricing["effective_price"])
    payload = build_advert_payload(product, category_id, final_price, cfg, defs, images)
    # Правки з діалогу мають пріоритет над автозгенерованим.
    if str(ov.get("title") or "").strip():
        payload["title"] = str(ov["title"]).strip()[:OLX_TITLE_MAX]
    if str(ov.get("description") or "").strip():
        payload["description"] = str(ov["description"]).strip()[:8000]
    if ov.get("attributes"):
        payload["attributes"] = [{"code": a.get("code"), "value": a.get("value")}
                                 for a in ov["attributes"]
                                 if a.get("code") and a.get("value")]

    sc, resp = _api_post(access, "/api/partner/adverts", payload)
    if sc >= 400:
        val = ((resp.get("error") or {}).get("validation")) if isinstance(resp, dict) else None
        msg = "; ".join(f"{v.get('field')}: {v.get('detail')}" for v in (val or [])[:6]) \
            or (resp.get("error", {}).get("detail") if isinstance(resp, dict) else str(resp)[:200])
        return {"ok": False, "error": f"OLX відхилив [{sc}]: {msg}", "validation": val,
                "pricing": pricing}

    advert = resp.get("data") or {}
    status = advert.get("status")
    needs_package = False
    package_note = None
    # Активація потрібна лише для ЧЕРНЕТКИ (new/unactivated). 'limited' уже
    # активоване — просто з обмеженою видимістю (потрібен пакет), тож команду
    # activate не шлемо (не допоможе).
    if status in _DRAFT_STATUSES and advert.get("id"):
        act_sc, act = _api_post(access, f"/api/partner/adverts/{advert['id']}/commands",
                                {"command": "activate"})
        if act_sc < 400:
            try:
                fresh = _api_get(access, f"/api/partner/adverts/{advert['id']}", {})
                advert = fresh.get("data") or advert
                status = advert.get("status")
            except Exception:
                pass
    if status in _PACKAGE_STATUSES:
        needs_package = True
        package_note = (
            "Обмежена видимість (limited): вичерпано безкоштовний ліміт публікацій — "
            "оголошення НЕ показується в публічному пошуку, доки не активовано пакет OLX."
            if status == _LIMITED_STATUS else
            "Потрібен активний пакет публікацій OLX.")

    _upsert_created(db, int(product_id), number, advert, needs_package, package_note)
    url = advert.get("url")
    if isinstance(url, dict):
        url = url.get("href")
    if status in _LIVE_STATUSES:
        note = "Оголошення опубліковано на OLX (повністю видиме)."
    elif status == _LIMITED_STATUS:
        note = ("Оголошення СТВОРЕНО, але має ОБМЕЖЕНУ видимість (limited) — воно не в "
                "публічному пошуку, бо вичерпано безкоштовний ліміт. Активуй пакет "
                "публікацій OLX, щоб зробити його повністю видимим.")
    else:
        note = ("Оголошення створено, але ще не активне (потрібен пакет публікацій OLX). "
                "Активуй пакет у кабінеті OLX.")
    return {"ok": True, "olx_id": advert.get("id"), "status": status, "url": url,
            "needs_package": needs_package, "limited": status == _LIMITED_STATUS,
            "pricing": pricing, "price": final_price, "note": note}


def create_adverts_batch(db: Session, product_ids: List[int]) -> dict:
    ids = [int(x) for x in (product_ids or []) if x]
    if not ids:
        return {"ok": False, "error": "Не вибрано товарів"}
    if len(ids) > 200:
        return {"ok": False, "error": "За раз максимум 200 товарів"}
    created, need_pkg, already, skipped = 0, 0, 0, []
    first_error = None
    for pid in ids:
        try:
            r = create_advert(db, pid)
        except Exception as e:
            skipped.append({"product_id": pid, "reason": str(e)[:160]})
            continue
        if r.get("ok"):
            created += 1
            if r.get("needs_package"):
                need_pkg += 1
        elif r.get("already_on_olx"):
            already += 1
        else:
            skipped.append({"product_id": pid, "reason": r.get("error")})
            first_error = first_error or r.get("error")
    note = (f"OLX: опубліковано {created}"
            + (f" (з них {need_pkg} чекають активації пакета)" if need_pkg else "")
            + (f", вже було {already}" if already else "")
            + (f", пропущено {len(skipped)}" if skipped else "") + ".")
    return {"ok": created > 0 or already > 0, "created": created,
            "needs_package": need_pkg, "already": already, "skipped": skipped,
            "note": note, "error": None if (created or already) else (first_error or "Жоден товар не опубліковано")}


def olx_product_status(db: Session, product_id: int) -> dict:
    """Стан товару щодо OLX для картки: ціна, категорія, вартість пакета, оголошення."""
    prom_service, _ = _prom()
    product = prom_service._bms_product_for_export(db, int(product_id))
    if not product:
        return {"ok": False, "error": "Товар не знайдено"}
    number = str(product.get("productnumber") or "").lstrip("#")
    category_id = resolve_category(db, product)
    adv = db.execute(text("""
        SELECT olx_id, status, url, price, needs_package, last_error, created_by_bms
        FROM olx_adverts
        WHERE product_id = :pid OR product_number_raw = :num
        ORDER BY (status = ANY(:live)) DESC, last_synced_at DESC LIMIT 1
    """), {"pid": int(product_id), "num": number, "live": list(_LIVE_STATUSES)}).mappings().first()
    cfg = _load_config(db)
    warnings: List[str] = []
    st = get_status(db)
    if not st.get("authorized"):
        warnings.append("OLX не авторизовано — пройдіть одноразову авторизацію.")
    if not category_id:
        warnings.append(f"Тип «{product.get('typename')}» не вдалося зіставити з категорією OLX — задайте вручну.")
    try:
        images = prom_service._product_image_urls(product.get("productnumber"),
                                                  product.get("official_photos_from"))
    except Exception:
        images = []
    if not images:
        warnings.append("Немає фото — OLX не прийме оголошення.")
    pricing = _pricing_for(db, product, cfg, category_id,
                           current_price=(adv or {}).get("price")) if category_id else None
    packet_unit = get_packets(db, category_id).get("unit_cost") if category_id else None
    adv_status = (adv or {}).get("status")
    live = adv_status in _LIVE_STATUSES                     # лише 'active' = публічне
    # «Потрібен пакет» визначаємо ЗІ СТАТУСУ (limited/…), а не лише зі збереженого
    # прапорця — навіть якщо прапорець застарів, реальність показуємо правильно.
    needs_pkg = bool(adv and (adv.get("needs_package") or adv_status in _PACKAGE_STATUSES))
    if adv_status == _LIMITED_STATUS:
        warnings.append("Оголошення СТВОРЕНО, але має обмежену видимість (limited): "
                        "його немає в публічному пошуку, бо вичерпано безкоштовний ліміт — "
                        "активуй пакет публікацій OLX.")
    elif needs_pkg:
        warnings.append("Оголошення створене, але потребує активного пакета публікацій OLX.")
    warnings.append("OLX бере плату за публікацію (пакет), а не % з продажу; "
                    + (f"~{packet_unit:.0f} грн/оголошення." if packet_unit else "вартість — з активного пакета."))
    return {
        "ok": True, "productnumber": number, "typename": product.get("typename"),
        "gendername": product.get("gendername"), "category_id": category_id,
        "authorized": bool(st.get("authorized")),
        "on_olx": live, "olx_status": adv_status, "limited": adv_status == _LIMITED_STATUS,
        "olx_url": (adv or {}).get("url"), "olx_id": (adv or {}).get("olx_id"),
        "needs_package": needs_pkg,
        "created_by_bms": bool(adv and adv.get("created_by_bms")),
        "last_error": (adv or {}).get("last_error"),
        "image_count": len(images), "packet_unit": packet_unit,
        "pricing": pricing, "config": cfg, "warnings": warnings,
    }

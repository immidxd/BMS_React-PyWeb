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
            product_id         = EXCLUDED.product_id,
            product_number_raw = EXCLUDED.product_number_raw,
            title              = EXCLUDED.title,
            description        = EXCLUDED.description,
            status             = EXCLUDED.status,
            url                = EXCLUDED.url,
            external_id        = EXCLUDED.external_id,
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
    "галош", "топсайд", "трекінг", "сліпон")


def _norm_type(typename: Optional[str]) -> str:
    t = str(typename or "").strip().lower()
    return _TYPE_ALIASES.get(t, t)


def _is_shoe_type(t: str) -> bool:
    return any(t.startswith(p) for p in _SHOE_PREFIXES)


def olx_category_for(typename: Optional[str], gendername: Optional[str]) -> Optional[int]:
    """Категорія OLX за типом+статтю BMS (статична мапа). None — якщо тип не
    розпізнано (тоді викликач пробує навчання з наявних оголошень)."""
    t = _norm_type(typename)
    g = str(gendername or "").strip().lower()
    if not t:
        return None
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


def resolve_category(db: Session, product: dict) -> Optional[int]:
    """Категорія OLX: статична мапа → навчання з наявних оголошень → None."""
    return olx_category_for(product.get("typename"), product.get("gendername")) \
        or _learn_category(db, product)


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
    щоб працювало для будь-якої категорії: color / bags_color / material_ch_o…)."""
    if not label:
        return None
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


def _build_attributes(defs: list, product: dict) -> list:
    """Універсальне автозаповнення атрибутів OLX із даних BMS для БУДЬ-ЯКОЇ
    категорії: кожен атрибут класифікуємо за семантикою його коду й підбираємо
    значення за збігом label. `state` (Нове/Вживане) — завжди."""
    cond = str(product.get("conditionname") or "").lower()
    is_new = "нов" in cond
    sizes = [str(s) for s in (product.get("sizes") or []) if s]
    color = product.get("colorname")
    brand = product.get("brandname")
    mats = [m for m in (product.get("materials") or {}).values() if m]
    material = mats[0] if mats else None
    season_raw = str(product.get("season") or "").strip().lower()
    season_key = re.split(r"[\s,/]+", season_raw)[0] if season_raw else ""
    season = _SEASON_MAP.get(season_key)

    attrs: List[dict] = []
    for d in defs:
        code = (d.get("code") or "").lower()
        val = None
        if code == "state":
            val = _match_in_def(d, "Нове" if is_new else "Вживане") or ("new" if is_new else "used")
        elif "size" in code:
            if len(sizes) == 1:            # ростовка (кілька розмірів) → пропускаємо
                val = _match_in_def(d, sizes[0], is_size=True)
        elif "color" in code or "colour" in code:
            val = _match_in_def(d, color)
        elif "brand" in code:
            val = _match_in_def(d, brand)
        elif "material" in code:
            val = _match_in_def(d, material)
        elif "season" in code:
            val = _match_in_def(d, season)
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


def _build_olx_description(product: dict) -> str:
    """Опис для OLX — ЧИСТИЙ ТЕКСТ (OLX не рендерить HTML), кілька рядків."""
    prom_service, _ = _prom()
    brand = product.get("brandname") or ""
    typ = product.get("typename") or "Взуття"
    color = product.get("colorname") or ""
    gender = product.get("gendername") or ""
    cond = product.get("conditionname") or ""
    sizes = [str(s) for s in (product.get("sizes") or []) if s]
    mats = ", ".join(v for v in (product.get("materials") or {}).values() if v)
    head = " ".join(x for x in (gender, typ.lower(), brand, color.lower()) if x).strip()
    lines = [head.capitalize() if head else typ]
    facts = []
    if brand:
        facts.append(f"Бренд: {brand}")
    facts.append(f"Тип: {typ}")
    if color:
        facts.append(f"Колір: {color}")
    if cond:
        facts.append(f"Стан: {cond}")
    if mats:
        facts.append(f"Матеріал: {mats}")
    if product.get("season"):
        facts.append(f"Сезон: {product.get('season')}")
    if product.get("manufacturer"):
        facts.append(f"Виробник: {product.get('manufacturer')}")
    if sizes:
        facts.append(f"Розмір{'и' if len(sizes) > 1 else ''}: {', '.join(sizes)}")
    lines += ["", *[f"• {f}" for f in facts]]
    extra = (product.get("description") or "").strip()
    if extra and "<" not in extra:  # не тягнемо HTML-опис
        lines += ["", extra]
    lines += ["", "Швидка відправка Новою поштою / Укрпоштою.",
              "Пишіть — відповім на всі питання."]
    text_out = "\n".join(lines).strip()
    return text_out[:8000]


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
_LIVE_STATUSES = {"active", "limited"}
_PACKAGE_STATUSES = {"unpaid", "payment_waiting", "outdated", "disabled", "new"}


def create_advert(db: Session, product_id: int, price: Optional[float] = None,
                  force: bool = False) -> dict:
    """Створити (опублікувати) оголошення OLX з картки товару BMS."""
    if not is_configured():
        return {"ok": False, "error": "OLX не налаштовано (OLX_CLIENT_ID/SECRET)"}
    access = get_access_token(db)
    if not access:
        return {"ok": False, "error": "OLX не авторизовано — пройдіть OAuth"}
    prom_service, _ = _prom()
    product = prom_service._bms_product_for_export(db, int(product_id))
    if not product:
        return {"ok": False, "error": "Товар не знайдено"}
    number = str(product.get("productnumber") or "").lstrip("#")
    category_id = resolve_category(db, product)
    if not category_id:
        return {"ok": False, "need_category": True,
                "error": f"Категорію OLX для типу «{product.get('typename')}» не визначено"}

    # Уже є активне оголошення на цей номер?
    existing = db.execute(text("""
        SELECT olx_id, status, url FROM olx_adverts
        WHERE (product_id = :pid OR product_number_raw = :num)
          AND status = ANY(:live) LIMIT 1
    """), {"pid": int(product_id), "num": number, "live": list(_LIVE_STATUSES)}).mappings().first()
    if existing and not force:
        return {"ok": False, "already_on_olx": True, "olx_id": existing["olx_id"],
                "url": existing["url"], "error": "Товар уже опубліковано на OLX"}

    images = prom_service._product_image_urls(product.get("productnumber"),
                                              product.get("official_photos_from"))
    if not images:
        return {"ok": False, "error": f"{number}: немає фото — OLX відхилить оголошення"}

    cfg = _ensure_defaults(db, access, _load_config(db))
    if not cfg.get("contact_phone"):
        return {"ok": False, "error": "Не задано контактний телефон OLX (Налаштування)"}
    if not cfg.get("default_city_id"):
        return {"ok": False, "error": "Не задано місто OLX (Налаштування)"}

    pricing = _pricing_for(db, product, cfg, category_id)
    if not pricing.get("margin_safe") or not pricing.get("effective_price"):
        return {"ok": False, "error": "Не вдалося порахувати ціну із захищеною маржею",
                "pricing": pricing}
    final_price = int(price or pricing["effective_price"])

    defs = _attr_defs(access, category_id)
    payload = build_advert_payload(product, category_id, final_price, cfg, defs, images)

    sc, resp = _api_post(access, "/api/partner/adverts", payload)
    if sc >= 400:
        val = ((resp.get("error") or {}).get("validation")) if isinstance(resp, dict) else None
        msg = "; ".join(f"{v.get('field')}: {v.get('detail')}" for v in (val or [])[:6]) \
            or (resp.get("error", {}).get("detail") if isinstance(resp, dict) else str(resp)[:200])
        return {"ok": False, "error": f"OLX відхилив [{sc}]: {msg}", "validation": val,
                "pricing": pricing}

    advert = resp.get("data") or {}
    status = advert.get("status")
    # Активація (best-effort): якщо оголошення ще не «живе», пробуємо команду
    # activate — саме тут OLX і скаже, що бракує пакета публікацій.
    needs_package = False
    package_note = None
    if status not in _LIVE_STATUSES and advert.get("id"):
        act_sc, act = _api_post(access, f"/api/partner/adverts/{advert['id']}/commands",
                                {"command": "activate"})
        if act_sc >= 400:
            needs_package = True
            err = (act.get("error", {}) if isinstance(act, dict) else {})
            package_note = err.get("detail") or "Потрібен активний пакет публікацій OLX"
        else:
            # перечитати статус
            try:
                fresh = _api_get(access, f"/api/partner/adverts/{advert['id']}", {})
                advert = fresh.get("data") or advert
                status = advert.get("status")
            except Exception:
                pass
    if status in _PACKAGE_STATUSES:
        needs_package = True

    _upsert_created(db, int(product_id), number, advert, needs_package, package_note)
    url = advert.get("url")
    if isinstance(url, dict):
        url = url.get("href")
    note = ("Оголошення опубліковано на OLX." if not needs_package else
            "Оголошення створено, але НЕ активне: бракує пакета публікацій OLX. "
            "Активуй пакет у кабінеті OLX — воно з'явиться автоматично.")
    return {"ok": True, "olx_id": advert.get("id"), "status": status, "url": url,
            "needs_package": needs_package, "pricing": pricing, "price": final_price,
            "note": note}


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
    live = bool(adv and adv.get("status") in _LIVE_STATUSES)
    if adv and adv.get("needs_package"):
        warnings.append("Оголошення створене, але потребує активного пакета публікацій OLX.")
    warnings.append("OLX бере плату за публікацію (пакет), а не % з продажу; "
                    + (f"~{packet_unit:.0f} грн/оголошення." if packet_unit else "вартість — з активного пакета."))
    return {
        "ok": True, "productnumber": number, "typename": product.get("typename"),
        "gendername": product.get("gendername"), "category_id": category_id,
        "authorized": bool(st.get("authorized")),
        "on_olx": live, "olx_status": (adv or {}).get("status"),
        "olx_url": (adv or {}).get("url"), "olx_id": (adv or {}).get("olx_id"),
        "needs_package": bool(adv and adv.get("needs_package")),
        "created_by_bms": bool(adv and adv.get("created_by_bms")),
        "last_error": (adv or {}).get("last_error"),
        "image_count": len(images), "packet_unit": packet_unit,
        "pricing": pricing, "config": cfg, "warnings": warnings,
    }

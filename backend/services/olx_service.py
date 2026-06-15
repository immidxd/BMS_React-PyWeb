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
    }

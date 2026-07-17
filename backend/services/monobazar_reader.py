"""monoБазар — публічний (без токенів) читач для верифікації/моніторингу.

Знайдено реверс-інжинірингом публічної вітрини продавця (Next.js JS-бандли
`assets.monobazar.com.ua`): вебсторінка вітрини (`{username}.monobazar.com.ua`)
тягне дані з ПУБЛІЧНОГО REST-шлюзу без будь-якої авторизації:

    GET https://resale-public-api-gateway.monobazar.com.ua
        /v1/resale/seller-shopfront/{username}         — усі активні оголошення
        /v1/resale/seller-shopfront/{username}/count    — кількість
        /v1/resale/clients/{username}/profile           — профіль продавця

Перевірено наживо (акаунт власника, username ivanm1210): усі три віддають
реальні дані без cookie/токена.

⚠️ ТІЛЬКИ ЧИТАННЯ. У JS-бандлах вітрини НЕМАЄ жодного write/POST-ендпоінта —
«Нове оголошення» існує лише в мобільному застосунку (приватне API, не
досліджувалось: інша категорія ризику, ніж публічний веб-код). Тому
автостворення оголошень тут НЕ реалізовано.

Матчинг оголошення → товар BMS: на відміну від OLX/Shafa, продавець НЕ включає
внутрішній номер (#Ф...) у заголовок monoБазар, тому лінкуємо евристично за
збігом бренду/типу/кольору/моделі/розміру/ціни (`_score_candidate`). Автолінк
лише за високим і однозначним рахунком; інакше — `ambiguous`/`none` без лінку.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

API_BASE = "https://resale-public-api-gateway.monobazar.com.ua"
_TIMEOUT = 15
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_HEADERS = {"Accept": "application/json", "User-Agent": _USER_AGENT}
_MAX_PAGES = 40          # запобіжник від нескінченної пагінації (акаунт малий)


def _get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        r = requests.get(f"{API_BASE}{path}", params=params or {},
                         headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception as exc:  # мережа/JSON — не валимо цикл
        logger.warning("monoБазар GET %s failed: %s", path, exc)
        return None


def fetch_profile(username: str) -> Optional[dict]:
    return _get(f"/v1/resale/clients/{username}/profile")


def fetch_count(username: str) -> Optional[int]:
    d = _get(f"/v1/resale/seller-shopfront/{username}/count")
    return d.get("count") if d else None


def fetch_all_listings(username: str) -> List[dict]:
    """Усі активні оголошення продавця (курсорна пагінація)."""
    out: List[dict] = []
    cursor: Optional[str] = None
    for _ in range(_MAX_PAGES):
        params = {"cursor": cursor} if cursor else {}
        d = _get(f"/v1/resale/seller-shopfront/{username}", params)
        if not d:
            break
        items = d.get("items") or []
        out.extend(items)
        if not d.get("hasMore") or not d.get("nextCursor"):
            break
        cursor = d["nextCursor"]
    return out


# ── Конфіг ────────────────────────────────────────────────────────────────────
def get_seller_username(db: Session) -> Optional[str]:
    row = db.execute(text(
        "SELECT seller_username FROM monobazar_config WHERE id=1")).first()
    return (row[0] if row else None) or None


def set_seller_username(db: Session, username: str) -> None:
    db.execute(text("""
        UPDATE monobazar_config SET seller_username = :u, updated_at = now()
        WHERE id = 1
    """), {"u": username.strip()[:120]})
    db.commit()


# ── Матчинг заголовка оголошення → товар BMS ──────────────────────────────────
def _stem(v: Optional[str], keep: int = 3) -> str:
    """Грубий стем для толерантності до відмінка/роду (чорний↔чорні)."""
    v = str(v or "").strip().lower()
    return v[:max(len(v) - 2, keep)] if len(v) > keep else v


def _candidate_index(db: Session) -> List[dict]:
    rows = db.execute(text("""
        SELECT p.id, p.productnumber, p.price,
               lower(coalesce(b.brandname,'')) AS brand,
               lower(coalesce(t.typename,'')) AS typ,
               lower(coalesce(c.colorname,'')) AS color,
               coalesce(p.sizeeu,'') AS size,
               lower(coalesce(p.model,'')) AS model
        FROM products p
        LEFT JOIN brands b ON b.id = p.brandid
        LEFT JOIN types t ON t.id = p.typeid
        LEFT JOIN colors c ON c.id = p.colorid
        WHERE p.productnumber IS NOT NULL
    """)).mappings().all()
    return [dict(r) for r in rows]


def _score_candidate(title_lower: str, price: Optional[float], cand: dict) -> int:
    score = 0
    if cand["brand"] and _stem(cand["brand"]) in title_lower:
        score += 4
    if cand["typ"] and _stem(cand["typ"]) in title_lower:
        score += 3
    if cand["color"] and _stem(cand["color"]) in title_lower:
        score += 2
    if cand["model"] and len(cand["model"]) >= 3 and cand["model"] in title_lower:
        score += 3
    if cand["size"]:
        if re.search(r"(?<!\d)" + re.escape(str(cand["size"])) + r"(?!\d)", title_lower):
            score += 2
    if cand["price"] is not None and price:
        diff = abs(float(cand["price"]) - float(price))
        base = float(price) or 1.0
        if diff <= max(base * 0.05, 5):
            score += 3
        elif diff <= max(base * 0.15, 20):
            score += 1
    return score


_AUTO_LINK_MIN_SCORE = 8
_AUTO_LINK_MIN_MARGIN = 2   # відрив від другого найкращого — щоб уникнути неоднозначності


def match_listing(title: str, price: Optional[float], candidates: List[dict]) -> Dict:
    """Найкращий кандидат за title+price. Повертає {product_id, number, score, confidence}.

    Кандидати групуються за productnumber ПЕРЕД перевіркою неоднозначності:
    кілька рядків розмірів того самого товару (той самий номер, різний sizeeu)
    — це НЕ конкуруючі кандидати, а один товар. Неоднозначність лишається
    лише коли найкращий рахунок ділять РІЗНІ номери (справді різні товари)."""
    title_lower = (title or "").strip().lower()
    scored = [(c, _score_candidate(title_lower, price, c)) for c in candidates]
    if not scored:
        return {"product_id": None, "number": None, "score": 0, "confidence": "none"}

    best_by_number: Dict[str, tuple] = {}
    for c, s in scored:
        num = c["productnumber"]
        if num not in best_by_number or s > best_by_number[num][1]:
            best_by_number[num] = (c, s)
    ranked = sorted(best_by_number.values(), key=lambda x: x[1], reverse=True)

    if ranked[0][1] < _AUTO_LINK_MIN_SCORE:
        return {"product_id": None, "number": None, "score": ranked[0][1], "confidence": "none"}
    best, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    if best_score - second_score < _AUTO_LINK_MIN_MARGIN:
        return {"product_id": None, "number": None, "score": best_score,
                "confidence": "ambiguous"}
    return {"product_id": best["id"], "number": best["productnumber"],
            "score": best_score, "confidence": "confident"}


# ── Синхронізація ──────────────────────────────────────────────────────────────
def sync_listings(db: Session) -> dict:
    username = get_seller_username(db)
    if not username:
        return {"ok": False, "error": "Не задано username продавця monoБазар"}
    listings = fetch_all_listings(username)
    candidates = _candidate_index(db)

    seen_ids: List[str] = []
    confident = ambiguous = none = 0
    for item in listings:
        mid = item.get("id")
        if not mid:
            continue
        seen_ids.append(mid)
        title = item.get("title") or ""
        price = item.get("price")
        m = match_listing(title, price, candidates)
        if m["confidence"] == "confident":
            confident += 1
        elif m["confidence"] == "ambiguous":
            ambiguous += 1
        else:
            none += 1
        db.execute(text("""
            INSERT INTO monobazar_listings (
                monobazar_id, product_id, product_number_raw, title, price,
                photo_url, status, view_count, match_score, match_confidence,
                last_synced_at
            ) VALUES (
                :mid, :pid, :num, :title, :price, :photo, :status, :views,
                :score, :conf, now()
            )
            ON CONFLICT (monobazar_id) DO UPDATE SET
                -- Не втрачаємо вже наявний лінк, якщо цей sync його не визначив.
                product_id         = COALESCE(EXCLUDED.product_id, monobazar_listings.product_id),
                product_number_raw = COALESCE(EXCLUDED.product_number_raw, monobazar_listings.product_number_raw),
                title = EXCLUDED.title, price = EXCLUDED.price, photo_url = EXCLUDED.photo_url,
                status = EXCLUDED.status, view_count = EXCLUDED.view_count,
                match_score = EXCLUDED.match_score, match_confidence = EXCLUDED.match_confidence,
                last_synced_at = now()
        """), {
            "mid": mid, "pid": m["product_id"], "num": m["number"],
            "title": title[:300], "price": price,
            "photo": (item.get("photo") or "")[:500] or None,
            "status": item.get("status"), "views": item.get("viewCount") or 0,
            "score": m["score"], "conf": m["confidence"],
        })
    db.commit()

    removed = 0
    if seen_ids:
        r = db.execute(text(
            "DELETE FROM monobazar_listings WHERE monobazar_id <> ALL(:ids)"),
            {"ids": seen_ids})
        removed = r.rowcount or 0
    db.execute(text(
        "UPDATE monobazar_config SET store_synced_at = now() WHERE id = 1"))
    db.commit()

    logger.info("monoБазар sync: total=%d confident=%d ambiguous=%d none=%d removed=%d",
               len(listings), confident, ambiguous, none, removed)
    return {"ok": True, "total": len(listings), "confident": confident,
            "ambiguous": ambiguous, "unmatched": none, "removed": removed}


def listing_status(db: Session, product_id: int) -> dict:
    row = db.execute(text("""
        SELECT monobazar_id, title, price, status, view_count, match_confidence,
               last_synced_at
        FROM monobazar_listings WHERE product_id = :pid
        ORDER BY (status = 'active') DESC, last_synced_at DESC LIMIT 1
    """), {"pid": int(product_id)}).mappings().first()
    return dict(row) if row else {}

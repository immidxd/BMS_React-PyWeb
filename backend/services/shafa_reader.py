"""Публічний (без токенів) читач Shafa для ЧЕСНОЇ верифікації.

Shafa не має seller API, але її публічний GraphQL віддає per-listing дані без
авторизації:  ``product(id){ name price statusTitle owner{ username } }``
(перевірено серверним запитом — працює без cookie/токена).

Цей модуль читає ЛИШЕ публічні дані ВЖЕ ВІДОМИХ лістингів (за URL/ID, які
продавець підтвердив чи прив'язав), щоб:
  • тримати статус ``confirmed`` без ручного клацання,
  • звіряти фактичну наявність саме на Shafa (``statusTitle``),
  • один раз «вивчити» Shafa-username продавця з ``owner.username``.

Автовиявлення нових лістингів за назвою (bridge_ready → confirmed без URL) —
окремий крок і сюди свідомо не входить: він потребує відтворення прихованого
пошукового запиту Shafa.
"""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SHAFA_GRAPHQL = "https://shafa.ua/api/v5/graphql"
_TIMEOUT = 15
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Скільки лістингів перевіряємо за один цикл (щоб не молотити Shafa щоразу) і
# скільки чекати між запитами. Публічний GraphQL, тож поводимось делікатно.
_MAX_PER_CYCLE = 60
_SLEEP_BETWEEN = 0.25
# statusTitle Shafa: AVAILABLE = живе й у наявності. Решта (RESERVED/SOLD/
# MODERATION/REJECTED/DEACTIVATED/…) означає, що покупець не може купити зараз.
_AVAILABLE_STATUS = "AVAILABLE"


def _listing_id(url: Optional[str], explicit_id: Optional[str] = None) -> Optional[int]:
    """Числовий ID оголошення з shafa_listing_id або з хвоста URL Shafa."""
    for raw in (explicit_id, url):
        if not raw:
            continue
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        # URL типу /uk/women/.../211316208-slug → перший довгий числовий сегмент.
        if url and raw is url:
            tail = urlparse(str(url)).path.rstrip("/").rsplit("/", 1)[-1]
            head = tail.split("-", 1)[0]
            digits = head if head.isdigit() else digits
        if digits.isdigit() and len(digits) >= 6:
            return int(digits)
    return None


def fetch_listing(listing_id: int) -> Optional[Dict[str, Any]]:
    """Публічно прочитати одне оголошення Shafa. None — якщо його немає/помилка.

    ``id`` інлайниться як ціле (ін'єкція неможлива — це int), тож не залежимо
    від типу GraphQL-змінної.
    """
    query = (
        "{ product(id:%d){ id name price statusTitle userStatusTitle "
        "owner{ username } } }" % int(listing_id)
    )
    try:
        r = requests.post(
            SHAFA_GRAPHQL,
            json={"query": query},
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
        data = r.json()
    except Exception as exc:  # мережа/JSON — не валимо цикл
        logger.warning("Shafa fetch_listing(%s) failed: %s", listing_id, exc)
        return None
    product = (data or {}).get("data", {}).get("product")
    if not product:
        return None
    owner = product.get("owner") or {}
    return {
        "id": product.get("id"),
        "name": product.get("name"),
        "price": product.get("price"),
        "status_title": product.get("statusTitle"),
        "user_status_title": product.get("userStatusTitle"),
        "owner_username": owner.get("username"),
        "available": product.get("statusTitle") == _AVAILABLE_STATUS,
    }


# ── Конфіг: Shafa-username продавця (вивчається сам) ─────────────────────────
def get_seller_username(db: Session) -> Optional[str]:
    row = db.execute(text(
        "SELECT seller_username FROM shafa_config WHERE id=1"
    )).first()
    return (row[0] if row else None) or None


def learn_seller_username(db: Session, username: Optional[str]) -> None:
    """Запам'ятати username продавця, якщо він ще не заданий. Не перезаписуємо
    вже наявний (щоб випадковий чужий лістинг не «викрав» ідентичність)."""
    username = (username or "").strip()
    if not username:
        return
    db.execute(text("""
        UPDATE shafa_config
        SET seller_username = :u, store_synced_at = now()
        WHERE id=1 AND (seller_username IS NULL OR BTRIM(seller_username) = '')
    """), {"u": username[:120]})


def _record_snapshot(db: Session, productnumber: str, listing: Dict[str, Any]) -> None:
    presence = "available" if listing.get("available") else "not_available"
    db.execute(text("""
        UPDATE shafa_publications
        SET shafa_presence = :presence, shafa_checked_at = now(),
            shafa_url = COALESCE(shafa_url, :url),
            last_error = NULL, updated_at = updated_at
        WHERE productnumber = :n
    """), {
        "presence": presence, "n": productnumber,
        "url": None,  # URL не змінюємо тут; лише наявність/час перевірки
    })


def reconcile_confirmed(db: Session) -> Dict[str, Any]:
    """Пройтися ВІДОМИМИ (з URL/ID) підтвердженими лістингами й публічно звірити:

      • оголошення ще існує і належить нашому продавцю → тримаємо confirmed;
      • оновлюємо фактичну наявність Shafa (statusTitle) у shafa_presence;
      • один раз вивчаємо seller_username із owner.username.

    Свідомо консервативний: якщо оголошення зникло (продано/знято), ми НЕ
    перекидаємо запис у removed автоматично — лише фіксуємо стан і час, бо
    «продано» — це успіх, а не помилка. Рішення знімати лишається за людиною.
    """
    rows = db.execute(text("""
        SELECT productnumber, shafa_url, shafa_listing_id, status
        FROM shafa_publications
        WHERE status IN ('confirmed', 'manual_existing')
          AND (NULLIF(BTRIM(shafa_url), '') IS NOT NULL
               OR NULLIF(BTRIM(shafa_listing_id), '') IS NOT NULL)
        ORDER BY COALESCE(shafa_checked_at, '1970-01-01'::timestamptz) ASC
        LIMIT :lim
    """), {"lim": _MAX_PER_CYCLE}).mappings().all()

    seller = get_seller_username(db)
    checked = matched = available = missing = foreign = 0
    for row in rows:
        lid = _listing_id(row.get("shafa_url"), row.get("shafa_listing_id"))
        if not lid:
            continue
        listing = fetch_listing(lid)
        checked += 1
        if listing is None:
            missing += 1
            db.execute(text(
                "UPDATE shafa_publications SET shafa_checked_at=now() "
                "WHERE productnumber=:n"), {"n": row["productnumber"]})
            time.sleep(_SLEEP_BETWEEN)
            continue
        owner = listing.get("owner_username")
        # Якщо продавця вже знаємо — цей лістинг має бути його. Якщо ще ні —
        # вивчаємо з першого валідного оголошення.
        if seller and owner and owner != seller:
            foreign += 1
            time.sleep(_SLEEP_BETWEEN)
            continue
        if not seller and owner:
            learn_seller_username(db, owner)
            seller = owner
        matched += 1
        if listing.get("available"):
            available += 1
        _record_snapshot(db, row["productnumber"], listing)
        time.sleep(_SLEEP_BETWEEN)

    db.commit()
    return {
        "ok": True, "seller_username": seller, "checked": checked,
        "matched": matched, "available_on_shafa": available,
        "missing": missing, "foreign_owner": foreign,
    }


def verify_product(db: Session, product_id: int) -> Dict[str, Any]:
    """On-demand: звірити конкретний товар за його відомим лістингом Shafa."""
    row = db.execute(text("""
        SELECT sp.productnumber, sp.shafa_url, sp.shafa_listing_id
        FROM shafa_publications sp
        JOIN products p ON TRIM(LEADING '#' FROM p.productnumber) = sp.productnumber
        WHERE p.id = :pid
        LIMIT 1
    """), {"pid": int(product_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "Немає прив'язаного оголошення Shafa"}
    lid = _listing_id(row.get("shafa_url"), row.get("shafa_listing_id"))
    if not lid:
        return {"ok": False, "error": "Немає URL/ID оголошення"}
    listing = fetch_listing(lid)
    if listing is None:
        db.execute(text(
            "UPDATE shafa_publications SET shafa_checked_at=now() WHERE productnumber=:n"),
            {"n": row["productnumber"]})
        db.commit()
        return {"ok": True, "exists": False,
                "note": "Оголошення не знайдено публічно (могло бути продане чи зняте)."}
    if listing.get("owner_username"):
        learn_seller_username(db, listing["owner_username"])
    _record_snapshot(db, row["productnumber"], listing)
    db.commit()
    return {
        "ok": True, "exists": True,
        "available_on_shafa": bool(listing.get("available")),
        "status_title": listing.get("status_title"),
        "shafa_price": listing.get("price"),
        "owner_username": listing.get("owner_username"),
    }

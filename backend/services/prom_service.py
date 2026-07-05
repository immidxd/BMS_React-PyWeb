"""Prom.ua Public API — інтеграція (Фаза 2).

Токен СТАТИЧНИЙ (Bearer, з кабінету продавця) — простіше за OLX OAuth, без refresh.
База https://my.prom.ua/api/v1. Немає вебхуків → polling.

Напрями:
  1. ЧИТАННЯ: sync_products (дзеркало prom_products) + sync_orders (prom_orders,
     ОКРЕМЕ від core orders — рішення власника). Лінк BMS↔Prom за SKU=productnumber.
  2. ЗАПИС: push_availability — presence available/not_available на Prom за
     наявністю в BMS. ЛИШЕ за явним викликом (кнопка), не авто.

Довідка API → memory [[prom-api-reference]].
"""
import os
import re
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PROM_API_BASE = os.getenv("PROM_API_BASE", "https://my.prom.ua/api/v1").rstrip("/")
_TIMEOUT = 60


# ── Token storage (prom_config, single-row id=1) ─────────────────────────────
def _load_config(db: Session) -> Optional[dict]:
    row = db.execute(text(
        "SELECT api_token, token_expires_at FROM prom_config WHERE id = 1"
    )).fetchone()
    if not row or not row[0]:
        return None
    return {"api_token": row[0], "token_expires_at": row[1]}


def save_token(db: Session, token: str, expires_at: Optional[str] = None) -> None:
    db.execute(text("""
        INSERT INTO prom_config (id, api_token, token_expires_at, updated_at)
        VALUES (1, :t, :e, now())
        ON CONFLICT (id) DO UPDATE SET
            api_token = EXCLUDED.api_token,
            token_expires_at = COALESCE(EXCLUDED.token_expires_at, prom_config.token_expires_at),
            updated_at = now()
    """), {"t": (token or "").strip(), "e": expires_at})
    db.commit()


def is_authorized(db: Session) -> bool:
    return _load_config(db) is not None


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _api_get(token: str, path: str, params: dict = None) -> dict:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(f"{PROM_API_BASE}{path}", params=params or {},
                             headers=_headers(token), timeout=_TIMEOUT)
            if r.status_code == 401:
                raise RuntimeError("Prom API 401 — токен недійсний або протух")
            if r.status_code >= 400:
                raise RuntimeError(f"Prom API {path} [{r.status_code}]: {r.text[:200]}")
            return r.json()
        except RuntimeError:
            raise
        except Exception as e:  # мережа/таймаут — ретрай
            last = e
            if attempt < 2:
                import time; time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Prom API {path}: мережевий збій — {last}")


def _api_post(token: str, path: str, body) -> dict:
    r = requests.post(f"{PROM_API_BASE}{path}", json=body,
                      headers=_headers(token), timeout=_TIMEOUT)
    if r.status_code == 401:
        raise RuntimeError("Prom API 401 — токен недійсний або протух")
    if r.status_code >= 400:
        raise RuntimeError(f"Prom API {path} [{r.status_code}]: {r.text[:300]}")
    return r.json()


# ── Лінк за SKU (= productnumber) ────────────────────────────────────────────
def _resolve_product_id(db: Session, sku: Optional[str]) -> Optional[int]:
    """SKU Prom → products.id за номером. Пробуємо форми з/без '#'/'Ф'."""
    if not sku:
        return None
    s = str(sku).strip().lstrip("#")
    forms = {s, f"#{s}", s.upper(), f"#{s.upper()}"}
    if s[:1].isdigit():
        forms |= {f"Ф{s}", f"#Ф{s}"}
    row = db.execute(text(
        "SELECT id FROM products WHERE productnumber = ANY(:f) ORDER BY id LIMIT 1"
    ), {"f": list(forms)}).fetchone()
    return row[0] if row else None


def _price_num(price_text: Optional[str]) -> Optional[float]:
    """'1 890 грн' → 1890.0. Бере перше число (без пробілів-роздільників)."""
    if not price_text:
        return None
    m = re.search(r"[\d\s]+(?:[.,]\d+)?", str(price_text))
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return None


# ── Sync products ────────────────────────────────────────────────────────────
def sync_products(db: Session) -> Dict:
    cfg = _load_config(db)
    if not cfg:
        return {"ok": False, "error": "Prom токен не задано"}
    token = cfg["api_token"]
    try:
        seen: List[int] = []
        created = linked = 0
        last_id = None
        for _ in range(100):  # сторінки по last_id (спадна пагінація)
            params = {"limit": 100}
            if last_id is not None:
                params["last_id"] = last_id
            data = _api_get(token, "/products/list", params)
            prods = data.get("products") or []
            if not prods:
                break
            for p in prods:
                pid = _resolve_product_id(db, p.get("sku"))
                res = db.execute(text("""
                    INSERT INTO prom_products (prom_id, product_id, sku, name, presence, status, price, url, last_synced_at)
                    VALUES (:id, :pid, :sku, :name, :pres, :st, :price, :url, now())
                    ON CONFLICT (prom_id) DO UPDATE SET
                        product_id=EXCLUDED.product_id, sku=EXCLUDED.sku, name=EXCLUDED.name,
                        presence=EXCLUDED.presence, status=EXCLUDED.status, price=EXCLUDED.price,
                        url=EXCLUDED.url, last_synced_at=now()
                    RETURNING (xmax = 0) AS inserted
                """), {
                    "id": p.get("id"), "pid": pid, "sku": (p.get("sku") or "")[:80] or None,
                    "name": (p.get("name") or "")[:400], "pres": p.get("presence"),
                    "st": p.get("status"), "price": p.get("price"),
                    "url": (p.get("main_image") and None) or None,
                })
                r = res.fetchone()
                created += 1 if (r and r[0]) else 0
                linked += 1 if pid else 0
                if p.get("id") is not None:
                    seen.append(int(p["id"]))
            if len(prods) < 100:
                break
            last_id = min(int(p["id"]) for p in prods if p.get("id") is not None) - 1
        db.commit()
        removed = 0
        if seen:
            r = db.execute(text("DELETE FROM prom_products WHERE prom_id <> ALL(:ids)"), {"ids": seen})
            removed = r.rowcount or 0
            db.commit()
        return {"ok": True, "total": len(seen), "created": created, "linked": linked, "removed": removed}
    except Exception as e:
        db.rollback()
        logger.error(f"Prom sync_products failed: {e}")
        return {"ok": False, "error": str(e)}


# ── Sync orders (ОКРЕМЕ дзеркало) ────────────────────────────────────────────
def sync_orders(db: Session, date_from: Optional[str] = None, max_pages: int = 50) -> Dict:
    cfg = _load_config(db)
    if not cfg:
        return {"ok": False, "error": "Prom токен не задано"}
    token = cfg["api_token"]
    try:
        created = updated = 0
        last_id = None
        seen = 0
        for _ in range(max_pages):
            params = {"limit": 50}
            if date_from:
                params["date_from"] = date_from
            if last_id is not None:
                params["last_id"] = last_id
            data = _api_get(token, "/orders/list", params)
            orders = data.get("orders") or []
            if not orders:
                break
            for o in orders:
                items, linked = [], 0
                for pr in (o.get("products") or []):
                    lp = _resolve_product_id(db, pr.get("sku"))
                    if lp:
                        linked += 1
                    items.append({"sku": pr.get("sku"), "name": pr.get("name"),
                                  "quantity": pr.get("quantity"), "price": pr.get("price"),
                                  "product_id": lp})
                fname = " ".join(x for x in (o.get("client_first_name"),
                                             o.get("client_last_name")) if x) or None
                import json as _json
                res = db.execute(text("""
                    INSERT INTO prom_orders (prom_id, status, source, date_created, client_name,
                        phone, price_text, price_num, products, linked_count, client_notes, last_synced_at)
                    VALUES (:id, :st, :src, :dc, :cn, :ph, :pt, :pn, CAST(:pr AS jsonb), :lc, :note, now())
                    ON CONFLICT (prom_id) DO UPDATE SET
                        status=EXCLUDED.status, price_text=EXCLUDED.price_text, price_num=EXCLUDED.price_num,
                        products=EXCLUDED.products, linked_count=EXCLUDED.linked_count,
                        client_notes=EXCLUDED.client_notes, last_synced_at=now()
                    RETURNING (xmax = 0) AS inserted
                """), {
                    "id": o.get("id"), "st": o.get("status"), "src": o.get("source"),
                    "dc": o.get("date_created"), "cn": fname, "ph": o.get("phone"),
                    "pt": o.get("price"), "pn": _price_num(o.get("price")),
                    "pr": _json.dumps(items, ensure_ascii=False), "lc": linked,
                    "note": o.get("client_notes"),
                })
                r = res.fetchone()
                created += 1 if (r and r[0]) else 0
                updated += 0 if (r and r[0]) else 1
                seen += 1
            if len(orders) < 50:
                break
            last_id = min(int(o["id"]) for o in orders if o.get("id") is not None) - 1
        db.commit()
        return {"ok": True, "total": seen, "created": created, "updated": updated}
    except Exception as e:
        db.rollback()
        logger.error(f"Prom sync_orders failed: {e}")
        return {"ok": False, "error": str(e)}


# ── Push availability (ЗАПИС, лише за викликом) ──────────────────────────────
def push_availability(db: Session, dry_run: bool = False) -> Dict:
    """BMS-наявність → Prom presence. Для кожного prom_product зі злінкованим
    товаром: available_qty>0 → 'available', інакше 'not_available'. products/edit
    за prom_id. dry_run=True — лише рахує, що змінилось би (нічого не пише)."""
    cfg = _load_config(db)
    if not cfg:
        return {"ok": False, "error": "Prom токен не задано"}
    token = cfg["api_token"]
    # Наявність BMS по номеру (GREATEST(quantity - sold_count, 0) сумарно на productnumber)
    rows = db.execute(text("""
        SELECT pp.prom_id, pp.sku, pp.presence AS cur_presence,
               COALESCE(SUM(GREATEST(COALESCE(p.quantity,0) - COALESCE(s.sold_count,0), 0)), 0) AS avail
        FROM prom_products pp
        JOIN products p ON p.id = pp.product_id
        LEFT JOIN (
            SELECT oi.product_id,
                   GREATEST(COUNT(*) FILTER (WHERE o.order_status_id=7 OR (o.order_status_id=1 AND o.payment_status_id=1))
                            - COUNT(*) FILTER (WHERE o.order_status_id=9), 0) AS sold_count
            FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE oi.product_id IS NOT NULL AND o.order_status_id IN (1,7,9)
            GROUP BY oi.product_id
        ) s ON s.product_id = p.id
        WHERE pp.product_id IS NOT NULL
        GROUP BY pp.prom_id, pp.sku, pp.presence, p.productnumber
    """)).fetchall()

    changes = []
    for prom_id, sku, cur, avail in rows:
        want = "available" if (avail or 0) > 0 else "not_available"
        if cur != want:
            changes.append({"id": int(prom_id), "sku": sku, "from": cur, "to": want})

    if dry_run:
        return {"ok": True, "dry_run": True, "would_change": len(changes),
                "sample": changes[:10], "checked": len(rows)}
    if not changes:
        return {"ok": True, "changed": 0, "checked": len(rows), "note": "усе вже синхронне"}
    try:
        # products/edit приймає масив об'єктів; шлемо лише presence (+ id).
        payload = [{"id": c["id"], "presence": c["to"]} for c in changes]
        _api_post(token, "/products/edit", payload)
        # локально теж оновимо дзеркало, щоб не пушити двічі
        for c in changes:
            db.execute(text("UPDATE prom_products SET presence=:p WHERE prom_id=:id"),
                       {"p": c["to"], "id": c["id"]})
        db.commit()
        return {"ok": True, "changed": len(changes), "checked": len(rows), "sample": changes[:10]}
    except Exception as e:
        db.rollback()
        logger.error(f"Prom push_availability failed: {e}")
        return {"ok": False, "error": str(e)}


# ── Status (для UI + попередження про термін токена) ─────────────────────────
def get_status(db: Session) -> Dict:
    cfg = _load_config(db)
    exp = cfg.get("token_expires_at") if cfg else None
    days_left = None
    if exp:
        try:
            days_left = (exp - datetime.now(timezone.utc)).days
        except TypeError:
            days_left = (exp - datetime.now()).days
    return {
        "configured": cfg is not None,
        "token_expires_at": exp.isoformat() if exp else None,
        "token_days_left": days_left,
        "token_expiring_soon": (days_left is not None and days_left <= 30),
        "product_count": db.execute(text("SELECT COUNT(*) FROM prom_products")).scalar() or 0,
        "linked_count": db.execute(text("SELECT COUNT(*) FROM prom_products WHERE product_id IS NOT NULL")).scalar() or 0,
        "order_count": db.execute(text("SELECT COUNT(*) FROM prom_orders")).scalar() or 0,
    }

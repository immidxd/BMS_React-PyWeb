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


# ── Експорт товару НА Prom (Фаза 3): фід + import ────────────────────────────
# Prom не має «create» — товари створюються через імпорт-фід (Prom XML/YML).
# mark_missing='none' → інші товари НЕ чіпаються. Автозаповнення з бази BMS.
from xml.sax.saxutils import escape as _xesc, quoteattr as _xqattr

_PROM_FALLBACK_CATEGORY = 580301  # «Обувь, общее» — коли тип не мапиться
_PROM_SHOE_GROUP = 154833694      # група каталогу «Взуття»


def _bms_product_for_export(db: Session, product_id: int) -> Optional[dict]:
    row = db.execute(text("""
        SELECT p.id, p.productnumber, p.model, p.price, p.description,
               b.brandname, t.typename, t.id AS typeid, st.subtypename, st.id AS subtypeid,
               c.colorname, g.gendername, p.season, p.year,
               co.countryname AS manufacturer, cond.conditionname,
               ARRAY(SELECT DISTINCT p2.sizeeu FROM products p2
                     WHERE p2.productnumber = p.productnumber AND p2.sizeeu IS NOT NULL AND p2.sizeeu <> '') AS sizes
        FROM products p
        LEFT JOIN brands b ON b.id=p.brandid
        LEFT JOIN types t ON t.id=p.typeid
        LEFT JOIN subtypes st ON st.id=p.subtypeid
        LEFT JOIN colors c ON c.id=p.colorid
        LEFT JOIN genders g ON g.id=p.genderid
        LEFT JOIN countries co ON co.id=p.manufacturercountryid
        LEFT JOIN conditions cond ON cond.id=COALESCE(p.current_conditionid, p.conditionid)
        WHERE p.id = :id
    """), {"id": product_id}).mappings().first()
    if not row:
        return None
    d = dict(row)
    mats = db.execute(text("""
        SELECT pm.position, string_agg(m.materialname, ', ') AS names
        FROM product_materials pm JOIN materials m ON m.id=pm.material_id
        WHERE pm.product_id = :id GROUP BY pm.position
    """), {"id": product_id}).fetchall()
    d["materials"] = {p: n for p, n in mats}
    return d


def _prom_category_for(db: Session, token: str, bms: dict) -> int:
    """Категорія Prom для нового товару — з мапи НАЯВНИХ товарів (за типом/підвидом
    BMS). Не вгадуємо: беремо ту категорію, яку Prom уже призначив схожим товарам."""
    try:
        data = _api_get(token, "/products/list", {"limit": 100})
    except Exception:
        return _PROM_FALLBACK_CATEGORY
    sku_cat = {str(p.get("sku")): (p.get("category") or {}).get("id")
               for p in data.get("products", []) if p.get("sku") and (p.get("category") or {}).get("id")}
    if not sku_cat:
        return _PROM_FALLBACK_CATEGORY
    rows = db.execute(text("""
        SELECT pp.sku, p.typeid, p.subtypeid FROM prom_products pp
        JOIN products p ON p.id = pp.product_id WHERE pp.sku = ANY(:skus)
    """), {"skus": list(sku_cat.keys())}).fetchall()
    from collections import Counter
    by_sub, by_type = {}, {}
    for sku, tid, sid in rows:
        cat = sku_cat.get(str(sku))
        if not cat:
            continue
        if sid:
            by_sub.setdefault(sid, Counter())[cat] += 1
        if tid:
            by_type.setdefault(tid, Counter())[cat] += 1
    if bms.get("subtypeid") and by_sub.get(bms["subtypeid"]):
        return int(by_sub[bms["subtypeid"]].most_common(1)[0][0])
    if bms.get("typeid") and by_type.get(bms["typeid"]):
        return int(by_type[bms["typeid"]].most_common(1)[0][0])
    return _PROM_FALLBACK_CATEGORY


_GENDER_ADJ = {"чоловіча": "Чоловічі", "жіноча": "Жіночі", "дитяча": "Дитячі", "унісекс": "Унісекс"}


def _build_name(bms: dict) -> str:
    """Назва (укр): стать(прикметник) + тип(мала) + бренд + модель + колір + розмір.
    Напр. «Жіночі кросівки Nike Air Max чорні розмір 38»."""
    g = _GENDER_ADJ.get(str(bms.get("gendername") or "").strip().lower())
    typ = str(bms.get("typename") or "").strip().lower()
    parts = [g, typ, bms.get("brandname"), bms.get("model"), bms.get("colorname")]
    name = " ".join(str(x).strip() for x in parts if x and str(x).strip())
    sizes = bms.get("sizes") or []
    if len(sizes) == 1:
        name += f" розмір {sizes[0]}"
    return name[:250] or (bms.get("productnumber") or "Товар")


_MAT_LABELS = {"upper": "Верх", "middle": "Середина", "membrane": "Мембрана",
               "insole": "Устілка", "midsole": "Проміжна підошва", "sole": "Підошва"}


def _build_description(bms: dict) -> str:
    """HTML-опис з характеристик BMS."""
    rows = []
    for label, key in [("Бренд", "brandname"), ("Модель", "model"), ("Тип", "typename"),
                       ("Колір", "colorname"), ("Стать", "gendername"), ("Сезон", "season"),
                       ("Стан", "conditionname"), ("Виробник", "manufacturer")]:
        v = bms.get(key)
        if v:
            rows.append(f"<li><b>{_xesc(label)}:</b> {_xesc(str(v))}</li>")
    for pos, names in (bms.get("materials") or {}).items():
        if names:
            rows.append(f"<li><b>{_xesc(_MAT_LABELS.get(pos, pos))}:</b> {_xesc(names)}</li>")
    sizes = bms.get("sizes") or []
    if sizes:
        rows.append(f"<li><b>Доступні розміри:</b> {_xesc(', '.join(map(str, sizes)))}</li>")
    intro = _xesc(_build_name(bms))
    return f"<p>{intro}</p><ul>{''.join(rows)}</ul>"


def _build_params(bms: dict) -> List[tuple]:
    """(назва, значення) для <param> — характеристики Prom."""
    out = []
    for label, key in [("Бренд", "brandname"), ("Колір", "colorname"), ("Стать", "gendername"),
                       ("Сезон", "season"), ("Стан", "conditionname"), ("Країна-виробник", "manufacturer")]:
        v = bms.get(key)
        if v:
            out.append((label, str(v)))
    if (bms.get("materials") or {}).get("upper"):
        out.append(("Матеріал", bms["materials"]["upper"]))
    for sz in (bms.get("sizes") or []):
        out.append(("Розмір", str(sz)))
    return out


def _product_image_urls(product_number: str) -> List[str]:
    """Публічні R2-URL офіційних фото товару (fallback — реальні). Порожньо, якщо R2 не налаштовано."""
    try:
        from services.product_images import list_images
        from services import r2_storage
        from urllib.parse import unquote
    except ImportError:
        from backend.services.product_images import list_images
        from backend.services import r2_storage
        from urllib.parse import unquote
    imgs = list_images(product_number)
    official = [i for i in imgs if getattr(i, "kind", "") == "official"]
    chosen = official or [i for i in imgs if getattr(i, "kind", "") != "defect"]
    urls = []
    for e in chosen[:10]:
        u = getattr(e, "url", "") or ""
        path = u.split("?")[0]
        if "/product-images/" in path:
            key = unquote(path.split("/product-images/", 1)[1])
            pub = r2_storage.public_url(key)
            if pub:
                urls.append(pub)
    return urls


def build_export_feed(db: Session, product_id: int, category_id: int, available: bool = True) -> str:
    """Prom XML-фід із ОДНИМ товаром (для import_file). available=False —
    товар створюється прихованим/без наявності (для draft-режиму, без «живого вікна»)."""
    bms = _bms_product_for_export(db, product_id)
    if not bms:
        raise RuntimeError("Товар не знайдено")
    num = (bms["productnumber"] or "").lstrip("#")
    price = float(bms["price"] or 0)
    imgs = _product_image_urls(bms["productnumber"])
    name = _build_name(bms)
    desc = _build_description(bms)
    params = _build_params(bms)

    def tag(t, v):
        return f"<{t}>{_xesc(str(v))}</{t}>"
    parts = [
        f'<item id={_xqattr(num)} available="{"true" if available else "false"}">',
        tag("price", int(price) if price == int(price) else price),
        tag("currencyId", "UAH"),
        tag("categoryId", category_id),
        tag("name", name),
        tag("vendorCode", num),
    ]
    if bms.get("brandname"):
        parts.append(tag("vendor", bms["brandname"]))
    parts.append(f"<description><![CDATA[{desc}]]></description>")
    for u in imgs:
        parts.append(tag("image", u))
    for pn, pv in params:
        parts.append(f'<param name={_xqattr(pn)}>{_xesc(pv)}</param>')
    parts.append("</item>")
    item_xml = "".join(parts)
    cat_caption = _xesc(bms.get("subtypename") or bms.get("typename") or "Товар")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<price><date>{d}</date>'
        '<currencies><currency id="UAH" rate="1"/></currencies>'
        '<categories><category id="{cid}">{cap}</category></categories>'
        '<items>{item}</items></price>'
    ).format(d=datetime.now().strftime("%Y-%m-%d %H:%M"), cid=category_id, cap=cat_caption, item=item_xml)


def _find_prom_id_by_sku(token: str, sku: str) -> Optional[int]:
    try:
        data = _api_get(token, "/products/list", {"limit": 100})
    except Exception:
        return None
    for p in data.get("products", []):
        if str(p.get("sku")) == str(sku):
            return int(p["id"])
    return None


def _queue_draft(db: Session, sku: str) -> None:
    db.execute(text("INSERT INTO prom_draft_queue (sku) VALUES (:s) "
                    "ON CONFLICT (sku) DO UPDATE SET requested_at = now(), attempts = 0"), {"s": sku})
    db.commit()


def process_draft_queue(db: Session) -> Dict:
    """Перевести в draft усі товари з prom_draft_queue, що вже з'явились на Prom.
    Idempotent, викликається щоциклу синку. Здається після ~60 спроб (не вічно)."""
    cfg = _load_config(db)
    if not cfg:
        return {"resolved": 0, "resolved_skus": []}
    token = cfg["api_token"]
    rows = db.execute(text("SELECT sku, attempts FROM prom_draft_queue")).fetchall()
    resolved = []
    for sku, attempts in rows:
        pid = _find_prom_id_by_sku(token, sku)
        if pid:
            try:
                _api_post(token, "/products/edit", [{"id": pid, "status": "draft"}])
                db.execute(text("DELETE FROM prom_draft_queue WHERE sku = :s"), {"s": sku})
                resolved.append(sku)
            except Exception as e:
                logger.warning(f"Prom draft-set failed for {sku}: {e}")
        else:
            if (attempts or 0) + 1 > 60:
                db.execute(text("DELETE FROM prom_draft_queue WHERE sku = :s"), {"s": sku})
            else:
                db.execute(text("UPDATE prom_draft_queue SET attempts = COALESCE(attempts,0)+1 WHERE sku = :s"), {"s": sku})
    db.commit()
    return {"resolved": len(resolved), "resolved_skus": resolved}


def export_product_to_prom(db: Session, product_id: int, as_draft: bool = True,
                           preview: bool = False) -> Dict:
    """Виставити товар BMS на Prom (Фаза 3). preview=True — повертає згенеровані
    назву/опис/категорію/фото БЕЗ створення. Інакше: import_file (mark_missing=none
    — інші товари не чіпає) + (as_draft) переводить у чернетку."""
    cfg = _load_config(db)
    if not cfg:
        return {"ok": False, "error": "Prom токен не задано"}
    token = cfg["api_token"]
    bms = _bms_product_for_export(db, product_id)
    if not bms:
        return {"ok": False, "error": "Товар не знайдено"}
    imgs = _product_image_urls(bms["productnumber"])
    cat = _prom_category_for(db, token, bms)
    sku = (bms["productnumber"] or "").lstrip("#")

    if preview:
        return {"ok": True, "preview": True, "sku": sku, "name": _build_name(bms),
                "category_id": cat, "images": imgs, "image_count": len(imgs),
                "params": _build_params(bms), "already_on_prom": _find_prom_id_by_sku(token, sku) is not None}
    if not imgs:
        return {"ok": False, "error": "У товару немає фото — Prom вимагає зображення. Додай фото й повтори."}
    if not (bms.get("price") and float(bms["price"]) > 0):
        return {"ok": False, "error": "У товару немає ціни"}

    # Draft: створюємо ВІДРАЗУ прихованим (available=false) — без «живого вікна»,
    # поки фоновий крок переведе в статус draft (створення на Prom АСИНХРОННЕ:
    # статус імпорту одразу каже created=0, товар з'являється за 1-3 хв).
    feed = build_export_feed(db, product_id, cat, available=not as_draft)
    try:
        import json as _json
        files = {"file": ("feed.xml", feed.encode("utf-8"), "application/xml")}
        payload = {"data": _json.dumps({"mark_missing_product_as": "none", "force_update": True})}
        r = requests.post(f"{PROM_API_BASE}/products/import_file",
                          headers={"Authorization": f"Bearer {token}"},
                          files=files, data=payload, timeout=90)
        if r.status_code >= 400:
            return {"ok": False, "error": f"Import [{r.status_code}]: {r.text[:250]}"}
        import_id = (r.json() or {}).get("id")

        # НАДІЙНЕ переведення в чернетку через чергу: створення на Prom асинхронне
        # (1-3 хв), тож кладемо sku в prom_draft_queue — і Prom-синк-цикл щоразу
        # намагається знайти товар і виставити draft, доки не вдасться. Плюс
        # короткий фоновий «швидкий» прохід, щоб зазвичай устигнути за ~хвилину.
        if as_draft:
            _queue_draft(db, sku)
            import threading as _th

            def _quick():
                import time as _t
                from models.database import SessionLocal as _SL
                for _ in range(24):          # ~4 хв швидких спроб
                    _t.sleep(10)
                    _db = _SL()
                    try:
                        if process_draft_queue(_db).get("resolved_skus") and sku not in \
                           [x[0] for x in _db.execute(text("SELECT sku FROM prom_draft_queue")).fetchall()]:
                            break
                    except Exception:
                        pass
                    finally:
                        _db.close()

            _th.Thread(target=_quick, daemon=True).start()

        note = ("Товар створюється на Prom як ЧЕРНЕТКА (з'явиться за 1-3 хв — особливість "
                "Prom API). Знайти: Prom → Товари → фільтр «Видимість: чернетка». "
                "Перевір автозаповнення й опублікуй.")
        return {"ok": True, "sku": sku, "category_id": cat, "images": len(imgs),
                "import_id": import_id, "name": _build_name(bms), "as_draft": as_draft, "note": note}
    except Exception as e:
        logger.error(f"Prom export failed for {sku}: {e}")
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

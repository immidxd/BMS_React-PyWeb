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
    """SKU Prom → products.id. Спершу як цілий номер (форми з/без '#'/'Ф'); якщо не
    знайдено — ростовка «номер-розмір»: відрізаємо останній «-розмір» і шукаємо рядок
    за номером І sizeeu (реальні номери-варіанти на кшталт «Ф1067-2» матчаться раніше)."""
    if not sku:
        return None
    s = str(sku).strip().lstrip("#")

    def _forms(x):
        f = {x, f"#{x}", x.upper(), f"#{x.upper()}"}
        if x[:1].isdigit():
            f |= {f"Ф{x}", f"#Ф{x}"}
        return list(f)

    row = db.execute(text(
        "SELECT id FROM products WHERE productnumber = ANY(:f) ORDER BY id LIMIT 1"
    ), {"f": _forms(s)}).fetchone()
    if row:
        return row[0]
    if "-" in s:                                   # ростовка: номер-розмір
        base, size = s.rsplit("-", 1)
        r2 = db.execute(text(
            "SELECT id FROM products WHERE productnumber = ANY(:f) AND sizeeu = :sz ORDER BY id LIMIT 1"
        ), {"f": _forms(base), "sz": size}).fetchone()
        if r2:
            return r2[0]
    return None


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
               pk.packagingname,
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
        LEFT JOIN packaging_types pk ON pk.id = p.packagingid
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

# Країна-власник бренду (публічні дані) → у дужках у описі: «Ecco (Данія)».
BRAND_COUNTRY = {
    "nike": "США", "adidas": "Німеччина", "reebok": "США", "puma": "Німеччина",
    "new balance": "США", "asics": "Японія", "hoka": "США", "teva": "США",
    "merrell": "США", "salomon": "Франція", "columbia": "США", "vans": "США",
    "converse": "США", "timberland": "США", "crocs": "США", "skechers": "США",
    "ecco": "Данія", "geox": "Італія", "clarks": "Великобританія", "caprice": "Німеччина",
    "rieker": "Німеччина", "tamaris": "Німеччина", "gabor": "Німеччина", "lasocki": "Польща",
    "gino rossi": "Польща", "badura": "Польща", "guess": "США", "tommy hilfiger": "США",
    "calvin klein": "США", "karl lagerfeld": "Німеччина", "michael kors": "США",
    "lacoste": "Франція", "fila": "Італія", "kappa": "Італія", "champion": "США",
    "ugg": "США", "birkenstock": "Німеччина", "dr. martens": "Великобританія",
    "keen": "США", "jack wolfskin": "Німеччина", "under armour": "США", "mizuno": "Японія",
    "saucony": "США", "brooks": "США", "diesel": "Італія", "gucci": "Італія",
    "versace": "Італія", "emporio armani": "Італія", "liu jo": "Італія", "pinko": "Італія",
    "levis": "США", "wrangler": "США", "mustang": "Німеччина", "s.oliver": "Німеччина",
    "bugatti": "Німеччина", "marco tozzi": "Німеччина", "legero": "Австрія", "ara": "Німеччина",
    "remonte": "Німеччина", "josef seibel": "Німеччина", "salamander": "Німеччина",
    "camper": "Іспанія", "pikolinos": "Іспанія", "aldo": "Канада", "steve madden": "США",
    "palladium": "Франція", "hey dude": "США", "keds": "США", "sorel": "Канада",
    "the north face": "США", "cmp": "Італія", "helly hansen": "Норвегія",
}

# Націнка на Prom за типом (30-40%, з дослідження цін власника).
_TYPE_MARKUP = {
    "кросівки": 1.35, "кеди": 1.35, "черевики": 1.35, "ботінки": 1.35, "чоботи": 1.35,
    "босоніжки": 1.35, "сандалі": 1.33, "шльопанці": 1.30, "туфлі": 1.28,
    "балетки": 1.28, "мокасини": 1.30, "сумка": 1.35, "валіза": 1.35,
}


def _prom_price(base, typename: str):
    """Ціна для Prom: база × націнка(за типом) → «психологічне» округлення (…90,
    подекуди …50 лишаємо), але не нижче бази й без роботизованості."""
    base = float(base or 0)
    if base <= 0:
        return base
    m = _TYPE_MARKUP.get(str(typename or "").strip().lower(), 1.33)
    v = round(base * m / 10) * 10          # до найближчих 10
    rem = v % 100
    if rem <= 40:
        charm = v - rem - 10               # 2020 → 1990
    elif rem >= 60:
        charm = v - rem + 90               # 2070 → 2090
    else:
        charm = v                          # …50 лишаємо (як у реальних цінах)
    if charm < base:                       # ніколи нижче бази
        charm = v if v >= base else int(base)
    return int(charm)


def _cap(s):
    s = str(s or "").strip()
    return s[:1].upper() + s[1:] if s else s


# Прикметник статі в назві — укр і рос.
_GENDER_ADJ_RU = {"чоловіча": "Мужские", "жіноча": "Женские", "дитяча": "Детские", "унісекс": "Унисекс"}


def _val(bms: dict, key: str, lang: str, lower: bool = False) -> str:
    """Значення характеристики цільовою мовою (рос — через словники, укр — як є)."""
    raw = str(bms.get(key) or "").strip()
    if not raw:
        return ""
    if lang == "ru":
        m = {"typename": _TYPE_RU, "colorname": _COLOR_RU, "gendername": _GENDER_RU,
             "season": _SEASON_RU, "conditionname": _COND_RU}.get(key)
        raw = (m or {}).get(raw.lower(), raw) if m else raw
    return raw.lower() if lower else _cap(raw)


def _build_name(bms: dict, lang: str = "uk") -> str:
    """Назва: стать + тип + бренд + модель + колір + «N розмір» (число ПЕРЕД словом).
    lang='uk' (укр) або 'ru' (рос) — тип/колір/стать беруться відповідною мовою."""
    gl = str(bms.get("gendername") or "").strip().lower()
    g = (_GENDER_ADJ_RU if lang == "ru" else _GENDER_ADJ).get(gl)
    typ = _val(bms, "typename", lang, lower=True)
    color = _val(bms, "colorname", lang, lower=True)
    parts = [g, typ, bms.get("brandname"), bms.get("model"), color]
    name = " ".join(str(x).strip() for x in parts if x and str(x).strip())
    sizes = bms.get("sizes") or []
    if len(sizes) == 1:
        name += f" {sizes[0]} {'размер' if lang == 'ru' else 'розмір'}"
    return name[:250] or (bms.get("productnumber") or "Товар")


# Порядок матеріалів у описі — анатомічний (як у картці/каталозі).
_MAT_ORDER = ["upper", "middle", "membrane", "insole", "midsole", "sole"]
_MAT_LABELS = {"upper": "Верх", "middle": "Середина", "membrane": "Мембрана",
               "insole": "Устілка", "midsole": "Проміжна підошва", "sole": "Підошва"}
_MAT_LABELS_RU = {"upper": "Верх", "middle": "Средняя часть", "membrane": "Мембрана",
                  "insole": "Стелька", "midsole": "Промежуточная подошва", "sole": "Подошва"}
# Мітки характеристик і країни укр→рос для рос-опису.
_DESC_LABELS_RU = {"Стан": "Состояние", "Бренд": "Бренд", "Модель": "Модель", "Тип": "Вид",
                   "Колір": "Цвет", "Стать": "Пол", "Сезон": "Сезон", "Виробник": "Производитель",
                   "Доступні розміри": "Доступные размеры"}
_COUNTRY_UA2RU = {"данія": "Дания", "німеччина": "Германия", "сша": "США", "італія": "Италия",
                  "франція": "Франция", "японія": "Япония", "великобританія": "Великобритания",
                  "австрія": "Австрия", "іспанія": "Испания", "польща": "Польша", "канада": "Канада",
                  "норвегія": "Норвегия", "нідерланди": "Нидерланды", "португалія": "Португалия",
                  "туреччина": "Турция", "україна": "Украина", "вʼєтнам": "Вьетнам", "в'єтнам": "Вьетнам",
                  "китай": "Китай", "індонезія": "Индонезия", "індія": "Индия", "камбоджа": "Камбоджа",
                  "бангладеш": "Бангладеш", "румунія": "Румыния", "марокко": "Марокко",
                  "південна корея": "Южная Корея", "південна корея (пд. корея)": "Южная Корея"}


def _country(name: str, lang: str) -> str:
    return _COUNTRY_UA2RU.get(str(name or "").strip().lower(), str(name or "")) if lang == "ru" else str(name or "")


# Override з таблиці brand_countries (редаговане власником джерело). Оновлюється
# при кожному експорті; BRAND_COUNTRY у коді лишається як fallback/сід.
_BRAND_COUNTRY_DB: Dict[str, str] = {}


def _refresh_brand_countries(db: Session) -> None:
    global _BRAND_COUNTRY_DB
    try:
        rows = db.execute(text("SELECT lower(brand), country FROM brand_countries")).fetchall()
        _BRAND_COUNTRY_DB = {b: c for b, c in rows if b and c}
    except Exception as e:            # таблиці ще нема / БД недоступна — лишаємось на словнику
        logger.debug(f"brand_countries недоступна: {e}")


def _brand_country(brand: str) -> Optional[str]:
    b = str(brand or "").strip().lower()
    return _BRAND_COUNTRY_DB.get(b) or BRAND_COUNTRY.get(b)


def _brand_with_country(brand: str, lang: str = "uk") -> str:
    c = _brand_country(brand)
    return f"{brand} ({_country(c, lang)})" if c else str(brand or "")


def _condition_line(bms: dict, lang: str = "uk") -> Optional[str]:
    """Рядок «Стан» у описі. Новий/Хороший → «Нові…» (з пакуванням); Вживаний/
    Легковживаний/Пошкоджений → чесний реальний стан. lang керує мовою."""
    cond = str(bms.get("conditionname") or "").strip().lower()
    pack = str(bms.get("packagingname") or "").strip().lower()
    if cond in _COND_NEWLIKE:
        if "коробк" in pack:
            return "Новые, в коробке" if lang == "ru" else "Нові, в коробці"
        if not pack:
            return "Новые, без коробки" if lang == "ru" else "Нові, без коробки"
        return "Новые" if lang == "ru" else "Нові"
    if cond in _COND_USED:                       # чесно показуємо реальний стан вживаного
        return _COND_DESC_RU.get(cond, "Б/у") if lang == "ru" else _cap(bms.get("conditionname"))
    return None


def _build_description(bms: dict, lang: str = "uk") -> str:
    """HTML-опис (укр або рос): жирні назви характеристик, модель жирним у заголовку,
    рядок пакування, Виробник=Китай пропускаємо, стандартний регістр значень."""
    ru = lang == "ru"
    def L(uk_label):                                  # мітка мовою
        return _DESC_LABELS_RU.get(uk_label, uk_label) if ru else uk_label
    brand = bms.get("brandname"); model = bms.get("model")
    g = (_GENDER_ADJ_RU if ru else _GENDER_ADJ).get(str(bms.get("gendername") or "").strip().lower()) or ""
    typ = _val(bms, "typename", lang, lower=True)
    bm = " ".join(x for x in (brand, model) if x)
    head = " ".join(x for x in (g, typ) if x)
    color = _val(bms, "colorname", lang, lower=True)
    title = f"{head} <b>{_xesc(bm)}</b>" + (f" {_xesc(color)}" if color else "")

    rows = []
    pl = _condition_line(bms, lang)
    if pl:
        rows.append(f"<li><b>{L('Стан')}:</b> {_xesc(pl)}</li>")
    if brand:
        rows.append(f"<li><b>{L('Бренд')}:</b> {_xesc(_brand_with_country(brand, lang))}</li>")
    if model:
        rows.append(f"<li><b>{L('Модель')}:</b> {_xesc(str(model))}</li>")
    for label, key in [("Тип", "typename"), ("Колір", "colorname"), ("Стать", "gendername"),
                       ("Сезон", "season")]:
        v = _val(bms, key, lang)
        if v:
            rows.append(f"<li><b>{L(label)}:</b> {_xesc(v)}</li>")
    # Виробник: Китай — НЕ пишемо; інша країна — пишемо
    manuf = str(bms.get("manufacturer") or "").strip()
    if manuf and manuf.lower() != "китай":
        rows.append(f"<li><b>{L('Виробник')}:</b> {_xesc(_country(manuf, lang))}</li>")
    # Матеріали в анатомічному порядку
    mats = bms.get("materials") or {}
    labels = _MAT_LABELS_RU if ru else _MAT_LABELS
    for pos in _MAT_ORDER:
        if mats.get(pos):
            rows.append(f"<li><b>{labels[pos]}:</b> {_xesc(_norm_material(mats[pos], lang))}</li>")
    sizes = bms.get("sizes") or []
    if sizes:
        rows.append(f"<li><b>{L('Доступні розміри')}:</b> {_xesc(', '.join(map(str, sizes)))}</li>")
    return f"<p>{title}</p><ul>{''.join(rows)}</ul>"


# ── Мапи BMS(укр) → словник шаблону категорії Prom(рос) для <param>.
# КРИТИЧНО: назва <param> має ТОЧНО збігатися з назвою характеристики в шаблоні
# категорії Prom, інакше атрибут не застосується (офіц. вимога імпорту). Назви
# перевірено на РЕАЛЬНИХ заповнених лістингах вітрини: розмір — гендерозалежний
# («Размер женской/мужской/детской обуви»), колір — «Цвет» зі словника, тип — «Вид обуви».
_COLOR_RU = {
    "чорний": "Черный", "білий": "Белый", "сірий": "Серый", "червоний": "Красный",
    "синій": "Синий", "темно-синій": "Темно-синий", "блакитний": "Голубой",
    "зелений": "Зеленый", "салатовий": "Салатовый", "жовтий": "Желтый",
    "помаранчевий": "Оранжевый", "оранжевий": "Оранжевый", "кораловий": "Коралловый",
    "рожевий": "Розовый", "фіолетовий": "Фиолетовый", "бузковий": "Сиреневый",
    "коричневий": "Коричневый", "бежевий": "Бежевый", "світло-бежевий": "Бежевый",
    "золотий": "Золотой", "золотистий": "Золотой", "сріблястий": "Серебряный",
    "срібний": "Серебряный", "бордовий": "Бордовый", "хакі": "Хаки",
    "бірюзовий": "Бирюзовый", "малиновий": "Малиновый", "мʼятний": "Мятный",
    "м'ятний": "Мятный", "різнокольоровий": "Разноцветный", "мультиколор": "Разноцветный",
}
_TYPE_RU = {
    "шльопанці": "Шлепанцы", "босоніжки": "Босоножки", "сандалі": "Сандалии",
    "сандалії": "Сандалии", "кросівки": "Кроссовки", "кеди": "Кеды", "туфлі": "Туфли",
    "черевики": "Ботинки", "ботінки": "Ботинки", "чоботи": "Сапоги", "півчоботи": "Полусапоги",
    "мокасини": "Мокасины", "балетки": "Балетки", "сліпони": "Слипоны", "лофери": "Лоферы",
    "еспадрильї": "Эспадрильи", "угі": "Угги", "уггі": "Угги", "тапочки": "Тапочки",
    "снікерси": "Кроссовки",
}
_GENDER_RU = {"жіноча": "Женская", "чоловіча": "Мужская", "унісекс": "Унисекс", "дитяча": "Детская"}
# Гендерозалежні назви атрибута розміру. УНІСЕКС → обидві шкали (щоб товар був у
# фільтрах і чоловічого, і жіночого взуття — як у ручних лістингах власника).
_GENDER_SIZE_ATTRS = {
    "жіноча": ["Размер женской обуви"], "чоловіча": ["Размер мужской обуви"],
    "унісекс": ["Размер мужской обуви", "Размер женской обуви"],
    "дитяча": ["Размер детской обуви"],
}
_SEASON_RU = {"літо": "Лето", "зима": "Зима", "весна": "Весна", "осінь": "Осень",
              "демісезон": "Демисезон", "весна-осінь": "Демисезон", "всесезон": "Всесезонный"}
# Стан BMS → «Состояние» на Prom (лише 2 значення: Новое / Б/у).
# Політика власника: Новий/Нове/Хороший → «Новое»; Вживаний/Легковживаний/Пошкоджений → «Б/у».
_COND_RU = {"новий": "Новое", "нове": "Новое", "хороший": "Новое",
            "вживаний": "Б/у", "легковживаний": "Б/у", "пошкоджений": "Б/у", "б/у": "Б/у"}
_COND_NEWLIKE = {"новий", "нове", "хороший"}          # у описі → рядок «Нові…»
_COND_USED = {"вживаний", "легковживаний", "пошкоджений", "б/у"}
# Стани, що ПОТРЕБУЮТЬ підтвердження при публікації (усе, крім Новий/Нове).
_COND_WARN = {"хороший", "вживаний", "легковживаний", "пошкоджений"}
# Чесне відображення стану вживаного в описі (укр як є; рос — короткою фразою).
_COND_DESC_RU = {"вживаний": "Б/у", "легковживаний": "Легкое б/у", "пошкоджений": "С дефектом"}
_COUNTRY_RU = {"вʼєтнам": "Вьетнам", "в'єтнам": "Вьетнам", "туреччина": "Турция",
               "італія": "Италия", "португалія": "Португалия", "іспанія": "Испания",
               "польща": "Польша", "україна": "Украина", "індонезія": "Индонезия",
               "індія": "Индия", "камбоджа": "Камбоджа", "бангладеш": "Бангладеш",
               "німеччина": "Германия", "румунія": "Румыния", "марокко": "Марокко"}
_MATERIAL_RU = {
    "шкіра": "Кожа", "натуральна шкіра": "Натуральная кожа", "штучна шкіра": "Искусственная кожа",
    "екошкіра": "Экокожа", "замша": "Замша", "нубук": "Нубук", "текстиль": "Текстиль",
    "тканина": "Текстиль", "сітка": "Сетка", "гума": "Резина", "резина": "Резина",
    "синтетика": "Синтетика",
    "leather": "Кожа", "textile": "Текстиль", "suede": "Замша", "rubber": "Резина",
}
# Матеріали-АБРЕВІАТУРИ — завжди ВЕЛИКИМИ латиницею (як PU/TPU): виняток на всі мови.
_MATERIAL_ABBR = {"eva": "EVA", "ева": "EVA", "эва": "EVA", "pu": "PU", "пу": "PU",
                  "tpu": "TPU", "тпу": "TPU", "tpr": "TPR", "тпр": "TPR", "pvc": "PVC",
                  "пвх": "PVC", "tr": "TR", "abs": "ABS", "pp": "PP"}


def _norm_material(val, lang: str = "uk") -> str:
    """Назва матеріалу цільовою мовою; абревіатури (EVA/PU/TPU…) — завжди ВЕЛИКИМИ латиницею."""
    s = str(val or "").strip()
    if not s:
        return ""
    ab = _MATERIAL_ABBR.get(s.lower())
    if ab:
        return ab
    return _cap(_MATERIAL_RU.get(s.lower(), s)) if lang == "ru" else _cap(s)


def _build_params(bms: dict) -> List[tuple]:
    """(назва, значення) для <param> — атрибути КАТЕГОРІЇ Prom. Назви — рос., точно
    як у шаблоні категорії (перевірено на реальних лістингах), інакше Prom не змапить.
    Ключові для фільтрів: розмір (гендерозалежний) і «Цвет» зі словника."""
    out: List[tuple] = []
    gl = str(bms.get("gendername") or "").strip().lower()
    typ = str(bms.get("typename") or "").strip().lower()
    color = str(bms.get("colorname") or "").strip().lower()

    if typ:                                                      # Вид обуви
        out.append(("Вид обуви", _TYPE_RU.get(typ, _cap(bms.get("typename")))))
    size_attrs = _GENDER_SIZE_ATTRS.get(gl, ["Размер мужской обуви"])  # Розмір (унісекс → обидві шкали)
    for attr in size_attrs:
        for sz in (bms.get("sizes") or []):
            out.append((attr, str(sz)))
    if color:                                                   # Цвет
        out.append(("Цвет", _COLOR_RU.get(color, _cap(bms.get("colorname")))))
    if gl:                                                      # Стать
        out.append(("Стать", _GENDER_RU.get(gl, _cap(bms.get("gendername")))))
    season = str(bms.get("season") or "").strip().lower()
    if season:                                                  # Сезон
        out.append(("Сезон", _SEASON_RU.get(season, _cap(bms.get("season")))))
    cond = str(bms.get("conditionname") or "").strip().lower()
    if cond:                                                    # Состояние
        out.append(("Состояние", _COND_RU.get(cond, _cap(bms.get("conditionname")))))
    if bms.get("brandname"):                                    # Бренд
        out.append(("Производитель (Бренд)", str(bms["brandname"])))
    manuf = str(bms.get("manufacturer") or "").strip()          # Країна (Китай не вказуємо)
    if manuf and manuf.lower() != "китай":
        out.append(("Страна производитель", _COUNTRY_RU.get(manuf.lower(), _cap(manuf))))
    up = (bms.get("materials") or {}).get("upper")              # Матеріал верху
    if up:
        out.append(("Материал верха", _norm_material(up, "ru")))
    return out


# Транслітерація укр→лат для тегів (щоб ловити і латинські запити).
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia",
})


# Синоніми типу для ширшого пошуку: укр-тип → (uk-синоніми, ru-синоніми).
_TYPE_SYN = {
    "шльопанці": (["шльопки", "сланці"], ["шлепки", "сланцы"]),
    "босоніжки": (["сандалі"], ["сандалии"]),
    "кросівки": (["кеди"], ["кеды"]),
    "капці": (["тапочки"], ["тапочки"]),
    "чоботи": (["черевики"], ["сапоги"]),
}


def _build_keywords(bms: dict, lang: str = "uk") -> str:
    """Пошукові теги ЦІЛЬОВОЮ мовою (uk/ru) — багато доречних варіантів: бренд/модель/
    тип/стать/колір/сезон + синоніми + комбінації + транслітерація. RU → рос-поле keywords,
    UA → keywords_ua (мови НЕ змішувати)."""
    ru = lang == "ru"
    brand = str(bms.get("brandname") or "").strip()
    model = str(bms.get("model") or "").strip()
    typ_raw = str(bms.get("typename") or "").strip().lower()
    typ = (_TYPE_RU.get(typ_raw, typ_raw) if ru else typ_raw).lower()
    gl = str(bms.get("gendername") or "").strip().lower()
    gender = (_GENDER_RU.get(gl, gl) if ru else gl).lower()
    color = str(bms.get("colorname") or "").strip().lower()
    if ru:
        color = _COLOR_RU.get(color, color).lower()
    season = str(bms.get("season") or "").strip().lower()
    if ru:
        season = _SEASON_RU.get(season, season).lower()
    buy, orig, shoes = ("купить", "оригинал", "обувь") if ru else ("купити", "оригінал", "взуття")
    men, women = ("мужские", "женские") if ru else ("чоловічі", "жіночі")

    tags, seen = [], set()

    def add(*xs):
        for x in xs:
            x = " ".join(str(x or "").split()).strip().lower()
            if len(x) >= 2 and x not in seen:
                seen.add(x); tags.append(x)

    add(typ, brand, model, color)
    if brand:
        add(f"{brand} {typ}", f"{typ} {brand}", f"{brand} {shoes}", f"{orig} {brand}",
            brand.lower().translate(_TRANSLIT))
        if model:
            add(f"{brand} {model}", f"{brand} {model} {typ}", f"{model} {typ}")
        if color:
            add(f"{brand} {color}")
    if model:
        add(f"{model} {color}", model.lower().translate(_TRANSLIT))
    if typ:
        add(f"{typ} {color}", f"{color} {typ}", f"{gender} {typ}", f"{typ} {gender}",
            f"{buy} {typ}", f"{buy} {brand} {typ}", f"{season} {typ}", f"{typ} {season}",
            typ_raw.translate(_TRANSLIT))
        for s in (_TYPE_SYN.get(typ_raw, ([], []))[1 if ru else 0]):
            add(s, f"{s} {brand}", f"{brand} {s}")
        if gl == "унісекс":                          # унісекс → обидві статі (ширший пошук)
            add(f"{men} {typ}", f"{women} {typ}")
    if season:
        add(f"{season} {shoes}")
    return ", ".join(t for t in tags if t)[:1400]


def _select_images(product_number: str):
    """Публічні R2-URL фото + їхній тип. ТАБУ: якщо є ОФІЦІЙНІ — беремо ЛИШЕ офіційні
    (реальні НЕ домішуємо ніколи); якщо офіційних немає — реальні (не-дефектні).
    Повертає (urls, kind), kind ∈ {'official','real','none'}."""
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
    # Табу на змішування: офіційні → лише офіційні; інакше — реальні (без дефектних).
    chosen = official if official else [i for i in imgs if getattr(i, "kind", "") != "defect"]
    kind = "official" if official else ("real" if chosen else "none")
    urls = []
    for e in chosen[:10]:
        u = getattr(e, "url", "") or ""
        path = u.split("?")[0]
        if "/product-images/" in path:
            key = unquote(path.split("/product-images/", 1)[1])
            pub = r2_storage.public_url(key)
            if pub:
                urls.append(pub)
    return urls, kind


def _product_image_urls(product_number: str) -> List[str]:
    """Лише URL-и (тип див. _select_images). Офіційні або реальні, ніколи не змішані."""
    return _select_images(product_number)[0]


# Група каталогу продавця (categoryId у фіді = ГРУПА, не маркетплейс-категорія;
# маркетплейс-категорію Prom визначає сам). ВАЖЛИВО: фід-імпорт НЕ вміє класти товар
# у вручну-створену групу — він тримає власну фід-групу «Обувь» (усі експорти йдуть у
# неї консистентно). Назва первинна (рос.) «Обувь», щоб не плодити «Взуття».
_PROM_GROUP_SHOES = 154833694     # «Обувь» (ru) / «Взуття» (uk)
_PROM_GROUP_SHOES_NAME = "Обувь"


def _export_rows(db: Session, product_id: int) -> List[dict]:
    """Рядки для експорту: 1 розмір → 1 лістинг (sku=номер); РОСТОВКА (N розмірів) →
    N лістингів (sku=«номер-розмір»), кожен зі своїм єдиним розміром. Поле _sku — sku лістингу."""
    bms = _bms_product_for_export(db, product_id)
    if not bms:
        return []
    num = (bms["productnumber"] or "").lstrip("#")
    sizes = [str(s).strip() for s in (bms.get("sizes") or []) if str(s).strip()]
    if len(sizes) <= 1:
        bms["_sku"] = num
        return [bms]
    rows = []
    for sz in sizes:
        r = dict(bms)
        r["sizes"] = [sz]
        r["_sku"] = f"{num}-{sz}"
        rows.append(r)
    return rows


def _feed_item(bms: dict, sku: str, available: bool, imgs: List[str], group_id: int) -> str:
    """Один <item> фіду для конкретного лістингу (розміру)."""
    price = _prom_price(bms["price"], bms.get("typename"))
    name_ru = _build_name(bms, "ru"); name_ua = _build_name(bms, "uk")
    desc_ru = _build_description(bms, "ru"); desc_ua = _build_description(bms, "uk")
    params = _build_params(bms)
    kw_ru = _build_keywords(bms, "ru"); kw_ua = _build_keywords(bms, "uk")

    def tag(t, v):
        return f"<{t}>{_xesc(str(v))}</{t}>"
    parts = [
        f'<item id={_xqattr(sku)} available="{"true" if available else "false"}">',
        tag("price", price), tag("currencyId", "UAH"), tag("categoryId", group_id),
        tag("name", name_ru), tag("name_ua", name_ua), tag("vendorCode", sku),
        tag("quantity_in_stock", 1),        # залишок: 1 (одиничний товар/розмір)
        tag("presence", "available" if available else "not_available"),
    ]
    if bms.get("brandname"):
        parts.append(tag("vendor", bms["brandname"]))
    if kw_ru:                               # рос-теги → поле «Пошукові запити (Російська)»
        parts.append(tag("keywords", kw_ru))
    if kw_ua:                               # укр-теги → поле «Пошукові запити (Українська)»
        parts.append(tag("keywords_ua", kw_ua))
    parts.append(f"<description><![CDATA[{desc_ru}]]></description>")
    parts.append(f"<description_ua><![CDATA[{desc_ua}]]></description_ua>")
    for u in imgs:
        parts.append(tag("image", u))
    for pn, pv in params:
        parts.append(f'<param name={_xqattr(pn)}>{_xesc(pv)}</param>')
    parts.append("</item>")
    return "".join(parts)


def build_export_feed(db: Session, product_id: int, category_id: int, available: bool = True,
                      rows: Optional[List[dict]] = None) -> str:
    """Prom XML-фід (для import_file). available=False — приховано (draft-режим).
    categoryId = ГРУПА «Взуття». rows — попередньо відібрані рядки (напр. лише нові розміри
    ростовки); якщо None — усі рядки товару. Ростовка → кілька <item> в одному фіді."""
    _refresh_brand_countries(db)          # актуальний довідник країн-власників з БД
    rows = rows if rows is not None else _export_rows(db, product_id)
    if not rows:
        raise RuntimeError("Товар не знайдено")
    imgs = _product_image_urls(rows[0]["productnumber"])
    group_id = _PROM_GROUP_SHOES
    items = "".join(_feed_item(r, r["_sku"], available, imgs, group_id) for r in rows)
    # ОДНА категорія «Обувь» (Prom сам вкладе під корінь). Фід-імпорт НЕ матчить
    # вручну-створені групи — тримає власну; оголошення кореня плодить дубль-корінь,
    # тож НЕ оголошуємо. Усі експорти консистентно йдуть в одну фід-групу «Обувь».
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<price><date>{d}</date>'
        '<currencies><currency id="UAH" rate="1"/></currencies>'
        '<categories><category id="{gid}">{gname}</category></categories>'
        '<items>{items}</items></price>'
    ).format(d=datetime.now().strftime("%Y-%m-%d %H:%M"),
             gid=group_id, gname=_xesc(_PROM_GROUP_SHOES_NAME), items=items)


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
                           preview: bool = False, force: bool = False) -> Dict:
    """Виставити товар BMS на Prom (Фаза 3). preview=True — повертає згенеровані
    назву/опис/категорію/фото БЕЗ створення. Інакше: import_file (mark_missing=none
    — інші товари не чіпає) + (as_draft) переводить у чернетку.
    force=False (типово) — якщо товар з таким sku ВЖЕ на Prom, НЕ створювати дублікат
    (це рівно кейс «товар вже давно на Prom»); force=True — свідомо перезаписати."""
    cfg = _load_config(db)
    if not cfg:
        return {"ok": False, "error": "Prom токен не задано"}
    token = cfg["api_token"]
    _refresh_brand_countries(db)          # актуальний довідник країн-власників з БД
    rows = _export_rows(db, product_id)   # ростовка → кілька рядків (по розміру)
    if not rows:
        return {"ok": False, "error": "Товар не знайдено"}
    base = rows[0]
    number = (base["productnumber"] or "").lstrip("#")
    imgs, image_kind = _select_images(base["productnumber"])
    cat = _prom_category_for(db, token, base)
    skus = [r["_sku"] for r in rows]
    cond_l = str(base.get("conditionname") or "").strip().lower()

    if preview:
        existing = {s: _find_prom_id_by_sku(token, s) for s in skus}
        return {"ok": True, "preview": True, "sku": number, "skus": skus,
                "sizes_count": len(rows), "name": _build_name(base, "uk"),
                "category_id": cat, "images": imgs, "image_count": len(imgs),
                "image_kind": image_kind,                       # official | real | none
                "condition": base.get("conditionname"),
                "condition_prom": _COND_RU.get(cond_l, "Новое"),
                "condition_warn": cond_l in _COND_WARN,          # потребує підтвердження
                "params": _build_params(base),
                "already_on_prom": bool(existing) and all(existing.values())}

    if not imgs:
        return {"ok": False, "error": "У товару немає фото — Prom вимагає зображення. Додай фото й повтори."}
    if not (base.get("price") and float(base["price"]) > 0):
        return {"ok": False, "error": "У товару немає ціни"}

    # Запобіжник дублікатів (по КОЖНОМУ розміру): наявні sku не чіпаємо, лишаємо нові.
    # force=True → перезаписуємо всі. Якщо все вже є і не force → «вже на Prom».
    existing = {s: _find_prom_id_by_sku(token, s) for s in skus}
    if not force:
        rows = [r for r in rows if not existing.get(r["_sku"])]
        if not rows:
            have = len([v for v in existing.values() if v])
            return {"ok": False, "already_on_prom": True, "sku": number,
                    "prom_id": next((v for v in existing.values() if v), None),
                    "error": f"Товар {number} уже на Prom ({have}/{len(skus)} розмірів). "
                             f"Дублікати не створюю. Щоб перезаписати — підтверди примусово."}
    target_skus = [r["_sku"] for r in rows]

    # Draft: створюємо ВІДРАЗУ прихованим (available=false) — без «живого вікна»,
    # поки фоновий крок переведе в статус draft (створення на Prom АСИНХРОННЕ:
    # статус імпорту одразу каже created=0, товар з'являється за 1-3 хв).
    feed = build_export_feed(db, product_id, cat, available=not as_draft, rows=rows)
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

        # НАДІЙНЕ переведення в чернетку через чергу (створення асинхронне): кладемо
        # КОЖЕН sku в prom_draft_queue — Prom-синк-цикл виставить draft, коли зʼявиться.
        # Плюс короткий фоновий «швидкий» прохід, щоб устигнути за ~хвилину.
        if as_draft:
            for s in target_skus:
                _queue_draft(db, s)
            import threading as _th

            def _quick(pending):
                import time as _t
                from models.database import SessionLocal as _SL
                for _ in range(24):          # ~4 хв швидких спроб
                    _t.sleep(10)
                    _db = _SL()
                    try:
                        process_draft_queue(_db)
                        left = {x[0] for x in _db.execute(text("SELECT sku FROM prom_draft_queue")).fetchall()}
                        if not (set(pending) & left):
                            break
                    except Exception:
                        pass
                    finally:
                        _db.close()

            _th.Thread(target=_quick, args=(list(target_skus),), daemon=True).start()

        n = len(target_skus)
        note = (f"Створюється {'ЧЕРНЕТКА' if n == 1 else f'{n} ЧЕРНЕТОК (по розміру)'} на Prom "
                "(з'явиться за 1-3 хв — особливість Prom API). Знайти: Prom → Товари → "
                "фільтр «Видимість: чернетка». Перевір і опублікуй.")
        return {"ok": True, "sku": number, "skus": target_skus, "sizes_count": n,
                "category_id": cat, "images": len(imgs), "import_id": import_id,
                "name": _build_name(base, "uk"), "as_draft": as_draft, "note": note}
    except Exception as e:
        logger.error(f"Prom export failed for {number}: {e}")
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

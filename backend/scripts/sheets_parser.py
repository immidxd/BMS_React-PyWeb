"""
Google Sheets → PostgreSQL parser.

Modes:
  quick  — last QUICK_SHEETS_COUNT sheets per spreadsheet (default 30)
  full   — all sheets

Products source:  Журнал  (sheet "Data" is a summary; batch sheets are date-named)
Orders source:    Замовлення  (sheet "Клієнти" and date sheets)

Skip sheets: Publications, Data, New, Клієнти, Temporary, Лист*, Copy of*, Старі
"""

import logging
import re
import time
from datetime import datetime, date
from typing import Optional, Callable

import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
CREDS_PATH = "/Users/i.malashenko/Desktop/react-fastapi-app/mcp-google-sheets/working_credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
JOURNAL_ID    = "1s5Cz7ZGqhigzysGtAXbIo5tR3ni2zMn1salIaiRc2qA"
ORDERS_ID     = "1rjCN-xBm-maxp0S0o7Lypp8rHmW2qsBirJ7nfN09lqw"
WORKSPACE_ID  = "1q0hUp4oM3hAYciibe5v5h4Uc9Jvhkd8Dl7C8yk2niFA"
WORKSPACE_SHEET = "Воркспейс1"
QUICK_SHEETS_COUNT = 30
# Delay between sheet reads to stay within Google Sheets API quota (60 req/min/user)
SHEET_READ_DELAY_SEC = 1.1  # ~54 req/min, safe margin

SKIP_SHEETS_PATTERNS = re.compile(
    r"^(Publications|Data|New|Клієнти|Temporary|Лист\d*|Copy of|Старі)",
    re.IGNORECASE,
)

# ── GSheets client ────────────────────────────────────────────────────────────
def get_gc() -> gspread.Client:
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def is_skip_sheet(title: str) -> bool:
    return bool(SKIP_SHEETS_PATTERNS.match(title.strip()))


_MEASUREMENT_RE = re.compile(r'\d+[хxХX]\d+')
_NUMERIC_SLASH_RE = re.compile(r'^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$')


def _normalize_size(val: str) -> str:
    """Normalize a size value from Google Sheets.

    - commas → dots  (46,6 → 46.6)
    - measurements like 40x32x14 → '' (not a size)
    - slash → dash for numeric ranges (41/42 → 41-42)
    - 3XL → XXXL, 3/L → L, 4/XL → XL, М(cyrillic) → M
    - M (48/50) → 48-50
    - trailing dots removed (42. → 42)
    - garbage values removed (.12-13, 7340734, 86/92)
    """
    s = val.strip().replace(",", ".")
    if not s:
        return ""
    # Measurements (contain 'x' between digits)
    if _MEASUREMENT_RE.search(s):
        return ""
    # Garbage: leading dot, pure long digits, children ranges
    if s.startswith('.') or (s.isdigit() and len(s) > 4):
        return ""
    # Special text sizes
    upper = s.upper()
    if upper == '3XL':
        return 'XXXL'
    if upper in ('3/L',):
        return 'L'
    if upper in ('4/XL',):
        return 'XL'
    # Cyrillic М → Latin M
    if s == 'М':
        return 'M'
    # M (48/50) → 48-50
    m = re.match(r'^[A-Za-zА-Яа-я]+\s*\((\d+)/(\d+)\)$', s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # Numeric slash → dash (41/42 → 41-42)
    m = _NUMERIC_SLASH_RE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # Trailing dot (42. → 42)
    if s.endswith('.'):
        s = s[:-1]
    return s


_GENDER_MAP = {
    'жіноча': 'Жіноча',
    'жіночий': 'Жіноча',
    'жіноча': 'Жіноча',
    'чоловіча': 'Чоловіча',
    'чоловічий': 'Чоловіча',
    'унісекс': 'Унісекс',
    'дитяча': 'Унісекс',
    'дитячий': 'Унісекс',
}


def _normalize_gender(val: str) -> str:
    """Normalize gender value: case-insensitive mapping to canonical form.

    'жіноча' / 'жіночий' / 'Жіноча' → 'Жіноча'
    'чоловіча' / 'чоловічий' → 'Чоловіча'
    'унісекс' → 'Унісекс'
    Garbage (e.g. 'чорний, сірий...') → '' (empty = skip)
    """
    s = val.strip()
    if not s:
        return ""
    canonical = _GENDER_MAP.get(s.lower())
    if canonical:
        return canonical
    # If not in map, might be garbage — return empty to skip
    return ""


def parse_date_from_sheet_title(title: str) -> Optional[date]:
    """Extract date from sheet title like '24.02.2026' or '24.02.2026(Андрій)'."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", title.strip())
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def parse_supplier_from_sheet_title(title: str) -> Optional[str]:
    """Extract supplier name from sheet title like '24.02.2026(Андрій)' → 'Андрій'."""
    m = re.search(r"\(([^)]+)\)", title.strip())
    if m:
        name = m.group(1).strip()
        return name if name else None
    return None


def _get_or_create_supplier(session: Session, name: str) -> Optional[int]:
    """Get or create a supplier row by company_name.

    Lookup order:
      1. Check suppliers.company_name directly.
      2. Check synonyms_json for alias match.
      3. Create new supplier.
    """
    if not name or not name.strip():
        return None
    from sqlalchemy import text
    n = name.strip()

    # 1. Direct company_name lookup
    row = session.execute(
        text("SELECT id FROM suppliers WHERE company_name = :n"), {"n": n}
    ).fetchone()
    if row:
        return row[0]

    # 2. Synonyms lookup (jsonb array of alias strings)
    row = session.execute(
        text("SELECT id FROM suppliers WHERE synonyms_json IS NOT NULL AND synonyms_json @> CAST(:syn AS jsonb)"),
        {"syn": f'["{n}"]'}
    ).fetchone()
    if row:
        return row[0]

    # 3. Create new supplier
    row = session.execute(
        text("INSERT INTO suppliers (company_name) VALUES (:n) RETURNING id"), {"n": n}
    ).fetchone()
    session.flush()
    return row[0] if row else None


def _get_or_create_shipment(session: Session, sheet_name: str,
                             shipment_date: Optional[date],
                             supplier_id: Optional[int]) -> Optional[int]:
    """Get or create a delivery record for a sheet. Returns delivery ID.

    Dedup by deliveryname — re-parsing the same sheet reuses the delivery.
    """
    if not sheet_name:
        return None
    from sqlalchemy import text
    row = session.execute(
        text("SELECT id FROM deliveries WHERE deliveryname = :sn"),
        {"sn": sheet_name}
    ).fetchone()
    if row:
        # Update supplier_id if it changed (e.g. supplier was merged)
        session.execute(
            text("UPDATE deliveries SET supplier_id = :sid WHERE id = :id"),
            {"sid": supplier_id, "id": row[0]}
        )
        session.flush()
        return row[0]
    row = session.execute(
        text("""INSERT INTO deliveries (deliveryname, deliverydate, supplier_id)
                VALUES (:sn, :sd, :sid) RETURNING id"""),
        {"sn": sheet_name, "sd": shipment_date, "sid": supplier_id}
    ).fetchone()
    session.flush()
    return row[0] if row else None


# ── Reference-table helpers ──────────────────────────────────────────────────
def _auto_classify_color(session: Session, color_id: int, color_name: str):
    """Auto-classify a new color into base color groups (M2M)."""
    try:
        from backend.scripts.color_migration import classify_color
        from sqlalchemy import text
        groups = classify_color(color_name)
        if not groups:
            return
        for gname in groups:
            gid = session.execute(
                text("SELECT id FROM color_groups WHERE name = :n"), {"n": gname}
            ).scalar()
            if gid:
                existing = session.execute(
                    text("SELECT 1 FROM color_group_members WHERE color_id = :cid AND group_id = :gid"),
                    {"cid": color_id, "gid": gid}
                ).fetchone()
                if not existing:
                    session.execute(
                        text("INSERT INTO color_group_members (color_id, group_id) VALUES (:cid, :gid)"),
                        {"cid": color_id, "gid": gid}
                    )
    except Exception:
        pass  # Non-critical — classification can be done later via migration


def _is_garbage_ref_value(value: str) -> bool:
    """Reject values that are clearly prices, sizes, or other numeric garbage.
    Used for Type, Subtype, Condition, Status reference fields.
    """
    import re
    return bool(re.match(r'^[\d\s,.\-₴]+$', value))


def _normalize_ref_name(value: str) -> str:
    """Normalize reference table value: strip, capitalize first letter."""
    value = value.strip()
    if value and value[0].islower():
        value = value[0].upper() + value[1:]
    return value


def _get_or_create(session: Session, model, unique_field: str, value: str):
    """Get or create a reference-table row by unique string field.

    NOTE: DB collation is 'C' so ILIKE/LOWER don't work for Cyrillic.
    We load all rows and compare via Python lower() instead.
    Reference tables are tiny (< 100 rows) so this is safe.
    """
    from backend.models.models import Color, Type, Subtype
    value = value.strip()
    if not value:
        return None
    # Safety truncation to 100 chars — prevents VARCHAR overflow for any reference field
    value = value[:100]

    # Reject numeric garbage for Type (prices/sizes leaked from wrong column)
    if model is Type and _is_garbage_ref_value(value):
        return None

    # Normalize color names before lookup (synonyms, typos, plurals, Latin→Cyrillic)
    if model is Color:
        try:
            from backend.scripts.color_migration import normalize_color_name
            value = normalize_color_name(value)
            if not value:
                return None
        except ImportError:
            pass

    # Capitalize first letter for types
    if model is Type:
        value = _normalize_ref_name(value)

    val_lower = value.lower()
    all_rows = session.query(model).all()
    for row in all_rows:
        db_val = getattr(row, unique_field, None)
        if db_val and db_val.strip().lower() == val_lower:
            return row
    obj = model(**{unique_field: value})
    session.add(obj)
    session.flush()

    # Auto-classify new colors into color groups
    if model is Color:
        _auto_classify_color(session, obj.id, value)

    return obj


# ── Products parser ───────────────────────────────────────────────────────────

def _get_or_create_condition(session: Session, name: str) -> Optional[int]:
    """Get or create a condition row by name.
    Uses Python lower() comparison (DB collation 'C' breaks SQL LOWER for Cyrillic).
    Rejects numeric/price values that indicate a wrong column was parsed.
    """
    import re
    if not name or not name.strip():
        return None
    n = name.strip()
    # Відхиляємо числові значення (ціни, розміри) — вони не є станом товару
    if re.match(r'^[\d\s,.\-₴]+$', n):
        return None
    from sqlalchemy import text
    n_lower = n.lower()
    rows = session.execute(text("SELECT id, conditionname FROM conditions")).fetchall()
    for r in rows:
        if r[1] and r[1].strip().lower() == n_lower:
            return r[0]
    row = session.execute(
        text("INSERT INTO conditions (conditionname) VALUES (:n) RETURNING id"), {"n": n}
    ).fetchone()
    session.flush()
    return row[0] if row else None


def _get_or_create_status(session: Session, name: str) -> Optional[int]:
    """Get or create a status row by name.
    Rejects numeric/price values that indicate a wrong column was parsed.
    """
    import re
    if not name or not name.strip():
        return None
    from sqlalchemy import text
    n = name.strip()
    # Відхиляємо числові значення (ціни, розміри) — вони не є статусом товару
    if re.match(r'^[\d\s,.\-₴]+$', n):
        return None
    # Нормалізація варіантів написання
    _STATUS_ALIASES = {
        "Не продано": "Непродано",
        "не продано": "Непродано",
        "НЕ ПРОДАНО": "Непродано",
    }
    n = _STATUS_ALIASES.get(n, n)
    row = session.execute(
        text("SELECT id FROM statuses WHERE statusname = :n"), {"n": n}
    ).fetchone()
    if row:
        return row[0]
    row = session.execute(
        text("INSERT INTO statuses (statusname) VALUES (:n) RETURNING id"), {"n": n}
    ).fetchone()
    session.flush()
    return row[0] if row else None


def _next_suffix_pnum(session: Session, base_pnum: str) -> str:
    """
    Given a base productnumber (e.g. '#125'), find the next free suffix variant:
    '#125' → '#125-2' → '#125-3' etc.
    Handles chains: if '#125-2' exists, tries '#125-3', and so on.
    """
    from backend.models.models import Product
    # Strip existing suffix to get clean base
    clean = re.sub(r"-\d+$", "", base_pnum)
    # Find all existing productnumbers that start with this base
    existing = session.query(Product.productnumber).filter(
        Product.productnumber.like(f"{clean}%")
    ).all()
    existing_set = {r[0] for r in existing}
    n = 2
    while True:
        candidate = f"{clean}-{n}"
        if candidate not in existing_set:
            return candidate
        n += 1


def _fields_match(a, b) -> bool:
    """Compare two values for duplication check.
    
    If either value is None/empty/0 → treat as 'no data' → match (True).
    Only return False when BOTH values are non-empty AND different.
    This prevents false -2 duplicates when one sheet lacks a column.
    """
    def is_empty(v):
        return v is None or str(v).strip() == "" or v == 0
    if is_empty(a) or is_empty(b):
        return True
    return str(a).strip().lower() == str(b).strip().lower()


def _parse_products_sheet(
    ws: gspread.Worksheet,
    session: Session,
    sheet_date: Optional[date],
    progress_cb: Optional[Callable] = None,
    seen_in_run: Optional[dict] = None,
    supplier_id: Optional[int] = None,
    shipment_id: Optional[int] = None,
) -> dict:
    """
    Parse one batch sheet from Журнал into products table.

    Duplication logic (per sheet AND across sheets / re-parse):
    ──────────────────────────────────────────────────────────
    For each source row with productnumber=X:

    1. Collect all existing DB records with productnumber LIKE 'X%'
       (covers X, X-2, X-3 … from previous parses).

    2. Among those, look for a record where ALL identity fields match:
       (brand, type, condition, color, sizeeu).
       → "Full duplicate":  quantity += 1  (same item, another copy)

    3. If not found but there IS a record with X where only sizeeu differs
       (brand/type/condition/color identical):
       → "Ростовка": create new record with productnumber = X  (same base number,
         different size is treated as a separate product entry).

    4. If there IS a record with X but brand/type/condition/color differ:
       → "Accidental duplicate number": create new record with productnumber = X-2
         (or X-3 … next free suffix).

    5. No existing record at all → plain INSERT, quantity=1.

    Re-parse safety: because we MATCH on identity fields before deciding,
    running the same sheet twice is idempotent — the full-duplicate branch
    will find the record created on the first run and just increment quantity,
    which you then reset by truncating products before a clean full re-parse.
    """
    from backend.models.models import (
        Product, Brand, Type, Color, Gender,
    )

    rows = ws.get_all_values()
    if not rows:
        return {"added": 0, "updated": 0, "skipped": 0}

    header = [h.strip() for h in rows[0]]

    # ID статусу "Продано" — він має пріоритет при кількох входженнях товару.
    # Якщо хоча б один примірник "Продано" — товар проданий.
    sold_status_id = _get_or_create_status(session, "Продано")

    def col(row, name):
        try:
            idx = header.index(name)
            return row[idx].strip() if idx < len(row) else ""
        except ValueError:
            return ""

    from sqlalchemy.exc import IntegrityError

    added = updated = skipped = 0
    total = len(rows) - 1
    # seen_in_run передається ззовні (run_products_parsing) щоб лічити
    # появи продукту між усіма аркушами в одному run.
    if seen_in_run is None:
        seen_in_run = {}
    # Deferred productnumber renames: {product.id: desired_productnumber}
    # Applied after the main loop to safely handle swaps (A↔B).
    pending_renames: dict[int, str] = {}

    for i, row in enumerate(rows[1:], 1):
        if progress_cb and i % 20 == 0:
            progress_cb(i, total)

        pnum = col(row, "Номер").strip()
        if not pnum or pnum == "#":
            skipped += 1
            continue

        clones     = col(row, "Номера-клони")
        type_val   = col(row, "Вид")
        sub_val    = col(row, "Підвид")
        brand_val  = col(row, "Бренд")
        model_val  = col(row, "Модель")
        marking    = col(row, "Маркування")
        year_val   = col(row, "Рік")
        gender_val = _normalize_gender(col(row, "Стать"))
        color_raw  = col(row, "Колір")
        # Якщо в клітинці кілька кольорів через кому — беремо перший
        color_val  = color_raw.split(",")[0].strip() if color_raw else ""
        cond_val   = col(row, "Стан")
        status_val = col(row, "Статус") if "Статус" in header else ""
        mfr_cntry  = col(row, "Країна-виробник")
        own_cntry  = col(row, "Країна-власник")
        size_val   = _normalize_size(col(row, "Розмір"))
        cm_val     = _normalize_size(col(row, "СМ"))
        price_val  = col(row, "Ціна")
        desc_val   = col(row, "Опис") if "Опис" in header else ""

        # ── Resolve FK refs ────────────────────────────────────────────────
        brand_obj  = _get_or_create(session, Brand,  "brandname",  brand_val)  if brand_val  else None
        type_obj   = _get_or_create(session, Type,   "typename",   type_val)   if type_val   else None
        color_obj  = _get_or_create(session, Color,  "colorname",  color_val)  if color_val  else None
        gender_obj = _get_or_create(session, Gender, "gendername", gender_val) if gender_val else None
        mfr_id     = _get_or_create_country(session, mfr_cntry)
        own_id     = _get_or_create_country(session, own_cntry)
        sub_id     = _get_or_create_subtype(session, sub_val, type_obj.id if type_obj else None)
        cond_id    = _get_or_create_condition(session, cond_val)
        status_id  = _get_or_create_status(session, status_val if status_val else "Непродано")

        brand_id  = brand_obj.id if brand_obj else None
        type_id   = type_obj.id  if type_obj  else None
        color_id  = color_obj.id if color_obj else None
        gender_id = gender_obj.id if gender_obj else None

        year_int = None
        if year_val:
            try:
                year_int = int(year_val)
            except ValueError:
                pass

        price_float = 0.0
        if price_val:
            try:
                price_float = float(price_val.replace(",", "."))
            except ValueError:
                pass

        # ── Identity fields for duplicate detection ────────────────────────
        # These five fields together define "is this the same physical item?"
        def id_match(p: "Product") -> bool:
            return (
                _fields_match(p.brandid,     brand_id)
                and _fields_match(p.typeid,  type_id)
                and _fields_match(p.conditionid, cond_id)
                and _fields_match(p.colorid, color_id)
                and _fields_match(p.sizeeu,  size_val)
            )

        def base_match(p: "Product") -> bool:
            """Same brand/type/condition/color but ANY size (ростовка check)."""
            return (
                _fields_match(p.brandid,     brand_id)
                and _fields_match(p.typeid,  type_id)
                and _fields_match(p.conditionid, cond_id)
                and _fields_match(p.colorid, color_id)
            )

        # ── Fetch all existing records whose productnumber starts with pnum base
        base_pnum = re.sub(r"-\d+$", "", pnum)  # strip suffix if any
        existing_all = session.query(Product).filter(
            Product.productnumber.like(f"{base_pnum}%")
        ).all()
        # Narrow to exact-base matches (X, X-2, X-3 …)
        existing_base = [
            p for p in existing_all
            if p.productnumber == base_pnum
            or re.fullmatch(re.escape(base_pnum) + r"-\d+", p.productnumber)
        ]

        # ── Decision logic ─────────────────────────────────────────────────
        full_match = next((p for p in existing_base if id_match(p)), None)

        if full_match:
            # Case 1: exact duplicate — SET quantity = кількість появ у цьому run
            # (НЕ кумулятивний += 1, щоб re-parse не роздував значення)
            cnt = seen_in_run.get(full_match.id, 0) + 1
            seen_in_run[full_match.id] = cnt
            full_match.quantity = cnt
            # Статус: перше входження — ставимо як є.
            # Наступні входження — оновлюємо якщо новий статус "Продано".
            # Це гарантує: якщо хоча б один примірник "Продано" — товар
            # відображається як "Продано".
            if cnt == 1:
                full_match.statusid = status_id
            elif status_id == sold_status_id:
                full_match.statusid = status_id
            # Оновлюємо поля якщо нові дані непорожні
            if marking:
                full_match.marking = marking
            if model_val:
                full_match.model = model_val
            if year_int is not None:
                full_match.year = year_int
            if desc_val:
                full_match.description = desc_val
            if clones:
                full_match.clonednumbers = clones
            if cm_val:
                full_match.measurementscm = cm_val
            # Логіка oldprice: якщо ціна з журналу відрізняється — зберегти стару
            if price_float and full_match.price and price_float != full_match.price:
                full_match.oldprice = full_match.price
                full_match.price = price_float
            elif price_float and not full_match.price:
                full_match.price = price_float
            full_match.updated_at = datetime.utcnow()
            if shipment_id and not full_match.deliveryid:
                full_match.deliveryid = shipment_id
            # ── Productnumber sync: якщо в Google Sheets номер відрізняється
            # від того що в БД — запам'ятати для відкладеного перейменування.
            # Застосовується після основного циклу двофазно (temp → final),
            # щоб безпечно обробляти свопи (A↔B).
            if pnum != full_match.productnumber:
                pending_renames[full_match.id] = pnum
                logger.info(
                    f"[pnum-sync] id={full_match.id}: "
                    f"'{full_match.productnumber}' → '{pnum}' (deferred)"
                )
            updated += 1

        elif not existing_base:
            # ── Global dedup: check for orphaned product with same marking+brand+sizeeu
            # This catches products left with ???_ or __tmp_rename_ numbers after
            # failed renames, preventing duplicate creation.
            orphan = None
            if marking and brand_id:
                orphan = session.query(Product).filter(
                    Product.marking == marking,
                    Product.brandid == brand_id,
                    Product.sizeeu == (size_val or None),
                    Product.productnumber.notlike(f"{base_pnum}%"),
                ).first()
            if orphan:
                # Reclaim orphan: update its productnumber + data
                cnt = seen_in_run.get(orphan.id, 0) + 1
                seen_in_run[orphan.id] = cnt
                orphan.quantity = cnt
                if marking:
                    orphan.marking = marking
                if model_val:
                    orphan.model = model_val
                if desc_val:
                    orphan.description = desc_val
                if price_float:
                    orphan.price = price_float
                if shipment_id and not orphan.deliveryid:
                    orphan.deliveryid = shipment_id
                orphan.updated_at = datetime.utcnow()
                if pnum != orphan.productnumber:
                    pending_renames[orphan.id] = pnum
                    logger.info(
                        f"[dedup-orphan] Reclaimed id={orphan.id} "
                        f"'{orphan.productnumber}' → '{pnum}' (deferred)"
                    )
                updated += 1
            else:
                # Case 5: brand new productnumber
                product = Product(
                    productnumber         = pnum,
                    clonednumbers         = clones or None,
                    model                 = model_val or None,
                    marking               = marking or None,
                    year                  = year_int,
                    description           = desc_val or None,
                    price                 = price_float,
                    sizeeu                = size_val or None,
                    measurementscm        = cm_val or None,
                    dateadded             = sheet_date or date.today(),
                    quantity              = 1,
                    brandid               = brand_id,
                    typeid                = type_id,
                    subtypeid             = sub_id,
                    genderid              = gender_id,
                    colorid               = color_id,
                    conditionid           = cond_id,
                    statusid              = status_id,
                    manufacturercountryid = mfr_id,
                    ownercountryid        = own_id,
                    deliveryid            = shipment_id,
                )
                session.add(product)
                try:
                    session.flush()
                    seen_in_run[product.id] = 1
                    added += 1
                except IntegrityError:
                    session.rollback()
                    # Conflict on uix_products_num_size: record was inserted by a
                    # previous row in this batch (flush not yet visible to query).
                    # Find it now and set quantity via seen_in_run.
                    existing_now = session.query(Product).filter(
                        Product.productnumber == pnum,
                        Product.sizeeu == (size_val or None),
                    ).first()
                    if existing_now:
                        cnt = seen_in_run.get(existing_now.id, 0) + 1
                        seen_in_run[existing_now.id] = cnt
                        existing_now.quantity = cnt
                        existing_now.updated_at = datetime.utcnow()
                        session.flush()
                        updated += 1
                    else:
                        skipped += 1

        else:
            # Check whether any existing record has same base attrs (ростовка candidate)
            base_m = next((p for p in existing_base if base_match(p)), None)

            if base_m:
                # Case 3: ростовка — same brand/type/condition/color, different size
                # → new DB record, same productnumber (e.g. both are #125)
                product = Product(
                    productnumber         = base_pnum,
                    clonednumbers         = clones or None,
                    model                 = model_val or None,
                    marking               = marking or None,
                    year                  = year_int,
                    description           = desc_val or None,
                    price                 = price_float,
                    sizeeu                = size_val or None,
                    measurementscm        = cm_val or None,
                    dateadded             = sheet_date or date.today(),
                    quantity              = 1,
                    brandid               = brand_id,
                    typeid                = type_id,
                    subtypeid             = sub_id,
                    genderid              = gender_id,
                    colorid               = color_id,
                    conditionid           = cond_id,
                    statusid              = status_id,
                    manufacturercountryid = mfr_id,
                    ownercountryid        = own_id,
                    deliveryid            = shipment_id,
                )
                session.add(product)
                try:
                    session.flush()
                    seen_in_run[product.id] = 1
                    added += 1
                except IntegrityError:
                    session.rollback()
                    existing_now = session.query(Product).filter(
                        Product.productnumber == base_pnum,
                        Product.sizeeu == (size_val or None),
                    ).first()
                    if existing_now:
                        cnt = seen_in_run.get(existing_now.id, 0) + 1
                        seen_in_run[existing_now.id] = cnt
                        existing_now.quantity = cnt
                        existing_now.updated_at = datetime.utcnow()
                        session.flush()
                        updated += 1
                    else:
                        skipped += 1
            else:
                # Case 4: genuine duplicate number — both records have non-empty
                # identity fields that differ (e.g. brand=Nike vs brand=Adidas).
                # _fields_match treats NULL as 'no data' (match), so this only
                # triggers for real conflicts → create new record with -2 suffix.
                target_pnum = _next_suffix_pnum(session, base_pnum)
                logger.info(
                    f"[dup-number] Genuine duplicate: {base_pnum} → {target_pnum} "
                    f"(brand={brand_id}, type={type_id}, color={color_id})"
                )
                product = Product(
                    productnumber         = target_pnum,
                    clonednumbers         = clones or None,
                    model                 = model_val or None,
                    marking               = marking or None,
                    year                  = year_int,
                    description           = desc_val or None,
                    price                 = price_float,
                    sizeeu                = size_val or None,
                    measurementscm        = cm_val or None,
                    dateadded             = sheet_date or date.today(),
                    quantity              = 1,
                    brandid               = brand_id,
                    typeid                = type_id,
                    subtypeid             = sub_id,
                    genderid              = gender_id,
                    colorid               = color_id,
                    conditionid           = cond_id,
                    statusid              = status_id,
                    manufacturercountryid = mfr_id,
                    ownercountryid        = own_id,
                    deliveryid            = shipment_id,
                )
                session.add(product)
                try:
                    session.flush()
                    seen_in_run[product.id] = 1
                    added += 1
                except IntegrityError:
                    session.rollback()
                    existing_now = session.query(Product).filter(
                        Product.productnumber == target_pnum,
                        Product.sizeeu == (size_val or None),
                    ).first()
                    if existing_now:
                        cnt = seen_in_run.get(existing_now.id, 0) + 1
                        seen_in_run[existing_now.id] = cnt
                        existing_now.quantity = cnt
                        existing_now.updated_at = datetime.utcnow()
                        session.flush()
                        updated += 1
                    else:
                        skipped += 1

    # ── Apply deferred productnumber renames (two-phase for swap safety) ──
    if pending_renames:
        logger.info(f"[pnum-sync] Applying {len(pending_renames)} deferred renames")
        # Phase 1: rename affected records to temporary unique names,
        # remembering original names so we can revert on conflict.
        original_map: dict[int, str] = {}   # pid → original productnumber
        for pid, desired in pending_renames.items():
            prod = session.query(Product).get(pid)
            if prod is None:
                continue
            original_map[pid] = prod.productnumber
            tmp_name = f"__tmp_rename_{pid}"
            logger.debug(f"[pnum-sync] Phase 1: id={pid} '{prod.productnumber}' → '{tmp_name}'")
            prod.productnumber = tmp_name
        session.flush()

        # Phase 2: rename from temporary to final desired names
        for pid, desired in pending_renames.items():
            prod = session.query(Product).get(pid)
            if prod is None:
                continue
            savepoint = session.begin_nested()
            try:
                prod.productnumber = desired
                session.flush()
                savepoint.commit()
                logger.info(f"[pnum-sync] Phase 2: id={pid} → '{desired}' ✓")
            except IntegrityError:
                savepoint.rollback()
                # Conflict: revert to original productnumber instead of
                # leaving the __tmp_rename_ placeholder.
                orig = original_map.get(pid, f"???_{pid}")
                savepoint2 = session.begin_nested()
                try:
                    prod.productnumber = orig
                    session.flush()
                    savepoint2.commit()
                except IntegrityError:
                    savepoint2.rollback()
                    # Even original conflicts (rare), use safe fallback
                    prod.productnumber = f"???_{pid}"
                    session.flush()
                logger.warning(
                    f"[pnum-sync] Phase 2: id={pid} → '{desired}' CONFLICT, "
                    f"reverted to '{prod.productnumber}'"
                )

    session.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


def _get_or_create_country(session: Session, name: str) -> Optional[int]:
    if not name or not name.strip():
        return None
    from sqlalchemy import text
    name = name.strip()
    name_lower = name.lower()
    rows = session.execute(text("SELECT id, countryname FROM countries")).fetchall()
    for r in rows:
        if r[1] and r[1].strip().lower() == name_lower:
            return r[0]
    row = session.execute(
        text("INSERT INTO countries (countryname) VALUES (:n) RETURNING id"), {"n": name}
    ).fetchone()
    session.flush()
    return row[0] if row else None


def _get_or_create_subtype(session: Session, name: str, type_id: Optional[int]) -> Optional[int]:
    if not name or not name.strip():
        return None
    from sqlalchemy import text
    name = name.strip()
    # Reject numeric garbage (prices/sizes)
    if _is_garbage_ref_value(name):
        return None
    # Capitalize first letter
    name = _normalize_ref_name(name)
    name_lower = name.lower()
    rows = session.execute(text("SELECT id, subtypename FROM subtypes")).fetchall()
    for r in rows:
        if r[1] and r[1].strip().lower() == name_lower:
            return r[0]
    row = session.execute(
        text("INSERT INTO subtypes (subtypename, typeid) VALUES (:n, :t) RETURNING id"),
        {"n": name, "t": type_id}
    ).fetchone()
    session.flush()
    return row[0] if row else None


# ── Clients / deduplication ───────────────────────────────────────────────────
def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _find_or_create_client(session: Session, name: str, phone: str,
                            facebook: str, viber: str, telegram: str,
                            instagram: str, olx: str, email: str) -> int:
    from backend.models.models import Client

    name = name.strip()
    if not name:
        return None

    # Очищення невалідних значень з Google Sheets (#N/A, #REF!, #VALUE! тощо)
    _INVALID = {"#N/A", "#REF!", "#VALUE!", "#ERROR!", "#NAME?", "#NULL!", "#DIV/0!", "#NUM!"}
    def _clean(val: str) -> str:
        v = (val or "").strip()
        return "" if v.upper() in _INVALID or v == "ㅤ" else v
    phone    = _clean(phone)
    facebook = _clean(facebook)
    viber    = _clean(viber)
    telegram = _clean(telegram)
    instagram= _clean(instagram)
    olx      = _clean(olx)
    email    = _clean(email)

    # Якщо в phone_number стоїть URL (Facebook, Telegram тощо) — переносимо в відповідне поле
    if phone and phone.startswith(("http://", "https://")):
        if "facebook.com" in phone and not facebook:
            facebook = phone
        elif "t.me" in phone and not telegram:
            telegram = phone
        elif "instagram.com" in phone and not instagram:
            instagram = phone
        phone = ""  # URL — не телефон

    parts = name.split(maxsplit=1)
    first = parts[0] if parts else name
    last  = parts[1] if len(parts) > 1 else ""

    # Dedup: match by phone (best signal), or by facebook/telegram, or by normalized name
    candidate = None
    if phone:
        candidate = session.query(Client).filter(Client.phone_number == phone).first()
    if not candidate and facebook and "facebook.com" in facebook:
        candidate = session.query(Client).filter(Client.facebook == facebook).first()
    if not candidate and telegram:
        candidate = session.query(Client).filter(Client.telegram == telegram).first()
    if not candidate:
        # Python-side name comparison (collation 'C' breaks ilike for Cyrillic)
        first_lower = first.lower()
        last_lower = last.lower() if last else None
        all_clients = session.query(Client).all()
        for c in all_clients:
            c_first = (c.first_name or '').strip().lower()
            c_last = (c.last_name or '').strip().lower() if c.last_name else None
            if c_first == first_lower and c_last == last_lower:
                candidate = c
                break

    if candidate:
        # Enrich existing client with any new contact data
        if phone   and not candidate.phone_number: candidate.phone_number = phone.strip()
        if facebook and not candidate.facebook:    candidate.facebook     = facebook.strip()
        if viber   and not candidate.viber:        candidate.viber        = viber.strip()
        if telegram and not candidate.telegram:    candidate.telegram     = telegram.strip()
        if instagram and not candidate.instagram:  candidate.instagram    = instagram.strip()
        if olx     and not candidate.olx:          candidate.olx          = olx.strip()
        if email   and not candidate.email:        candidate.email        = email.strip()
        session.flush()
        return candidate.id

    client = Client(
        first_name   = first,
        last_name    = last if last else None,
        phone_number = phone.strip()    if phone    else None,
        facebook     = facebook.strip() if facebook else None,
        viber        = viber.strip()    if viber    else None,
        telegram     = telegram.strip() if telegram else None,
        instagram    = instagram.strip() if instagram else None,
        olx          = olx.strip()      if olx      else None,
        email        = email.strip()    if email    else None,
        created_at   = datetime.utcnow(),
    )
    session.add(client)
    session.flush()
    return client.id


# ── Orders parser ─────────────────────────────────────────────────────────────
_PAYMENT_STATUS_MAP = {
    "ОПЛАЧЕНО":         "Оплачено",
    "ЧАСТКОВО":         "Частково оплачено",
    "ОЧІКУЄ":           "Очікує оплати",
    "НЕ ОПЛАЧЕНО":      "Не оплачено",
    "НЕОПЛАЧЕНО":       "Не оплачено",
}

_DELIVERY_MAP = {
    "НП": "Нова пошта",
    "НОВА ПОШТА": "Нова пошта",
    "УП": "Укрпошта",
    "УКРПОШТА": "Укрпошта",
    "МІСЦЕВИЙ": "Самовивіз",
    "САМОВИВІЗ": "Самовивіз",
    "КУР'ЄР": "Кур'єр",
    "КУР'ЄР": "Кур'єр",
}

_ORDER_STATUS_MAP = {
    "ПІДТВЕРДЖЕННО": "Доставлено",
    "ПІДТВЕРДЖЕНО":  "Доставлено",
    "ВІДПРАВЛЕНО":   "Доставляється",
    "ПОДАРУНОК":     "Доставлено",
    "ВІДМОВА":       "Скасовано",
    "СКАСОВАНО":     "Скасовано",
}


def _resolve_payment_status(session: Session, raw: str) -> Optional[int]:
    from backend.models.models import PaymentStatus
    if not raw:
        return None
    raw_up = raw.strip().upper()
    for key, mapped in _PAYMENT_STATUS_MAP.items():
        if key in raw_up:
            all_ps = session.query(PaymentStatus).all()
            ps = next((p for p in all_ps if p.status_name and p.status_name.strip().lower() == mapped.lower()), None)
            if ps:
                return ps.id
    return None


_INVISIBLE_CHARS = re.compile(r'^[\s\u3164\u115f\u1160\u0020\u00a0\u200b\ufeff]+$')

def _is_blank(s: str) -> bool:
    return not s or bool(_INVISIBLE_CHARS.match(s))

def _resolve_delivery_method(session: Session, raw: str) -> Optional[int]:
    from backend.models.models import DeliveryMethod
    if _is_blank(raw):
        return None
    raw_clean = raw.strip()
    raw_up = raw_clean.upper()
    mapped = _DELIVERY_MAP.get(raw_up)
    if not mapped:
        for key, val in _DELIVERY_MAP.items():
            if key in raw_up:
                mapped = val
                break
    target_name = mapped or raw_clean
    all_dm = session.query(DeliveryMethod).all()
    dm = next((d for d in all_dm if d.method_name and d.method_name.strip().lower() == target_name.lower()), None)
    if dm:
        return dm.id
    from backend.models.models import DeliveryMethod as DM
    dm = DM(method_name=target_name)
    session.add(dm)
    session.flush()
    return dm.id


def _resolve_order_status(session: Session, raw: str) -> Optional[int]:
    from backend.models.models import OrderStatus
    if not raw:
        return None
    raw_up = raw.strip().upper()
    mapped = _ORDER_STATUS_MAP.get(raw_up)
    if mapped:
        all_os = session.query(OrderStatus).all()
        os = next((o for o in all_os if o.status_name and o.status_name.strip().lower() == mapped.lower()), None)
        if os:
            return os.id
    # Fallback: "Нове"
    all_os = session.query(OrderStatus).all()
    os = next((o for o in all_os if o.status_name and o.status_name.strip().lower() == 'нове'), None)
    return os.id if os else None


def _normalize_pnum(pnum: str) -> str:
    """Normalize product number to always have leading # (e.g. 'Ф3432' → '#Ф3432')."""
    p = pnum.strip()
    if p and not p.startswith("#"):
        p = "#" + p
    return p


def _resolve_order_product(session, pnum_clean: str, size_hints: dict):
    """
    Find the correct Product for an order item.

    Logic:
    1. If there is a size hint for this pnum in 'Уточнення' (e.g. "Ф2982 (39)"),
       try to find a product with matching productnumber AND sizeeu.
       Size hint may be a range like "37-38" — try both values.
    2. If no size hint or no match found with size, fall back to:
       a. Single product with this productnumber (not a rostovka) → use it.
       b. Multiple products (rostovka without hint) → use first, log warning.
    3. Try clonednumbers match as last resort.
    """
    from backend.models.models import Product

    # Normalize: ensure leading # for DB lookup
    pnum_with_hash = _normalize_pnum(pnum_clean)
    key_with    = pnum_with_hash.upper()
    key_without = pnum_clean.lstrip("#").upper()

    # ── Step 1: size-hint match ───────────────────────────────────────────────
    hint = size_hints.get(key_with) or size_hints.get(key_without)

    if hint:
        # Collect candidate sizes from hint (handles "37-38", "39", "40.5")
        size_candidates = [s.strip() for s in re.split(r"[-–]", hint) if s.strip()]
        # Also add the full hint string itself (e.g. "37-38" as-is)
        if len(size_candidates) > 1:
            size_candidates.append(hint.strip())

        for sz in size_candidates:
            product = session.query(Product).filter(
                Product.productnumber == pnum_with_hash,
                Product.sizeeu == sz,
            ).first()
            if product:
                return product

    # ── Step 2: fallback — all products with this number (with # prefix) ─────
    candidates = session.query(Product).filter(
        Product.productnumber == pnum_with_hash
    ).all()

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # Rostovka without a usable hint — return first (ambiguous but best effort)
        logger.debug("Ambiguous rostovka match for %s (no size hint), using first", pnum_with_hash)
        return candidates[0]

    # ── Step 3: clonednumbers fallback ───────────────────────────────────────
    return session.query(Product).filter(
        Product.clonednumbers.like(f"%{pnum_with_hash}%")
    ).first()


def _parse_orders_sheet(
    ws: gspread.Worksheet,
    sheet_date: date,
    session: Session,
    progress_cb: Optional[Callable] = None,
    cutoff_date: date = None,
) -> dict:
    from backend.models.models import Order, OrderItem, Product

    if cutoff_date is None:
        cutoff_date = date.min

    rows = ws.get_all_values()
    if not rows:
        return {"orders": 0, "items": 0, "clients": 0, "skipped": 0}

    header = [h.strip() for h in rows[0]]

    def col(row, name, default=""):
        try:
            idx = header.index(name)
            return row[idx].strip() if idx < len(row) else default
        except ValueError:
            return default

    import hashlib
    orders_added = orders_updated = items_added = clients_added = skipped = 0
    total = len(rows) - 1

    for i, row in enumerate(rows[1:], 1):
        if progress_cb and i % 10 == 0:
            progress_cb(i, total)

        product_nums_raw = col(row, "Номера товарів")
        client_name      = col(row, "Клієнт")

        if not product_nums_raw.strip():
            skipped += 1
            continue

        # Для магазинних / walk-in продажів без клієнта —
        # підставляємо placeholder, щоб замовлення не пропускалось.
        if not client_name.strip():
            delivery_hint = col(row, "Доставка").strip().upper()
            if delivery_hint == "МАГАЗИН":
                client_name = "Магазин (walk-in)"
            else:
                client_name = "Невідомий клієнт"

        # Parse product numbers (semicolon-separated)
        product_nums = [p.strip().rstrip(";").strip() for p in product_nums_raw.split(";") if p.strip().rstrip(";").strip()]
        if not product_nums:
            skipped += 1
            continue

        # Parse prices (semicolon-separated, parallel to product_nums)
        prices_raw = col(row, "Ціна")
        prices = []
        for p in prices_raw.split(";"):
            p = p.strip().rstrip(";").strip()
            try:
                prices.append(float(p.replace(",", ".")) if p else 0.0)
            except ValueError:
                prices.append(0.0)

        # Pad prices if fewer than product_nums
        while len(prices) < len(product_nums):
            prices.append(0.0)

        discount_raw = col(row, "Знижка")
        total_raw    = col(row, "Сума")
        try:
            total_amount = float(total_raw.replace(",", ".")) if total_raw else sum(prices)
        except ValueError:
            total_amount = sum(prices)

        # Client
        client_id = _find_or_create_client(
            session,
            name      = client_name,
            phone     = col(row, "Контактний номер"),
            facebook  = col(row, "Facebook"),
            viber     = col(row, "Viber"),
            telegram  = col(row, "Telegram"),
            instagram = col(row, "Instagram"),
            olx       = col(row, "Olx"),
            email     = col(row, "E-mail"),
        )
        if client_id is None:
            skipped += 1
            continue

        delivery_raw = col(row, "Доставка")
        pay_status_id   = _resolve_payment_status(session, col(row, "Статус оплати"))
        delivery_id     = _resolve_delivery_method(session, delivery_raw)
        order_status_id = _resolve_order_status(session, col(row, "Статус відповіді"))
        tracking       = col(row, "Номер накладної")
        address_raw    = col(row, "Адреса доставки")
        recipient      = col(row, "Отримувач")
        notes_raw      = col(row, "Коментарі")
        clarification  = col(row, "Уточнення")

        # ── Extract size hints from 'Уточнення' ───────────────────────────
        # Format examples: "Ф2982 (39);"  "Ф3320 (37-38);"  "TG" (ignored)
        # Build map: normalized_pnum → size_hint
        size_hints: dict = {}  # pnum_clean → size string
        for hint_part in clarification.split(";"):
            hint_part = hint_part.strip()
            # Match: optional # + alphanum + space + (size)
            m = re.match(r"#?([\wА-ЯҐЄІЇа-яґєії]+)\s*\(([^)]+)\)", hint_part)
            if m:
                hint_pnum = "#" + m.group(1).strip().lstrip("#")
                hint_size = m.group(2).strip()
                size_hints[hint_pnum.upper()] = hint_size

        order_date_col = col(row, "Дата замовлення")
        order_date = sheet_date
        if order_date_col:
            try:
                order_date = datetime.strptime(order_date_col, "%d.%m.%Y").date()
            except ValueError:
                pass

        # Пропускаємо carried-over замовлення: якщо order_date <= cutoff_date,
        # це замовлення з попередньої вкладки, воно вже парсилось звідти.
        if order_date <= cutoff_date:
            skipped += 1
            continue

        deferred_raw = col(row, "Відкладено до")
        deferred = None
        if deferred_raw:
            try:
                deferred = datetime.strptime(deferred_raw, "%d.%m.%Y").date()
            except ValueError:
                pass

        priority_raw = col(row, "Пріорітетність")
        try:
            priority = int(priority_raw) if priority_raw else 0
        except ValueError:
            priority = 0

        combined_notes = "; ".join(filter(None, [notes_raw, clarification, recipient]))

        # ── Fingerprint для дедуплікації ──────────────────────────────────
        # Стабільний ключ: client_name + date + sorted product nums
        # (без total_amount — він змінюється між версіями замовлення в різних вкладках)
        norm_pnums = sorted(
            re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", p).upper()
            for p in product_nums if p.strip()
        )
        fp_raw = f"{client_name.strip().lower()}|{order_date.isoformat()}|{'|'.join(norm_pnums)}"
        source_fp = hashlib.md5(fp_raw.encode("utf-8")).hexdigest()

        existing_order = session.query(Order).filter(
            Order.source_fingerprint == source_fp
        ).first()

        # Fallback для старих замовлень без fingerprint
        if not existing_order:
            notes_val = combined_notes if combined_notes else None
            fb_q = session.query(Order).filter(
                Order.client_id == client_id,
                Order.order_date == order_date,
                Order.total_amount == total_amount,
                Order.source_fingerprint.is_(None),
            )
            if notes_val:
                fb_q = fb_q.filter(Order.notes == notes_val)
            else:
                fb_q = fb_q.filter(Order.notes.is_(None))
            existing_order = fb_q.first()
            if existing_order:
                existing_order.source_fingerprint = source_fp

        if existing_order:
            # Оновлюємо існуюче замовлення (статуси, трекінг, тощо)
            existing_order.order_status_id   = order_status_id
            existing_order.payment_status_id = pay_status_id
            existing_order.delivery_method_id= delivery_id
            existing_order.tracking_number   = tracking if tracking else None
            existing_order.deferred_until    = deferred
            existing_order.priority          = priority
            existing_order.notes             = combined_notes if combined_notes else None
            existing_order.updated_at        = datetime.utcnow()
            # Видаляємо старі items і перестворюємо нижче
            session.query(OrderItem).filter(OrderItem.order_id == existing_order.id).delete()
            session.flush()
            order = existing_order
            orders_updated += 1
        else:
            order = Order(
                client_id          = client_id,
                order_date         = order_date,
                order_status_id    = order_status_id,
                total_amount       = total_amount,
                payment_status_id  = pay_status_id,
                delivery_method_id = delivery_id,
                tracking_number    = tracking if tracking else None,
                deferred_until     = deferred,
                priority           = priority,
                notes              = combined_notes if combined_notes else None,
                source_fingerprint = source_fp,
                created_at         = datetime.utcnow(),
            )
            session.add(order)
            session.flush()
            orders_added += 1

        for pnum, price in zip(product_nums, prices):
            # Strip emoji / special chars from product number
            pnum_clean = re.sub(r"[^\w#А-ЯҐЄІЇа-яґєії]", "", pnum).strip()
            if not pnum_clean:
                continue

            product = _resolve_order_product(session, pnum_clean, size_hints)

            if not product:
                logger.debug("Product not found for pnum=%s, skipping order_item", pnum_clean)
                continue

            # Oldprice logic: якщо ціна продажу відрізняється від журнальної
            if price and price > 0 and product.price:
                if price != product.price:
                    product.oldprice = product.price
                    product.price = price
                    product.updated_at = datetime.utcnow()

            item = OrderItem(
                order_id   = order.id,
                product_id = product.id,
                quantity   = 1,
                price      = price,
            )
            session.add(item)
            items_added += 1

            # ── Автооновлення статусу на "Продано" ────────────────────────
            # Якщо продукт залінкований і замовлення оплачене —
            # ставимо "Продано" (id=2), якщо товар ще не має спецстатусу.
            # Спецстатуси (Повернуто=6, Пошкоджений=8) не перезаписуються.
            SOLD_STATUS_ID = 2
            PAID_STATUSES = (pay_status_id,) if pay_status_id in (1, 2) else ()
            SPECIAL_STATUSES = {6, 8}  # Повернуто, Пошкоджений
            if (product
                    and pay_status_id in (1, 2)
                    and product.statusid not in SPECIAL_STATUSES
                    and product.statusid != SOLD_STATUS_ID):
                product.statusid = SOLD_STATUS_ID
                product.updated_at = datetime.utcnow()

        session.commit()

    return {"orders": orders_added, "items": items_added, "clients": clients_added,
            "updated": orders_updated, "skipped": skipped}


# ── Workspace parser ─────────────────────────────────────────────────────────

def _workspace_merge_score(
    p: "Product",
    brand_id, color_id, size_val, marking_val, model_val
) -> int:
    """
    Count how many of the 5 key characteristics match between a DB product
    and a workspace row.  Returns 0-5.
    """
    score = 0
    if _fields_match(p.brandid,  brand_id):  score += 1
    if _fields_match(p.colorid,  color_id):  score += 1
    if _fields_match(p.sizeeu,   size_val):  score += 1
    if _fields_match(p.marking,  marking_val): score += 1
    if _fields_match(p.model,    model_val):  score += 1
    return score


def _append_clone(existing_clones: Optional[str], new_num: str) -> str:
    """Append new_num to a semicolon-separated clonednumbers string."""
    if not new_num or not new_num.strip():
        return existing_clones or ""
    parts = [c.strip() for c in (existing_clones or "").split(";") if c.strip()]
    if new_num.strip() not in parts:
        parts.append(new_num.strip())
    return "; ".join(parts)


def _parse_workspace_sheet(
    ws: gspread.Worksheet,
    session: Session,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Parse the Workspace sheet and merge/add products.

    For each row:
    1. Resolve brand/color/size/marking/model from workspace row.
    2. Search all DB products for a match on ≥4 of these 5 characteristics.
    3. MERGE (best match ≥4): append workspace number to clonednumbers of the
       matched product (if workspace row has a number and it is not already there).
    4. NO MERGE (<4 matches):
       a. Has a number  → insert as new product (productnumber = that number).
       b. No number     → insert as new product with productnumber = '???'
          (will be highlighted red in UI).
    """
    from backend.models.models import (
        Product, Brand, Type, Color, Gender,
    )

    rows = ws.get_all_values()
    if not rows:
        return {"merged": 0, "added": 0, "skipped": 0}

    header = [h.strip() for h in rows[0]]

    def col(row, name):
        try:
            idx = header.index(name)
            return row[idx].strip() if idx < len(row) else ""
        except ValueError:
            return ""

    merged = added = skipped = 0
    touched_product_ids: set = set()
    total = len(rows) - 1

    for i, row in enumerate(rows[1:], 1):
        if progress_cb and i % 20 == 0:
            progress_cb(i, total)

        # Skip truly empty rows
        if not any(c.strip() for c in row):
            skipped += 1
            continue

        pnum       = col(row, "Номер").strip()
        clones_raw = col(row, "Номера-клони")
        type_val   = col(row, "Вид")
        sub_val    = col(row, "Підвид")
        brand_val  = col(row, "Бренд")
        model_val  = col(row, "Модель")
        marking    = col(row, "Маркування")
        year_val   = col(row, "Рік")
        gender_val = _normalize_gender(col(row, "Стать"))
        color_raw  = col(row, "Колір")
        # Якщо в клітинці кілька кольорів через кому — беремо перший
        color_val  = color_raw.split(",")[0].strip() if color_raw else ""
        cond_val   = col(row, "Стан")
        mfr_cntry  = col(row, "Країна-виробник")
        own_cntry  = col(row, "Країна-власник")
        size_val   = _normalize_size(col(row, "Розмір"))
        cm_val     = _normalize_size(col(row, "СМ"))
        price_val  = col(row, "Ціна")
        desc_val   = col(row, "Опис") or col(row, "Екстра примітка")

        # Resolve FK refs
        brand_obj  = _get_or_create(session, Brand,  "brandname",  brand_val)  if brand_val  else None
        type_obj   = _get_or_create(session, Type,   "typename",   type_val)   if type_val   else None
        color_obj  = _get_or_create(session, Color,  "colorname",  color_val)  if color_val  else None
        gender_obj = _get_or_create(session, Gender, "gendername", gender_val) if gender_val else None
        mfr_id     = _get_or_create_country(session, mfr_cntry)
        own_id     = _get_or_create_country(session, own_cntry)
        sub_id     = _get_or_create_subtype(session, sub_val, type_obj.id if type_obj else None)
        cond_id    = _get_or_create_condition(session, cond_val)

        brand_id  = brand_obj.id if brand_obj else None
        type_id   = type_obj.id  if type_obj  else None
        color_id  = color_obj.id if color_obj else None
        gender_id = gender_obj.id if gender_obj else None

        year_int = None
        if year_val:
            try:
                year_int = int(year_val)
            except ValueError:
                pass

        price_float = 0.0
        if price_val:
            try:
                price_float = float(price_val.replace(",", "."))
            except ValueError:
                pass

        # ── At least one meaningful field must exist to bother searching ──
        has_any_attr = any([brand_id, color_id, size_val, marking, model_val])
        if not has_any_attr:
            skipped += 1
            continue

        # ── Search for best match in DB (scan all products with same brand
        #    first for performance, fall back to full scan if no brand) ────
        from backend.models.models import Product
        if brand_id:
            candidates = session.query(Product).filter(
                Product.brandid == brand_id
            ).all()
        else:
            # No brand — must scan all (rare edge case)
            candidates = session.query(Product).all()

        best_product = None
        best_score = 0
        for p in candidates:
            score = _workspace_merge_score(p, brand_id, color_id, size_val, marking, model_val)
            if score > best_score:
                best_score = score
                best_product = p

        if best_score >= 4 and best_product is not None:
            # ── MERGE: append workspace number to clonednumbers ──────────
            if pnum:
                best_product.clonednumbers = _append_clone(best_product.clonednumbers, pnum)
            # Also append any extra clones listed in the workspace row
            if clones_raw:
                for extra in clones_raw.split(";"):
                    best_product.clonednumbers = _append_clone(best_product.clonednumbers, extra.strip())
            best_product.updated_at = datetime.utcnow()
            session.flush()
            merged += 1
            touched_product_ids.add(best_product.id)
            logger.info(
                "[workspace] MERGED pnum=%s → product id=%s (score=%d)",
                pnum or "(none)", best_product.id, best_score
            )
        else:
            # ── NEW PRODUCT ──────────────────────────────────────────────
            # Use workspace number if present; otherwise assign '???'
            target_pnum = pnum if pnum else "???"

            # If pnum already exists in DB with matching identity → skip
            # (idempotency: don't double-insert on re-parse)
            if pnum:
                existing = session.query(Product).filter(
                    Product.productnumber == pnum
                ).first()
                if existing:
                    skipped += 1
                    continue

            from sqlalchemy.exc import IntegrityError as _IE
            product = Product(
                productnumber         = target_pnum,
                clonednumbers         = clones_raw or None,
                model                 = model_val or None,
                marking               = marking or None,
                year                  = year_int,
                description           = desc_val or None,
                price                 = price_float,
                sizeeu                = size_val or None,
                measurementscm        = cm_val or None,
                dateadded             = date.today(),
                quantity              = 1,
                brandid               = brand_id,
                typeid                = type_id,
                subtypeid             = sub_id,
                genderid              = gender_id,
                colorid               = color_id,
                conditionid           = cond_id,
                manufacturercountryid = mfr_id,
                ownercountryid        = own_id,
            )
            session.add(product)
            try:
                session.flush()
                added += 1
                touched_product_ids.add(product.id)
                logger.info(
                    "[workspace] NEW product pnum=%s (score=%d, no match)",
                    target_pnum, best_score
                )
            except _IE:
                session.rollback()
                existing_now = session.query(Product).filter(
                    Product.productnumber == target_pnum,
                    Product.sizeeu == (size_val or None),
                ).first()
                if existing_now:
                    skipped += 1
                    logger.info("[workspace] CONFLICT skipped (already exists) pnum=%s", target_pnum)
                else:
                    skipped += 1
                    logger.warning("[workspace] SKIPPED unresolvable conflict pnum=%s", target_pnum)

    session.commit()
    return {"merged": merged, "added": added, "skipped": skipped, "touched_product_ids": touched_product_ids}


# ── Public API ────────────────────────────────────────────────────────────────
def run_products_parsing(
    session: Session,
    mode: str = "quick",
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Parse Журнал sheets → products table.
    mode: 'quick' = last QUICK_SHEETS_COUNT batch sheets
          'full'  = all batch sheets
    """
    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    all_sheets = sh.worksheets()

    batch_sheets = [ws for ws in all_sheets if not is_skip_sheet(ws.title)]
    if mode == "quick":
        batch_sheets = batch_sheets[:QUICK_SHEETS_COUNT]

    total_added = total_updated = total_skipped = 0
    total_sheets = len(batch_sheets)
    # Спільний лічильник появ product.id між усіма аркушами в цьому run.
    # Передається у _parse_products_sheet щоб quantity = кількість появ
    # по всьому журналу, а не лише в одному аркуші.
    seen_in_run: dict = {}

    shipment_ids = []
    for idx, ws in enumerate(batch_sheets):
        sheet_date = parse_date_from_sheet_title(ws.title)
        supplier_name = parse_supplier_from_sheet_title(ws.title)
        supplier_id = _get_or_create_supplier(session, supplier_name) if supplier_name else None
        shipment_id = _get_or_create_shipment(session, ws.title, sheet_date, supplier_id)
        if shipment_id:
            shipment_ids.append(shipment_id)
        logger.info(f"[products] Parsing sheet {idx+1}/{total_sheets}: {ws.title} (supplier={supplier_name}, shipment={shipment_id})")

        def _cb(done, total, _ws=ws, _idx=idx):
            if progress_cb:
                overall = int((_idx / total_sheets + done / total / total_sheets) * 100)
                progress_cb(overall, f"{_ws.title}: {done}/{total}")

        # Rate-limit: wait before each sheet read
        if idx > 0:
            time.sleep(SHEET_READ_DELAY_SEC)

        result = _parse_products_sheet(ws, session, sheet_date, _cb, seen_in_run, supplier_id, shipment_id)
        total_added   += result["added"]
        total_updated += result["updated"]
        total_skipped += result["skipped"]

    if shipment_ids:
        session.commit()

    return {
        "mode":    mode,
        "sheets":  total_sheets,
        "added":   total_added,
        "updated": total_updated,
        "skipped": total_skipped,
        "seen_product_ids": set(seen_in_run.keys()),
    }


def run_orders_parsing(
    session: Session,
    mode: str = "quick",
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Parse Замовлення sheets → orders + order_items + clients.
    mode: 'quick' = last QUICK_SHEETS_COUNT date sheets
          'full'  = all date sheets
    """
    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)
    all_sheets = sh.worksheets()

    order_sheets = [ws for ws in all_sheets if not is_skip_sheet(ws.title)]
    if mode == "quick":
        order_sheets = order_sheets[:QUICK_SHEETS_COUNT]

    total_orders = total_items = total_updated = total_skipped = 0
    total_sheets = len(order_sheets)

    # Обчислюємо дати кожної вкладки (вкладки йдуть найновіша → найстаріша)
    sheet_dates = [
        parse_date_from_sheet_title(ws.title) or date.today()
        for ws in order_sheets
    ]

    for idx, ws in enumerate(order_sheets):
        sheet_date = sheet_dates[idx]
        # cutoff: дата наступної (старішої) вкладки — замовлення з order_date <= cutoff
        # є carried-over і вже парсились з їх "рідної" вкладки → пропускаємо
        cutoff_date = sheet_dates[idx + 1] if idx + 1 < len(sheet_dates) else date.min
        logger.info(f"[orders] Parsing sheet {idx+1}/{total_sheets}: {ws.title} (cutoff={cutoff_date})")

        def _cb(done, total, _ws=ws, _idx=idx):
            if progress_cb:
                overall = int((_idx / total_sheets + done / total / total_sheets) * 100)
                progress_cb(overall, f"{_ws.title}: {done}/{total}")

        # Rate-limit: wait before each sheet read
        if idx > 0:
            time.sleep(SHEET_READ_DELAY_SEC)

        result = _parse_orders_sheet(ws, sheet_date, session, _cb, cutoff_date)
        total_orders  += result["orders"]
        total_items   += result["items"]
        total_updated += result.get("updated", 0)
        total_skipped += result["skipped"]

    return {
        "mode":    mode,
        "sheets":  total_sheets,
        "orders":  total_orders,
        "items":   total_items,
        "updated": total_updated,
        "skipped": total_skipped,
    }


def run_workspace_parsing(
    session: Session,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Parse Воркспейс1 → merge into products or add as new.
    Always processes the single sheet (no mode/pagination needed).
    """
    gc = get_gc()
    sh = gc.open_by_key(WORKSPACE_ID)
    ws = sh.worksheet(WORKSPACE_SHEET)
    logger.info("[workspace] Parsing sheet '%s'", WORKSPACE_SHEET)

    def _cb(done, total):
        if progress_cb:
            pct = int(done / total * 100) if total else 0
            progress_cb(pct, f"{WORKSPACE_SHEET}: {done}/{total}")

    result = _parse_workspace_sheet(ws, session, _cb)
    return {
        "sheet":  WORKSPACE_SHEET,
        "merged": result["merged"],
        "added":  result["added"],
        "skipped":result["skipped"],
        "touched_product_ids": result["touched_product_ids"],
    }


def run_full_parsing(
    session: Session,
    mode: str = "quick",
    progress_cb: Optional[Callable] = None,
) -> dict:
    """Run products → orders → workspace parsing sequentially."""
    def products_cb(pct, msg):
        if progress_cb:
            progress_cb(pct // 3, f"[Товари] {msg}")

    def orders_cb(pct, msg):
        if progress_cb:
            progress_cb(33 + pct // 3, f"[Замовлення] {msg}")

    def workspace_cb(pct, msg):
        if progress_cb:
            progress_cb(66 + pct // 3, f"[Воркспейс] {msg}")

    products_result  = run_products_parsing(session, mode=mode, progress_cb=products_cb)
    orders_result    = run_orders_parsing(session, mode=mode, progress_cb=orders_cb)
    workspace_result = run_workspace_parsing(session, progress_cb=workspace_cb)

    # ── Mark & Sweep: delete orphan products after full parse ────────────
    from sqlalchemy import text
    sweep_deleted = 0
    if mode == "full":
        keep_ids = products_result.get("seen_product_ids", set()) | workspace_result.get("touched_product_ids", set())
        if keep_ids:
            all_ids = {r[0] for r in session.execute(text("SELECT id FROM products")).fetchall()}
            orphan_ids = all_ids - keep_ids
            if orphan_ids:
                result = session.execute(text("""
                    DELETE FROM products
                    WHERE id = ANY(:ids)
                      AND NOT EXISTS (
                          SELECT 1 FROM order_items oi WHERE oi.product_id = products.id
                      )
                    RETURNING id
                """), {"ids": list(orphan_ids)})
                deleted_ids = [r[0] for r in result.fetchall()]
                sweep_deleted = len(deleted_ids)
                session.commit()
                logger.info(
                    "[sweep] Products: kept %d, orphans %d, deleted %d (skipped %d with orders)",
                    len(keep_ids), len(orphan_ids), sweep_deleted, len(orphan_ids) - sweep_deleted
                )

    # ── Mark & Sweep: delete duplicate orders after full parse ─────────
    orders_sweep_deleted = 0
    if mode == "full":
        r_items = session.execute(text("""
            DELETE FROM order_items WHERE order_id IN (
                SELECT o_old.id FROM orders o_old
                WHERE o_old.source_fingerprint IS NULL
                AND EXISTS (
                    SELECT 1 FROM orders o_new
                    WHERE o_new.source_fingerprint IS NOT NULL
                    AND o_new.client_id = o_old.client_id
                    AND o_new.order_date = o_old.order_date
                    AND o_new.total_amount = o_old.total_amount
                )
            )
        """))
        r_orders = session.execute(text("""
            DELETE FROM orders WHERE id IN (
                SELECT o_old.id FROM orders o_old
                WHERE o_old.source_fingerprint IS NULL
                AND EXISTS (
                    SELECT 1 FROM orders o_new
                    WHERE o_new.source_fingerprint IS NOT NULL
                    AND o_new.client_id = o_old.client_id
                    AND o_new.order_date = o_old.order_date
                    AND o_new.total_amount = o_old.total_amount
                )
            )
        """))
        orders_sweep_deleted = r_orders.rowcount
        r2_items = session.execute(text("""
            DELETE FROM order_items WHERE order_id IN (
                SELECT o.id FROM orders o
                WHERE source_fingerprint IS NULL
                AND o.id NOT IN (
                    SELECT MIN(id) FROM orders
                    WHERE source_fingerprint IS NULL
                    GROUP BY client_id, order_date, total_amount
                )
            )
        """))
        r2_orders = session.execute(text("""
            DELETE FROM orders
            WHERE source_fingerprint IS NULL
            AND id NOT IN (
                SELECT MIN(id) FROM orders
                WHERE source_fingerprint IS NULL
                GROUP BY client_id, order_date, total_amount
            )
        """))
        orders_sweep_deleted += r2_orders.rowcount
        if orders_sweep_deleted > 0:
            session.commit()
            logger.info(
                "[sweep] Orders: deleted %d duplicates (%d overlap + %d no-fp dups)",
                orders_sweep_deleted, r_orders.rowcount, r2_orders.rowcount
            )

    # Strip internal tracking sets before returning
    products_result.pop("seen_product_ids", None)
    workspace_result.pop("touched_product_ids", None)

    return {
        "products":  products_result,
        "orders":    orders_result,
        "workspace": workspace_result,
        "sweep_deleted": sweep_deleted,
        "orders_sweep_deleted": orders_sweep_deleted,
    }

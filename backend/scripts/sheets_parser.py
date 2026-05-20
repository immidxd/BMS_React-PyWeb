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
from sqlalchemy import or_
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
    # Fraction notation: "36 1/3" → "36.3", "36 2/3" → "36.6"
    # Convention: 1/3 → .3 (first third), 2/3 → .6 (second third)
    m_frac = re.match(r'^(\d+(?:\.\d+)?)\s+([12])/3$', s)
    if m_frac:
        base = m_frac.group(1)
        num = m_frac.group(2)
        s = f"{base}.3" if num == '1' else f"{base}.6"
    # Reject garbage: contains spaces between numbers with dot (e.g. "35. 36")
    if re.match(r'^\d+\.\s+\d+', s):
        return ""
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


# ── Season auto-classification ──────────────────────────────────────────────
# Викликається коли в Sheet порожнє поле "Сезон" — підставляє автоматичний.
# Пріоритет (зверху вниз — перший збіг виграє):
#   1) Sport-keywords → Всесезон
#   2) Зима-keywords (description найперший — найбільш explicit)
#   3) Літо-keywords
#   4) Демі-keywords
#   5) Єврозима за видом (Ботинки, Сапоги, Черевики...)
#   6) Fallback → Всесезон
_SPORT_KW = (
    'спорт', 'футзалк', 'футбольн', 'сорокон', 'бутс', 'аквашуз',
    'волейбольн', 'баскетбольн', 'бігов', 'валянк', 'домашн', 'хатн',
)
_WINTER_DESC_KW = (
    'утеплен', 'з утеплення', 'зим', 'мороз', 'тепл', 'ізоля',
    'сніг', 'екстим', 'екстрим', 'пуховик', 'градус', 'мінус',
)
_WINTER_STSUB_KW = ('зимов', 'зима', 'снігоход', 'уггі')
_SUMMER_KW = ('літ', 'пляж')
_SUMMER_TYPE_KW = ('босоніжк', 'шльопанц', 'шльлопанц', 'шльпанц', 'босоніжкиї')
_DEMI_KW = ('дем', 'весн', 'осін', 'вітрівк', 'стограмівк')
_EUROWINTER_TYPE_KW = (
    'ботинк', 'ботінк', 'напівсапог', 'напівботинк', 'сапог',
    'напівчоб', 'черевик', 'напівчеревик', 'ботильйон', 'чобот',
)


def _classify_season(type_val: str, subtype_val: str, style_val: str,
                     description: str) -> str:
    """Auto-classify season when not explicitly set in Sheet."""
    desc = (description or '').lower()
    style = (style_val or '').lower()
    subtype = (subtype_val or '').lower()
    type_l = (type_val or '').lower()
    stsub_style = f'{subtype} {style}'
    all_text = f'{type_l} {subtype} {style} {desc}'

    if any(kw in all_text for kw in _SPORT_KW):
        return 'Всесезон'
    if any(kw in desc for kw in _WINTER_DESC_KW):
        return 'Зима'
    if any(kw in stsub_style or kw in type_l for kw in _WINTER_STSUB_KW):
        return 'Зима'
    if any(kw in desc for kw in _SUMMER_KW):
        return 'Літо'
    if any(kw in stsub_style for kw in _SUMMER_KW):
        return 'Літо'
    if any(kw in type_l for kw in _SUMMER_TYPE_KW):
        return 'Літо'
    if any(kw in desc for kw in _DEMI_KW):
        return 'Демі'
    if any(kw in stsub_style for kw in _DEMI_KW):
        return 'Демі'
    if any(kw in type_l for kw in _EUROWINTER_TYPE_KW):
        return 'Єврозима'
    return 'Всесезон'


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


def _auto_detect_gender(size_val: str, desc_val: str, extra_val: str) -> str:
    """Auto-detect gender from text keywords and shoe size when not set in sheet.

    Priority order:
    1. Keywords in description/extranote: дитяч/підлітк → Унісекс,
       жіноч → Жіноча, чоловіч → Чоловіча
    2. Size > 43 → Чоловіча
    3. Size 40–43 → Унісекс
    4. Size < 40 → Жіноча
    Returns '' if nothing can be determined.
    """
    text_combined = ((desc_val or "") + " " + (extra_val or "")).lower()

    # Text keywords take priority over size-based detection
    if re.search(r'дитяч|підлітк', text_combined):
        return "Унісекс"
    if re.search(r'жіноч', text_combined):
        return "Жіноча"
    if re.search(r'чоловіч', text_combined):
        return "Чоловіча"

    # Size-based detection
    if size_val:
        try:
            size_num = float(size_val.replace(",", "."))
            if size_num > 43:
                return "Чоловіча"
            if 40 <= size_num <= 43:
                return "Унісекс"
            if size_num < 40:
                return "Жіноча"
        except ValueError:
            pass

    return ""


# ── Sales channel detection from order notes/comments ────────────────────────
_SALES_CHANNEL_PATTERNS = [
    # (compiled regex, channel name)
    # Order matters: more specific patterns first
    (re.compile(r'\b(?:viber|вайбер|вайб|vb|вб)\b', re.IGNORECASE), 'Viber'),
    (re.compile(r'\b(?:telegram|телеграм|тг|tg)\b', re.IGNORECASE), 'Telegram'),
    (re.compile(r'\b(?:instagram|інстаграм|інста|inst|ig|інст)\b', re.IGNORECASE), 'Instagram'),
    (re.compile(r'\b(?:tik[\s\-]?tok|тік[\s\-]?ток|тт|tt)\b', re.IGNORECASE), 'TikTok'),
    (re.compile(r'\b(?:olx|олх)\b', re.IGNORECASE), 'OLX'),
    (re.compile(r'\b(?:grail+ed|грейл+ед)\b', re.IGNORECASE), 'Grailed'),
    (re.compile(r'\b(?:shafa|шафа)\b', re.IGNORECASE), 'Shafa'),
]


def _detect_sales_channel(text_combined: str) -> Optional[str]:
    """Detect sales channel from order notes/comments/clarification.

    Scans combined text for messenger/platform keywords.
    Returns channel name or None if not detected (defaults to Ефір).
    """
    if not text_combined:
        return None
    for pattern, channel in _SALES_CHANNEL_PATTERNS:
        if pattern.search(text_combined):
            return channel
    return None


# ── Together-with detection (client_relations auto-link) ─────────────────
# Регекс — лише консервативний шаблон з реальних даних. UA + RU + Latin.
_TOGETHER_RE = re.compile(
    r"разом\s+з(?:і|о)?\s+([A-Za-z\u0400-\u04FF\u00C0-\u017F][A-Za-z\u0400-\u04FF\u00C0-\u017F'\-]+(?:\s+[A-Za-z\u0400-\u04FF\u00C0-\u017F][A-Za-z\u0400-\u04FF\u00C0-\u017F'\-]+)?)",
    re.IGNORECASE,
)


def _link_together_partners(session: Session, order_id: int, client_id: int, notes: str) -> None:
    """Знаходить у нотатках 'разом з <Імʼя [Прізвище]>' та апсертить дзеркальні
    рядки в client_relations + junction client_relation_orders.
    Strict-guard: матчиться лише коли в БД РІВНО 1 клієнт.
    Self-references та повторні апсерти ігноруються (idempotent).
    """
    from sqlalchemy import text as _sql_text  # local — cheap, avoids module-top change risk
    seen: set = set()
    for m in _TOGETHER_RE.finditer(notes or ""):
        raw = m.group(1).strip().rstrip(",.;")
        if not raw or raw.lower() in seen:
            continue
        seen.add(raw.lower())
        rows = session.execute(_sql_text("""
            SELECT id FROM clients
             WHERE (COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')) ILIKE :q
                OR (COALESCE(last_name,'')  || ' ' || COALESCE(first_name,'')) ILIKE :q
             LIMIT 2
        """), {"q": f"%{raw}%"}).fetchall()
        if len(rows) != 1:
            continue
        partner_id = rows[0][0]
        if partner_id == client_id:
            continue
        # Дзеркальний апсерт: A→B і B→A
        for x, y in ((client_id, partner_id), (partner_id, client_id)):
            rid = session.execute(_sql_text("""
                INSERT INTO client_relations (client_id, related_id, relation_type, source, confirmed)
                VALUES (:c, :r, 'together', 'order_import', FALSE)
                ON CONFLICT (client_id, related_id) DO UPDATE SET updated_at = NOW()
                RETURNING id
            """), {"c": x, "r": y}).scalar()
            if rid:
                session.execute(_sql_text("""
                    INSERT INTO client_relation_orders (relation_id, order_id)
                    VALUES (:rid, :oid)
                    ON CONFLICT DO NOTHING
                """), {"rid": rid, "oid": order_id})


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


def _parse_delivery_financials(rows: list) -> dict:
    """Extract purchase_cost and delivery_cost from the 'Інформація про завоз' block.

    The block is located in the right portion of the sheet (not in the main product columns).
    Labels are searched across all cells; value is taken from the cell immediately to the right.

    Returns: {"purchase_cost": float, "delivery_cost": float}
    """
    import re as _re
    result = {"purchase_cost": 0.0, "delivery_cost": 0.0}

    def _to_float(val: str) -> float:
        try:
            cleaned = _re.sub(r"[^\d.,]", "", str(val)).replace(",", ".")
            return float(cleaned) if cleaned else 0.0
        except (ValueError, TypeError):
            return 0.0

    for row in rows:
        for c_idx, cell in enumerate(row):
            cell_s = str(cell).strip().lower()
            # "Сума" — закупівельна вартість (без доставки)
            # Must not match "Сума доставки" (which contains "сума")
            if cell_s == "сума" and c_idx + 1 < len(row):
                val = _to_float(row[c_idx + 1])
                if val > 0:
                    result["purchase_cost"] = val
            # "Сума доставки" — окрема вартість доставки
            elif "сума доставки" in cell_s and c_idx + 1 < len(row):
                val = _to_float(row[c_idx + 1])
                if val > 0:
                    result["delivery_cost"] = val

    return result


def _normalize_supplier_key(name: str) -> str:
    """Normalize supplier name for fuzzy matching.
    Converts to lowercase and removes spaces, underscores, hyphens, dots.
    e.g. 'Go-Stock', 'Go_Stock', 'GoStock', 'GO STOCK' → 'gostock'
    """
    import re as _re
    return _re.sub(r"[\s_\-\.\,]+", "", name.strip().lower())


def _get_or_create_supplier(session: Session, name: str) -> Optional[int]:
    """Get or create a supplier row by company_name.

    Lookup order:
      1. Check supplier_aliases — covers previously merged/deleted names.
      2. Check suppliers.company_name directly (exact, then normalized).
      3. Check synonyms_json for alias match (legacy).
      4. Create new supplier (only if truly new).
    """
    if not name or not name.strip():
        return None
    from sqlalchemy import text
    n = name.strip()
    n_key = _normalize_supplier_key(n)

    # 1. Check supplier_aliases (most important — handles merged/deleted suppliers)
    row = session.execute(
        text("SELECT supplier_id FROM supplier_aliases WHERE alias_name = :n"), {"n": n}
    ).fetchone()
    if row:
        return row[0]

    # 1b. Check aliases by normalized key (handles spacing/casing variants)
    alias_rows = session.execute(
        text("SELECT alias_name, supplier_id FROM supplier_aliases")
    ).fetchall()
    for alias_name, supplier_id in alias_rows:
        if _normalize_supplier_key(alias_name) == n_key:
            # Save exact variant as alias so next lookup is instant
            session.execute(
                text("INSERT INTO supplier_aliases (alias_name, supplier_id) VALUES (:alias, :sid) ON CONFLICT DO NOTHING"),
                {"alias": n, "sid": supplier_id}
            )
            session.flush()
            return supplier_id

    # 2. Direct company_name lookup (exact)
    row = session.execute(
        text("SELECT id FROM suppliers WHERE company_name = :n"), {"n": n}
    ).fetchone()
    if row:
        return row[0]

    # 2b. Normalized company_name lookup (catches GoStock == Go Stock == GO_STOCK)
    all_suppliers = session.execute(
        text("SELECT id, company_name FROM suppliers")
    ).fetchall()
    for sid, company_name in all_suppliers:
        if company_name and _normalize_supplier_key(company_name) == n_key:
            # Save as alias for future instant lookup
            session.execute(
                text("INSERT INTO supplier_aliases (alias_name, supplier_id) VALUES (:alias, :sid) ON CONFLICT DO NOTHING"),
                {"alias": n, "sid": sid}
            )
            session.flush()
            return sid

    # 3. Legacy: synonyms_json lookup
    row = session.execute(
        text("SELECT id FROM suppliers WHERE synonyms_json IS NOT NULL AND synonyms_json @> CAST(:syn AS jsonb)"),
        {"syn": f'["{n}"]'}
    ).fetchone()
    if row:
        return row[0]

    # 4. Truly new supplier — create it
    row = session.execute(
        text("INSERT INTO suppliers (company_name) VALUES (:n) RETURNING id"), {"n": n}
    ).fetchone()
    session.flush()
    return row[0] if row else None


def _get_or_create_shipment(session: Session, sheet_name: str,
                             shipment_date: Optional[date],
                             supplier_id: Optional[int],
                             purchase_cost: float = 0.0,
                             delivery_cost: float = 0.0) -> Optional[int]:
    """Get or create a delivery record for a sheet. Returns delivery ID.

    Dedup by deliveryname — re-parsing the same sheet reuses the delivery.
    purchase_cost and delivery_cost are parsed from the sheet's info block.
    """
    if not sheet_name:
        return None
    from sqlalchemy import text
    row = session.execute(
        text("SELECT id FROM deliveries WHERE deliveryname = :sn"),
        {"sn": sheet_name}
    ).fetchone()
    if row:
        # Update supplier_id and costs on every re-parse (values may have changed in Sheet)
        session.execute(
            text("""UPDATE deliveries
                    SET supplier_id = :sid,
                        purchase_cost = CASE WHEN :pc > 0 THEN :pc ELSE purchase_cost END,
                        delivery_cost = CASE WHEN :dc > 0 THEN :dc ELSE delivery_cost END
                    WHERE id = :id"""),
            {"sid": supplier_id, "pc": purchase_cost, "dc": delivery_cost, "id": row[0]}
        )
        session.flush()
        return row[0]
    row = session.execute(
        text("""INSERT INTO deliveries (deliveryname, deliverydate, supplier_id, purchase_cost, delivery_cost)
                VALUES (:sn, :sd, :sid, :pc, :dc) RETURNING id"""),
        {"sn": sheet_name, "sd": shipment_date, "sid": supplier_id,
         "pc": purchase_cost, "dc": delivery_cost}
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
    Also rejects '?' / '??' / '???' marker values (user uses these for unknown).
    """
    import re
    v = (value or "").strip()
    if not v:
        return True
    # Numeric garbage (price / size / date fragments)
    if re.match(r'^[\d\s,.\-₴]+$', v):
        return True
    # Question-mark markers used by user for 'unknown' (? / ?? / ??? / ????)
    if re.match(r'^[?]+$', v):
        return True
    return False


def _is_garbage_color_value(value: str) -> bool:
    """Return True if value looks like an article number / product code / model name
    rather than a color name.

    All legitimate colors in this database are Ukrainian words (Cyrillic).
    Anything without Cyrillic is treated as garbage (article code, model name, etc.).
    Additional checks for mixed Cyrillic+digit codes like '3109270-9зк'.
    """
    import re
    v = (value or "").strip()
    if not v:
        return True
    # Question-mark placeholders
    if re.match(r'^[?]+$', v):
        return True
    # PRIMARY RULE: no Cyrillic → not a color (catches 'air jordan', 'adilette',
    # article codes like '01468914-3922', 'j036492', '#wearealpenblitz', etc.)
    if not re.search(r'[а-яА-ЯіІїЇєЄёЁ]', v):
        return True
    # Starts with digit even if it has some Cyrillic suffix (e.g. '3109270-9зк')
    if re.match(r'^[0-9]', v):
        return True
    return False


def _is_garbage_brand_value(value: str) -> bool:
    """Return True if value looks like a description fragment, color, or material
    rather than a brand name.

    Real brands are Latin-prefixed (Nike, Tamaris) or properly-capitalized Cyrillic
    proper nouns ("Сказка Kids"). Description leakage from column-shift looks like:
    lowercase Cyrillic, contains commas, or matches material/feature keywords.
    """
    import re
    v = (value or "").strip()
    if not v:
        return True
    # Question-mark placeholders
    if re.match(r'^[?]+$', v):
        return True
    # Comma → almost certainly a description ("замша з камінцями, шнурівка")
    if ',' in v or ';' in v:
        return True
    # Starts with lowercase Cyrillic — real brands never do
    if re.match(r'^[а-яіїєґ]', v):
        return True
    # Prepositional phrases ("на підборах", "з набору", "без застібки")
    if re.match(r'^(на|з|із|зі|без|для|під|над|при)\s', v, re.IGNORECASE):
        return True
    # Material / feature keywords typical of description column leakage
    desc_keywords = (
        'замш', 'шкір', 'шкіра', 'текстил', 'тканин', 'шнурів', 'шнурок',
        'підбор', 'каблук', 'камінц', 'стрази', 'пряжк', 'липуч', 'застібк',
        'набір', 'набору', 'принт', 'квіт', 'смуг', 'смуж', 'лого', 'вставк',
    )
    v_low = v.lower()
    for kw in desc_keywords:
        if kw in v_low:
            return True
    return False


def _looks_like_brand_name(session, value: str) -> bool:
    """Return True if value matches an existing brand (case-insensitive, normalized).
    Used to prevent brand names from being mistakenly stored as subtypes/types
    when columns in the Google Sheet are shifted.
    """
    if not value:
        return False
    try:
        from sqlalchemy import text as sa_text
        from backend.models.models import Brand
    except ImportError:
        from sqlalchemy import text as sa_text
        from models.models import Brand
    val_key = _normalize_brand_key(value)
    if not val_key:
        return False
    rows = session.execute(sa_text("SELECT brandname FROM brands")).fetchall()
    for r in rows:
        if r[0] and _normalize_brand_key(r[0]) == val_key:
            return True
    return False


def _normalize_ref_name(value: str) -> str:
    """Normalize reference table value: strip, capitalize first letter."""
    value = value.strip()
    if value and value[0].islower():
        value = value[0].upper() + value[1:]
    return value


def _split_combined_type(raw: str) -> tuple:
    """Split combined type like 'Кросівки/Кеди' or 'Ботинки-челсі' into (type, subtype).

    Never allows '/' or '-' in types table. Returns (type_part, subtype_part_or_None).
    If raw has no separator, returns (raw, None).
    Examples:
      'Туфлі/кросівки'           → ('Туфлі', 'Кросівки')
      'Шльопанці-босоніжки'      → ('Шльопанці', 'Босоніжки')
      'Напівсапоги/ботинки-челсі'→ ('Напівсапоги', 'Ботинки')
      'Кросівки'                 → ('Кросівки', None)
    """
    if not raw:
        return (raw, None)
    s = raw.strip()
    if '/' not in s and '-' not in s:
        return (s, None)
    parts = re.split(r'[/\-]', s)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return (s, None)

    def _cap(x: str) -> str:
        return x[0].upper() + x[1:].lower() if x else ''

    t = _cap(parts[0])
    st = _cap(parts[1])
    if st.lower() == t.lower():
        st = None
    return (t, st)


def _normalize_brand_key(name: str) -> str:
    """Canonical brand key for dedup: lowercase, no diacritics, no spaces/punct.
    'Go Soft' and 'GoSoft' and 'GO SOFT' all → 'gosoft'.
    """
    try:
        from backend.scripts.brand_utils import normalize_brand
        n = normalize_brand(name)
        return n.replace(" ", "") if n else ""
    except ImportError:
        return re.sub(r"\s+", "", name.strip().lower())


def _get_or_create(session: Session, model, unique_field: str, value: str):
    """Get or create a reference-table row by unique string field.

    NOTE: DB collation is 'C' so ILIKE/LOWER don't work for Cyrillic.
    We load all rows and compare via Python lower() instead.
    Reference tables are tiny (< 100 rows) so this is safe.
    """
    from backend.models.models import Color, Type, Subtype, Brand
    value = value.strip()
    if not value:
        return None
    # Safety truncation to 100 chars — prevents VARCHAR overflow for any reference field
    value = value[:100]

    # Reject numeric garbage for Type/Subtype (prices/sizes leaked from wrong column)
    if model in (Type, Subtype) and _is_garbage_ref_value(value):
        return None

    # Reject brand-matching values from being stored as Type/Subtype (column shift in sheet)
    if model in (Type, Subtype) and _looks_like_brand_name(session, value):
        logger.warning(f"[parser-guard] Rejected {model.__name__} '{value}' — matches existing brand")
        return None

    # Reject article numbers / product codes masquerading as colors (column-shift guard)
    if model is Color and _is_garbage_color_value(value):
        logger.warning(f"[parser-guard] Rejected Color '{value}' — looks like article/code, not a color name")
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

    # ── Brand: check blocklist → aliases → normalized comparison ──
    if model is Brand:
        from sqlalchemy import text as sa_text

        if _is_garbage_brand_value(value):
            logger.warning(f"[parser-guard] Rejected Brand '{value}' — looks like description/material, not a brand name")
            return None

        val_key = _normalize_brand_key(value)
        if not val_key:
            return None

        # Step 0: Check blocklist — deleted/blocked brands must NOT be recreated
        blocked = session.execute(
            sa_text("SELECT 1 FROM brand_blocklist WHERE normalized_name = :nn"),
            {"nn": val_key}
        ).fetchone()
        if blocked:
            return None

        # Step 1: Check brand_aliases — if "Adidas TERREX" was merged into "Adidas",
        #         the alias table remembers this and returns "Adidas" directly.
        alias_row = session.execute(
            sa_text("SELECT brand_id FROM brand_aliases WHERE alias_name = :name"),
            {"name": value}
        ).fetchone()
        if alias_row:
            target_brand = session.query(Brand).get(alias_row[0])
            if target_brand:
                return target_brand

        # Step 2: Also check aliases by normalized key (GoSoft = Go Soft)
        all_aliases = session.execute(
            sa_text("SELECT alias_name, brand_id FROM brand_aliases")
        ).fetchall()
        for alias_name, alias_brand_id in all_aliases:
            if _normalize_brand_key(alias_name) == val_key:
                target_brand = session.query(Brand).get(alias_brand_id)
                if target_brand:
                    return target_brand

        # Step 3: Normal lookup by normalized key
        all_rows = session.query(model).all()
        for row in all_rows:
            db_val = getattr(row, unique_field, None)
            if db_val and _normalize_brand_key(db_val) == val_key:
                return row
        obj = Brand(brandname=value, normalized_name=val_key)
        session.add(obj)
        session.flush()
        return obj

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
    prefetched_rows: Optional[list] = None,
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

    4. If there IS a record with X but identity fields differ:
       a) BRAND matches (or either is NULL) → same product with edited attributes
          → UPDATE the closest existing record (no suffix created).
       b) BRAND genuinely differs → different product, same number
          → create new record with productnumber = X-2 (or X-3 … next free suffix).

    5. No existing record at all → plain INSERT, quantity=1.

    Re-parse safety: because we MATCH on identity fields before deciding,
    running the same sheet twice is idempotent — the full-duplicate branch
    will find the record created on the first run and just increment quantity,
    which you then reset by truncating products before a clean full re-parse.
    """
    from backend.models.models import (
        Product, Brand, Type, Color, Gender, Style,
    )

    rows = prefetched_rows if prefetched_rows is not None else ws.get_all_values()
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

        pnum = col(row, "Номер").strip().rstrip(";").strip()
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
        # Опційні розширені колонки (старі аркуші можуть їх не мати)
        season_val       = col(row, "Сезон").strip() if "Сезон" in header else ""
        style_val        = col(row, "Стиль").strip() if "Стиль" in header else ""
        current_cond_val = col(row, "Поточний стан").strip() if "Поточний стан" in header else ""

        # ── Resolve FK refs ────────────────────────────────────────────────
        # Guard: split combined types ("Туфлі/кросівки", "Ботинки-челсі") → Type + Subtype
        if type_val:
            t_part, st_part = _split_combined_type(type_val)
            type_val = t_part
            if st_part and not sub_val:
                sub_val = st_part

        # Auto-classify season if not explicitly set in Sheet
        if not season_val:
            season_val = _classify_season(type_val, sub_val, style_val, desc_val)

        brand_obj  = _get_or_create(session, Brand,  "brandname",  brand_val)  if brand_val  else None
        type_obj   = _get_or_create(session, Type,   "typename",   type_val)   if type_val   else None
        color_obj  = _get_or_create(session, Color,  "colorname",  color_val)  if color_val  else None
        gender_obj = _get_or_create(session, Gender, "gendername", gender_val) if gender_val else None
        mfr_id     = _get_or_create_country(session, mfr_cntry)
        own_id     = _get_or_create_country(session, own_cntry)
        sub_id     = _get_or_create_subtype(session, sub_val, type_obj.id if type_obj else None)
        cond_id    = _get_or_create_condition(session, cond_val)
        # "Поточний стан": якщо порожнє в Sheet → успадковує "Стан"; інакше — окреме значення
        current_cond_id = (
            _get_or_create_condition(session, current_cond_val)
            if current_cond_val
            else cond_id
        )
        style_obj  = _get_or_create(session, Style, "stylename", style_val) if style_val else None
        style_id   = style_obj.id if style_obj else None
        status_id  = _get_or_create_status(session, status_val if status_val else "Непродано")

        brand_id  = brand_obj.id if brand_obj else None
        type_id   = type_obj.id  if type_obj  else None
        color_id  = color_obj.id if color_obj else None
        gender_id = gender_obj.id if gender_obj else None

        # Auto-detect gender if not specified in sheet
        if not gender_id:
            extra_val = col(row, "Екстра примітка") if "Екстра примітка" in header else ""
            auto_gender = _auto_detect_gender(size_val, desc_val, extra_val)
            if auto_gender:
                auto_gender_obj = _get_or_create(session, Gender, "gendername", auto_gender)
                gender_id = auto_gender_obj.id if auto_gender_obj else None

        # Type-based gender fallback: specific item types have known default gender
        # Applied only if gender is still not determined after text/size detection
        if not gender_id and type_val:
            type_lower = type_val.strip().lower()
            type_gender_defaults = {
                # Унісекс types
                "валіза": "Унісекс",
                "рюкзак": "Унісекс",
                # Жіноча types
                "сумка": "Жіноча",
            }
            for type_keyword, default_gender in type_gender_defaults.items():
                if type_keyword in type_lower:
                    type_gender_obj = _get_or_create(session, Gender, "gendername", default_gender)
                    gender_id = type_gender_obj.id if type_gender_obj else None
                    break

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
        # Normalize: search both with and without '#' prefix to avoid duplicates
        # (old records may have "Ф1067", new sheet data has "#Ф1067")
        base_no_hash = base_pnum.lstrip("#")
        base_with_hash = "#" + base_no_hash
        existing_all = session.query(Product).filter(
            or_(
                Product.productnumber.like(f"{base_with_hash}%"),
                Product.productnumber.like(f"{base_no_hash}%"),
            )
        ).all()
        # Narrow to exact-base matches (X, X-2, X-3, #X, #X-2 …)
        def _is_base_match(p_num: str) -> bool:
            """Check if productnumber matches base (with or without #)."""
            p_stripped = p_num.lstrip("#")
            return (
                p_stripped == base_no_hash
                or bool(re.fullmatch(re.escape(base_no_hash) + r"\s*-\s*\d+", p_stripped))
            )
        existing_base = [p for p in existing_all if _is_base_match(p.productnumber)]

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
            # ── Fill NULL identity fields from re-parse data ──────────
            # If a product was created with NULL brand/type/color/sizeEU
            # (e.g. sheet was partially filled at parse time), subsequent
            # parses should populate these fields.
            if brand_id and not full_match.brandid:
                full_match.brandid = brand_id
            if type_id and not full_match.typeid:
                full_match.typeid = type_id
            if color_id and not full_match.colorid:
                full_match.colorid = color_id
            if gender_id and not full_match.genderid:
                full_match.genderid = gender_id
            if size_val and not full_match.sizeeu:
                full_match.sizeeu = size_val
            if sub_id and not full_match.subtypeid:
                full_match.subtypeid = sub_id
            if cond_id and not full_match.conditionid:
                full_match.conditionid = cond_id
            # "Поточний стан":
            #   - якщо явно вказано у Sheet → завжди оновлюємо
            #   - якщо порожній у Sheet, але в БД ще NULL → успадковуємо "Стан"
            if current_cond_val and current_cond_id:
                full_match.current_conditionid = current_cond_id
            elif not full_match.current_conditionid and cond_id:
                full_match.current_conditionid = cond_id
            if season_val and not full_match.season:
                full_match.season = season_val
            if style_id and not full_match.styleid:
                full_match.styleid = style_id
            if mfr_id and not full_match.manufacturercountryid:
                full_match.manufacturercountryid = mfr_id
            if own_id and not full_match.ownercountryid:
                full_match.ownercountryid = own_id
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
                    current_conditionid   = current_cond_id,
                    season                = season_val or None,
                    styleid               = style_id,
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
                    # Conflict on uix_products_num_size_color: record was inserted by a
                    # previous row in this batch (flush not yet visible to query).
                    # Find it now and set quantity via seen_in_run.
                    existing_now = session.query(Product).filter(
                        Product.productnumber == pnum,
                        Product.sizeeu == (size_val or None),
                        Product.colorid == color_id,
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
                    current_conditionid   = current_cond_id,
                    season                = season_val or None,
                    styleid               = style_id,
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
                        Product.colorid == color_id,
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
                # Case 4: identity fields differ from all existing records.
                # ──────────────────────────────────────────────────────────
                # OLD behaviour: always create -2 suffix.  This caused
                # phantom duplicates when user edits color/condition/type
                # in the journal (same product, slightly different description).
                #
                # NEW behaviour: check if BRAND matches any existing record.
                #   • Brand MATCHES → same product, updated attributes → UPDATE.
                #   • Brand DIFFERS → genuinely different product    → suffix -2.
                #
                # Rationale: in shoe business, same brand + same article number
                # always = same physical product.  Color/condition/type names
                # change due to edits, multi-sheet descriptions, or refinement
                # (e.g. "Ботинки" → "Ботинки-уггі", "ліловий" → "бузковий").

                # Find records with compatible brand.
                # Compare by NORMALIZED brand name (not just ID) so that
                # "GoSoft" matches "Go Soft", "ECCO" matches "Ecco", etc.
                def _brand_compatible(p) -> bool:
                    if _fields_match(p.brandid, brand_id):
                        return True  # same ID or either is NULL
                    # Both have brand IDs but they differ — check normalized names
                    if p.brandid and brand_id:
                        from backend.models.models import Brand
                        pb = session.get(Brand, p.brandid)
                        nb = session.get(Brand, brand_id)
                        if pb and nb:
                            k1 = _normalize_brand_key(pb.brandname)
                            k2 = _normalize_brand_key(nb.brandname)
                            if k1 and k2 and k1 == k2:
                                return True
                    return False

                brand_compat = [
                    p for p in existing_base
                    if _brand_compatible(p)
                ]

                if brand_compat:
                    # ── Same brand → UPDATE the closest existing record ──
                    # Exception: if a record has the SAME size but an explicitly
                    # DIFFERENT color (both non-NULL), it's a color variant —
                    # a genuinely separate product, not an attribute edit.
                    def _is_color_variant(p) -> bool:
                        size_same = _fields_match(p.sizeeu, size_val)
                        color_genuinely_differs = (
                            p.colorid is not None
                            and color_id is not None
                            and p.colorid != color_id
                        )
                        if not (size_same and color_genuinely_differs):
                            return False
                        # Marking analysis (article number = physical product identifier):
                        #   • DB has marking AND Sheet has marking AND they differ
                        #       → genuinely different products (real color variant).
                        #   • Either marking is NULL/empty
                        #       → cannot prove they're different → assume same product
                        #       (Sheet is source of truth, color was corrected/refined).
                        #     Without this rule, parsing a re-edited sheet creates
                        #     duplicate rows like '#Ф4003' and '#Ф4003-3' when the old
                        #     DB row lacked marking (legacy data).
                        db_mark = (p.marking or "").strip().upper()
                        sheet_mark = (marking or "").strip().upper()
                        if not db_mark or not sheet_mark:
                            return False  # treat as same product → update existing
                        if db_mark == sheet_mark:
                            return False  # same article = same product
                        return True  # both markings present and differ → real variant

                    # Exclude color variants from update candidates
                    brand_compat_updatable = [p for p in brand_compat if not _is_color_variant(p)]

                    # If all brand_compat records are color variants → create new record
                    if not brand_compat_updatable:
                        target_pnum = _next_suffix_pnum(session, base_pnum)
                        logger.info(
                            f"[color-variant] Same brand+size, different color → new record: "
                            f"{base_pnum} → {target_pnum} (color={color_id})"
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
                            current_conditionid   = current_cond_id,
                            season                = season_val or None,
                            styleid               = style_id,
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
                                Product.colorid == color_id,
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
                        # Skip the update block below
                        brand_compat = []  # signal that we're done for this row

                if brand_compat and brand_compat_updatable:
                    # ── Same brand → UPDATE the closest existing record ──
                    # Pick record with fewest genuine field conflicts.
                    def _conflict_score(p):
                        score = 0
                        if not _fields_match(p.typeid,      type_id):  score += 1
                        if not _fields_match(p.conditionid, cond_id):  score += 1
                        if not _fields_match(p.colorid,     color_id): score += 1
                        if not _fields_match(p.sizeeu,      size_val): score += 2  # size is heavier
                        return score

                    target = min(brand_compat_updatable, key=_conflict_score)
                    cnt = seen_in_run.get(target.id, 0) + 1
                    seen_in_run[target.id] = cnt
                    target.quantity = cnt
                    # Update identity fields to latest non-empty values
                    if brand_id:  target.brandid     = brand_id
                    if type_id:   target.typeid      = type_id
                    if cond_id:   target.conditionid = cond_id
                    if color_id:  target.colorid     = color_id
                    if size_val:  target.sizeeu      = size_val
                    if sub_id:    target.subtypeid   = sub_id
                    if gender_id: target.genderid    = gender_id
                    # Update data fields
                    if marking:   target.marking     = marking
                    if model_val: target.model       = model_val
                    if desc_val:  target.description = desc_val
                    if cm_val:    target.measurementscm = cm_val
                    if year_int is not None: target.year = year_int
                    if price_float and target.price and price_float != target.price:
                        target.oldprice = target.price
                        target.price    = price_float
                    elif price_float and not target.price:
                        target.price = price_float
                    if cnt == 1:
                        target.statusid = status_id
                    elif status_id == sold_status_id:
                        target.statusid = status_id
                    target.updated_at = datetime.utcnow()
                    if shipment_id and not target.deliveryid:
                        target.deliveryid = shipment_id
                    if pnum != target.productnumber:
                        pending_renames[target.id] = pnum
                    logger.info(
                        f"[dup-update] Same brand, updated instead of suffix: "
                        f"{base_pnum} (id={target.id}, conflicts={_conflict_score(target)})"
                    )
                    updated += 1
                else:
                    # ── Brand genuinely differs → create -2 suffix ───────
                    target_pnum = _next_suffix_pnum(session, base_pnum)
                    logger.info(
                        f"[dup-number] Genuine duplicate (brand differs): "
                        f"{base_pnum} → {target_pnum} "
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
                        current_conditionid   = current_cond_id,
                        season                = season_val or None,
                        styleid               = style_id,
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
                            Product.colorid == color_id,
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
    # Reject numeric garbage, empty, and '?' markers
    if _is_garbage_ref_value(name):
        return None
    # Guard: if value itself is a combined "A/B" or "A-B" form, take the second part
    if '/' in name or '-' in name:
        _, st_part = _split_combined_type(name)
        if st_part:
            name = st_part
    # Reject values that match a known brand name (column shift in sheet)
    if _looks_like_brand_name(session, name):
        logger.warning(f"[parser-guard] Rejected subtype '{name}' — matches existing brand")
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


def _norm_key(first: str, last: str, nickname: str) -> str:
    """Канонічний ключ для UNIQUE alias (lower(first)|lower(last)|lower(nick))."""
    return "|".join([
        (first or "").strip().lower(),
        (last or "").strip().lower(),
        (nickname or "").strip().lower(),
    ])


def _register_alias(session: Session, client_id: int,
                    first: str, last: str, nickname: str,
                    full_raw: str, source: str = "parser") -> None:
    """Idempotent upsert у client_aliases. Інкрементує seen_count + last_seen_at.
    Гарантує що навіть після ручного редагування імені оригінал лишається в історії
    і парсер знайде кандидата по будь-якому з варіантів.
    """
    if not client_id:
        return
    key = _norm_key(first or "", last or "", nickname or "")
    # Pure-empty key (no name at all) — пропускаємо
    if key == "||":
        return
    session.execute(text("""
        INSERT INTO client_aliases
            (client_id, first_name, last_name, nickname, full_raw,
             norm_key, source, seen_count, first_seen_at, last_seen_at)
        VALUES
            (:cid, :f, :l, :n, :raw, :k, :src, 1, NOW(), NOW())
        ON CONFLICT (client_id, norm_key) DO UPDATE
            SET seen_count = client_aliases.seen_count + 1,
                last_seen_at = NOW(),
                full_raw = COALESCE(EXCLUDED.full_raw, client_aliases.full_raw)
    """), {
        "cid": client_id,
        "f": (first or "").strip() or None,
        "l": (last or "").strip() or None,
        "n": (nickname or "").strip() or None,
        "raw": (full_raw or "").strip() or None,
        "k": key,
        "src": source,
    })


def _add_client_flag(session: Session, client_id: int, flag_type: str,
                     peer_ids=None, details: str = "", severity: str = "warn") -> None:
    """Створити flag, якщо ще немає активного такого ж типу. UNIQUE-індекс гарантує idempotency."""
    if not client_id:
        return
    try:
        peers = list(peer_ids) if peer_ids else None
        session.execute(text("""
            INSERT INTO client_flags
                (client_id, flag_type, severity, peer_client_ids, details, dismissed, created_at)
            VALUES (:cid, :ft, :sv, :peers, :det, FALSE, NOW())
            ON CONFLICT DO NOTHING
        """), {
            "cid": client_id, "ft": flag_type, "sv": severity,
            "peers": peers, "det": details or None,
        })
    except Exception as _e:  # noqa: BLE001
        logger.warning("could not insert client_flag (%s) for %s: %s", flag_type, client_id, _e)


def _locked_fields(candidate) -> set:
    """Поля що НЕ мають перезатиратися парсером (юзер їх відредагував)."""
    raw = (getattr(candidate, "manually_edited_fields", None) or "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _find_or_create_client(session: Session, name: str, phone: str,
                            facebook: str, viber: str, telegram: str,
                            instagram: str, olx: str, email: str) -> int:
    """Identity-aware дедуплікація (Step 4):
      1. Strong signals: phone / facebook / telegram / instagram → exact match
      2. Alias-based name lookup (client_aliases.norm_key) — пам'ятає історичні варіанти
      3. Якщо знайдено 2+ кандидатів за іменем → STRICT GUARD: створюємо нового +
         flag 'ambiguous_name_at_parse' на новому з peer_client_ids
      4. Якщо знайдений кандидат має конфлікт сильного сигналу (різний phone/fb/tg) →
         не мерджимо, створюємо нового + flag 'phone_mismatch_with_alias' на обох
      5. Enrichment поважає manually_edited_fields — не перезатирає locked поля
      6. Завжди реєструємо/оновлюємо alias для нового рядка → ім'я ніколи не "губиться"
    """
    from backend.models.models import Client
    from backend.utils.name_parser import parse_client_name

    raw_name = (name or "").strip()
    if not raw_name:
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

    # Якщо в phone_number стоїть URL — переносимо в відповідне поле
    if phone and phone.startswith(("http://", "https://")):
        if "facebook.com" in phone and not facebook:
            facebook = phone
        elif "t.me" in phone and not telegram:
            telegram = phone
        elif "instagram.com" in phone and not instagram:
            instagram = phone
        phone = ""

    parsed = parse_client_name(raw_name)
    first = parsed.first_name or ""
    last  = parsed.last_name or ""
    nickname = parsed.nickname
    gender_id = parsed.gender_id if parsed.gender_id else None

    # ── Stage 1: strong signal lookup ────────────────────────────────────
    candidate = None
    strong_signal_matched = None  # 'phone' | 'facebook' | 'telegram' | 'instagram'
    if phone:
        candidate = session.query(Client).filter(Client.phone_number == phone).first()
        if candidate:
            strong_signal_matched = "phone"
    if not candidate and facebook and "facebook.com" in facebook:
        candidate = session.query(Client).filter(Client.facebook == facebook).first()
        if candidate:
            strong_signal_matched = "facebook"
    if not candidate and telegram:
        candidate = session.query(Client).filter(Client.telegram == telegram).first()
        if candidate:
            strong_signal_matched = "telegram"
    if not candidate and instagram and "instagram.com" in instagram:
        candidate = session.query(Client).filter(Client.instagram == instagram).first()
        if candidate:
            strong_signal_matched = "instagram"

    # ── Stage 2: alias-based name lookup (тільки якщо нема strong match) ─
    ambiguous_peer_ids = []
    if not candidate and (first or last or nickname):
        key = _norm_key(first, last, nickname)
        # Спочатку точне співпадіння full key
        rows = session.execute(text("""
            SELECT DISTINCT client_id FROM client_aliases
             WHERE norm_key = :k
             LIMIT 5
        """), {"k": key}).fetchall()

        # Fallback: nickname-only match (якщо повний key не знайшов)
        if not rows and nickname:
            nick_only_key = _norm_key("", "", nickname)
            rows = session.execute(text("""
                SELECT DISTINCT client_id FROM client_aliases
                 WHERE norm_key = :k OR norm_key LIKE :wild
                 LIMIT 5
            """), {"k": nick_only_key, "wild": f"%|{nickname.lower()}"}).fetchall()

        # Fallback: name-only without nickname
        if not rows and (first or last):
            name_only_key = _norm_key(first, last, "")
            rows = session.execute(text("""
                SELECT DISTINCT client_id FROM client_aliases
                 WHERE norm_key = :k OR norm_key LIKE :wild
                 LIMIT 5
            """), {"k": name_only_key, "wild": f"{first.lower()}|{(last or '').lower()}|%"}).fetchall()

        cids = [r[0] for r in rows]
        if len(cids) == 1:
            candidate = session.query(Client).filter(Client.id == cids[0]).first()
        elif len(cids) > 1:
            # AMBIGUITY: ОБИРАЄМО НАЙКРАЩОГО кандидата замість створення клона.
            # Раніше тут було `ambiguous_peer_ids = cids` → парсер створював нового
            # → chain reaction (1 → 2 → 4 → … → 44 «Мар'яна Сливка» за день).
            # Ranking: hasStrongSignal DESC, ordersCount DESC, manuallyEdited DESC, id ASC.
            ranked = session.execute(text("""
                SELECT c.id,
                       (CASE WHEN c.phone_number IS NOT NULL AND c.phone_number <> '' THEN 1 ELSE 0 END +
                        CASE WHEN c.facebook    IS NOT NULL AND c.facebook    <> '' THEN 1 ELSE 0 END +
                        CASE WHEN c.telegram    IS NOT NULL AND c.telegram    <> '' THEN 1 ELSE 0 END +
                        CASE WHEN c.instagram   IS NOT NULL AND c.instagram   <> '' THEN 1 ELSE 0 END) AS sig_score,
                       (SELECT COUNT(*) FROM orders o WHERE o.client_id = c.id) AS orders_cnt,
                       (CASE WHEN c.manually_edited_at IS NOT NULL THEN 1 ELSE 0 END) AS manual_lock
                FROM clients c
                WHERE c.id = ANY(:ids)
                ORDER BY sig_score DESC, orders_cnt DESC, manual_lock DESC, c.id ASC
                LIMIT 1
            """), {"ids": cids}).fetchone()
            if ranked:
                candidate = session.query(Client).filter(Client.id == ranked[0]).first()
                # peers — для м'якого flag possible_duplicate; нового клієнта НЕ створюємо
                ambiguous_peer_ids = [c for c in cids if c != ranked[0]]

    # ── Stage 3: conflict detection ───────────────────────────────────────
    # ВАЖЛИВО: порівнюємо НОРМАЛІЗОВАНІ форми, а не raw. Інакше
    # 'https://www.facebook.com/profile.php?id=X' vs 'facebook.com/profile.php?id=X'
    # вважалися б конфліктом, хоча це той самий FB-профіль.
    create_new_due_to_conflict = False
    conflict_details = None
    if candidate and not strong_signal_matched:
        if norm_phone and candidate.phone_normalized and candidate.phone_normalized != norm_phone:
            create_new_due_to_conflict = True
            conflict_details = f"phone mismatch: row={norm_phone} vs candidate={candidate.phone_normalized}"
        elif norm_fb and candidate.facebook_normalized and candidate.facebook_normalized != norm_fb:
            create_new_due_to_conflict = True
            conflict_details = f"facebook mismatch: row={norm_fb} vs candidate={candidate.facebook_normalized}"
        elif norm_tg and candidate.telegram_normalized and candidate.telegram_normalized != norm_tg:
            create_new_due_to_conflict = True
            conflict_details = f"telegram mismatch: row={norm_tg} vs candidate={candidate.telegram_normalized}"
        elif norm_ig and candidate.instagram_normalized and candidate.instagram_normalized != norm_ig:
            create_new_due_to_conflict = True
            conflict_details = f"instagram mismatch: row={norm_ig} vs candidate={candidate.instagram_normalized}"

    # ── Stage 4: enrich existing OR create new ────────────────────────────
    if candidate and not create_new_due_to_conflict:
        locked = _locked_fields(candidate)
        manual_lock_active = candidate.manually_edited_at is not None

        def _can_set(field: str, current_value, new_value) -> bool:
            """Парсер може записати поле тільки якщо: ще порожнє AND (не залочене вручну)."""
            if not new_value:
                return False
            if current_value:
                return False
            if manual_lock_active and field in locked:
                return False
            return True

        if _can_set("phone_number", candidate.phone_number, phone): candidate.phone_number = phone.strip()
        if _can_set("facebook", candidate.facebook, facebook):       candidate.facebook = facebook.strip()
        if _can_set("viber", candidate.viber, viber):                candidate.viber = viber.strip()
        if _can_set("telegram", candidate.telegram, telegram):       candidate.telegram = telegram.strip()
        if _can_set("instagram", candidate.instagram, instagram):    candidate.instagram = instagram.strip()
        if _can_set("olx", candidate.olx, olx):                      candidate.olx = olx.strip()
        if _can_set("email", candidate.email, email):                candidate.email = email.strip()
        if gender_id and (not candidate.gender_id or candidate.gender_id == 0) and \
           not (manual_lock_active and "gender_id" in locked):
            candidate.gender_id = gender_id
        # nickname: тільки якщо не залочений І ще порожній
        if nickname and not candidate.nickname and not (manual_lock_active and "nickname" in locked):
            candidate.nickname = nickname

        # КЛЮЧОВЕ: реєструємо alias ЗАВЖДИ — навіть для locked клієнта.
        # Так "Льоша (Балу)" знов прийде з Sheets → знайде клієнта по alias
        # навіть якщо в clients зараз "Льоша" з NULL nickname.
        _register_alias(session, candidate.id, first, last, nickname, raw_name, source="parser")
        session.flush()
        return candidate.id

    # ── Stage 5: create new client ───────────────────────────────────────
    client = Client(
        first_name   = first if first else None,
        last_name    = last if last else None,
        nickname     = nickname,
        gender_id    = gender_id,
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

    # Реєструємо перший alias одразу
    _register_alias(session, client.id, first, last, nickname, raw_name, source="parser")

    # Якщо створили через ambiguity → flag на новому з peer_ids
    if ambiguous_peer_ids:
        _add_client_flag(
            session, client.id, "ambiguous_name_at_parse",
            peer_ids=ambiguous_peer_ids,
            details=f"Created from row '{raw_name}' — same name matches {len(ambiguous_peer_ids)} existing clients",
        )
        # Також підсвітимо peers (взаємно)
        for peer_id in ambiguous_peer_ids:
            _add_client_flag(
                session, peer_id, "possible_duplicate",
                peer_ids=[client.id] + [p for p in ambiguous_peer_ids if p != peer_id],
                details=f"Same normalized name as new client #{client.id}",
            )

    # Якщо створили через conflict → flag на обох
    if create_new_due_to_conflict and candidate:
        _add_client_flag(
            session, client.id, "phone_mismatch_with_alias",
            peer_ids=[candidate.id],
            details=conflict_details or "Strong-signal mismatch with existing client",
        )
        _add_client_flag(
            session, candidate.id, "phone_mismatch_with_alias",
            peer_ids=[client.id],
            details=conflict_details or "Strong-signal mismatch with new client",
        )

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
    # Нова пошта
    "НП": "Нова пошта", "НОВА ПОШТА": "Нова пошта", "НОВАПОШТА": "Нова пошта",
    "НОВА": "Нова пошта",
    # Укрпошта
    "УП": "Укрпошта", "УКРПОШТА": "Укрпошта", "УКР ПОШТА": "Укрпошта",
    # Самовивіз
    "САМОВИВІЗ": "Самовивіз", "САМОВИВОЗ": "Самовивіз",
    "МІСЦЕВИЙ": "Самовивіз", "МІСЦЕВ": "Самовивіз", "МІСТ": "Самовивіз",
    # Магазин
    "МАГАЗИН": "Магазин", "МАГ": "Магазин",
    # Відкладено
    "ВІДКЛАДЕНО": "Відкладено", "ВІДКЛАД": "Відкладено",
    # Разом
    "РАЗОМ": "Разом",
    # Кур'єр
    "КУР'ЄР": "Кур'єр", "КУРЄР": "Кур'єр",
}

# Maps Google Sheets "Статус відповіді" → DB order_statuses.status_name.
# Uses PREFIX matching (key can be a prefix of the raw value).
_ORDER_STATUS_MAP = {
    "ПІДТВЕРДЖ":  "Підтверджено",
    "ОЧІКУЄТЬСЯ":  "Очікується",
    "УТОЧНИТИ":    "Уточнити",
    "ФОТО":        "Фото",
    "ВІДМІНА":     "Відміна",
    "ВІДМОВА":     "Відміна",
    "СКАСОВАНО":   "Відміна",
    "ІГНОРУВАН":   "Ігнорування",
    "ІГНОРОВАН":   "Ігнорування",
    "ПОДАРУНОК":   "Подарунок",
    "В ЧЕРЗІ":     "В черзі",
    "ПОВЕРН":      "Повернення",
    "ОБМІН":       "Обмін",
    "ПЕРЕДАТИ":    "Передати",
    "ВІДПРАВЛЕНО": "Підтверджено",
    "ВІДКЛАД":     "Очікується",
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
    # Capitalize if no mapping found (e.g. "магазин" → "Магазин")
    target_name = mapped or (raw_clean[0].upper() + raw_clean[1:] if raw_clean else raw_clean)
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
    # Prefix matching: "ПІДТВЕРДЖ" matches key "ПІДТВЕРДЖ" even if sheet truncates
    mapped = None
    for key, val in _ORDER_STATUS_MAP.items():
        if raw_up.startswith(key) or key.startswith(raw_up):
            mapped = val
            break
    if mapped:
        all_os = session.query(OrderStatus).all()
        os = next((o for o in all_os if o.status_name and o.status_name.strip().lower() == mapped.lower()), None)
        if os:
            return os.id
    # Fallback: direct match against DB status names
    all_os = session.query(OrderStatus).all()
    os = next((o for o in all_os if o.status_name and o.status_name.strip().upper() == raw_up), None)
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

    # ── Step 2: fallback — all products with this number (with or without # prefix)
    pnum_no_hash = pnum_with_hash.lstrip("#")
    candidates = session.query(Product).filter(
        or_(
            Product.productnumber == pnum_with_hash,
            Product.productnumber == pnum_no_hash,
        )
    ).all()

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # Prefer the one with # prefix (newer convention)
        with_hash = [c for c in candidates if c.productnumber.startswith("#")]
        if with_hash:
            return with_hash[0]
        return candidates[0]

    # ── Step 3: clonednumbers fallback ───────────────────────────────────────
    return session.query(Product).filter(
        or_(
            Product.clonednumbers.like(f"%{pnum_with_hash}%"),
            Product.clonednumbers.like(f"%{pnum_no_hash}%"),
        )
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

        # Визначаємо sales_channel для замовлень без клієнта
        _sales_channel = None
        if not client_name.strip():
            delivery_hint = col(row, "Доставка").strip().upper()
            if delivery_hint == "МАГАЗИН":
                _sales_channel = "Магазин"
            # client_id залишиться None — не створюємо фейкових клієнтів

        # Auto-detect sales_channel з контактних полів та нотаток
        if not _sales_channel or _sales_channel == "Ефір":
            _channel_sources = " ".join(filter(None, [
                col(row, "Коментарі"), col(row, "Уточнення"),
                col(row, "Отримувач"), col(row, "Доставка"),
                col(row, "Viber"), col(row, "Telegram"),
                col(row, "Instagram"), col(row, "Olx"),
            ]))
            detected = _detect_sales_channel(_channel_sources)
            if detected:
                _sales_channel = detected

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

        # ── Парсимо знижку з колонки 'Знижка' ──────────────────────────────
        # Формати: '100;', '10%', '100%', '\-85;', '160'
        _discount_type: str | None = None
        _discount_value: float | None = None
        _d = discount_raw.strip().rstrip(";").strip().replace("\\-", "-").replace("−", "-").replace("–", "-")
        if _d and _d not in ("0", "ㅤ"):
            _is_pct = _d.endswith("%")
            if _is_pct:
                _d = _d[:-1].strip()
            import re as _re2
            _m = _re2.search(r"-?\d+(?:[.,]\d+)?", _d)
            if _m:
                try:
                    _dval = abs(float(_m.group(0).replace(",", ".")))
                    if _dval > 0:
                        _discount_type = "Відсоток" if _is_pct else "Фіксована"
                        _discount_value = _dval
                except ValueError:
                    pass

        # Client (None якщо ім'я порожнє — анонімне замовлення)
        client_id = None
        if client_name.strip():
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
        # Format 1: "П321 (37);"  "Ф3320 (37-38);"       → pnum (size)
        # Format 2: "Розмір: 38 (П321);"                  → keyword size (pnum)
        # Build map: normalized_pnum → size_hint
        size_hints: dict = {}  # pnum_upper → size string
        for hint_part in clarification.split(";"):
            hint_part = hint_part.strip()
            if not hint_part:
                continue
            # Format 1: pnum (size) — e.g. "П321 (37)", "Ф2982 (39-40)"
            m = re.match(r"#?([\wА-ЯҐЄІЇа-яґєії]+)\s*\(([^)]+)\)", hint_part)
            if m:
                hint_pnum = "#" + m.group(1).strip().lstrip("#")
                hint_size = _normalize_size(m.group(2).strip())
                if hint_size:
                    size_hints[hint_pnum.upper()] = hint_size
                continue
            # Format 2: "Розмір: 38 (П321)" or "Розмір 38 (П321)" (keyword first)
            m2 = re.search(
                r"(?:розмір|розм\.?|size)[:\s]+([0-9][0-9.,]*(?:\s*[-–]\s*[0-9][0-9.,]*)?)"
                r"\s*\(#?([\wА-ЯҐЄІЇа-яґєії]+)\)",
                hint_part,
                re.IGNORECASE,
            )
            if m2:
                hint_size = _normalize_size(m2.group(1).strip())
                hint_pnum = "#" + m2.group(2).strip().lstrip("#")
                if hint_size:
                    size_hints[hint_pnum.upper()] = hint_size

        order_date_col = col(row, "Дата замовлення")
        order_date = sheet_date
        if order_date_col:
            # Google Sheets може віддавати дату в різних форматах:
            # "05.04.2026" (DD.MM.YYYY) — ручний ввід
            # "2026-04-05" (YYYY-MM-DD) — ISO / serial date format
            # "04/05/2026" (MM/DD/YYYY) — рідко, але можливо
            for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    order_date = datetime.strptime(order_date_col.strip(), fmt).date()
                    break
                except ValueError:
                    continue

        # Пропускаємо carried-over замовлення: якщо order_date <= cutoff_date,
        # це замовлення з попередньої вкладки, воно вже парсилось звідти.
        if order_date <= cutoff_date:
            skipped += 1
            continue

        deferred_raw = col(row, "Відкладено до")
        deferred = None
        if deferred_raw:
            for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    deferred = datetime.strptime(deferred_raw.strip(), fmt).date()
                    break
                except ValueError:
                    continue

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
        fp_raw = f"{(client_name or '').strip().lower()}|{order_date.isoformat()}|{'|'.join(norm_pnums)}"
        source_fp = hashlib.md5(fp_raw.encode("utf-8")).hexdigest()

        existing_order = session.query(Order).filter(
            Order.source_fingerprint == source_fp
        ).first()

        # Level 1.5: Date-migration fallback
        # Раніше парсер ігнорував колонку "Дата замовлення" і використовував sheet_date.
        # Тепер order_date може бути реальною датою → fingerprint змінився.
        # Перевіряємо також старий fingerprint (з sheet_date) для плавного переходу.
        if not existing_order and order_date != sheet_date:
            fp_old_raw = f"{(client_name or '').strip().lower()}|{sheet_date.isoformat()}|{'|'.join(norm_pnums)}"
            source_fp_old = hashlib.md5(fp_old_raw.encode("utf-8")).hexdigest()
            existing_order = session.query(Order).filter(
                Order.source_fingerprint == source_fp_old
            ).first()
            if existing_order:
                # Мігруємо: оновлюємо дату і fingerprint на правильні
                existing_order.order_date = order_date
                existing_order.source_fingerprint = source_fp
                logger.info(
                    "Order #%d: migrated date %s → %s (was sheet_date)",
                    existing_order.id, sheet_date, order_date,
                )

        # Level 2: Client + date + total_amount + same item count
        # Catches cases where product numbers were corrected in Sheet but
        # client/date/total stayed the same → same order, not a new one.
        # Extra guard: item count must match to avoid merging genuinely
        # different orders from the same client on the same day with same total.
        if not existing_order and client_id and total_amount > 0:
            # Спершу шукаємо з реальною датою, потім з sheet_date (для міграції)
            for search_date in ([order_date, sheet_date] if order_date != sheet_date else [order_date]):
                candidates = session.query(Order).filter(
                    Order.client_id == client_id,
                    Order.order_date == search_date,
                    Order.total_amount == total_amount,
                    Order.source_fingerprint.isnot(None),
                ).all()
                num_items_current = len(product_nums)
                for candidate in candidates:
                    existing_items_count = session.query(OrderItem).filter(
                        OrderItem.order_id == candidate.id
                    ).count()
                    if existing_items_count == num_items_current:
                        existing_order = candidate
                        existing_order.source_fingerprint = source_fp
                        if search_date != order_date:
                            existing_order.order_date = order_date
                        break
                if existing_order:
                    break

        # Level 2.5: Date-window match (захист від дублів при переносі замовлення між вкладками).
        # Якщо користувач переніс замовлення на іншу дату/вкладку (або заповнив колонку
        # "Дата замовлення" іншим числом), Level 1/1.5/2 не зловлять його — fingerprint
        # та дата відрізняються. Шукаємо у ±7 днів за (client + total + повний сет товарів).
        # Тільки при ОДНОЗНАЧНОМУ збігу (рівно 1 кандидат), щоб не злити різні замовлення.
        if not existing_order and client_id and total_amount > 0 and product_nums:
            from datetime import timedelta as _td
            _norm_set_current = set(norm_pnums)
            _num_items_current = len(product_nums)
            _window_lo = order_date - _td(days=7)
            _window_hi = order_date + _td(days=7)
            _candidates = session.query(Order).filter(
                Order.client_id == client_id,
                Order.total_amount == total_amount,
                Order.order_date >= _window_lo,
                Order.order_date <= _window_hi,
                Order.order_date != order_date,  # точний date вже перевірений у Level 2
            ).all()
            _matches = []
            for _cand in _candidates:
                _items = session.query(OrderItem, Product).join(
                    Product, OrderItem.product_id == Product.id
                ).filter(OrderItem.order_id == _cand.id).all()
                if len(_items) != _num_items_current:
                    continue
                _cand_set = set(
                    re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", (p.productnumber or "")).upper()
                    for _, p in _items
                )
                if _cand_set == _norm_set_current:
                    _matches.append(_cand)
            if len(_matches) == 1:
                existing_order = _matches[0]
                logger.info(
                    "Order #%d: matched via date-window (was %s → now %s), updating fingerprint %s → %s",
                    existing_order.id, existing_order.order_date, order_date,
                    existing_order.source_fingerprint, source_fp,
                )
                existing_order.order_date = order_date
                existing_order.source_fingerprint = source_fp
            elif len(_matches) > 1:
                logger.warning(
                    "Order date-window match ambiguous for client_id=%s date=%s total=%s: %d candidates — skipping merge",
                    client_id, order_date, total_amount, len(_matches),
                )

        # Level 3: Fallback для старих замовлень без fingerprint
        if not existing_order:
            notes_val = combined_notes if combined_notes else None
            for search_date in ([order_date, sheet_date] if order_date != sheet_date else [order_date]):
                fb_q = session.query(Order).filter(
                    Order.client_id == client_id,
                    Order.order_date == search_date,
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
                    if search_date != order_date:
                        existing_order.order_date = order_date
                    break

        if existing_order:
            # Оновлюємо існуюче замовлення (статуси, трекінг, тощо)
            existing_order.order_status_id   = order_status_id
            existing_order.payment_status_id = pay_status_id
            existing_order.delivery_method_id= delivery_id
            existing_order.tracking_number   = tracking if tracking else None
            existing_order.deferred_until    = deferred
            existing_order.priority          = priority
            existing_order.notes             = combined_notes if combined_notes else None
            existing_order.total_amount      = total_amount  # оновлюємо суму (знижку вже враховано в Сума)
            if _sales_channel:
                existing_order.sales_channel = _sales_channel
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
                sales_channel      = _sales_channel if _sales_channel else None,
                source_fingerprint = source_fp,
                created_at         = datetime.utcnow(),
            )
            session.add(order)
            session.flush()
            orders_added += 1

        # ── Auto-detect "разом з <Name>" → client_relations (safe, isolated) ──
        # Не валимо парсер, навіть якщо щось пішло не так зі звʼязками.
        if combined_notes and client_id and order is not None and getattr(order, "id", None):
            try:
                _link_together_partners(session, order.id, client_id, combined_notes)
            except Exception as _rel_e:  # noqa: BLE001
                logger.warning("client_relations link failed for order=%s: %s", order.id, _rel_e)

        total_recalc = 0.0
        any_price_substituted = False
        # Збираємо список (product, item_price) щоб застосувати знижку до останнього
        _order_items_buf: list = []  # list of (product, item_price)

        for pnum, price in zip(product_nums, prices):
            # Strip emoji / special chars from product number
            pnum_clean = re.sub(r"[^\w#А-ЯҐЄІЇа-яґєії]", "", pnum).strip()
            if not pnum_clean:
                continue

            product = _resolve_order_product(session, pnum_clean, size_hints)

            if not product:
                logger.debug("Product not found for pnum=%s, skipping order_item", pnum_clean)
                continue

            # Fallback: якщо ціна в замовленні = 0, підтягуємо з журналу
            item_price = price
            if (not price or price <= 0) and product.price and product.price > 0:
                item_price = product.price
                any_price_substituted = True

            # ── Sync product price from order ──────────────────────────────
            # Пріоритет ціни: Замовлення > Журнал.
            # Якщо ціна в товарі NULL — записуємо ціну з замовлення.
            # Якщо ціна в товарі є і відрізняється — зберігаємо стару як oldprice.
            if price and price > 0:
                if not product.price:
                    product.price = price
                    product.updated_at = datetime.utcnow()
                elif price != product.price:
                    product.oldprice = product.price
                    product.price = price
                    product.updated_at = datetime.utcnow()

            _order_items_buf.append((product, item_price))

        # ── Застосовуємо знижку до ОСТАННЬОГО товару ──────────────────────
        if _discount_type and _discount_value and _order_items_buf:
            last_prod, last_price = _order_items_buf[-1]
            if _discount_type == "Відсоток":
                new_last = max(0.0, round(last_price * (1.0 - _discount_value / 100.0), 2))
            else:  # Фіксована
                new_last = max(0.0, round(last_price - _discount_value, 2))
            _order_items_buf[-1] = (last_prod, new_last)

        # ── Записуємо OrderItems в БД ──────────────────────────────────────
        for idx, (product, item_price) in enumerate(_order_items_buf):
            is_last = (idx == len(_order_items_buf) - 1)
            item_kwargs = dict(
                order_id   = order.id,
                product_id = product.id,
                quantity   = 1,
                price      = item_price,
            )
            if is_last and _discount_type and _discount_value:
                item_kwargs["discount_type"]  = _discount_type
                item_kwargs["discount_value"] = _discount_value
            item = OrderItem(**item_kwargs)
            session.add(item)
            items_added += 1
            total_recalc += item_price

        # Перераховуємо total_amount, якщо використано fallback-ціни з журналу
        if any_price_substituted and (not total_amount or total_amount <= 0):
            order.total_amount = total_recalc

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
        Product, Brand, Type, Color, Gender, Style,
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

        pnum       = col(row, "Номер").strip().rstrip(";").strip()
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
        # Guard: split combined types ("Туфлі/кросівки", "Ботинки-челсі") → Type + Subtype
        if type_val:
            t_part, st_part = _split_combined_type(type_val)
            type_val = t_part
            if st_part and not sub_val:
                sub_val = st_part

        # Auto-classify season (orders parser has no "Сезон" column)
        season_val = _classify_season(type_val, sub_val, "", desc_val)

        brand_obj  = _get_or_create(session, Brand,  "brandname",  brand_val)  if brand_val  else None
        type_obj   = _get_or_create(session, Type,   "typename",   type_val)   if type_val   else None
        color_obj  = _get_or_create(session, Color,  "colorname",  color_val)  if color_val  else None
        gender_obj = _get_or_create(session, Gender, "gendername", gender_val) if gender_val else None
        mfr_id     = _get_or_create_country(session, mfr_cntry)
        own_id     = _get_or_create_country(session, own_cntry)
        sub_id     = _get_or_create_subtype(session, sub_val, type_obj.id if type_obj else None)
        cond_id    = _get_or_create_condition(session, cond_val)
        # Orders sheet немає "Поточний стан" — успадковуємо від "Стан"
        current_cond_id = cond_id

        brand_id  = brand_obj.id if brand_obj else None
        type_id   = type_obj.id  if type_obj  else None
        color_id  = color_obj.id if color_obj else None
        gender_id = gender_obj.id if gender_obj else None

        # Auto-detect gender if not specified in sheet
        if not gender_id:
            auto_gender = _auto_detect_gender(size_val, desc_val, "")
            if auto_gender:
                auto_gender_obj = _get_or_create(session, Gender, "gendername", auto_gender)
                gender_id = auto_gender_obj.id if auto_gender_obj else None

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
                season                = season_val or None,
                dateadded             = date.today(),
                quantity              = 1,
                brandid               = brand_id,
                typeid                = type_id,
                subtypeid             = sub_id,
                genderid              = gender_id,
                colorid               = color_id,
                conditionid           = cond_id,
                current_conditionid   = current_cond_id,
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

        # Rate-limit: wait before each sheet read
        if idx > 0:
            time.sleep(SHEET_READ_DELAY_SEC)

        # Read all rows once — used for both financial parsing and product parsing
        all_rows = ws.get_all_values()

        # Parse purchase/delivery costs from the info block on the right side of the sheet
        financials = _parse_delivery_financials(all_rows)
        logger.info(f"[products] Sheet '{ws.title}': purchase_cost={financials['purchase_cost']}, delivery_cost={financials['delivery_cost']}")

        shipment_id = _get_or_create_shipment(
            session, ws.title, sheet_date, supplier_id,
            purchase_cost=financials["purchase_cost"],
            delivery_cost=financials["delivery_cost"],
        )
        if shipment_id:
            shipment_ids.append(shipment_id)
        logger.info(f"[products] Parsing sheet {idx+1}/{total_sheets}: {ws.title} (supplier={supplier_name}, shipment={shipment_id})")

        def _cb(done, total, _ws=ws, _idx=idx):
            if progress_cb:
                overall = int((_idx / total_sheets + done / total / total_sheets) * 100)
                progress_cb(overall, f"{_ws.title}: {done}/{total}")

        result = _parse_products_sheet(ws, session, sheet_date, _cb, seen_in_run, supplier_id, shipment_id, prefetched_rows=all_rows)
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

        # ── Sweep Phase 3: fingerprint-drift deduplication ────────────────
        # When a Google Sheets row is edited (products added/removed),
        # the fingerprint changes → parser creates a NEW order.
        # Old orders with stale fingerprints remain → inflated sold_count.
        # Also handles date-migration: old orders with sheet_date may now
        # have a sibling with the real order_date from "Дата замовлення".
        # Fix: for each (client_id) with nearby-date orders (±7 days window),
        # detect overlapping item sets (>50% Jaccard) → keep only the latest.
        fp_drift_deleted = 0
        try:
            # Group by client_id where the client has 3+ orders within any 7-day window
            drift_groups = session.execute(text("""
                SELECT DISTINCT o1.client_id, o1.order_date
                FROM orders o1
                WHERE o1.source_fingerprint IS NOT NULL AND o1.client_id IS NOT NULL
                AND (
                    SELECT COUNT(*) FROM orders o2
                    WHERE o2.client_id = o1.client_id
                      AND o2.source_fingerprint IS NOT NULL
                      AND ABS(o2.order_date - o1.order_date) <= 7
                ) >= 3
            """)).fetchall()

            # Deduplicate: process each client once with their full date range
            processed_clients = set()
            for client_id_val, anchor_date in drift_groups:
                if client_id_val in processed_clients:
                    continue
                processed_clients.add(client_id_val)

                # Fetch all orders for this client (with fingerprint)
                group_orders = session.execute(text("""
                    SELECT o.id, o.order_date,
                           ARRAY_AGG(oi.product_id ORDER BY oi.product_id) AS product_ids
                    FROM orders o
                    LEFT JOIN order_items oi ON oi.order_id = o.id
                    WHERE o.client_id = :cid
                      AND o.source_fingerprint IS NOT NULL
                    GROUP BY o.id, o.order_date
                    ORDER BY o.id
                """), {"cid": client_id_val}).fetchall()

                # Build clusters of overlapping orders (same real order edited over time)
                # Use union-find: if two orders share >50% products AND dates within 7 days
                order_products = {}
                order_dates = {}
                for row in group_orders:
                    pids = set(p for p in (row[2] or []) if p is not None)
                    order_products[row[0]] = pids
                    order_dates[row[0]] = row[1]

                order_ids = list(order_products.keys())
                parent = {oid: oid for oid in order_ids}

                def find(x):
                    while parent[x] != x:
                        parent[x] = parent[parent[x]]
                        x = parent[x]
                    return x

                def union(a, b):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb

                for i in range(len(order_ids)):
                    for j in range(i + 1, len(order_ids)):
                        a, b = order_ids[i], order_ids[j]
                        # Only cluster orders with dates within 7 days of each other
                        date_diff = abs((order_dates[a] - order_dates[b]).days)
                        if date_diff > 7:
                            continue
                        pids_a, pids_b = order_products[a], order_products[b]
                        if not pids_a or not pids_b:
                            continue
                        intersection = len(pids_a & pids_b)
                        union_size = len(pids_a | pids_b)
                        if union_size > 0 and intersection / union_size > 0.5:
                            union(a, b)

                # Group by cluster root
                from collections import defaultdict
                clusters = defaultdict(list)
                for oid in order_ids:
                    clusters[find(oid)].append(oid)

                for cluster_root, cluster_ids in clusters.items():
                    if len(cluster_ids) < 3:
                        continue
                    # Keep the latest order (max id), delete the rest
                    cluster_ids_sorted = sorted(cluster_ids)
                    to_delete = cluster_ids_sorted[:-1]  # all except the latest

                    if to_delete:
                        session.execute(text(
                            "DELETE FROM order_items WHERE order_id = ANY(:ids)"
                        ), {"ids": to_delete})
                        session.execute(text(
                            "DELETE FROM orders WHERE id = ANY(:ids)"
                        ), {"ids": to_delete})
                        fp_drift_deleted += len(to_delete)

            if fp_drift_deleted > 0:
                session.commit()
                logger.info(
                    "[sweep] Fingerprint-drift: deleted %d stale duplicate orders",
                    fp_drift_deleted,
                )
        except Exception as e:
            logger.warning("[sweep] Fingerprint-drift cleanup failed: %s", e)
            session.rollback()

        orders_sweep_deleted += fp_drift_deleted

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

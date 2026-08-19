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
import os
import re
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Callable

import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
try:
    from services.runtime_config import credentials_file as _runtime_credentials_file
except ImportError:
    from backend.services.runtime_config import credentials_file as _runtime_credentials_file


def get_credentials_path() -> str:
    """Resolve Google credentials for every Sheets parser mode.

    One resolver is shared by products/orders/full/workspace, manual runs,
    startup auto-parse and the journal poller. No absolute machine path is
    allowed here: dev-Mac and packaged Windows use runtime_config fallbacks.
    """
    resolved = _runtime_credentials_file("GOOGLE_SHEETS_CREDENTIALS_FILE")
    if resolved:
        return resolved

    # Compatibility fallback for older developer setups that only define the
    # key filename and keep it under backend/scripts/secure_creds/.
    key_name = os.getenv("GOOGLE_SHEETS_JSON_KEY", "").strip()
    if key_name:
        candidate = Path(__file__).resolve().parent / "secure_creds" / key_name
        if candidate.is_file():
            return str(candidate.resolve())

    raise FileNotFoundError(
        "Google Sheets credentials не знайдено. Перевірте "
        "GOOGLE_SHEETS_CREDENTIALS_FILE, BMS_DATA_DIR або "
        "mcp-google-sheets/working_credentials.json."
    )


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
JOURNAL_ID    = "102usNqWz07mKUr77_9K_sp9PcsOiAskSnJybk72OeTk"
ORDERS_ID     = "1rjCN-xBm-maxp0S0o7Lypp8rHmW2qsBirJ7nfN09lqw"
WORKSPACE_ID  = "1q0hUp4oM3hAYciibe5v5h4Uc9Jvhkd8Dl7C8yk2niFA"
WORKSPACE_SHEET = "Воркспейс1"
QUICK_SHEETS_COUNT = 30
# Delay between sheet reads to stay within Google Sheets API quota (60 req/min/user)
SHEET_READ_DELAY_SEC = 1.1  # ~54 req/min, safe margin

# ── Layer A: batch reads ──────────────────────────────────────────────────────
# values_batch_get fetches many sheets' values in ONE API request, collapsing
# ~410 round-trips (+ per-sheet sleeps) into a handful of chunked calls.
# Parsing logic is unchanged — only how rows are delivered. Disable via
# PARSER_BATCH_READ=0 for an instant behavioural rollback to per-sheet reads.
BATCH_READ_ENABLED = os.getenv("PARSER_BATCH_READ", "1") != "0"
BATCH_CHUNK = int(os.getenv("PARSER_BATCH_CHUNK", "50"))  # sheets per batch read

# ── Layer B: per-sheet change detection (hash skip) ───────────────────────────
# A worksheet is reprocessed iff its content hash changed OR PARSER_VERSION was
# bumped since the last parse. This catches manual sheet edits (content differs)
# and forced reparses (version bump) while skipping untouched sheets.
#
# Applied to ORDERS only, and only in 'quick' mode. Products are never skipped
# (their quantity is recomputed from cross-sheet appearance counts each run, so
# skipping a sheet would undercount). 'full' mode never skips — it is the
# escape hatch that rebuilds everything from the authoritative sheets.
#
# Bump PARSER_VERSION whenever the orders parsing logic changes in a way that
# would produce different output for the same input → forces a full reparse.
PARSER_VERSION = 11  # v11: імпорт «Витрати на рекламу» з підсумкового блоку вкладки. v10: + Level 0T — дедуп zero-total ордерів за pnum_key+клієнт+вікно (Level 1K/2N вимагали total>0, тож total=0 щопрогону плодив дубль). v9: source_pnum_key (resolution-INDEPENDENT identity) + Level 1K дедуп + анонімний sweep + Level 2N. Корінь ghost-дублів закрито: той самий рядок аркуша не створює дубль навіть при нестабільному resolution / анонімному клієнті / legacy без gid / zero-total
HASH_SKIP_ENABLED = os.getenv("PARSER_HASH_SKIP", "1") != "0"

# ── Layer C: whole-file change gate ───────────────────────────────────────────
# If the spreadsheet's Drive lastUpdateTime is unchanged since the last
# successful parse of the same (spreadsheet, mode), skip the entire run — no
# cell changed, so re-parsing is a no-op. Safe even for products: an unchanged
# file means quantity would recompute identically. Keyed by mode so a 'full'
# request is never short-circuited by a prior partial 'quick'.
# Rollback: env PARSER_FILE_GATE=0 or DROP TABLE spreadsheet_sync_state.
FILE_GATE_ENABLED = os.getenv("PARSER_FILE_GATE", "1") != "0"

# ── Phase 2a: in-app edit locks ───────────────────────────────────────────────
# Fields a user can edit in the app; once edited (products.manually_edited_at set)
# the parser must not overwrite them. Implemented as snapshot-restore at run
# level: capture locked values before parsing, restore after — leaves the ~40
# scattered field-set sites in _parse_products_sheet untouched.
# Keep in sync with LOCKABLE_PRODUCT_FIELDS in product_service.
PRODUCT_LOCKS_ENABLED = os.getenv("PRODUCT_LOCKS", "1") != "0"
# oldprice included so the auto-markdown ("Стара ціна") survives reparse too.
PRODUCT_LOCK_FIELDS = {"price", "oldprice", "model", "marking", "description", "extranote", "season",
                       # Нові журнальні колонки (2026-06-10): Колекція/GTIN/Геометрична форма.
                       "collection", "gtin", "geometric_shape",
                       # Shoe-lookup FKs edited in-app (model-level). Snapshot/restore by id.
                       "heeltypeid", "lacetypeid", "packagingid", "technologyid", "sole_colorid",
                       # «Інше» shoe-lookups edited in-app (model-level).
                       "soletypeid", "toeshapeid", "fasteningtypeid", "liningid",
                       # Main color edited in-app (model-level).
                       "colorid",
                       # Класифікація edited in-app (model-level, dropdown).
                       "typeid", "subtypeid", "styleid", "brandid", "genderid",
                       # Condition (per-item) edited in-app.
                       "current_conditionid"}
# Order fields protected from parser overwrite (Order editing Phase A). Keep in
# sync with LOCKABLE_ORDER_FIELDS in order_service.
ORDER_LOCKS_ENABLED = os.getenv("ORDER_LOCKS", "1") != "0"
ORDER_LOCK_FIELDS = {"notes", "tracking_number", "sales_channel",
                     "order_status_id", "payment_status_id", "delivery_method_id"}


def _snapshot_product_locks(session: Session) -> dict:
    """Return {product_id: {field: value}} for products with active in-app
    locks, capturing the user's current (edited) values before a reparse."""
    if not PRODUCT_LOCKS_ENABLED:
        return {}
    try:
        from backend.models.models import Product
    except ImportError:
        from models.models import Product
    rows = session.execute(text(
        "SELECT id, manually_edited_fields FROM products "
        "WHERE manually_edited_at IS NOT NULL "
        "AND manually_edited_fields IS NOT NULL AND btrim(manually_edited_fields) <> ''"
    )).fetchall()
    snapshot: dict = {}
    for pid, fields_csv in rows:
        flds = [f.strip() for f in fields_csv.split(",")
                if f.strip() in PRODUCT_LOCK_FIELDS]
        if not flds:
            continue
        prod = session.get(Product, pid)
        if prod is None:
            continue
        snapshot[pid] = {f: getattr(prod, f) for f in flds}
    return snapshot


def _restore_product_locks(session: Session, snapshot: dict) -> int:
    """Re-apply locked field values the parser may have overwritten. Returns the
    number of fields restored."""
    if not snapshot:
        return 0
    try:
        from backend.models.models import Product
    except ImportError:
        from models.models import Product
    restored = 0
    for pid, fieldvals in snapshot.items():
        prod = session.get(Product, pid)
        if prod is None:  # product merged/removed during parse — skip
            continue
        for f, v in fieldvals.items():
            if getattr(prod, f) != v:
                setattr(prod, f, v)
                restored += 1
    if restored:
        session.commit()
    return restored


def _snapshot_order_locks(session: Session) -> dict:
    """{order_id: {field: value}} for orders with active in-app locks."""
    if not ORDER_LOCKS_ENABLED:
        return {}
    try:
        from backend.models.models import Order
    except ImportError:
        from models.models import Order
    rows = session.execute(text(
        "SELECT id, manually_edited_fields FROM orders "
        "WHERE manually_edited_at IS NOT NULL "
        "AND manually_edited_fields IS NOT NULL AND btrim(manually_edited_fields) <> ''"
    )).fetchall()
    snap: dict = {}
    for oid, csv in rows:
        flds = [f.strip() for f in csv.split(",") if f.strip() in ORDER_LOCK_FIELDS]
        if not flds:
            continue
        o = session.get(Order, oid)
        if o is None:
            continue
        snap[oid] = {f: getattr(o, f) for f in flds}
    return snap


def _restore_order_locks(session: Session, snapshot: dict) -> int:
    """Re-apply locked order field values the parser may have overwritten."""
    if not snapshot:
        return 0
    try:
        from backend.models.models import Order
    except ImportError:
        from models.models import Order
    restored = 0
    for oid, fieldvals in snapshot.items():
        o = session.get(Order, oid)
        if o is None:
            continue
        for f, v in fieldvals.items():
            if getattr(o, f) != v:
                setattr(o, f, v)
                restored += 1
    if restored:
        session.commit()
    return restored


# ── Phase 2b: write-back of in-app edits to the journal sheet ─────────────────
# Maps editable product fields → journal column header (resolved by NAME, not
# position; rename-safe). A field with no column here cannot be written back.
WRITEBACK_FIELD_HEADERS = {
    "price":          "Ціна",
    "oldprice":       "Стара ціна",
    "model":          "Модель",
    "description":    "Опис",
    "season":         "Сезон",
    "marking":        "Маркування",
    "extranote":      "Екстра примітка",
    "collection":     "Колекція",
    "geometric_shape": "Геометрична форма",
    # Model-level (однакові на всіх рядках ростовки) — безпечний write-to-all-rows.
    "year":           "Рік",
    "width":          "Ширина",
    "clonednumbers":  "Номера-клони",
    # Shoe-lookup FKs — model-level. Router passes the resolved NAME as the value,
    # so write-back treats them as plain text (see SHOE_FK_NAME_FIELDS in service).
    "heeltypeid":     "Тип каблука",
    "lacetypeid":     "Тип шнурівки",
    "packagingid":    "Пакування",
    "technologyid":   "Технології",
    "sole_colorid":   "Колір підошви",
    "soletypeid":     "Тип підошви",
    "toeshapeid":     "Форма носка",
    "fasteningtypeid": "Застібка",
    "liningid":       "Підкладка",
    "colorid":        "Колір",
    # Класифікація (model-level) → колонки журналу.
    # ⚠️ Журнал має колонки «Вид»/«Підвид» (НЕ «Тип»/«Підтип»). Парсер ЧИТАЄ саме
    # їх (col(row,"Вид") нижче), тож і writeback мусить писати в них — інакше
    # header-резолвер не знаходив колонку й тип/підтип, змінені в додатку, ніколи
    # не потрапляли в аркуш («не синхронізується»), хоч решта полів писалась.
    "typeid":         "Вид",
    "subtypeid":      "Підвид",
    "styleid":        "Стиль",
    "brandid":        "Бренд",
    "genderid":       "Стать",
    # Per-item FK (унікальний на пару ростовки) — журнальна колонка «Поточний стан».
    "current_conditionid": "Поточний стан",
    # Країна-виробник (model-level) → журнальна колонка «Країна-виробник».
    "manufacturercountryid": "Країна-виробник",
    # Матеріали — синтетичні поля material_<position> → колонки позицій (model-level).
    "material_upper":    "Верх",
    "material_middle":   "Середина",
    "material_insole":   "Устілка",
    "material_sole":     "Підошва",
    "material_midsole":  "Проміжна підошва",
    "material_membrane": "Мембрана",
    # Заміри — синтетичні meas_<name> → колонки (per-item: значення-діапазон рядком).
    "meas_length":         "Довжина",
    "meas_pog":            "Груди (н/о)",
    "meas_pob":            "Бедра (н/о)",
    "meas_pot":            "Талія (н/о)",
    "meas_sleeve":         "Рукав",
    "meas_height":         "Висота",
    "meas_sole_thickness": "Товщина підошви",
    "meas_heel":           "Підбор",
    # Per-item (унікальні на КОЖЕН рядок ростовки) — пишуться лише коли номер
    # у аркуші займає один рядок (див. PER_ITEM_WRITEBACK_FIELDS + guard нижче).
    "sizeeu":         "Розмір",
    "size_letter":    "Буквений",
    "measurementscm": "СМ",
    "dimensions":     "Габарити",
    "gtin":           "GTIN",
}
# Поля, унікальні на кожен рядок ростовки. Поточний write-back пише в УСІ рядки
# спільного номера → для таких полів це затерло б сусідні розміри. Тому пишемо
# їх лише коли номер у аркуші — один рядок (guard у writeback_field_to_journal).
PER_ITEM_WRITEBACK_FIELDS = {"sizeeu", "size_letter", "measurementscm", "dimensions",
                             "gtin",  # штрихкод свій на кожен розмір ростовки
                             "current_conditionid",
                             # Заміри — унікальні на розмір ростовки.
                             "meas_length", "meas_pog", "meas_pob", "meas_pot",
                             "meas_sleeve", "meas_height", "meas_sole_thickness", "meas_heel"}
# Text fields written RAW (literal, no formula interpretation); numeric fields
# USER_ENTERED so the sheet stores a real number.
WRITEBACK_TEXT_FIELDS = {"model", "marking", "description", "season", "extranote",
                         "width", "clonednumbers",
                         # GTIN обовʼязково RAW: USER_ENTERED перетворив би довгий
                         # штрихкод на число (наукова нотація, втрата провідних нулів).
                         "collection", "gtin", "geometric_shape",
                         "sizeeu", "size_letter", "measurementscm", "dimensions",
                         # Shoe-lookup + condition FKs written back as canonical name text.
                         "heeltypeid", "lacetypeid", "packagingid", "technologyid", "sole_colorid",
                         "soletypeid", "toeshapeid", "fasteningtypeid", "liningid",
                         "colorid",
                         "typeid", "subtypeid", "styleid", "brandid", "genderid",
                         "current_conditionid",
                         # Матеріали (CSV назв) — пишуться як літеральний текст.
                         "material_upper", "material_middle", "material_insole",
                         "material_sole", "material_midsole", "material_membrane",
                         # Заміри — рядок-діапазон ("25-27") пишемо літерально (RAW),
                         # щоб Sheets не трактував як дату/формулу.
                         "meas_length", "meas_pog", "meas_pob", "meas_pot",
                         "meas_sleeve", "meas_height", "meas_sole_thickness", "meas_heel"}
WRITEBACK_ENABLED = os.getenv("PARSER_WRITEBACK", "1") != "0"
_WRITEBACK_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "writeback_backups")

# Глобальний лок ЗАПИСУ в журнал (write-back, реордер, append/delete/rename рядків).
# Реордер робить read-modify-write усієї вкладки: паралельний запис у цю мить
# був би затертий застарілим знімком. Усі писарі беруть цей лок.
import threading as _threading
JOURNAL_WRITE_LOCK = _threading.RLock()

# Ghost-замовлення: коли набір товарів у рядку аркуша змінюється, fingerprint
# змінюється → парсер створює НОВИЙ ордер, старий зависає орфаном (NULL-status).
# Після прогону прибираємо такі привиди ВУЗЬКОЮ сигнатурою (untouched + строгий
# containment із touched-сиблінгом same client+date + NULL-status + має fingerprint)
# з бекапом. НЕ глобальний mark&sweep. Див. feedback_orders_ghost_dupes.md.
ORDER_GHOST_SWEEP = os.getenv("ORDER_GHOST_SWEEP", "1") != "0"
# Scoped-sweep за стабільним gid вкладки: вкладка-джерело = повна істина для СВОЇХ
# ордерів, тож після її парсингу будь-який parser-origin ордер цієї вкладки, що НЕ
# матчив живого рядка цього прогону, — привид (рядок видалено/змінено). Ловить навіть
# disjoint-привидів (інший набір товарів), які containment-сигнатура не бачить.
ORDER_GID_SWEEP = os.getenv("ORDER_GID_SWEEP", "1") != "0"
# Орфани товарів після парсу вкладки завозу (номер зник з аркуша: перейменування
# 4336 → Ф4336, видалений рядок). Вузько: тільки товари ЦЬОГО завозу, тільки без
# продажів, з JSON-бекапом. Запобіжник спрацьовує лише на ВЕЛИКИХ пачках
# (> PARSE_ORPHAN_MIN_ABS штук І > PARSE_ORPHAN_MAX_SHARE завозу) — щоб недочитана
# вкладка не знесла завіз, але дрібне перейменування на 5 рядків лікувалось саме.
# PARSE_ORPHAN_MAX_SHARE=0 + MIN_ABS=0 → вимкнути авто-прибирання (ручний синк
# картки завозу працює як і раніше).
# ⚠️ ВИМКНЕНО за замовчуванням після інциденту 19.08.2026: авто-прохід знищив
# 135 живих товарів (рядок переїхав в іншу вкладку, а deliveryid лишився старим).
# Логіку полагоджено (рішення в кінці прогону, з seen_in_run і номерами ВСІХ
# вкладок), але вмикати — свідомо: PARSE_ORPHAN_AUTO=1. Ручний синк картки завозу
# працює завжди, незалежно від цього прапорця.
PARSE_ORPHAN_AUTO = os.getenv("PARSE_ORPHAN_AUTO", "0") != "0"
PARSE_ORPHAN_MAX_SHARE = float(os.getenv("PARSE_ORPHAN_MAX_SHARE", "0.5"))
PARSE_ORPHAN_MIN_ABS = int(os.getenv("PARSE_ORPHAN_MIN_ABS", "20"))
_GHOST_SWEEP_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ghost_sweep_backups")

# Marker written into a merged lost product's Воркспейс row ("Екстра примітка").
# The workspace parser skips rows carrying it, so a merged item is not re-created
# as is_lost after the source row stays in the sheet.
MERGE_MARKER_PREFIX = "[BMS:обʼєднано"


def _canon_pnum_for_match(s: str) -> str:
    """Canonical productnumber for sheet matching: strip #, trailing ;, spaces."""
    return (s or "").strip().lstrip("#").strip().rstrip(";").strip()


def _save_writeback_backup(sheet_title: str, productnumber: str, field: str, backups: list) -> Optional[str]:
    """Persist old cell values before a write-back so it can be reverted."""
    try:
        os.makedirs(_WRITEBACK_BACKUP_DIR, exist_ok=True)
        import json
        from datetime import datetime as _dt
        fname = f"{_dt.now():%Y%m%d_%H%M%S}_{_canon_pnum_for_match(productnumber)}_{field}.json"
        path = os.path.join(_WRITEBACK_BACKUP_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sheet": sheet_title, "productnumber": productnumber,
                       "field": field, "cells": backups}, f, ensure_ascii=False, indent=2)
        return path
    except Exception as e:
        logger.warning(f"[writeback] backup failed: {e}")
        return None


def writeback_field_to_journal(sheet_title: str, productnumber: str, field: str, value) -> dict:
    """Write `value` for `field` into ALL rows of `productnumber` in the journal
    worksheet `sheet_title`. Під JOURNAL_WRITE_LOCK — щоб read-modify-write
    реордера/append не перетинався з цим записом (див. journal_writer)."""
    with JOURNAL_WRITE_LOCK:
        return _writeback_field_to_journal_locked(sheet_title, productnumber, field, value)


def _writeback_field_to_journal_locked(sheet_title: str, productnumber: str, field: str, value) -> dict:
    """Write `value` for `field` into ALL rows of `productnumber` in the journal
    worksheet `sheet_title`. Resolves the target column by header name, backs up
    old values first, then writes only changed cells in one batch call.

    Returns {ok, rows_updated, ...} or {ok: False, reason}.
    """
    if not WRITEBACK_ENABLED:
        return {"ok": False, "reason": "writeback disabled"}
    header_name = WRITEBACK_FIELD_HEADERS.get(field)
    if not header_name:
        return {"ok": False, "reason": f"no journal column for '{field}'"}
    if not sheet_title:
        return {"ok": False, "reason": "no sheet_title (product has no delivery)"}

    import gspread as _gspread
    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    try:
        ws = sh.worksheet(sheet_title)
    except Exception:
        return {"ok": False, "reason": f"worksheet '{sheet_title}' not found"}

    all_values = ws.get_all_values()
    if not all_values:
        return {"ok": False, "reason": "empty sheet"}
    header = [h.strip() for h in all_values[0]]
    if "Номер" not in header or header_name not in header:
        return {"ok": False, "reason": f"column 'Номер' or '{header_name}' missing in sheet"}
    num_idx = header.index("Номер")
    col_idx = header.index(header_name)

    # Normalize the new value to a cell string
    if value is None:
        new_str = ""
    elif field in ("price", "oldprice"):
        # Whole numbers → "1900" (not "1900.0"); keep decimals otherwise.
        try:
            fv = float(value)
            new_str = str(int(fv)) if fv == int(fv) else str(fv)
        except (TypeError, ValueError):
            new_str = str(value)
    else:
        new_str = str(value)

    target = _canon_pnum_for_match(productnumber)

    # Guard ростовки: per-item поле (розмір/СМ/габарити) безпечно писати лише
    # коли номер займає ОДИН рядок. Інакше write-to-all-rows затер би сусідні
    # розміри. У такому разі правка лишається в БД (lock), аркуш не чіпаємо.
    if field in PER_ITEM_WRITEBACK_FIELDS:
        matching_rows = sum(
            1 for row in all_values[1:]
            if _canon_pnum_for_match(row[num_idx] if num_idx < len(row) else "") == target
        )
        if matching_rows > 1:
            return {"ok": False, "reason": f"per-item field '{field}' skipped: "
                    f"{matching_rows} rostovka rows share number {target} "
                    f"(saved to DB only, sheet untouched to avoid overwriting siblings)"}

    updates, backups = [], []
    for r_i, row in enumerate(all_values[1:], start=2):  # row 1 = header
        cell_num = row[num_idx] if num_idx < len(row) else ""
        if _canon_pnum_for_match(cell_num) != target:
            continue
        old = row[col_idx] if col_idx < len(row) else ""
        if old == new_str:
            continue  # already up to date
        a1 = _gspread.utils.rowcol_to_a1(r_i, col_idx + 1)
        updates.append({"range": a1, "values": [[new_str]]})
        backups.append({"a1": a1, "row": r_i, "old": old, "new": new_str})

    if not updates:
        return {"ok": True, "rows_updated": 0, "note": "no matching rows or already current"}

    backup_path = _save_writeback_backup(sheet_title, productnumber, field, backups)
    value_input = "USER_ENTERED" if field not in WRITEBACK_TEXT_FIELDS else "RAW"
    # Retry transient failures (network reset / 429 rate-limit). The write-back runs
    # in a background thread, so a swallowed transient error would silently desync the
    # sheet from the (locked) DB value forever — retry with backoff instead.
    last_err = None
    for attempt in range(4):
        try:
            ws.batch_update(updates, value_input_option=value_input)
            last_err = None
            break
        except Exception as e:  # gspread APIError / ConnectionError / etc.
            last_err = e
            if attempt < 3:
                time.sleep(2 * (attempt + 1))   # 2s, 4s, 6s
    if last_err is not None:
        logger.error(f"[writeback] {field} → '{sheet_title}' {productnumber}: "
                     f"all retries failed: {last_err}")
        return {"ok": False, "reason": f"sheet write failed after retries: {last_err}",
                "header": header_name, "backup": backup_path}
    logger.info(f"[writeback] {field} → '{sheet_title}' {productnumber}: {len(updates)} row(s), backup={backup_path}")
    return {"ok": True, "rows_updated": len(updates), "header": header_name, "backup": backup_path}


def mark_workspace_row_merged(lost_pnum: str, orig_pnum: str) -> dict:
    """Mark the merged lost product's row(s) in the Воркспейс sheet with a
    non-destructive note ("Екстра примітка") instead of deleting the row.
    The workspace parser then skips these rows, so the item is not re-created.

    Matched by the "Номер" column (canonical). Backs up old cell values first.
    Returns {ok, rows_marked, ...} or {ok: False, reason}.
    """
    if not WRITEBACK_ENABLED:
        return {"ok": False, "reason": "writeback disabled"}
    lost = _canon_pnum_for_match(lost_pnum)
    if not lost or lost == "???":
        return {"ok": False, "reason": "lost product has no resolvable number (cannot locate row)"}

    import gspread as _gspread
    gc = get_gc()
    sh = gc.open_by_key(WORKSPACE_ID)
    try:
        ws = sh.worksheet(WORKSPACE_SHEET)
    except Exception:
        return {"ok": False, "reason": f"workspace sheet '{WORKSPACE_SHEET}' not found"}

    all_values = ws.get_all_values()
    if not all_values:
        return {"ok": False, "reason": "empty workspace sheet"}
    header = [h.strip() for h in all_values[0]]
    if "Номер" not in header or "Екстра примітка" not in header:
        return {"ok": False, "reason": "column 'Номер' or 'Екстра примітка' missing"}
    num_idx = header.index("Номер")
    note_idx = header.index("Екстра примітка")

    marker = f"{MERGE_MARKER_PREFIX}→#{_canon_pnum_for_match(orig_pnum)}]"
    updates, backups = [], []
    for r_i, row in enumerate(all_values[1:], start=2):
        cell_num = row[num_idx] if num_idx < len(row) else ""
        if _canon_pnum_for_match(cell_num) != lost:
            continue
        old_note = row[note_idx] if note_idx < len(row) else ""
        if MERGE_MARKER_PREFIX in old_note:
            continue  # already marked
        new_note = f"{marker} {old_note}".strip()
        a1 = _gspread.utils.rowcol_to_a1(r_i, note_idx + 1)
        updates.append({"range": a1, "values": [[new_note]]})
        backups.append({"a1": a1, "row": r_i, "old": old_note, "new": new_note})

    if not updates:
        return {"ok": True, "rows_marked": 0, "note": "no matching/unmarked rows"}

    backup_path = _save_writeback_backup(WORKSPACE_SHEET, lost_pnum, "merge_marker", backups)
    ws.batch_update(updates, value_input_option="RAW")
    logger.info(f"[merge-mark] {lost_pnum} → {orig_pnum}: marked {len(updates)} workspace row(s), backup={backup_path}")
    return {"ok": True, "rows_marked": len(updates), "backup": backup_path}


# ── Order editing Phase B: write-back to the «Замовлення» sheet ───────────────
# Raw-text columns only (B1): tracking → "Номер накладної", notes → "Коментарі",
# sales_channel → token appended into "Уточнення" (the same text the parser reads
# the channel from). Status/payment/delivery (id→text) are a later step (B2).
ORDER_WRITEBACK_HEADERS = {"tracking_number": "Номер накладної", "notes": "Коментарі"}
ORDER_WB_SEARCH_TABS = int(os.getenv("ORDER_WB_SEARCH_TABS", "6"))  # newest tabs to search for an order's row
# Canonical channel → token written into "Уточнення" (matches _SALES_CHANNEL_PATTERNS).
CHANNEL_TOKENS = {"Telegram": "TG", "Viber": "Viber", "Instagram": "IG",
                  "TikTok": "TT", "OLX": "OLX", "Grailed": "Grailed", "Shafa": "Shafa",
                  "Prom": "Prom", "MONO": "MONO", "Каталог": "Catalog"}


def compute_order_fingerprint(client_name: str, order_date, product_numbers) -> str:
    """Stable order identity = md5(client | date | sorted normalized product nums).
    SINGLE SOURCE OF TRUTH — the parser AND add/remove edits must use this so a
    product-set change updates the fingerprint identically (no reparse duplicate)."""
    import hashlib
    norm = sorted(
        re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", p).upper()
        for p in (product_numbers or []) if p and p.strip()
    )
    iso = order_date.isoformat() if hasattr(order_date, "isoformat") else str(order_date)
    fp_raw = f"{(client_name or '').strip().lower()}|{iso}|{'|'.join(norm)}"
    return hashlib.md5(fp_raw.encode("utf-8")).hexdigest()


def compute_pnum_key(product_numbers) -> str:
    """Resolution-INDEPENDENT order identity = md5(sorted normalized RAW product
    numbers from the sheet 'Номера товарів' cell).

    Unlike the resolved order_items set, this NEVER drifts when
    _resolve_order_product matches differently across runs (the #Ф2261 flaky-
    resolution class) — so the same sheet row can never spawn a duplicate even
    if product resolution is inconsistent. Keeps '-' so ростовка-суфікс
    Ф1810-2 ≠ Ф1810, strips '#'/emoji/whitespace. Empty → '' (no key)."""
    import hashlib
    norm = sorted(
        n for n in (
            re.sub(r"[^\w\-А-ЯҐЄІЇа-яґєії]", "", p).upper()
            for p in (product_numbers or []) if p and p.strip()
        ) if n
    )
    if not norm:
        return ""
    return hashlib.md5("|".join(norm).encode("utf-8")).hexdigest()


def _fmt_price(v) -> str:
    """Price as the sheet writes it: integer if whole, else plain float."""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return "" if v is None else str(v)


def _norm_pnum_set(raw: str) -> set:
    """Normalized set of product numbers from a 'Номера товарів' cell."""
    out = set()
    for tok in re.split(r"[;,]", raw or ""):
        t = _canon_pnum_for_match(tok)
        if t:
            out.add(t.lower())
    return out


def writeback_order_to_journal(session: Session, order_id: int, dry_run: bool = True,
                               locate_pnums: set = None) -> dict:
    """Write an order's locked fields back to its «Замовлення» row.

    Locates the row by client name + product-number set (EXACTLY one match —
    aborts on 0/>1 to never touch the wrong sale). `locate_pnums` (the OLD set)
    is used for location when the product set changed (add/remove) — the row
    still has the old numbers. dry_run=True returns the plan without writing.
    """
    if not WRITEBACK_ENABLED:
        return {"ok": False, "reason": "writeback disabled"}
    try:
        from backend.models.models import Order, OrderItem, Product, Client
    except ImportError:
        from models.models import Order, OrderItem, Product, Client

    o = session.get(Order, order_id)
    if not o:
        return {"ok": False, "reason": "order not found"}
    if not o.order_date:
        return {"ok": False, "reason": "order has no date (cannot find tab)"}
    locked = {x.strip() for x in (o.manually_edited_fields or "").split(",") if x.strip()}

    cl = session.get(Client, o.client_id) if o.client_id else None
    client_name = f"{(cl.first_name or '') if cl else ''} {(cl.last_name or '') if cl else ''}".strip()
    pnums = {(_canon_pnum_for_match(r[0]) or "").lower()
             for r in session.execute(text(
                 "SELECT p.productnumber FROM order_items oi JOIN products p ON p.id=oi.product_id "
                 "WHERE oi.order_id=:oid"), {"oid": order_id}).fetchall() if r[0]}

    if not pnums:
        return {"ok": False, "reason": "order has no product numbers to match a row"}

    # Locate by the OLD set when the product set changed (row still has old nums)
    match_pnums = {(_canon_pnum_for_match(p) or "").lower() for p in locate_pnums} if locate_pnums else pnums

    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)
    # Orders live in the current batch tab(s), NOT a tab named by order_date, so
    # search non-skip tabs newest→oldest for a UNIQUE client+products row. Limit
    # to the newest ORDER_WB_SEARCH_TABS tabs (active orders are recent).
    search_tabs = [w for w in sh.worksheets() if not is_skip_sheet(w.title)][:ORDER_WB_SEARCH_TABS]
    matches = []  # (ws, header, r_i, row)
    for w in search_tabs:
        rows = w.get_all_values()
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]
        if "Клієнт" not in header or "Номера товарів" not in header:
            continue
        ci, ni = header.index("Клієнт"), header.index("Номера товарів")
        for r_i, row in enumerate(rows[1:], start=2):
            rc = (row[ci] if ci < len(row) else "").strip()
            if rc.lower() != client_name.lower():
                continue
            if _norm_pnum_set(row[ni] if ni < len(row) else "") == match_pnums:
                matches.append((w, header, r_i, row))
        if matches:
            break  # stop at the newest tab that has a match (current order home)
    if len(matches) != 1:
        return {"ok": False, "reason": f"ambiguous row match ({len(matches)} found) — not writing",
                "client": client_name, "pnums": sorted(pnums)}
    target_ws, header, r_i, row = matches[0]

    def cell(name):
        i = header.index(name) if name in header else -1
        return (row[i] if 0 <= i < len(row) else ""), i

    planned = []  # {field, header, a1, old, new}
    # tracking → "Номер накладної" (raw)
    if "tracking_number" in locked and "Номер накладної" in header:
        old, idx = cell("Номер накладної")
        new = "" if o.tracking_number is None else str(o.tracking_number)
        if old != new:
            planned.append({"field": "tracking_number", "header": "Номер накладної",
                            "a1": _gspread_a1(r_i, idx + 1), "old": old, "new": new})

    # notes AND sales_channel both live in "Коментарі" (parser: notes ← Коментарі;
    # channel is derived from its text). Combine into ONE target value so they
    # don't clobber each other. "Уточнення" is for per-product clarifications — NOT touched.
    if ("notes" in locked or "sales_channel" in locked) and "Коментарі" in header:
        old, idx = cell("Коментарі")
        final = ("" if o.notes is None else str(o.notes)) if "notes" in locked else old
        if "sales_channel" in locked:
            tok = CHANNEL_TOKENS.get(o.sales_channel or "")
            if tok and not re.search(rf"\b{re.escape(tok)}\b", final, re.IGNORECASE):
                final = f"{final.rstrip(' ;')}; {tok}".lstrip("; ").strip() if final.strip() else tok
        if final != old:
            planned.append({"field": "notes/channel", "header": "Коментарі",
                            "a1": _gspread_a1(r_i, idx + 1), "old": old, "new": final})

    # ── B2: status / payment / delivery (FK → sheet text) ──────────────────
    # id→text is derived from the column's OWN existing values (a value already
    # present is a valid dropdown option the parser re-reads to the same id).
    # Read-only resolution (never creates a DeliveryMethod).
    try:
        from backend.models.models import DeliveryMethod
    except ImportError:
        from models.models import DeliveryMethod

    def _ro_resolve(field, v):
        if not v or not v.strip():
            return None
        if field == "order_status_id":
            return _resolve_order_status(session, v)
        if field == "payment_status_id":
            return _resolve_payment_status(session, v)
        # delivery — read-only (do NOT create)
        up = v.strip().upper()
        name = _DELIVERY_MAP.get(up) or next((val for k, val in _DELIVERY_MAP.items() if k in up), None)
        if not name:
            name = v.strip()
        dm = next((d for d in session.query(DeliveryMethod).all()
                   if d.method_name and d.method_name.strip().lower() == name.lower()), None)
        return dm.id if dm else None

    fk_skipped = []
    for field, hdr in [("order_status_id", "Статус відповіді"),
                       ("payment_status_id", "Статус оплати"),
                       ("delivery_method_id", "Доставка")]:
        if field not in locked or hdr not in header:
            continue
        col_idx = header.index(hdr)
        old = row[col_idx] if col_idx < len(row) else ""
        tid = getattr(o, field)
        if tid is None:
            if old.strip():
                planned.append({"field": field, "header": hdr,
                                "a1": _gspread_a1(r_i, col_idx + 1), "old": old, "new": ""})
            continue
        # build id→text from this column's existing values
        id2text = {}
        for rr in rows[1:]:
            cv = (rr[col_idx] if col_idx < len(rr) else "").strip()
            if not cv:
                continue
            rid = _ro_resolve(field, cv)
            if rid is not None and rid not in id2text:
                id2text[rid] = cv
        # If the cell ALREADY resolves to the target id, leave it — don't rewrite
        # a valid synonym (e.g. cell text differs but means the same method).
        if _ro_resolve(field, old) == tid:
            continue
        new = id2text.get(tid)
        if new is None:
            fk_skipped.append(f"{field}=id{tid}: no existing sheet value to copy (won't guess)")
            continue
        if old.strip() != new.strip():
            planned.append({"field": field, "header": hdr,
                            "a1": _gspread_a1(r_i, col_idx + 1), "old": old, "new": new})

    # ── Item prices → "Ціна" (parallel list) + auto-sum → "Сума" ────────────
    # Triggered when item prices were edited (lock marker "item_prices"). Rebuilds
    # the "Ціна" list aligned to the "Номера товарів" positions; "Сума" = total.
    # Product SET is unchanged (add/remove is a separate future step), so the row
    # identity (fingerprint) stays stable.
    if "item_prices" in locked and "items" not in locked and "Ціна" in header and "Номера товарів" in header:
        from collections import deque, defaultdict
        items = session.execute(text(
            "SELECT p.productnumber, oi.price FROM order_items oi JOIN products p ON p.id=oi.product_id "
            "WHERE oi.order_id=:oid ORDER BY oi.id"), {"oid": order_id}).fetchall()
        by_pnum = defaultdict(deque)
        for pn, pr in items:
            by_pnum[(_canon_pnum_for_match(pn) or "").lower()].append(pr)
        positions = [t for t in re.split(r"[;,]", row[header.index("Номера товарів")]) if t.strip()]
        new_prices, aligned = [], bool(positions)
        for pos in positions:
            key = (_canon_pnum_for_match(pos) or "").lower()
            if by_pnum[key]:
                new_prices.append(_fmt_price(by_pnum[key].popleft()))
            else:
                aligned = False
                break
        if aligned:
            old_cena, cidx = cell("Ціна")
            new_cena = "; ".join(new_prices) + ";"
            if old_cena.strip() != new_cena.strip():
                planned.append({"field": "item_prices", "header": "Ціна",
                                "a1": _gspread_a1(r_i, cidx + 1), "old": old_cena, "new": new_cena})
        else:
            fk_skipped.append("item_prices: products don't align to sheet row — Ціна not written")
        if "Сума" in header:
            old_sum, sidx = cell("Сума")
            new_sum = _fmt_price(o.total_amount)
            if old_sum.strip() != new_sum.strip():
                planned.append({"field": "total_amount", "header": "Сума",
                                "a1": _gspread_a1(r_i, sidx + 1), "old": old_sum, "new": new_sum})

    # ── Add/remove products → rewrite "Номера товарів" + recompute fingerprint ──
    # ANTI-DUPLICATION: the new fingerprint is computed from the SHEET client text
    # + the EXACT tokens we write, so the next parse computes the same fp → matches
    # THIS order (UPDATE, never CREATE). Updated only after a successful sheet write.
    new_fingerprint = None
    if "items" in locked and "Номера товарів" in header:
        rows_pp = session.execute(text(
            "SELECT p.productnumber, oi.price FROM order_items oi JOIN products p ON p.id=oi.product_id "
            "WHERE oi.order_id=:oid ORDER BY oi.id"), {"oid": order_id}).fetchall()
        new_nums = [(pn or "").lstrip("#").strip() for pn, _ in rows_pp if pn]
        new_pric = [_fmt_price(pr) for pn, pr in rows_pp if pn]
        if new_nums:
            sheet_client = (row[header.index("Клієнт")] if header.index("Клієнт") < len(row) else "").strip()
            fp_candidate = compute_order_fingerprint(sheet_client, o.order_date, new_nums)
            clash = session.execute(text(
                "SELECT id FROM orders WHERE source_fingerprint=:fp AND id<>:oid LIMIT 1"),
                {"fp": fp_candidate, "oid": order_id}).fetchone()
            if clash:
                fk_skipped.append(f"items: new fingerprint collides with order {clash[0]} — NOT writing (anti-dup)")
            else:
                new_fingerprint = fp_candidate
                # rebuild "Номера товарів" + parallel "Ціна" from order_items (same order)
                old_nums_cell, nidx = cell("Номера товарів")
                new_nums_cell = "; ".join(new_nums) + ";"
                if old_nums_cell.strip() != new_nums_cell.strip():
                    planned.append({"field": "items", "header": "Номера товарів",
                                    "a1": _gspread_a1(r_i, nidx + 1), "old": old_nums_cell, "new": new_nums_cell})
                if "Ціна" in header:
                    old_cena, cidx = cell("Ціна")
                    new_cena = "; ".join(new_pric) + ";"
                    if old_cena.strip() != new_cena.strip():
                        planned.append({"field": "items_prices", "header": "Ціна",
                                        "a1": _gspread_a1(r_i, cidx + 1), "old": old_cena, "new": new_cena})
                if "Сума" in header:
                    old_sum, sidx = cell("Сума")
                    new_sum = _fmt_price(o.total_amount)
                    if old_sum.strip() != new_sum.strip():
                        planned.append({"field": "total_amount", "header": "Сума",
                                        "a1": _gspread_a1(r_i, sidx + 1), "old": old_sum, "new": new_sum})

    result = {"ok": True, "tab": target_ws.title, "row": r_i, "client": client_name,
              "dry_run": dry_run, "planned": planned, "fk_skipped": fk_skipped,
              "new_fingerprint": new_fingerprint}
    if not planned and new_fingerprint is None:
        result["note"] = "nothing to write (already current or no locked fields)"
        return result
    if dry_run:
        return result
    if planned:
        _save_writeback_backup(target_ws.title, f"order{order_id}", "order_fields", planned)
        target_ws.batch_update([{"range": p["a1"], "values": [[p["new"]]]} for p in planned],
                               value_input_option="RAW")
        logger.info(f"[order-writeback] order {order_id} → '{target_ws.title}' row {r_i}: {len(planned)} cell(s)")
    # Update fingerprint ONLY after the sheet row was successfully rewritten, so a
    # failed/aborted write leaves fp untouched → next parse safely reverts the edit.
    if new_fingerprint and new_fingerprint != o.source_fingerprint:
        o.source_fingerprint = new_fingerprint
        session.commit()
        logger.info(f"[order-writeback] order {order_id} fingerprint → {new_fingerprint[:8]} (product set changed)")
    return result


def _gspread_a1(r: int, c: int) -> str:
    import gspread as _g
    return _g.utils.rowcol_to_a1(r, c)


def _file_unchanged(session: Session, spreadsheet_id: str, mode: str, last_update_time: str) -> bool:
    """True if the whole file's lastUpdateTime + parser_version match the last
    successful run of this (spreadsheet, mode)."""
    row = session.execute(
        text(
            "SELECT last_update_time, parser_version FROM spreadsheet_sync_state "
            "WHERE spreadsheet_id = :s AND mode = :m"
        ),
        {"s": spreadsheet_id, "m": mode},
    ).fetchone()
    return bool(row and row[0] == last_update_time and row[1] == PARSER_VERSION)


def _record_file_state(session: Session, spreadsheet_id: str, mode: str, last_update_time: str) -> None:
    """Upsert the whole-file sync marker after a successful run."""
    session.execute(
        text(
            "INSERT INTO spreadsheet_sync_state "
            "(spreadsheet_id, mode, last_update_time, parser_version, checked_at) "
            "VALUES (:s, :m, :t, :ver, now()) "
            "ON CONFLICT (spreadsheet_id, mode) DO UPDATE SET "
            "last_update_time = EXCLUDED.last_update_time, "
            "parser_version = EXCLUDED.parser_version, "
            "checked_at = now()"
        ),
        {"s": spreadsheet_id, "m": mode, "t": last_update_time, "ver": PARSER_VERSION},
    )


def _file_gate_check(session: Session, sh, spreadsheet_id: str, mode: str, force: bool = False):
    """Returns (gated: bool, last_update_time: Optional[str]).
    gated=True → whole file unchanged, caller should skip the run.
    last_update_time is the value to record after a successful run (None if the
    gate is disabled or the lookup failed → don't record/skip).

    force=True → НІКОЛИ не гейтити (manual/user-тригер мусить завжди читати й
    обробляти), але lut усе одно повертаємо, щоб записати маркер після успіху →
    майбутні авто-парси гейтяться коректно. Причина: Google `lastUpdateTime` лагає
    кілька хв після правки, тож gate міг мовчки пропустити щойно доданий рядок.
    Layer B (per-sheet hash-skip) лишається активним і коректним (читає контент)."""
    if not FILE_GATE_ENABLED:
        return False, None
    try:
        lut = sh.lastUpdateTime
    except Exception as e:
        logger.warning(f"[file-gate] lastUpdateTime fetch failed ({e}); proceeding without gate")
        return False, None
    if force:
        return False, lut
    if lut and _file_unchanged(session, spreadsheet_id, mode, lut):
        return True, lut
    return False, lut


def _compute_sheet_hash(rows: list) -> str:
    """Stable content hash of a sheet's values (order-sensitive)."""
    import hashlib
    import json
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _sheet_unchanged(session: Session, spreadsheet_id: str, sheet_gid: int, content_hash: str) -> bool:
    """True if this sheet's content AND parser_version match the last parse."""
    row = session.execute(
        text(
            "SELECT content_hash, parser_version FROM sheet_sync_state "
            "WHERE spreadsheet_id = :sid AND sheet_gid = :gid"
        ),
        {"sid": spreadsheet_id, "gid": sheet_gid},
    ).fetchone()
    return bool(row and row[0] == content_hash and row[1] == PARSER_VERSION)


def _record_sheet_state(session: Session, spreadsheet_id: str, sheet_gid: int,
                        sheet_title: str, content_hash: str) -> None:
    """Upsert the per-sheet sync marker (content hash + parser version + time)."""
    session.execute(
        text(
            "INSERT INTO sheet_sync_state "
            "(spreadsheet_id, sheet_gid, sheet_title, content_hash, parser_version, parsed_at) "
            "VALUES (:sid, :gid, :title, :h, :ver, now()) "
            "ON CONFLICT (spreadsheet_id, sheet_gid) DO UPDATE SET "
            "content_hash = EXCLUDED.content_hash, "
            "sheet_title = EXCLUDED.sheet_title, "
            "parser_version = EXCLUDED.parser_version, "
            "parsed_at = now()"
        ),
        {"sid": spreadsheet_id, "gid": sheet_gid, "title": sheet_title[:255],
         "h": content_hash, "ver": PARSER_VERSION},
    )

SKIP_SHEETS_PATTERNS = re.compile(
    r"^(Publications|Data|New|Клієнти|Temporary|Лист\d*|Copy of|Старі)",
    re.IGNORECASE,
)

# ── GSheets client ────────────────────────────────────────────────────────────
def get_gc() -> gspread.Client:
    creds = Credentials.from_service_account_file(get_credentials_path(), scopes=SCOPES)
    gc = gspread.authorize(creds)
    # Таймаут HTTP-викликів до Google. БЕЗ нього мертвий сокет (типово після сну
    # Mac: TCP-з'єднання «напіввідкрите») висить ХВИЛИНАМИ/годинами — і будь-яка
    # операція з журналом (а з нею й JOURNAL_WRITE_LOCK) «заморожує» застосунок.
    # З таймаутом виклик швидко падає транзієнтною помилкою → _with_retry
    # повторює вже на СВІЖОМУ з'єднанні.
    gc.set_timeout(60)
    return gc


def is_skip_sheet(title: str) -> bool:
    return bool(SKIP_SHEETS_PATTERNS.match(title.strip()))


def _chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _batch_read_values(sh, worksheets: list) -> dict:
    """Read values for many worksheets in ONE API call via values_batch_get.

    Returns {ws.id: rows} where rows is list[list[str]] — ragged (trailing
    empty cells trimmed), exactly like the Sheets values API. All three row
    consumers (_parse_products_sheet.col, _parse_orders_sheet.col,
    _parse_delivery_financials) guard with `idx < len(row)`, so ragged rows
    are safe. valueRanges come back in request order → map positionally.
    """
    from gspread.utils import absolute_range_name
    ranges = [absolute_range_name(ws.title) for ws in worksheets]
    resp = sh.values_batch_get(ranges, params={"majorDimension": "ROWS"})
    value_ranges = resp.get("valueRanges", [])
    out = {}
    for ws, vr in zip(worksheets, value_ranges):
        out[ws.id] = vr.get("values", []) or []
    return out


def _iter_sheets_with_rows(sh, worksheets: list):
    """Yield (idx, ws, rows) for each worksheet, reading in batches to bound
    both API calls and memory (only BATCH_CHUNK sheets held at once).

    Falls back to per-sheet get_all_values() when batching is disabled or a
    batch call errors — guarantees identical behaviour on the slow path.
    """
    if not BATCH_READ_ENABLED:
        for idx, ws in enumerate(worksheets):
            if idx > 0:
                time.sleep(SHEET_READ_DELAY_SEC)
            yield idx, ws, ws.get_all_values()
        return

    idx = 0
    for chunk_i, chunk in enumerate(_chunks(worksheets, BATCH_CHUNK)):
        if chunk_i > 0:
            time.sleep(SHEET_READ_DELAY_SEC)
        try:
            rows_map = _batch_read_values(sh, chunk)
        except Exception as e:
            logger.warning(f"[batch] read failed ({e}); falling back to per-sheet reads")
            rows_map = {}
            for j, ws in enumerate(chunk):
                if j > 0:
                    time.sleep(SHEET_READ_DELAY_SEC)
                rows_map[ws.id] = ws.get_all_values()
        for ws in chunk:
            yield idx, ws, rows_map.get(ws.id, [])
            idx += 1


_MEASUREMENT_RE = re.compile(r'\d+[хxХX]\d+')
_NUMERIC_SLASH_RE = re.compile(r'^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$')
_RANGE_RE = re.compile(r'^(\d+(?:\.\d+)?)[\-‐‑‒–—―](\d+(?:\.\d+)?)$')
# Accepts hyphen-minus + Unicode hyphens: ‐ ‑ ‒ – — ―


def _parse_measurement_range(val: str) -> tuple[Optional[float], Optional[float]]:
    """
    Parse a measurement (length/chest/hips/waist/sleeve/height/sole_thickness) into (min, max).

    Convention: single value → (v, v). Lets range filters
    `WHERE col_min <= X AND col_max >= X` match single values too.

      "45"                              → (45.0, 45.0)
      "45-48" / "45–48" / "45—48"       → (45.0, 48.0)
      "45/48"                           → (45.0, 48.0)
      ""                                → (None, None)
    """
    s = (val or "").strip().replace(",", ".")
    if not s:
        return (None, None)

    m_range = _RANGE_RE.match(s)
    if m_range:
        try:
            return (float(m_range.group(1)), float(m_range.group(2)))
        except (ValueError, TypeError):
            pass

    m_slash = _NUMERIC_SLASH_RE.match(s)
    if m_slash:
        try:
            return (float(m_slash.group(1)), float(m_slash.group(2)))
        except (ValueError, TypeError):
            pass

    try:
        v = float(s)
        return (v, v)
    except (ValueError, TypeError):
        return (None, None)


# ─────────────────────────────────────────────────────────────────────────────
# Materials parsing
# ─────────────────────────────────────────────────────────────────────────────
# Accepted separators between materials in one cell: "," ";" "+" "/"
_MATERIAL_SPLIT_RE = re.compile(r'\s*[,;+/]\s*')
# Optional leading percentage: "80% бавовна" → "бавовна"
_MATERIAL_PCT_RE = re.compile(r'^\s*\d+(?:[.,]\d+)?\s*%\s*')

# Sheet column header → DB position
MATERIAL_POSITIONS: dict[str, str] = {
    "Верх":     "upper",
    "Середина": "middle",
    "Устілка":  "insole",
    "Підошва":  "sole",
    "Проміжна підошва": "midsole",   # 2026-06-10: міжпідошва (ЕВА/ПУ між устілкою і підошвою)
    "Мембрана": "membrane",
}


def _split_materials_cell(raw: str) -> list[str]:
    """'гладка шкіра, текстиль + замша' → ['гладка шкіра','текстиль','замша'] (lowercased)."""
    if not raw:
        return []
    parts = _MATERIAL_SPLIT_RE.split(raw.strip())
    out: list[str] = []
    for p in parts:
        p = _MATERIAL_PCT_RE.sub("", p).strip().lower()
        if p:
            out.append(p)
    return out


# Cache: lowercased materialname → material_id. Populated lazily per parser run.
_materials_cache: dict[str, int] = {}

# Per-table cache for shoe lookups (sole_types / toe_shapes / fastening_types / linings)
# Keyed by (table_name, lowercased_value) → id.
_shoe_lookup_cache: dict[tuple[str, str], Optional[int]] = {}

# ── Per-run reference-table lookup cache ─────────────────────────────────────
# _get_or_create* раніше вантажили ЦІЛУ довідкову таблицю (brands 1103, colors
# 697, …) на КОЖЕН товар → десятки млн зайвих завантажень (93% часу фази товарів).
# Цей кеш індексує таблицю ОДИН раз за прогін: {cache_key: {norm_value: id}}.
# Скидається на старті кожного run_* (щоб між прогонами перейменування/видалення
# довідника не давало застарілих влучань). Кожен кеш-хіт валідується проти живої
# сесії (session.query(model).get(id) / SELECT WHERE id) — id відкоченого/
# видаленого рядка → міс → падаємо у звичайний scan+create, тож поведінка
# ІДЕНТИЧНА некешованому коду.
_ref_lookup_cache: dict[str, dict[str, int]] = {}
_ref_lookup_loaded: set[str] = set()


def _reset_ref_lookup_cache() -> None:
    """Очистити per-run кеш довідкових лукапів. Викликати на старті кожного run_*."""
    _ref_lookup_cache.clear()
    _ref_lookup_loaded.clear()


def _ref_cache_table(cache_key: str, loader) -> dict[str, int]:
    """Повернути (лениво заповнений один раз) індекс {norm_value: id} для cache_key.

    ``loader`` — callable, що повертає ітерабель (norm_value, id) для ВСІЄЇ таблиці;
    викликається щонайбільше раз на прогін. Перший запис на ключ виграє (setdefault) —
    дзеркалить «перший збіг у порядку сканування» лінійного пошуку (дублікатів
    norm_value у довідниках немає — перевірено, тож це не впливає).
    """
    tbl = _ref_lookup_cache.setdefault(cache_key, {})
    if cache_key not in _ref_lookup_loaded:
        for nv, rid in loader():
            if nv:
                tbl.setdefault(nv, rid)
        _ref_lookup_loaded.add(cache_key)
    return tbl


def _resolve_shoe_lookup_id(session, table: str, name_col: str, value: str) -> Optional[int]:
    """
    Resolve a single-FK lookup (sole_types/toe_shapes/fastening_types/linings) by
    canonical lowercase name. Never auto-creates; unknown → None (caller decides
    whether to log to unmapped queue).
    """
    if not value:
        return None
    key_val = value.strip().lower()
    if not key_val:
        return None
    cache_key = (table, key_val)
    if cache_key in _shoe_lookup_cache:
        return _shoe_lookup_cache[cache_key]
    from sqlalchemy import text as _sql
    row = session.execute(
        _sql(f"SELECT id FROM {table} WHERE LOWER({name_col}) = :v LIMIT 1"),
        {"v": key_val},
    ).first()
    rid = int(row[0]) if row else None
    _shoe_lookup_cache[cache_key] = rid
    return rid


# Sheet column → (table, name_col, FK column on products) for shoe lookups
SHOE_LOOKUP_COLUMNS: dict[str, tuple[str, str, str]] = {
    "Тип підошви":  ("sole_types",       "soletypename",       "soletypeid"),
    "Форма носка":  ("toe_shapes",       "toeshapename",       "toeshapeid"),
    "Застібка":     ("fastening_types",  "fasteningtypename",  "fasteningtypeid"),
    "Підкладка":    ("linings",          "liningname",         "liningid"),
    "Тип каблука":  ("heel_types",       "heeltypename",       "heeltypeid"),
    "Тип шнурівки": ("lace_types",       "lacetypename",       "lacetypeid"),
    "Пакування":    ("packaging_types",  "packagingname",      "packagingid"),
    "Технології":   ("technologies",     "technologyname",     "technologyid"),
}
# "Колір підошви" → reuse colors table (auto-create), resolved separately (not here),
# written into sole_colorid. See _resolve_sole_color() usage in both parser paths.

# New min/max measurement pairs (paired so we never set min without max).
_NEW_MIN_MAX_PAIRS: list[tuple[str, str]] = [
    ("measurementscm_min",              "measurementscm_max"),
    ("measurements_length_min",         "measurements_length_max"),
    ("measurements_pog_min",            "measurements_pog_max"),
    ("measurements_pob_min",            "measurements_pob_max"),
    ("measurements_pot_min",            "measurements_pot_max"),
    ("measurements_sleeve_min",         "measurements_sleeve_max"),
    ("measurements_height_min",         "measurements_height_max"),
    ("measurements_sole_thickness_min", "measurements_sole_thickness_max"),
    ("measurements_heel_min",           "measurements_heel_max"),
]
# Single-FK fields (one value per product).
_NEW_SINGLE_FK_FIELDS: list[str] = [
    "soletypeid", "toeshapeid", "fasteningtypeid", "liningid",
    "heeltypeid", "lacetypeid", "packagingid", "technologyid", "sole_colorid",
]
# Plain-text fields enriched NULL-only ТІЛЬКИ Воркспейс-парсером (Журнал-парсер
# пише їх авторитетно у своїй update-гілці і в new_fields сюди не передає).
_NEW_TEXT_FIELDS: list[str] = ["collection", "gtin", "geometric_shape"]


def _apply_new_fields_and_materials(
    session, prod, *,
    new_fields: dict,            # {'measurements_*_min/max': float|None, 'soletypeid': int|None, ...}
    materials_parsed: dict,      # {position: [name,...]} — populated only for non-empty cells
    source: str | None = None,
) -> None:
    """
    Apply new-style measurement + shoe-lookup + materials data to an EXISTING product
    in an UPDATE branch.

    Convention (matches existing "Fill NULL identity fields from re-parse" pattern):
      • Number pairs and single-FK fields are written ONLY if currently NULL in DB.
        That preserves any manual edits made via UI / DB and never overwrites.
      • Materials are full-replace per (product_id, position) — but only for positions
        the sheet actually has non-empty values for, so empty cells never wipe DB rows.

    Idempotent: re-running on the same row+product is a no-op for measurements/FKs
    (NULL-only guard) and re-asserts the same materials.
    """
    for min_f, max_f in _NEW_MIN_MAX_PAIRS:
        new_min = new_fields.get(min_f)
        if new_min is not None and getattr(prod, min_f, None) is None:
            setattr(prod, min_f, new_min)
            setattr(prod, max_f, new_fields.get(max_f))
    for f in _NEW_SINGLE_FK_FIELDS:
        v = new_fields.get(f)
        if v is not None and getattr(prod, f, None) is None:
            setattr(prod, f, v)
    for f in _NEW_TEXT_FIELDS:
        v = new_fields.get(f)
        if v and getattr(prod, f, None) is None:
            setattr(prod, f, v)
    if materials_parsed:
        _apply_product_materials(session, prod.id, materials_parsed, source)


def _looks_like_material(s: str) -> bool:
    """Чи схоже на матеріал (а не на сміття/число/ціну). Хоча б 2 літери, ≤60 символів."""
    import re as _re
    if not s or len(s) > 60:
        return False
    letters = _re.sub(r"[^A-Za-zА-Яа-яҐЄІЇґєії]", "", s)
    return len(letters) >= 2


def _resolve_material_id(session, name: str, create: bool = True) -> Optional[int]:
    """Look up canonical material_id by lowercased name. Якщо нема — GET-OR-CREATE
    (вільнотекстові матеріали користувача типу «плотний текстиль» зберігаємо як новий
    канон-матеріал, щоб лінкувалися й переживали re-parse). Сміття/числа — None."""
    if not name:
        return None
    key = name.strip().lower()
    if not key:
        return None
    if key in _materials_cache:
        return _materials_cache[key]
    from sqlalchemy import text as _sql
    row = session.execute(
        _sql("SELECT id FROM materials WHERE materialname = :n LIMIT 1"),
        {"n": key},
    ).first()
    mid = int(row[0]) if row else None
    if mid is None and create and _looks_like_material(key):
        try:
            ins = session.execute(
                _sql("INSERT INTO materials (materialname) VALUES (:n) RETURNING id"),
                {"n": key},
            ).first()
            mid = int(ins[0]) if ins else None
            session.flush()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[materials] get-or-create failed for {key!r}: {e}")
            mid = session.execute(
                _sql("SELECT id FROM materials WHERE materialname = :n LIMIT 1"), {"n": key},
            ).scalar()
    if mid is not None:
        _materials_cache[key] = mid
    return mid


def _log_unmapped_material(session, raw_value: str, position: str, product_id: Optional[int], sheet_source: Optional[str]) -> None:
    """Insert/bump unmapped_materials row. Never raises."""
    if not raw_value:
        return
    from sqlalchemy import text as _sql
    try:
        session.execute(
            _sql(
                """
                INSERT INTO unmapped_materials (raw_value, position, product_id, sheet_source)
                VALUES (:rv, :pos, :pid, :src)
                ON CONFLICT (raw_value, position) DO UPDATE
                  SET seen_count = unmapped_materials.seen_count + 1,
                      last_seen  = CURRENT_TIMESTAMP
                """
            ),
            {"rv": raw_value, "pos": position, "pid": product_id, "src": sheet_source},
        )
    except Exception:
        # Logging failures must never break the parser.
        pass


def _apply_product_materials(
    session,
    product_id: int,
    parsed: dict[str, list[str]],   # position → [raw lowercased names]
    sheet_source: Optional[str] = None,
) -> None:
    """
    Replace materials for a product (only for positions where parsed has a non-empty list).
    Empty/missing position = leave existing rows untouched (so partial sheet updates don't wipe data).
    Unknown names → unmapped_materials log; row not inserted.
    """
    if not parsed:
        return
    from sqlalchemy import text as _sql
    # Поважаємо інлайн-локи: позиції, відредаговані в програмі (`material_<pos>` у
    # manually_edited_fields), парсер НЕ перезаписує (sheet-write-back тримає аркуш
    # синхронним, а тут — захист від відкату до старого значення аркуша).
    mef_row = session.execute(
        _sql("SELECT manually_edited_fields FROM products WHERE id = :pid"),
        {"pid": product_id},
    ).first()
    mef = (mef_row[0] if mef_row else "") or ""
    locked_positions = {
        x.strip()[len("material_"):]
        for x in mef.split(",")
        if x.strip().startswith("material_")
    }
    for position, names in parsed.items():
        if position in locked_positions:
            continue  # відредаговано в програмі — не чіпаємо
        if not names:
            continue
        # Wipe existing rows for this (product, position), then re-insert in order.
        session.execute(
            _sql("DELETE FROM product_materials WHERE product_id = :pid AND position = :pos"),
            {"pid": product_id, "pos": position},
        )
        ord_idx = 0
        for nm in names:
            mid = _resolve_material_id(session, nm)
            if mid is None:
                _log_unmapped_material(session, nm, position, product_id, sheet_source)
                continue
            try:
                session.execute(
                    _sql(
                        """
                        INSERT INTO product_materials (product_id, position, material_id, ord)
                        VALUES (:pid, :pos, :mid, :ord)
                        ON CONFLICT (product_id, position, material_id) DO UPDATE SET ord = EXCLUDED.ord
                        """
                    ),
                    {"pid": product_id, "pos": position, "mid": mid, "ord": ord_idx},
                )
                ord_idx += 1
            except Exception as e:
                logger.warning(f"[materials] insert failed pid={product_id} pos={position} mid={mid}: {e}")


# Canonical letter sizes. Includes 4XL as XXXXL though rarely used.
_LETTER_SIZES_CANON = {"XS", "S", "M", "L", "XL", "XXL", "XXXL", "XXXXL", "XXXXXL", "XXXXXXL"}


def _normalize_size_letter(val: str) -> str:
    """Normalize letter size from the Sheet 'Буквений' column.

    Examples:
        'L'      → 'L'
        'xl'     → 'XL'
        '3XL'    → 'XXXL'   (3XL convention)
        '4XL'    → 'XXXXL'
        'М'      → 'M'      (Cyrillic М → Latin M)
        'XS/S'   → 'XS'     (multi-value: take first canonical token)
        'L (44)' → 'L'      (strip parenthetical numeric hints)
        ''       → ''
        '44'     → ''       (pure numeric → not a letter size)
    """
    if not val:
        return ""
    s = val.strip()
    if not s:
        return ""
    # Cyrillic → Latin (single char shortcut)
    s = s.replace("М", "M").replace("м", "m")
    # Strip parenthetical hints: "L (44)" → "L"
    s = re.sub(r"\s*\([^)]*\)", "", s).strip()
    if not s:
        return ""
    upper = s.upper()
    # 3XL → XXXL, 4XL → XXXXL
    m = re.match(r"^([2-9])\s*XL$", upper)
    if m:
        n = int(m.group(1))
        return "X" * n + "L"
    # Multi-value separators — take first canonical token
    if any(sep in upper for sep in ("/", ",", ";", " ")):
        for tok in re.split(r"[\s,/;]+", upper):
            cand = _normalize_size_letter(tok)
            if cand:
                return cand
        return ""
    return upper if upper in _LETTER_SIZES_CANON else ""


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


def _derive_eu_from_foreign_size(size_val: str, cm_val: str) -> tuple:
    """Деривує справжній EU-розмір, якщо в колонці «Розмір» лежить чужа
    (US/UK) нумерація замість європейської.

    Сигнал «чуже»: числовий розмір < 14 (взуттєвий EU стартує ~16 навіть для
    немовлят) ПЛЮС адекватна доросла довжина стопи 18–35 см у колонці «СМ».
    Реальні дитячі EU (cm<18) і дорослі EU (size≥14) НЕ чіпаємо; зіпсовані cm
    (напр. 350) відсікаються верхньою межею 35.

    EU = (см + 1.5) × 1.5, округлення до 0.5 (фізично-обґрунтовано, знімає
    неоднозначність US-чол/жін). Оригінальне число повертаємо як US-розмір.

    Повертає (eu_size_str, usa_size_str); usa порожній, якщо деривації не було.
    """
    if not size_val or not cm_val:
        return size_val, ""
    try:
        eu_num = float(size_val.replace(",", "."))
        cm_num = float(cm_val.replace(",", "."))
    except (ValueError, TypeError):
        return size_val, ""  # діапазони/нечислові — пропускаємо
    if eu_num >= 14 or not (18.0 <= cm_num <= 35.0):
        return size_val, ""
    derived = round(((cm_num + 1.5) * 1.5) * 2) / 2
    eu_str = str(int(derived)) if derived == int(derived) else str(derived)
    return eu_str, size_val


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
# Явна згадка єврозими в описі — додаємо мітку незалежно від типу
_EUROWINTER_DESC_KW = ('єврозим', 'євро зим', 'еврозим', 'евро зим')


# Canonical order in which seasons appear in the CSV string. Stable order makes
# downstream comparison / dedup predictable across the app.
SEASON_CANONICAL_ORDER = ('Зима', 'Єврозима', 'Демі', 'Літо', 'Всесезон')


def _classify_season(type_val: str, subtype_val: str, style_val: str,
                     description: str) -> str:
    """Auto-classify season(s) when not explicitly set in Sheet.

    Returns a comma-separated string of canonical seasons (e.g. 'Демі, Єврозима')
    or '' if nothing matched. Sport-keywords still collapse to a single
    'Всесезон'. Multiple distinct seasons can co-exist (description може
    згадувати і "демі" і "єврозима" — обидва присвоюємо).
    """
    desc = (description or '').lower()
    style = (style_val or '').lower()
    subtype = (subtype_val or '').lower()
    type_l = (type_val or '').lower()
    stsub_style = f'{subtype} {style}'
    all_text = f'{type_l} {subtype} {style} {desc}'

    # Sport short-circuit: одне значення, не змішується з іншими.
    if any(kw in all_text for kw in _SPORT_KW):
        return 'Всесезон'

    # Маскуємо "єврозим"/"евро зим" перед winter-перевіркою, щоб підстрока 'зим'
    # всередині 'єврозимі' не давала false-positive 'Зима'.
    desc_for_winter = re.sub(r'євро\s*зим\w*|евро\s*зим\w*', ' ', desc)

    detected: set[str] = set()
    if any(kw in desc_for_winter for kw in _WINTER_DESC_KW):
        detected.add('Зима')
    if any(kw in stsub_style for kw in _WINTER_STSUB_KW) or \
       any(kw in type_l for kw in _WINTER_STSUB_KW):
        detected.add('Зима')
    if any(kw in desc for kw in _SUMMER_KW) or \
       any(kw in stsub_style for kw in _SUMMER_KW) or \
       any(kw in type_l for kw in _SUMMER_TYPE_KW):
        detected.add('Літо')
    if any(kw in desc for kw in _DEMI_KW) or \
       any(kw in stsub_style for kw in _DEMI_KW):
        detected.add('Демі')
    if any(kw in type_l for kw in _EUROWINTER_TYPE_KW) or \
       any(kw in desc for kw in _EUROWINTER_DESC_KW) or \
       any(kw in stsub_style for kw in _EUROWINTER_DESC_KW):
        detected.add('Єврозима')

    if not detected:
        return 'Всесезон'

    ordered = [s for s in SEASON_CANONICAL_ORDER if s in detected]
    return ', '.join(ordered)


def normalize_season_csv(value: str) -> str:
    """Canonicalize a season value (from Sheet or DB): trim parts, dedupe,
    apply canonical ordering. Unknown tokens are preserved as-is (after trim)
    to avoid silently dropping legacy or custom values."""
    if not value:
        return ''
    parts = [p.strip() for p in value.split(',') if p.strip()]
    if not parts:
        return ''
    # Case-fold map for canonicals: 'літо' / 'ЛІТО' / 'Літо' → 'Літо'
    canon_by_lower = {c.lower(): c for c in SEASON_CANONICAL_ORDER}
    seen: set[str] = set()
    known: list[str] = []
    unknown: list[str] = []
    for p in parts:
        canon = canon_by_lower.get(p.lower())
        key = canon or p
        if key in seen:
            continue
        seen.add(key)
        if canon:
            known.append(canon)
        else:
            unknown.append(p)
    ordered = [s for s in SEASON_CANONICAL_ORDER if s in known]
    return ', '.join(ordered + unknown)


def _normalize_gender(val: str) -> str:
    """Normalize gender value: case-insensitive mapping to canonical form.

    'жіноча' / 'жіночий' / 'Жіноча' → 'Жіноча'
    'чоловіча' / 'чоловічий' → 'Чоловіча'
    'унісекс' → 'Унісекс'
    Garbage (e.g. 'чорний, сірий...') → '' (empty = skip)
    """
    # Єдине джерело правди — backend/utils/gender_normalizer (canonical 3 + prefix-
    # фолбек на будь-яке закінчення). Локальний _GENDER_MAP лишено для сумісності,
    # але резолв робить util, щоб «Жіночі»/«Чоловіче»/… не плодили дублі статей.
    try:
        from utils.gender_normalizer import normalize_gender
    except ImportError:
        from backend.utils.gender_normalizer import normalize_gender
    return normalize_gender(val)


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


# ── Style auto-detection from description ────────────────────────────────────
# Звужений набір закінчень українських прикметників — щоб не захопити
# випадкові іменники у конструкціях типу "одягу стиль".
_STYLE_ADJ_SUFFIXES = (
    "ський", "цький", "чний", "зний", "тний", "вний", "шний",
    "жний", "льний", "рний", "мний", "нний", "овий", "евий",
    "ий", "ій",
)
_STYLE_ADJ_RE = re.compile(
    r"(?<![А-ЯҐЄІЇа-яґєії])"
    r"([А-ЯҐЄІЇа-яґєії][а-яґєії’'\-]{3,20})"
    r"\s+стил[ьюіея]\b",
    re.IGNORECASE,
)
_STYLE_QUOTED_RE = re.compile(
    r"стил[ьюіея]\s*[«\"„‘']\s*"
    r"([А-ЯҐЄІЇа-яґєії][а-яґєії’'\-]{2,25})"
    r"\s*[»\"”’']",
    re.IGNORECASE,
)


def _auto_detect_style(desc_val: str) -> str:
    """Витягнути стиль із опису, якщо в аркуші "Стиль" порожній.

    Дві форми:
      • прикметник + "стиль": "англійський стиль" → "Англійський"
      • "стиль" + цитата:     'стиль "кантрі"'    → "Кантрі"
    Прикметникова гілка обмежена українськими закінченнями (-ий, -ій,
    -ський, -чний тощо), щоб не захопити випадковий іменник.
    """
    if not desc_val:
        return ""
    text = desc_val.strip()

    for match in _STYLE_ADJ_RE.finditer(text):
        word = match.group(1).lower().rstrip("'’-")
        if any(word.endswith(suf) for suf in _STYLE_ADJ_SUFFIXES):
            return word[:1].upper() + word[1:]

    m = _STYLE_QUOTED_RE.search(text)
    if m:
        word = m.group(1).strip().lower().rstrip("'’-")
        if len(word) >= 3:
            return word[:1].upper() + word[1:]

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
    # \b після «пром» НЕ матчить «промокод» (там далі літера) — лише окреме слово/prom.ua
    (re.compile(r'\b(?:prom|пром)\b', re.IGNORECASE), 'Prom'),
    (re.compile(r'\b(?:mono|моно)\b', re.IGNORECASE), 'MONO'),
    (re.compile(r'\b(?:ct|cg|catalog)\b', re.IGNORECASE), 'Каталог'),
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


_ADVERTISING_EXPENSE_LABEL = "витрати на рекламу"


def _parse_nonnegative_money(raw) -> Optional[float]:
    """Parse a Sheets money cell without depending on its visual currency format."""
    from decimal import Decimal, InvalidOperation

    text_value = str(raw or "").strip()
    if not text_value:
        return None

    match = re.search(r"-?[\d\s\u00a0.,]+", text_value)
    if not match:
        return None
    token = match.group(0).replace(" ", "").replace("\u00a0", "")

    # Support both Ukrainian 1.234,56 and English 1,234.56 formatting.
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        token = token.replace(",", ".")

    try:
        amount = Decimal(token)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return float(amount)


def _extract_advertising_expense(rows: list) -> dict:
    """Read the exact summary label and the value directly below it.

    Free-text mentions such as ``500 грн на рекламу`` inside order comments are
    intentionally ignored: only a cell equal to ``Витрати на рекламу`` is a
    source-of-truth expense.
    """
    matches: list[tuple[int, int]] = []
    for row_idx, row in enumerate(rows):
        for col_idx, cell in enumerate(row):
            if str(cell or "").strip().casefold() == _ADVERTISING_EXPENSE_LABEL:
                matches.append((row_idx, col_idx))

    if not matches:
        return {"found": False, "amount": None, "label_cell": None, "value_cell": None}

    if len(matches) > 1:
        logger.warning("Multiple 'Витрати на рекламу' blocks found; using the first")

    row_idx, col_idx = matches[0]
    raw_value = ""
    if row_idx + 1 < len(rows) and col_idx < len(rows[row_idx + 1]):
        raw_value = rows[row_idx + 1][col_idx]

    return {
        "found": True,
        "amount": _parse_nonnegative_money(raw_value),
        "label_cell": gspread.utils.rowcol_to_a1(row_idx + 1, col_idx + 1),
        "value_cell": gspread.utils.rowcol_to_a1(row_idx + 2, col_idx + 1),
    }


def _sync_advertising_expense(
    session: Session,
    ws: gspread.Worksheet,
    sheet_date: date,
    rows: list,
) -> Optional[float]:
    """Mirror one sheet-level advertising expense without creating duplicates."""
    extracted = _extract_advertising_expense(rows)
    source = {"sid": ORDERS_ID, "gid": int(ws.id)}

    if not extracted["found"]:
        session.execute(text("""
            DELETE FROM advertising_expenses
            WHERE source_spreadsheet_id = :sid AND source_sheet_gid = :gid
        """), source)
        return None

    amount = extracted["amount"]
    if amount is None:
        logger.warning(
            "Advertising expense has an invalid/empty value: sheet=%s cell=%s",
            ws.title, extracted["value_cell"],
        )
        return None

    session.execute(text("""
        INSERT INTO advertising_expenses (
            expense_date, amount, sales_channel,
            source_spreadsheet_id, source_sheet_gid, source_sheet_title,
            source_label_cell, source_value_cell, updated_at
        ) VALUES (
            :expense_date, :amount, 'Ефір',
            :sid, :gid, :title, :label_cell, :value_cell, NOW()
        )
        ON CONFLICT (source_spreadsheet_id, source_sheet_gid) DO UPDATE SET
            expense_date = EXCLUDED.expense_date,
            amount = EXCLUDED.amount,
            sales_channel = EXCLUDED.sales_channel,
            source_sheet_title = EXCLUDED.source_sheet_title,
            source_label_cell = EXCLUDED.source_label_cell,
            source_value_cell = EXCLUDED.source_value_cell,
            updated_at = NOW()
    """), {
        **source,
        "expense_date": sheet_date,
        "amount": amount,
        "title": ws.title[:255],
        "label_cell": extracted["label_cell"],
        "value_cell": extracted["value_cell"],
    })
    return amount


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
                             delivery_cost: float = 0.0,
                             sheet_gid: Optional[int] = None) -> Optional[int]:
    """Get or create a delivery record for a sheet. Returns delivery ID.

    Dedup priority:
      1. sheet_gid (Google's stable tab ID) — rename-safe: перейменування
         вкладки оновлює deliveryname існуючого завозу, а не плодить дубль
         (раніше дедуп лише за назвою → rename створював порожній завіз-сироту).
      2. deliveryname (fallback для legacy-завозів з NULL gid) — на збігу
         backfill-имо gid, щоб наступні rename'и вже ловились за gid.
    purchase_cost and delivery_cost are parsed from the sheet's info block.
    """
    if not sheet_name:
        return None
    from sqlalchemy import text

    def _name_taken_by_other(name: str, self_id: int) -> bool:
        return bool(session.execute(
            text("SELECT 1 FROM deliveries WHERE deliveryname = :n AND id <> :id"),
            {"n": name, "id": self_id}
        ).fetchone())

    row = None
    if sheet_gid is not None:
        row = session.execute(
            text("SELECT id, deliveryname FROM deliveries WHERE sheet_gid = :gid"),
            {"gid": sheet_gid}
        ).fetchone()
    if row:
        # gid-match: оновити назву (rename вкладки) лише якщо вона вільна —
        # інакше лишити стару, щоб не впертись у UNIQUE(deliveryname).
        new_name = sheet_name if (sheet_name != row[1] and not _name_taken_by_other(sheet_name, row[0])) else row[1]
        if new_name != row[1]:
            logger.info(f"[shipment] gid={sheet_gid}: rename '{row[1]}' → '{new_name}'")
        session.execute(
            text("""UPDATE deliveries
                    SET deliveryname = :nn,
                        deliverydate = COALESCE(:sd, deliverydate),
                        supplier_id = :sid,
                        purchase_cost = CASE WHEN :pc > 0 THEN :pc ELSE purchase_cost END,
                        delivery_cost = CASE WHEN :dc > 0 THEN :dc ELSE delivery_cost END
                    WHERE id = :id"""),
            {"nn": new_name, "sd": shipment_date, "sid": supplier_id,
             "pc": purchase_cost, "dc": delivery_cost, "id": row[0]}
        )
        session.flush()
        return row[0]

    row = session.execute(
        text("SELECT id FROM deliveries WHERE deliveryname = :sn"),
        {"sn": sheet_name}
    ).fetchone()
    if row:
        # name-match: backfill gid (rename-safe з наступного прогону) + оновити costs.
        session.execute(
            text("""UPDATE deliveries
                    SET supplier_id = :sid,
                        sheet_gid = COALESCE(sheet_gid, :gid),
                        purchase_cost = CASE WHEN :pc > 0 THEN :pc ELSE purchase_cost END,
                        delivery_cost = CASE WHEN :dc > 0 THEN :dc ELSE delivery_cost END
                    WHERE id = :id"""),
            {"sid": supplier_id, "gid": sheet_gid, "pc": purchase_cost, "dc": delivery_cost, "id": row[0]}
        )
        session.flush()
        return row[0]

    row = session.execute(
        text("""INSERT INTO deliveries (deliveryname, deliverydate, supplier_id, purchase_cost, delivery_cost, sheet_gid)
                VALUES (:sn, :sd, :sid, :pc, :dc, :gid) RETURNING id"""),
        {"sn": sheet_name, "sd": shipment_date, "sid": supplier_id,
         "pc": purchase_cost, "dc": delivery_cost, "gid": sheet_gid}
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

        # Step 3: Normal lookup by normalized key — кешовано (див. _ref_cache_table).
        # Ключ кешу окремий ('brands:normkey'), бо порівняння тут через
        # _normalize_brand_key, а не .lower() як у загальній гілці нижче.
        btbl = _ref_cache_table(
            "brands:normkey",
            lambda: ((_normalize_brand_key(getattr(r, "brandname", None) or ""), r.id)
                     for r in session.query(Brand).all()),
        )
        rid = btbl.get(val_key)
        if rid is not None:
            row = session.query(Brand).get(rid)
            if row is not None:
                return row
            btbl.pop(val_key, None)  # відкочено/видалено → створити заново нижче
        obj = Brand(brandname=value, normalized_name=val_key)
        session.add(obj)
        session.flush()
        btbl[val_key] = obj.id
        return obj

    val_lower = value.lower()
    # Кешований лукап (замість повнотабличного скану на кожен товар). Валідація
    # через session.query().get(): стале id (rollback/видалення) → міс → створення.
    gtbl = _ref_cache_table(
        getattr(model, "__tablename__", model.__name__),
        lambda: (((getattr(r, unique_field, None) or "").strip().lower(), r.id)
                 for r in session.query(model).all()),
    )
    rid = gtbl.get(val_lower)
    if rid is not None:
        row = session.query(model).get(rid)
        if row is not None:
            return row
        gtbl.pop(val_lower, None)
    # Описові довідники (sole_types, toe_shapes, ...) канон у БД з малої літери.
    # WKWebView капіталізує перше слово автоматично — нормалізуємо, щоб не плодити
    # «Шнурівка» поряд із «шнурівка». Бренди/типи/стать тримаємо як ввів користувач.
    try:
        from backend.services.product_service import LOWERCASE_LOOKUP_TABLES
    except ImportError:
        try:
            from services.product_service import LOWERCASE_LOOKUP_TABLES
        except ImportError:
            LOWERCASE_LOOKUP_TABLES = set()
    insert_val = value
    tname = getattr(model, "__tablename__", "")
    if tname in LOWERCASE_LOOKUP_TABLES and insert_val[:1].isupper():
        insert_val = insert_val[0].lower() + insert_val[1:]
    obj = model(**{unique_field: insert_val})
    session.add(obj)
    session.flush()
    # Занести новостворений рядок у кеш — щоб наступні товари цього ж прогону
    # знайшли його без повторного скану (ключ той самий value.lower()).
    gtbl[val_lower] = obj.id

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
        # Canonicalize: leading #, uppercase known prefix, Latin homoglyphs
        # (T642) folded to Cyrillic (Т642). Empty string if normalize() rejected.
        pnum = _normalize_pnum(pnum) or pnum

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
        # Габарити (сумки/валізи/рюкзаки): "22x16x7". Журнальний парсер раніше це поле
        # ІГНОРУВАВ (читав лише workspace) → усі журнальні сумки лишались без габаритів.
        dimensions_val = col(row, "Габарити").strip() if "Габарити" in header else ""
        mfr_cntry  = col(row, "Країна-виробник")
        own_cntry  = col(row, "Країна-власник")
        size_val   = _normalize_size(col(row, "Розмір"))
        # Letter size (XS/S/M/L/XL/...) — нова опційна колонка "Буквений" одразу після "Розмір".
        # Старі аркуші без неї повертають "" → size_letter = NULL.
        letter_val = _normalize_size_letter(col(row, "Буквений")) if "Буквений" in header else ""
        # Safety: якщо Буквений порожній, але Розмір — чиста літера,
        # маршрутизуємо літеру в size_letter і обнуляємо sizeeu (інакше "L" поллється у sizeeu).
        if not letter_val and size_val:
            _letter_from_size = _normalize_size_letter(size_val)
            if _letter_from_size and not re.search(r"\d", size_val):
                letter_val = _letter_from_size
                size_val   = ""
        cm_val     = _normalize_size(col(row, "СМ"))
        # Чужа (US/UK) нумерація в колонці «Розмір» → деривуємо EU зі СМ,
        # оригінал зберігаємо як US (usa_val). Реальні EU не чіпаються.
        size_val, usa_val = _derive_eu_from_foreign_size(size_val, cm_val)
        price_val  = col(row, "Ціна")
        oldprice_val = col(row, "Стара ціна") if "Стара ціна" in header else ""
        desc_val   = col(row, "Опис") if "Опис" in header else ""
        # Нові колонки 2026-06-10 (опційні — старі/інші аркуші можуть їх не мати).
        # Аркуш авторитетний: порожня клітинка → NULL (див. feedback_sheet_empty_clears_field);
        # in-app правки захищені snapshot-restore локами (PRODUCT_LOCK_FIELDS).
        collection_val  = col(row, "Колекція").strip() if "Колекція" in header else ""
        gtin_val        = col(row, "GTIN").strip() if "GTIN" in header else ""
        geom_shape_val  = col(row, "Геометрична форма").strip() if "Геометрична форма" in header else ""
        # Опційні розширені колонки (старі аркуші можуть їх не мати)
        season_val       = col(row, "Сезон").strip() if "Сезон" in header else ""
        style_val        = col(row, "Стиль").strip() if "Стиль" in header else ""
        current_cond_val = col(row, "Поточний стан").strip() if "Поточний стан" in header else ""
        # Нові колонки замірів (одяг: довжина, ПОГ, ПОБ, ПОТ, рукав; взуття: висота, товщина підошви)
        length_val          = col(row, "Довжина").strip()         if "Довжина" in header else ""
        pog_val             = col(row, "Груди (н/о)").strip()     if "Груди (н/о)" in header else ""
        pob_val             = col(row, "Бедра (н/о)").strip()     if "Бедра (н/о)" in header else ""
        pot_val             = col(row, "Талія (н/о)").strip()     if "Талія (н/о)" in header else ""
        sleeve_val          = col(row, "Рукав").strip()           if "Рукав" in header else ""
        height_val          = col(row, "Висота").strip()          if "Висота" in header else ""
        sole_thickness_val  = col(row, "Товщина підошви").strip() if "Товщина підошви" in header else ""
        heel_val            = col(row, "Підбор").strip()          if "Підбор" in header else ""

        # СМ — числовий range alongside legacy TEXT (cm_val above).
        cm_min, cm_max                 = _parse_measurement_range(cm_val)
        length_min, length_max         = _parse_measurement_range(length_val)
        pog_min, pog_max               = _parse_measurement_range(pog_val)
        pob_min, pob_max               = _parse_measurement_range(pob_val)
        pot_min, pot_max               = _parse_measurement_range(pot_val)
        sleeve_min, sleeve_max         = _parse_measurement_range(sleeve_val)
        height_min, height_max         = _parse_measurement_range(height_val)
        sole_thickness_min, sole_thickness_max = _parse_measurement_range(sole_thickness_val)
        heel_min, heel_max                     = _parse_measurement_range(heel_val)

        # Shoe-specific lookups (single FK each). Unknown values → unmapped log, FK stays NULL.
        resolved_shoe_fk: dict[str, Optional[int]] = {}
        for sheet_col, (tbl, name_col, fk_col) in SHOE_LOOKUP_COLUMNS.items():
            if sheet_col not in header:
                continue
            raw_val = col(row, sheet_col).strip()
            if not raw_val:
                continue
            rid = _resolve_shoe_lookup_id(session, tbl, name_col, raw_val)
            if rid is None:
                # Reuse unmapped_materials infra with position prefix to avoid a new table.
                _log_unmapped_material(session, raw_val, f"_{fk_col}", None, ws.title)
                continue
            resolved_shoe_fk[fk_col] = rid

        # "Колір підошви" → reuse colors (auto-create нові кольори, як основний колір).
        sole_color_raw = col(row, "Колір підошви").strip() if "Колір підошви" in header else ""
        sole_color_obj = _get_or_create(session, Color, "colorname", sole_color_raw) if sole_color_raw else None
        sole_color_id  = sole_color_obj.id if sole_color_obj else None

        # Bare aliases для Product(...)-конструкторів CREATE-гілок (рефакторинг на
        # resolved_shoe_fk не оновив їх → NameError при створенні взуття; file-gate
        # маскував це, бо products-парс рідко реально виконувався). Визначаємо ОДИН раз.
        sole_type_id      = resolved_shoe_fk.get("soletypeid")
        toe_shape_id      = resolved_shoe_fk.get("toeshapeid")
        fastening_type_id = resolved_shoe_fk.get("fasteningtypeid")
        lining_id         = resolved_shoe_fk.get("liningid")

        # Collect all new-style fields into one dict for UPDATE-branch helpers.
        parsed_new_fields = {
            "measurementscm_min":      cm_min,             "measurementscm_max":      cm_max,
            "measurements_length_min": length_min,         "measurements_length_max": length_max,
            "measurements_pog_min":    pog_min,            "measurements_pog_max":    pog_max,
            "measurements_pob_min":    pob_min,            "measurements_pob_max":    pob_max,
            "measurements_pot_min":    pot_min,            "measurements_pot_max":    pot_max,
            "measurements_sleeve_min": sleeve_min,         "measurements_sleeve_max": sleeve_max,
            "measurements_height_min": height_min,         "measurements_height_max": height_max,
            "measurements_sole_thickness_min": sole_thickness_min,
            "measurements_sole_thickness_max": sole_thickness_max,
            "measurements_heel_min":   heel_min,           "measurements_heel_max":   heel_max,
            "soletypeid":              resolved_shoe_fk.get("soletypeid"),
            "toeshapeid":              resolved_shoe_fk.get("toeshapeid"),
            "fasteningtypeid":         resolved_shoe_fk.get("fasteningtypeid"),
            "liningid":                resolved_shoe_fk.get("liningid"),
            "heeltypeid":              resolved_shoe_fk.get("heeltypeid"),
            "lacetypeid":              resolved_shoe_fk.get("lacetypeid"),
            "packagingid":             resolved_shoe_fk.get("packagingid"),
            "technologyid":            resolved_shoe_fk.get("technologyid"),
            "sole_colorid":            sole_color_id,
        }

        # Матеріали: збираємо за позиціями. Порожня клітинка = не чіпати існуюче в БД.
        materials_parsed: dict[str, list[str]] = {}
        for sheet_col, position in MATERIAL_POSITIONS.items():
            if sheet_col in header:
                raw_mat = col(row, sheet_col).strip()
                if raw_mat:
                    parts = _split_materials_cell(raw_mat)
                    if parts:
                        materials_parsed[position] = parts

        # ── Resolve FK refs ────────────────────────────────────────────────
        # Guard: split combined types ("Туфлі/кросівки", "Ботинки-челсі") → Type + Subtype
        if type_val:
            t_part, st_part = _split_combined_type(type_val)
            type_val = t_part
            if st_part and not sub_val:
                sub_val = st_part

        # Auto-detect style from description if Sheet "Стиль" is empty.
        # Strict pattern (adjective + "стиль", or quoted form), щоб не зачепити сміття.
        if not style_val and desc_val:
            style_val = _auto_detect_style(desc_val)

        # Auto-classify season if not explicitly set in Sheet.
        # Якщо Sheet вже має — канонізуємо ('Літо, демі' → 'Демі, Літо').
        if not season_val:
            season_val = _classify_season(type_val, sub_val, style_val, desc_val)
        else:
            season_val = normalize_season_csv(season_val)

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

        oldprice_float = None
        if oldprice_val and oldprice_val.strip():
            try:
                oldprice_float = float(oldprice_val.replace(",", "."))
            except ValueError:
                oldprice_float = None

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
            if letter_val and not full_match.size_letter:
                full_match.size_letter = letter_val
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
            # clonednumbers: аркуш (колонка "Номера-клони") — ЄДИНЕ джерело істини.
            # Раніше було `if clones: set` (NULL-only update) — коли клітинку в
            # аркуші спорожнювали, привидні клони лишались у БД назавжди
            # (напр. Ф3883 тримав 'В49; X3; В48', яких в аркуші немає).
            # Тепер порожня клітинка → NULL. Іншого джерела clonednumbers нема
            # (_append_clone — мертвий код), тож це безпечно.
            full_match.clonednumbers = clones or None
            if cm_val:
                full_match.measurementscm = cm_val
            if dimensions_val:
                full_match.dimensions = dimensions_val
            # Колекція/GTIN/Геометрична форма: аркуш авторитетний (порожнє → NULL),
            # але лише коли колонка реально існує в цьому аркуші.
            if "Колекція" in header:
                full_match.collection = collection_val or None
            if "GTIN" in header:
                full_match.gtin = gtin_val or None
            if "Геометрична форма" in header:
                full_match.geometric_shape = geom_shape_val or None
            # Ціна синкається з аркуша. oldprice — строго з колонки "Стара ціна"
            # (порожня клітинка = NULL); ніякої авто-деривації з попередньої ціни.
            if price_float:
                full_match.price = price_float
            full_match.oldprice = oldprice_float
            full_match.updated_at = datetime.utcnow()
            # Вкладка аркуша = завіз. Якщо рядок перенесли в іншу вкладку (розділили
            # завіз навпіл, перейменували), товар МУСИТЬ переїхати за ним. Раніше тут
            # стояло `not full_match.deliveryid`: прив'язка робилась ОДИН раз назавжди,
            # тож після розділення вкладки товари лишались у старому завозі, а новий
            # показував «0 товарів» — і жоден повторний парсинг цього не лікував.
            # Прив'язуємо на ПЕРШІЙ появі в прогоні (аркуші йдуть від найновішого),
            # тож товар, наявний у кількох вкладках, осідає в найсвіжішій.
            if shipment_id and (cnt == 1 or not full_match.deliveryid):
                full_match.deliveryid = shipment_id
            # Нові поля (measurements/lookups/materials) — NULL-only update,
            # materials = full-replace тільки для непорожніх позицій з аркуша.
            _apply_new_fields_and_materials(
                session, full_match,
                new_fields=parsed_new_fields,
                materials_parsed=materials_parsed,
                source=ws.title,
            )
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
            #
            # ⚠️ The candidate MUST already carry a genuine orphan number pattern
            # (NULL / '???' / '???_…' / '__tmp_rename_…'). Without this guard the
            # query reclaims a REAL, fully-numbered product that merely shares
            # marking+brand+size — e.g. ONLY #Ф3425 «ONLYTRACY BONDED BRIEF NOOS
            # 3-PK» and #Ф3431 «TRACY BONDED BRIEF» both have marking 15211634,
            # brand ONLY, no numeric size. That collapsed two distinct items onto
            # one row whose number ping-ponged #Ф3425↔#Ф3431 every run, so one of
            # them vanished from search. (model differs but wasn't compared.)
            orphan = None
            if marking and brand_id:
                orphan = session.query(Product).filter(
                    Product.marking == marking,
                    Product.brandid == brand_id,
                    Product.sizeeu == (size_val or None),
                    Product.productnumber.notlike(f"{base_pnum}%"),
                    or_(
                        Product.productnumber.is_(None),
                        Product.productnumber == '???',
                        Product.productnumber.like('???\\_%'),
                        Product.productnumber.like('\\_\\_tmp\\_rename\\_%'),
                    ),
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
                if "Колекція" in header:
                    orphan.collection = collection_val or None
                if "GTIN" in header:
                    orphan.gtin = gtin_val or None
                if "Геометрична форма" in header:
                    orphan.geometric_shape = geom_shape_val or None
                if price_float:
                    orphan.price = price_float
                # Реклейм сироти в ЦЕЙ аркуш → і завіз має бути цього аркуша.
                # (Див. коментар у Case 1 про «вкладка = завіз».)
                if shipment_id and (orphan.id not in seen_in_run or not orphan.deliveryid):
                    orphan.deliveryid = shipment_id
                orphan.updated_at = datetime.utcnow()
                _apply_new_fields_and_materials(
                    session, orphan,
                    new_fields=parsed_new_fields,
                    materials_parsed=materials_parsed,
                    source=ws.title,
                )
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
                    dimensions            = dimensions_val or None,
                    geometric_shape       = geom_shape_val or None,
                    clonednumbers         = clones or None,
                    model                 = model_val or None,
                    collection            = collection_val or None,
                    marking               = marking or None,
                    gtin                  = gtin_val or None,
                    year                  = year_int,
                    description           = desc_val or None,
                    price                 = price_float,
                    oldprice              = oldprice_float,
                    sizeeu                = size_val or None,
                    sizeusa               = usa_val or None,
                    size_letter           = letter_val or None,
                    measurementscm        = cm_val or None,
                    measurementscm_min              = cm_min,
                    measurementscm_max              = cm_max,
                    measurements_length_min         = length_min,
                    measurements_length_max         = length_max,
                    measurements_pog_min            = pog_min,
                    measurements_pog_max            = pog_max,
                    measurements_pob_min            = pob_min,
                    measurements_pob_max            = pob_max,
                    measurements_pot_min            = pot_min,
                    measurements_pot_max            = pot_max,
                    measurements_sleeve_min         = sleeve_min,
                    measurements_sleeve_max         = sleeve_max,
                    measurements_height_min         = height_min,
                    measurements_height_max         = height_max,
                    measurements_sole_thickness_min = sole_thickness_min,
                    measurements_sole_thickness_max = sole_thickness_max,
                    measurements_heel_min           = heel_min,
                    measurements_heel_max           = heel_max,
                    soletypeid                      = sole_type_id,
                    toeshapeid                      = toe_shape_id,
                    fasteningtypeid                 = fastening_type_id,
                    liningid                        = lining_id,
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
                    _apply_product_materials(session, product.id, materials_parsed, ws.title)
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
                        _apply_new_fields_and_materials(
                            session, existing_now,
                            new_fields=parsed_new_fields,
                            materials_parsed=materials_parsed,
                            source=ws.title,
                        )
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
                    dimensions            = dimensions_val or None,
                    geometric_shape       = geom_shape_val or None,
                    clonednumbers         = clones or None,
                    model                 = model_val or None,
                    collection            = collection_val or None,
                    marking               = marking or None,
                    gtin                  = gtin_val or None,
                    year                  = year_int,
                    description           = desc_val or None,
                    price                 = price_float,
                    oldprice              = oldprice_float,
                    sizeeu                = size_val or None,
                    sizeusa               = usa_val or None,
                    size_letter           = letter_val or None,
                    measurementscm        = cm_val or None,
                    measurementscm_min              = cm_min,
                    measurementscm_max              = cm_max,
                    measurements_length_min         = length_min,
                    measurements_length_max         = length_max,
                    measurements_pog_min            = pog_min,
                    measurements_pog_max            = pog_max,
                    measurements_pob_min            = pob_min,
                    measurements_pob_max            = pob_max,
                    measurements_pot_min            = pot_min,
                    measurements_pot_max            = pot_max,
                    measurements_sleeve_min         = sleeve_min,
                    measurements_sleeve_max         = sleeve_max,
                    measurements_height_min         = height_min,
                    measurements_height_max         = height_max,
                    measurements_sole_thickness_min = sole_thickness_min,
                    measurements_sole_thickness_max = sole_thickness_max,
                    measurements_heel_min           = heel_min,
                    measurements_heel_max           = heel_max,
                    soletypeid                      = sole_type_id,
                    toeshapeid                      = toe_shape_id,
                    fasteningtypeid                 = fastening_type_id,
                    liningid                        = lining_id,
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
                    _apply_product_materials(session, product.id, materials_parsed, ws.title)
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
                        _apply_new_fields_and_materials(
                            session, existing_now,
                            new_fields=parsed_new_fields,
                            materials_parsed=materials_parsed,
                            source=ws.title,
                        )
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
                    # Артикул виробника — ідентифікатор ФІЗИЧНОГО товару, сильніший
                    # за назву бренду. Той самий артикул під тим самим номером лота =
                    # той самий черевик, у якого просто ВІДРЕДАГУВАЛИ бренд у журналі
                    # (напр. 'Emporio Armani' → 'Armani Exchange' — суббренди одного
                    # дому, які легко переплутати при записі). Без цієї перевірки
                    # кожне таке уточнення бренду плодило фантомний '-2'.
                    # Та сама логіка, що вже діє для кольору в _is_color_variant.
                    db_mark = (p.marking or "").strip().upper()
                    sheet_mark = (marking or "").strip().upper()
                    if db_mark and sheet_mark and db_mark == sheet_mark:
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
                            dimensions            = dimensions_val or None,
                            geometric_shape       = geom_shape_val or None,
                            clonednumbers         = clones or None,
                            model                 = model_val or None,
                            collection            = collection_val or None,
                            marking               = marking or None,
                            gtin                  = gtin_val or None,
                            year                  = year_int,
                            description           = desc_val or None,
                            price                 = price_float,
                            oldprice              = oldprice_float,
                            sizeeu                = size_val or None,
                            sizeusa               = usa_val or None,
                            size_letter           = letter_val or None,
                            measurementscm        = cm_val or None,
                            measurements_length_min         = length_min,
                            measurements_length_max         = length_max,
                            measurements_pog_min            = pog_min,
                            measurements_pog_max            = pog_max,
                            measurements_pob_min            = pob_min,
                            measurements_pob_max            = pob_max,
                            measurements_pot_min            = pot_min,
                            measurements_pot_max            = pot_max,
                            measurements_sleeve_min         = sleeve_min,
                            measurements_sleeve_max         = sleeve_max,
                            measurements_height_min         = height_min,
                            measurements_height_max         = height_max,
                            measurements_sole_thickness_min = sole_thickness_min,
                            measurements_sole_thickness_max = sole_thickness_max,
                            measurements_heel_min           = heel_min,
                            measurements_heel_max           = heel_max,
                            soletypeid                      = sole_type_id,
                            toeshapeid                      = toe_shape_id,
                            fasteningtypeid                 = fastening_type_id,
                            liningid                        = lining_id,
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
                            _apply_product_materials(session, product.id, materials_parsed, ws.title)
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
                                _apply_new_fields_and_materials(
                                    session, existing_now,
                                    new_fields=parsed_new_fields,
                                    materials_parsed=materials_parsed,
                                    source=ws.title,
                                )
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
                    if letter_val: target.size_letter = letter_val
                    if sub_id:    target.subtypeid   = sub_id
                    if gender_id: target.genderid    = gender_id
                    # Update data fields
                    if marking:   target.marking     = marking
                    if model_val: target.model       = model_val
                    if desc_val:  target.description = desc_val
                    if cm_val:    target.measurementscm = cm_val
                    if year_int is not None: target.year = year_int
                    if price_float:
                        target.price = price_float
                    target.oldprice = oldprice_float
                    if cnt == 1:
                        target.statusid = status_id
                    elif status_id == sold_status_id:
                        target.statusid = status_id
                    target.updated_at = datetime.utcnow()
                    # Див. коментар у Case 1: вкладка = завіз, товар їде за рядком.
                    if shipment_id and (cnt == 1 or not target.deliveryid):
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
                        dimensions            = dimensions_val or None,
                        geometric_shape       = geom_shape_val or None,
                        clonednumbers         = clones or None,
                        model                 = model_val or None,
                        collection            = collection_val or None,
                        marking               = marking or None,
                        gtin                  = gtin_val or None,
                        year                  = year_int,
                        description           = desc_val or None,
                        price                 = price_float,
                        oldprice              = oldprice_float,
                        sizeeu                = size_val or None,
                        sizeusa               = usa_val or None,
                        size_letter           = letter_val or None,
                        measurementscm        = cm_val or None,
                        measurements_length_min         = length_min,
                        measurements_length_max         = length_max,
                        measurements_pog_min            = pog_min,
                        measurements_pog_max            = pog_max,
                        measurements_pob_min            = pob_min,
                        measurements_pob_max            = pob_max,
                        measurements_pot_min            = pot_min,
                        measurements_pot_max            = pot_max,
                        measurements_sleeve_min         = sleeve_min,
                        measurements_sleeve_max         = sleeve_max,
                        measurements_height_min         = height_min,
                        measurements_height_max         = height_max,
                        measurements_sole_thickness_min = sole_thickness_min,
                        measurements_sole_thickness_max = sole_thickness_max,
                        measurements_heel_min           = heel_min,
                        measurements_heel_max           = heel_max,
                        soletypeid                      = sole_type_id,
                        toeshapeid                      = toe_shape_id,
                        fasteningtypeid                 = fastening_type_id,
                        liningid                        = lining_id,
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
                        _apply_product_materials(session, product.id, materials_parsed, ws.title)
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
                            _apply_new_fields_and_materials(
                                session, existing_now,
                                new_fields=parsed_new_fields,
                                materials_parsed=materials_parsed,
                                source=ws.title,
                            )
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
    # Кешований лукап (subtypes=173 рядки × товари = зайвий скан). Матч ЛИШЕ за
    # subtypename (як і раніше — typeid тут не в умові пошуку, лише при вставці).
    stbl = _ref_cache_table(
        "subtypes",
        lambda: ((str(r[1]).strip().lower(), r[0])
                 for r in session.execute(text("SELECT id, subtypename FROM subtypes")).fetchall()
                 if r[1]),
    )
    rid = stbl.get(name_lower)
    if rid is not None:
        exists = session.execute(
            text("SELECT 1 FROM subtypes WHERE id = :i"), {"i": rid}
        ).fetchone()
        if exists:
            return rid
        stbl.pop(name_lower, None)  # відкочено/видалено → створити заново
    row = session.execute(
        text("INSERT INTO subtypes (subtypename, typeid) VALUES (:n, :t) RETURNING id"),
        {"n": name, "t": type_id}
    ).fetchone()
    session.flush()
    if row:
        stbl[name_lower] = row[0]
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

    # ── Захист від сміття в соц-полях ────────────────────────────────────────
    # У стовпцях аркуша (напр. "Olx") інколи трапляються чужі дані: ціни
    # ("1250;"), голі числа, телефони. Вебсоц-хендл/URL ЗАВЖДИ містить літери
    # або є посиланням — голе число туди потрапити не може. Тож відкидаємо
    # значення без жодної літери й без http(s) для olx/facebook/instagram/telegram.
    # Viber (телефон) та phone — НЕ чіпаємо.
    def _is_garbage_social(val: str) -> bool:
        v = (val or "").strip()
        if not v:
            return False
        if v.startswith(("http://", "https://")):
            return False
        if re.search(r"[A-Za-zА-Яа-яҐЄІЇґєії]", v):
            return False
        return True  # лишилось тільки цифри/пунктуація → сміття

    for _fld_name, _fld_val in (("olx", olx), ("facebook", facebook),
                                ("instagram", instagram), ("telegram", telegram)):
        if _is_garbage_social(_fld_val):
            logger.warning(
                f"[client parse] Відкинуто сміття в полі '{_fld_name}': "
                f"'{_fld_val}' (клієнт='{raw_name}')"
            )
            if _fld_name == "olx":       olx = ""
            elif _fld_name == "facebook": facebook = ""
            elif _fld_name == "instagram": instagram = ""
            elif _fld_name == "telegram":  telegram = ""

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

    # ── Stage 1: strong signal lookup через client_contacts ──────────────
    # Шукаємо по many-to-one таблиці контактів — людина може мати 2+ FB / 2+ phone,
    # і будь-який з них резолвить у master (а не плодить клона).
    from backend.utils.client_contacts import find_client_by_any_contact
    candidate = None
    strong_signal_matched = None
    match = find_client_by_any_contact(session, {
        "phone":     phone,
        "facebook":  facebook if facebook and "facebook.com" in facebook else "",
        "telegram":  telegram,
        "instagram": instagram if instagram and "instagram.com" in instagram else "",
    })
    if match:
        cid_match, strong_signal_matched = match
        candidate = session.query(Client).filter(Client.id == cid_match).first()

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
    try:
        from backend.utils.identity_normalizer import (
            normalize_phone as _n_phone,
            normalize_facebook as _n_fb,
            normalize_telegram as _n_tg,
            normalize_instagram as _n_ig,
        )
    except ImportError:
        from utils.identity_normalizer import (
            normalize_phone as _n_phone,
            normalize_facebook as _n_fb,
            normalize_telegram as _n_tg,
            normalize_instagram as _n_ig,
        )
    norm_phone = _n_phone(phone) or ""
    norm_fb    = _n_fb(facebook) or ""
    norm_tg    = _n_tg(telegram) or ""
    norm_ig    = _n_ig(instagram) or ""

    # ── Stage 3: more conflict checks SKIPPED for contact fields ──────────
    # Раніше тут «різний phone/fb» у кандидата-за-іменем змушував створити
    # клона. Тепер контакти many-to-one: розбіжний канал просто додається
    # до існуючого клієнта в client_contacts (Stage 4), і петля мерджу зникає.
    create_new_due_to_conflict = False
    conflict_details = None

    # ── Stage 4: enrich existing OR create new ────────────────────────────
    if candidate and not create_new_due_to_conflict:
        from backend.utils.client_contacts import upsert_contact

        locked = _locked_fields(candidate)
        manual_lock_active = candidate.manually_edited_at is not None

        # Контакти: upsert у client_contacts. Primary не чіпаємо, якщо у клієнта
        # вже є primary цього kind. Новий канал стає secondary — без конфлікту.
        for kind, raw in (("phone", phone), ("facebook", facebook), ("viber", viber),
                          ("telegram", telegram), ("instagram", instagram),
                          ("olx", olx), ("email", email)):
            if not raw:
                continue
            # Якщо primary цього kind ще нема — новий рядок стає primary.
            has_primary = session.execute(text("""
                SELECT 1 FROM client_contacts
                 WHERE client_id = :cid AND kind = :k AND is_primary = TRUE LIMIT 1
            """), {"cid": candidate.id, "k": kind}).first() is not None
            make_primary = not has_primary
            if manual_lock_active and kind in locked:
                make_primary = False  # юзер залочив поле — не перетираємо primary
            upsert_contact(session, candidate.id, kind, raw,
                           source="parser", make_primary=make_primary)

        if gender_id and (not candidate.gender_id or candidate.gender_id == 0) and \
           not (manual_lock_active and "gender_id" in locked):
            candidate.gender_id = gender_id
        if nickname and not candidate.nickname and not (manual_lock_active and "nickname" in locked):
            candidate.nickname = nickname

        # КЛЮЧОВЕ: реєструємо alias ЗАВЖДИ — навіть для locked клієнта.
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

    # Сінхронно прокидаємо primary-контакти у client_contacts (тригер створить
    # дзеркала, але вони стартують з is_primary=FALSE; явний upsert дає primary).
    from backend.utils.client_contacts import upsert_contact as _uc
    for kind, raw in (("phone", phone), ("facebook", facebook), ("viber", viber),
                      ("telegram", telegram), ("instagram", instagram),
                      ("olx", olx), ("email", email)):
        if raw:
            _uc(session, client.id, kind, raw, source="parser", make_primary=True)

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
    # Самовивіз — клієнт сам забирає
    "САМОВИВІЗ": "Самовивіз", "САМОВИВОЗ": "Самовивіз",
    # Місцевий — ми самі веземо (кур'єром); ОКРЕМИЙ метод, НЕ Самовивіз
    "МІСЦЕВИЙ": "Місцевий", "МІСЦЕВА": "Місцевий", "МІСЦЕВ": "Місцевий", "МІСТ": "Місцевий",
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
    """Canonical product number form: leading #, uppercase known prefix,
    Latin homoglyphs (T642) folded to Cyrillic (Т642)."""
    try:
        from backend.utils.productnumber_normalizer import normalize as _canon
    except ImportError:
        from utils.productnumber_normalizer import normalize as _canon
    return _canon(pnum) or ""


def _as_plain_date(v):
    """datetime|date|None → date|None (для порівнянь «товар уже існував»)."""
    if v is None:
        return None
    return v.date() if hasattr(v, "date") else v


def _pick_order_candidate(candidates: list, order_date=None, item_price=None):
    """Детермінований вибір товару, коли номер НЕ унікальний.

    Один `productnumber` цілком легально належить різним товарам (див. #А1124,
    #А1198), а ще той самий номер лишається у БД після перейменування у аркуші
    (4336 → Ф4336). Раніше тут був `candidates[0]` без ORDER BY: продаж ботинок
    Gino Rossi #4336 з 2023-го приклеївся до кросівок Ecco, завезених 2026-го.

    Пріоритет фільтрів (кожен застосовується, лише якщо лишає ≥1 кандидата):
      1. товар уже існував на дату замовлення (`dateadded` ≤ дата);
      2. ціна товару збігається з ціною позиції в замовленні;
      3. номер із '#' (новіша конвенція);
      4. найменший id — щоб вибір був стабільним між прогонами.
    """
    if not candidates:
        return None
    cands = sorted(candidates, key=lambda c: c.id)
    if len(cands) == 1:
        return cands[0]

    od = _as_plain_date(order_date)
    if od is not None:
        arrived = [c for c in cands
                   if c.dateadded is None or _as_plain_date(c.dateadded) <= od]
        if arrived:
            cands = arrived

    try:
        price_f = float(item_price) if item_price else 0.0
    except (TypeError, ValueError):
        price_f = 0.0
    if price_f > 0:
        exact = [c for c in cands
                 if c.price is not None and abs(float(c.price) - price_f) < 0.01]
        if exact:
            cands = exact

    with_hash = [c for c in cands if (c.productnumber or "").startswith("#")]
    if with_hash:
        cands = with_hash
    return cands[0]


def _resolve_order_product(session, pnum_clean: str, size_hints: dict,
                           order_date=None, item_price=None):
    """
    Find the correct Product for an order item.

    Logic:
    1. If there is a size hint for this pnum in 'Уточнення' (e.g. "Ф2982 (39)"),
       try to find a product with matching productnumber AND sizeeu.
       Size hint may be a range like "37-38" — try both values.
    2. If no size hint or no match found with size, fall back to all products
       with this productnumber.
    3. Try clonednumbers match as last resort.

    Коли на кроці 1/2 кандидатів кілька — вибір робить `_pick_order_candidate`
    (дата завозу + ціна + '#' + id), а не «перший, який повернула БД».
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
            sized = session.query(Product).filter(
                Product.productnumber == pnum_with_hash,
                Product.sizeeu == sz,
            ).all()
            product = _pick_order_candidate(sized, order_date, item_price)
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

    if candidates:
        return _pick_order_candidate(candidates, order_date, item_price)

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
    prefetched_rows: Optional[list] = None,
) -> dict:
    from backend.models.models import Order, OrderItem, Product

    if cutoff_date is None:
        cutoff_date = date.min

    rows = prefetched_rows if prefetched_rows is not None else ws.get_all_values()
    if not rows:
        return {"orders": 0, "items": 0, "clients": 0, "skipped": 0,
                "touched_order_ids": set()}

    header = [h.strip() for h in rows[0]]

    # Sheet-level operating expense: import once, independently from order rows.
    # The surrounding parser transaction keeps it atomic with this sheet parse.
    _sync_advertising_expense(session, ws, sheet_date, rows)

    def col(row, name, default=""):
        try:
            idx = header.index(name)
            return row[idx].strip() if idx < len(row) else default
        except ValueError:
            return default

    import hashlib
    orders_added = orders_updated = items_added = clients_added = skipped = 0
    touched_order_ids: set[int] = set()  # ордери, що мають живий рядок у цьому прогоні
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
        source_fp = compute_order_fingerprint(client_name, order_date, product_nums)
        # Resolution-INDEPENDENT identity (does not depend on how товари розпізнались).
        pnum_key = compute_pnum_key(product_nums)

        existing_order = session.query(Order).filter(
            Order.source_fingerprint == source_fp
        ).first()

        # Level 1.5: Date-migration fallback
        # Раніше парсер ігнорував колонку "Дата замовлення" і використовував sheet_date.
        # Тепер order_date може бути реальною датою → fingerprint змінився.
        # Перевіряємо також старий fingerprint (з sheet_date) для плавного переходу.
        if not existing_order and order_date != sheet_date:
            source_fp_old = compute_order_fingerprint(client_name, sheet_date, product_nums)
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

        # Level 1K: Resolution-INDEPENDENT key match (the ironclad guarantee).
        # Matches on source_pnum_key (raw sheet numbers) + total + date-window,
        # independent of client_id AND of how товари розпізнались. This is the
        # one identity that can't drift between runs, so it closes the residual
        # holes the other levels share: anonymous orders + flaky resolution +
        # legacy rows without source_sheet_gid. Unique match only (no false merge).
        if not existing_order and pnum_key and total_amount > 0:
            from datetime import timedelta as _tdk
            _klo, _khi = order_date - _tdk(days=7), order_date + _tdk(days=7)
            _kq = session.query(Order).filter(
                Order.source_pnum_key == pnum_key,
                Order.total_amount == total_amount,
                Order.order_date >= _klo,
                Order.order_date <= _khi,
            )
            # Scope to the same client identity (treat NULL==NULL) so two different
            # buyers of the same items in the same window are NOT merged.
            _kq = _kq.filter(Order.client_id.is_(None)) if client_id is None \
                else _kq.filter(Order.client_id == client_id)
            _kmatches = _kq.all()
            if len(_kmatches) == 1:
                existing_order = _kmatches[0]
                existing_order.order_date = order_date
                existing_order.source_fingerprint = source_fp
                logger.info(
                    "Order #%d: matched via resolution-independent pnum_key "
                    "(date→%s, total=%s)", existing_order.id, order_date, total_amount,
                )
            elif len(_kmatches) > 1:
                logger.warning(
                    "pnum_key match ambiguous key=%s total=%s window=±7d: %d candidates "
                    "— skipping merge", pnum_key[:8], total_amount, len(_kmatches),
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
            _norm_set_current = {re.sub(r"[^\w\-А-ЯҐЄІЇа-яґєії]", "", p).upper() for p in product_nums if p.strip()}  # keep '-' (ростовка-суфікс)
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
                    re.sub(r"[^\w\-А-ЯҐЄІЇа-яґєії]", "", (p.productnumber or "")).upper()  # keep '-' (ростовка-суфікс)
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

        # Level 2N: Дедуплікація АНОНІМНИХ замовлень (client_id IS NULL).
        # ⚠️ Корінь ghost-дублів: Level 2/2.5/3 усі вимагають client_id → анонімні
        # замовлення (порожнє ім'я клієнта) проходять лише Level 1 (точний fingerprint)
        # та 1.5. Якщо існує легасі-ордер без fingerprint, або дата/текст-клієнта
        # зсувається між вкладками/прогонами — fingerprint змінюється, нічого не
        # матчить → щопрогону створюється новий дубль-близнюк (роздуває sold_count).
        # Матчимо за вікном дат + total_amount + ІДЕНТИЧНИМ набором номерів товарів
        # (єдина стабільна ідентичність анонімного замовлення). Лише при ОДНОЗНАЧНОМУ
        # збігу (рівно 1 кандидат), щоб не злити різні анонімні замовлення.
        if not existing_order and client_id is None and total_amount > 0 and product_nums:
            from datetime import timedelta as _td2
            _anon_set = {(_canon_pnum_for_match(p) or "").lower() for p in product_nums if p.strip()}
            _anon_set.discard("")
            _lo2, _hi2 = order_date - _td2(days=7), order_date + _td2(days=7)
            _anon_cands = session.query(Order).filter(
                Order.client_id.is_(None),
                Order.total_amount == total_amount,
                Order.order_date >= _lo2,
                Order.order_date <= _hi2,
            ).all()
            _anon_matches = []
            for _ac in _anon_cands:
                _ac_nums = session.query(Product.productnumber).join(
                    OrderItem, OrderItem.product_id == Product.id
                ).filter(OrderItem.order_id == _ac.id).all()
                _ac_set = {(_canon_pnum_for_match(pn or "") or "").lower() for (pn,) in _ac_nums}
                _ac_set.discard("")
                if _ac_set and _ac_set == _anon_set:
                    _anon_matches.append(_ac)
            if len(_anon_matches) == 1:
                existing_order = _anon_matches[0]
                existing_order.order_date = order_date
                existing_order.source_fingerprint = source_fp
                logger.info(
                    "Order #%d: matched via NULL-client set-match (date→%s, total=%s) — updating",
                    existing_order.id, order_date, total_amount,
                )
            elif len(_anon_matches) > 1:
                logger.warning(
                    "NULL-client set-match ambiguous date=%s total=%s: %d candidates — skipping merge",
                    order_date, total_amount, len(_anon_matches),
                )

        # Level 0T: Zero-total ордери (total_amount = 0) оминають Level 1K і 2N,
        # бо ОБИДВА вимагають total>0 → щопрогону створюється дубль-близнюк.
        # Матчимо за resolution-INDEPENDENT pnum_key + ТА САМА ідентичність клієнта
        # (NULL==NULL) + вікно дат ±7д. total не дискримінує (=0), тож опора на
        # ключ+клієнт+вікно. Лише ОДНОЗНАЧНИЙ збіг (рівно 1 кандидат) — без false-merge.
        if not existing_order and pnum_key and total_amount == 0:
            from datetime import timedelta as _td0
            _0lo, _0hi = order_date - _td0(days=7), order_date + _td0(days=7)
            _0q = session.query(Order).filter(
                Order.source_pnum_key == pnum_key,
                Order.total_amount == 0,
                Order.order_date >= _0lo,
                Order.order_date <= _0hi,
            )
            _0q = _0q.filter(Order.client_id.is_(None)) if client_id is None \
                else _0q.filter(Order.client_id == client_id)
            _0matches = _0q.all()
            if len(_0matches) == 1:
                existing_order = _0matches[0]
                existing_order.order_date = order_date
                existing_order.source_fingerprint = source_fp
                logger.info(
                    "Order #%d: matched via zero-total pnum_key (date→%s)",
                    existing_order.id, order_date,
                )
            elif len(_0matches) > 1:
                logger.warning(
                    "zero-total pnum_key match ambiguous key=%s window=±7d: %d candidates "
                    "— skipping merge", pnum_key[:8], len(_0matches),
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
            # Exact fingerprint може знайти старе parser-origin замовлення, яке
            # колись створилось без client_id. Якщо поточний рядок уже однозначно
            # розпізнав клієнта — дозаповнюємо ТІЛЬКИ порожню прив'язку. Наявного
            # клієнта іншим автоматично не замінюємо (це потребує ручної звірки).
            try:
                from backend.utils.order_parser_logic import fill_missing_order_client
            except ImportError:
                from utils.order_parser_logic import fill_missing_order_client
            if fill_missing_order_client(existing_order, client_id):
                logger.info(
                    "Order #%d: attached previously missing client_id=%d",
                    existing_order.id, client_id,
                )
            elif client_id is not None and existing_order.client_id != client_id:
                logger.warning(
                    "Order #%d: parsed client_id=%d differs from existing client_id=%d; "
                    "keeping existing link for safety",
                    existing_order.id, client_id, existing_order.client_id,
                )
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
            existing_order.source_sheet_gid  = ws.id   # прив'язка до живої вкладки (для scoped-sweep)
            existing_order.source_pnum_key   = pnum_key  # resolution-independent identity
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
                source_sheet_gid   = ws.id,   # прив'язка до живої вкладки (для scoped-sweep)
                source_pnum_key    = pnum_key,  # resolution-independent identity
                created_at         = datetime.utcnow(),
            )
            session.add(order)
            session.flush()
            orders_added += 1

        # Цей ордер має живий рядок у аркуші цього прогону (matched або created).
        if getattr(order, "id", None):
            touched_order_ids.add(order.id)

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
            # Strip emoji / special chars from product number.
            # ⚠️ KEEP '-' : it is the rostovka suffix separator (Ф1810-2). Stripping it
            # turned "Ф1810-2" → "Ф18102" → no match → продаж приклеювався до базового
            # «Ф1810» (або взагалі губився), а суфіксна пара лишалась «Непродано».
            pnum_clean = re.sub(r"[^\w#\-А-ЯҐЄІЇа-яґєії]", "", pnum).strip()
            if not pnum_clean:
                continue

            product = _resolve_order_product(session, pnum_clean, size_hints,
                                             order_date=order_date, item_price=price)

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
            # oldprice НЕ деривуємо тут — це поле належить тільки колонці
            # "Стара ціна" з аркуша журналу товарів.
            if price and price > 0:
                if not product.price or price != product.price:
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

        # Перераховуємо total_amount, якщо використано fallback-ціни з журналу.
        # Статус products.statusid тут НАВМИСНО не змінюємо: це журнальний/ручний
        # знімок. Живий «Продано» UI і публікації визначають через sold_count із
        # order_items + orders (Підтверджено+Оплачено / Подарунок з урахуванням
        # повернень). Старий блок нижче мав hardcode Продано=2, хоча у реальній БД
        # id=2 означає Непродано, і зачіпав лише останній товар замовлення.
        if any_price_substituted and (not total_amount or total_amount <= 0):
            order.total_amount = total_recalc

        session.commit()

    return {"orders": orders_added, "items": items_added, "clients": clients_added,
            "updated": orders_updated, "skipped": skipped,
            "touched_order_ids": touched_order_ids}


# ── Workspace parser ─────────────────────────────────────────────────────────

def _strict_match(a, b) -> bool:
    """Match ONLY if both sides have data and they equal.
    Empty/None on either side → NOT a match (на відміну від _fields_match
    який «довіряє» порожньому полю). Потрібно для workspace merge — там
    порожні поля в Воркспейс-рядку не повинні зараховуватись як ствердження
    'це той самий товар'.
    """
    def is_empty(v):
        return v is None or str(v).strip() == "" or v == 0
    if is_empty(a) or is_empty(b):
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def _workspace_merge_score(
    p: "Product",
    brand_id, color_id, size_val, marking_val, model_val,
    type_id=None,
) -> int:
    """
    Count how many of the 5 key characteristics match between a DB product
    and a workspace row.  Returns 0-5.

    Hard-block: різний typeid (Ботинки ≠ Кросівки) → 0 одразу.
    Порожні поля з обох боків — НЕ дають бал (strict match).
    """
    # Hard-block: типи мають збігатись (якщо вказані з обох боків)
    if type_id is not None and p.typeid is not None and p.typeid != type_id:
        return 0

    score = 0
    if _strict_match(p.brandid,  brand_id):    score += 1
    if _strict_match(p.colorid,  color_id):    score += 1
    if _strict_match(p.sizeeu,   size_val):    score += 1
    if _strict_match(p.marking,  marking_val): score += 1
    if _strict_match(p.model,    model_val):   score += 1
    return score


def _append_clone(existing_clones: Optional[str], new_num: str) -> str:
    """Append new_num to a semicolon-separated clonednumbers string."""
    if not new_num or not new_num.strip():
        return existing_clones or ""
    parts = [c.strip() for c in (existing_clones or "").split(";") if c.strip()]
    if new_num.strip() not in parts:
        parts.append(new_num.strip())
    return "; ".join(parts)


def _is_already_clone(session: Session, pnum: str) -> bool:
    """
    True, якщо `pnum` уже фігурує як КЛОН якогось товару (тобто загублений товар
    з цим номером був раніше змерджений в оригінал через accept). Тоді воркспейс-
    парсер НЕ відтворює його заново. Порівняння delimiter-safe (точний токен),
    з нормалізацією '#' (клони зберігаються без '#').
    """
    if not pnum:
        return False
    bare = pnum.lstrip("#").strip()
    if not bare:
        return False
    from backend.models.models import Product
    # LIKE — лише префільтр; точну належність токена перевіряємо в Python
    rows = session.query(Product.clonednumbers).filter(
        Product.clonednumbers.like(f"%{bare}%")
    ).all()
    for (clones,) in rows:
        toks = {c.strip().lstrip("#") for c in (clones or "").split(";") if c.strip()}
        if bare in toks:
            return True
    return False


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

    merged = added = skipped = candidates_count = 0
    touched_product_ids: set = set()
    total = len(rows) - 1

    for i, row in enumerate(rows[1:], 1):
        if progress_cb and i % 20 == 0:
            progress_cb(i, total)

        # Skip truly empty rows
        if not any(c.strip() for c in row):
            skipped += 1
            continue

        # Skip rows already merged in BMS (marked) — don't re-create the lost item
        if MERGE_MARKER_PREFIX in col(row, "Екстра примітка"):
            skipped += 1
            continue

        pnum       = col(row, "Номер").strip().rstrip(";").strip()
        if pnum:
            pnum = _normalize_pnum(pnum) or pnum
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
        letter_val = _normalize_size_letter(col(row, "Буквений")) if "Буквений" in header else ""
        if not letter_val and size_val:
            _letter_from_size = _normalize_size_letter(size_val)
            if _letter_from_size and not re.search(r"\d", size_val):
                letter_val = _letter_from_size
                size_val   = ""
        cm_val     = _normalize_size(col(row, "СМ"))
        # Чужа (US/UK) нумерація в колонці «Розмір» → деривуємо EU зі СМ.
        size_val, usa_val = _derive_eu_from_foreign_size(size_val, cm_val)
        price_val  = col(row, "Ціна")
        oldprice_val = col(row, "Стара ціна") if "Стара ціна" in header else ""
        desc_val   = col(row, "Опис") or col(row, "Екстра примітка")
        # Нові колонки (опційні — старі версії аркуша повернуть "")
        season_val_sheet = col(row, "Сезон") if "Сезон" in header else ""
        dimensions_val   = col(row, "Габарити") if "Габарити" in header else ""
        style_val        = col(row, "Стиль") if "Стиль" in header else ""
        current_cond_val = col(row, "Поточний стан") if "Поточний стан" in header else ""
        width_val        = col(row, "Ширина") if "Ширина" in header else ""
        # Колонки 2026-06-10 (Колекція/GTIN/Геометрична форма) — вставлені й у Воркспейс.
        collection_val   = col(row, "Колекція").strip() if "Колекція" in header else ""
        gtin_val         = col(row, "GTIN").strip() if "GTIN" in header else ""
        geom_shape_val   = col(row, "Геометрична форма").strip() if "Геометрична форма" in header else ""

        # ── Нові поля (виміри/взуття/матеріали) — дзеркало Журнал-парсера ──
        # Виміри: парсимо діапазони (single value → min==max).
        cm_min, cm_max                 = _parse_measurement_range(cm_val)
        length_min, length_max         = _parse_measurement_range(col(row, "Довжина")         if "Довжина" in header else "")
        pog_min, pog_max               = _parse_measurement_range(col(row, "Груди (н/о)")     if "Груди (н/о)" in header else "")
        pob_min, pob_max               = _parse_measurement_range(col(row, "Бедра (н/о)")     if "Бедра (н/о)" in header else "")
        pot_min, pot_max               = _parse_measurement_range(col(row, "Талія (н/о)")     if "Талія (н/о)" in header else "")
        sleeve_min, sleeve_max         = _parse_measurement_range(col(row, "Рукав")           if "Рукав" in header else "")
        height_min, height_max         = _parse_measurement_range(col(row, "Висота")          if "Висота" in header else "")
        sole_thickness_min, sole_thickness_max = _parse_measurement_range(col(row, "Товщина підошви") if "Товщина підошви" in header else "")
        heel_min, heel_max             = _parse_measurement_range(col(row, "Підбор")          if "Підбор" in header else "")

        # Взуттєві lookup (single FK each). Невідоме → unmapped log, FK = NULL.
        resolved_shoe_fk: dict[str, Optional[int]] = {}
        for sheet_col, (tbl, name_col, fk_col) in SHOE_LOOKUP_COLUMNS.items():
            if sheet_col not in header:
                continue
            raw_val = col(row, sheet_col).strip()
            if not raw_val:
                continue
            rid = _resolve_shoe_lookup_id(session, tbl, name_col, raw_val)
            if rid is None:
                _log_unmapped_material(session, raw_val, f"_{fk_col}", None, ws.title)
                continue
            resolved_shoe_fk[fk_col] = rid

        # "Колір підошви" → reuse colors (auto-create нові кольори, як основний колір).
        sole_color_raw = col(row, "Колір підошви").strip() if "Колір підошви" in header else ""
        sole_color_obj = _get_or_create(session, Color, "colorname", sole_color_raw) if sole_color_raw else None
        sole_color_id  = sole_color_obj.id if sole_color_obj else None

        # Bare aliases для Product(...)-конструктора CREATE-гілки (як у головному
        # парсері рядки ~2442): без них NameError при створенні взуття у воркспейсі.
        sole_type_id      = resolved_shoe_fk.get("soletypeid")
        toe_shape_id      = resolved_shoe_fk.get("toeshapeid")
        fastening_type_id = resolved_shoe_fk.get("fasteningtypeid")
        lining_id         = resolved_shoe_fk.get("liningid")

        parsed_new_fields = {
            "measurementscm_min":      cm_min,             "measurementscm_max":      cm_max,
            "measurements_length_min": length_min,         "measurements_length_max": length_max,
            "measurements_pog_min":    pog_min,            "measurements_pog_max":    pog_max,
            "measurements_pob_min":    pob_min,            "measurements_pob_max":    pob_max,
            "measurements_pot_min":    pot_min,            "measurements_pot_max":    pot_max,
            "measurements_sleeve_min": sleeve_min,         "measurements_sleeve_max": sleeve_max,
            "measurements_height_min": height_min,         "measurements_height_max": height_max,
            "measurements_sole_thickness_min": sole_thickness_min,
            "measurements_sole_thickness_max": sole_thickness_max,
            "measurements_heel_min":   heel_min,           "measurements_heel_max":   heel_max,
            "soletypeid":              resolved_shoe_fk.get("soletypeid"),
            "toeshapeid":              resolved_shoe_fk.get("toeshapeid"),
            "fasteningtypeid":         resolved_shoe_fk.get("fasteningtypeid"),
            "liningid":                resolved_shoe_fk.get("liningid"),
            "heeltypeid":              resolved_shoe_fk.get("heeltypeid"),
            "lacetypeid":              resolved_shoe_fk.get("lacetypeid"),
            "packagingid":             resolved_shoe_fk.get("packagingid"),
            "technologyid":            resolved_shoe_fk.get("technologyid"),
            "sole_colorid":            sole_color_id,
            # Текстові поля 2026-06-10 — NULL-only збагачення (_NEW_TEXT_FIELDS):
            # воркспейс не перетирає журнальні значення, лише заповнює порожні.
            "collection":              collection_val or None,
            "gtin":                    gtin_val or None,
            "geometric_shape":         geom_shape_val or None,
        }

        # Матеріали за позиціями. Порожня клітинка = не чіпати існуюче в БД.
        materials_parsed: dict[str, list[str]] = {}
        for sheet_col, position in MATERIAL_POSITIONS.items():
            if sheet_col in header:
                raw_mat = col(row, sheet_col).strip()
                if raw_mat:
                    parts = _split_materials_cell(raw_mat)
                    if parts:
                        materials_parsed[position] = parts

        # Resolve FK refs
        # Guard: split combined types ("Туфлі/кросівки", "Ботинки-челсі") → Type + Subtype
        if type_val:
            t_part, st_part = _split_combined_type(type_val)
            type_val = t_part
            if st_part and not sub_val:
                sub_val = st_part

        # Auto-detect style from description if Sheet "Стиль" is empty.
        if (not style_val or not style_val.strip()) and desc_val:
            style_val = _auto_detect_style(desc_val)

        # Season: явне значення з аркуша > auto-classify. Канонізуємо CSV.
        if season_val_sheet and season_val_sheet.strip():
            season_val = normalize_season_csv(season_val_sheet)
        else:
            season_val = _classify_season(type_val, sub_val, style_val, desc_val)

        brand_obj  = _get_or_create(session, Brand,  "brandname",  brand_val)  if brand_val  else None
        type_obj   = _get_or_create(session, Type,   "typename",   type_val)   if type_val   else None
        color_obj  = _get_or_create(session, Color,  "colorname",  color_val)  if color_val  else None
        gender_obj = _get_or_create(session, Gender, "gendername", gender_val) if gender_val else None
        mfr_id     = _get_or_create_country(session, mfr_cntry)
        own_id     = _get_or_create_country(session, own_cntry)
        sub_id     = _get_or_create_subtype(session, sub_val, type_obj.id if type_obj else None)
        cond_id    = _get_or_create_condition(session, cond_val)
        style_obj  = _get_or_create(session, Style, "stylename", style_val) if style_val else None
        style_id   = style_obj.id if style_obj else None
        # Поточний стан: явне значення з аркуша > успадкування від "Стан"
        current_cond_id = (
            _get_or_create_condition(session, current_cond_val.strip()) or cond_id
            if current_cond_val.strip() else cond_id
        )

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

        oldprice_float = None
        if oldprice_val and oldprice_val.strip():
            try:
                oldprice_float = float(oldprice_val.replace(",", "."))
            except ValueError:
                oldprice_float = None

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

        # ── Score ВСІ кандидати (для UX-кандидат-merge, без авто-злиття) ──
        scored_candidates = []
        for p in candidates:
            score = _workspace_merge_score(
                p, brand_id, color_id, size_val, marking, model_val,
                type_id=type_id,
            )
            if score >= 2:
                scored_candidates.append((p, score))
        # Сортуємо: спершу більший score (UI покаже найкращих)
        scored_candidates.sort(key=lambda x: -x[1])

        # NO auto-merge. Завжди створюємо NEW product, далі пропонуємо merge
        # як кандидатів — користувач вирішує через UI.
        # (Старий auto-merge при score>=4 вимкнено, бо давав false-positive.)
        if False:
            # ── DEAD CODE (старий auto-merge), залишено для контексту ─────
            pass
        else:
            # ── NEW or REUSE PRODUCT (без auto-merge) ────────────────────
            # Немає власного «Номера» → перший КЛОН-номер стає productnumber:
            #   • унікальний природний ключ (уникає колізії «???» по унікальному
            #     індексу (productnumber,size,color) — раніше багато безномерних
            #     клон-рядків, як А1241, взаємно конфліктували й не персистились);
            #   • показується як ключовий номер (директива користувача).
            # Якщо й клонів нема — лишаємо "???" (буде «Невідомо» в UI).
            if pnum:
                target_pnum = pnum
            else:
                _first_clone = ""
                if clones_raw:
                    for _part in re.split(r"[;,]", clones_raw):
                        if _part.strip():
                            _first_clone = _part.strip()
                            break
                target_pnum = (_normalize_pnum(_first_clone) or _first_clone) if _first_clone else "???"
            from sqlalchemy.exc import IntegrityError as _IE

            # Idempotency: якщо номер уже є в БД — reuse, без створення дубля.
            # ⚠️ Перевіряємо саме target_pnum (ЕФЕКТИВНИЙ номер запису), а не лише
            # власний pnum рядка. Рядок без «Номера», але з клоном (напр. клон
            # «А1241») отримує target_pnum='#А1241' — і якщо звіряти тільки pnum,
            # перевірка взагалі не виконувалась і в БД з'являвся порожній двійник
            # уже наявного товару (price=0, без розміру/статі). Саме так і
            # народжувались фантоми А1241/А1124/А1198/Д13/Д16/Д17/Ф2346.
            product = None
            lookup_pnum = pnum or (target_pnum if target_pnum != "???" else "")
            if lookup_pnum:
                product = session.query(Product).filter(
                    Product.productnumber == lookup_pnum
                ).first()
                if product is None:
                    # Номер міг зберігатись без '#' (історичні записи) — пробуємо голий.
                    bare = lookup_pnum.lstrip("#").strip()
                    if bare and bare != lookup_pnum:
                        product = session.query(Product).filter(
                            Product.productnumber == bare
                        ).first()
                if product:
                    # is_lost ставимо ЛИШЕ якщо існуючий товар не має deliveryid
                    # (тобто це не реальний журнальний товар, а воркспейс-орфан).
                    # Реальний журнальний товар (має deliveryid) НЕ стає «загубленим»
                    # лише через збіг номера з аркушем Воркспейс.
                    skipped += 1
                    touched_product_ids.add(product.id)
                    if product.deliveryid is None:
                        product.is_lost = True
                    _apply_new_fields_and_materials(
                        session, product,
                        new_fields=parsed_new_fields,
                        materials_parsed=materials_parsed,
                        source=ws.title,
                    )
                    logger.info(
                        "[workspace] REUSED existing product pnum=%s id=%s",
                        lookup_pnum, product.id,
                    )

            # Фікс 1: якщо номер уже КЛОН існуючого товару (раніше змерджений) —
            # не відтворювати загублений запис. Так само за ефективним номером.
            if product is None and lookup_pnum and _is_already_clone(session, lookup_pnum):
                skipped += 1
                logger.info("[workspace] SKIP recreate merged-away pnum=%s", lookup_pnum)
                continue

            if product is None:
                product = Product(
                    productnumber         = target_pnum,
                    clonednumbers         = clones_raw or None,
                    model                 = model_val or None,
                    collection            = collection_val or None,
                    marking               = marking or None,
                    gtin                  = gtin_val or None,
                    year                  = year_int,
                    description           = desc_val or None,
                    price                 = price_float,
                    oldprice              = oldprice_float,
                    sizeeu                = size_val or None,
                    sizeusa               = usa_val or None,
                    size_letter           = letter_val or None,
                    measurementscm        = cm_val or None,
                    measurementscm_min              = cm_min,
                    measurementscm_max              = cm_max,
                    measurements_length_min         = length_min,
                    measurements_length_max         = length_max,
                    measurements_pog_min            = pog_min,
                    measurements_pog_max            = pog_max,
                    measurements_pob_min            = pob_min,
                    measurements_pob_max            = pob_max,
                    measurements_pot_min            = pot_min,
                    measurements_pot_max            = pot_max,
                    measurements_sleeve_min         = sleeve_min,
                    measurements_sleeve_max         = sleeve_max,
                    measurements_height_min         = height_min,
                    measurements_height_max         = height_max,
                    measurements_sole_thickness_min = sole_thickness_min,
                    measurements_sole_thickness_max = sole_thickness_max,
                    measurements_heel_min           = heel_min,
                    measurements_heel_max           = heel_max,
                    soletypeid                      = sole_type_id,
                    toeshapeid                      = toe_shape_id,
                    fasteningtypeid                 = fastening_type_id,
                    liningid                        = lining_id,
                    season                = season_val or None,
                    dimensions            = dimensions_val or None,
                    geometric_shape       = geom_shape_val or None,
                    width                 = width_val or None,
                    styleid               = style_id,
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
                    is_lost               = True,  # Воркспейс/Старі = загублені (Фаза 3: пошук оригіналу)
                )
                # ⚠️ SAVEPOINT на рядок: конфлікт унікальності (uix_products_num_size_color)
                # раніше робив ПОВНИЙ session.rollback() → зносив УСІ накопичені цього
                # прогону товари (воркспейс комітить лише в кінці). Через це більшість
                # клон-рядків «???» (А1253, А1241, …) не персистились. begin_nested()
                # відкочує ЛИШЕ проблемний рядок, решта партії лишається.
                # session.add ВСЕРЕДИНІ savepoint + expunge при відкаті — інакше
                # об'єкт лишиться в session.new і наступний autoflush повторить його
                # (вже поза цим try) → краш усього прогону.
                sp_ws = session.begin_nested()
                try:
                    session.add(product)
                    session.flush()
                    _apply_product_materials(session, product.id, materials_parsed, ws.title)
                    sp_ws.commit()  # release savepoint (товар лишається в зовнішній txn)
                    added += 1
                    touched_product_ids.add(product.id)
                    logger.info(
                        "[workspace] NEW product pnum=%s (top score=%d)",
                        target_pnum,
                        scored_candidates[0][1] if scored_candidates else 0,
                    )
                except _IE:
                    sp_ws.rollback()  # відкат ЛИШЕ цього рядка, не всієї партії
                    try:
                        session.expunge(product)  # прибрати з session.new (не ретраїти)
                    except Exception:
                        pass
                    skipped += 1
                    logger.warning(
                        "[workspace] SKIPPED conflict pnum=%s", target_pnum,
                    )
                    continue  # NO candidate insertion if product wasn't created

            # ── INSERT merge_candidates для всіх score≥2 (typeid вже фільтрований) ──
            # Користувач акцептить/декланє через UI; декланутi пари не пропонуються знову.
            for cand_product, cand_score in scored_candidates:
                if cand_product.id == product.id:
                    continue  # сам себе не пропонуємо
                # Якщо вже є decision (accepted/declined) — пропускаємо
                prior = session.execute(
                    text("""SELECT status FROM merge_candidates
                            WHERE new_product_id = :np AND suggested_id = :sg"""),
                    {"np": product.id, "sg": cand_product.id},
                ).fetchone()
                if prior is not None:
                    # pending → залишаємо; accepted/declined → не чіпаємо
                    continue
                # Human-readable reason
                _reasons = []
                if _strict_match(cand_product.brandid, brand_id):  _reasons.append("бренд")
                if _strict_match(cand_product.colorid, color_id):  _reasons.append("колір")
                if _strict_match(cand_product.sizeeu, size_val):   _reasons.append("розмір")
                if _strict_match(cand_product.marking, marking):   _reasons.append("маркування")
                if _strict_match(cand_product.model, model_val):   _reasons.append("модель")
                reason_txt = "збіг: " + "+".join(_reasons) if _reasons else None
                session.execute(
                    text("""INSERT INTO merge_candidates
                                (new_product_id, suggested_id, score, reason, status)
                            VALUES (:np, :sg, :sc, :rs, 'pending')
                            ON CONFLICT (new_product_id, suggested_id) DO NOTHING"""),
                    {"np": product.id, "sg": cand_product.id,
                     "sc": cand_score, "rs": reason_txt},
                )
                candidates_count += 1

    session.commit()
    return {
        "merged": merged,
        "added": added,
        "skipped": skipped,
        "candidates_created": candidates_count,
        "touched_product_ids": touched_product_ids,
    }


# ── Public API ────────────────────────────────────────────────────────────────
def run_products_parsing(
    session: Session,
    mode: str = "quick",
    progress_cb: Optional[Callable] = None,
    force: bool = False,
) -> dict:
    """
    Parse Журнал sheets → products table.
    mode: 'quick' = last QUICK_SHEETS_COUNT batch sheets
          'full'  = all batch sheets
    force: True → обійти file-gate (manual-тригер завжди читає; див. _file_gate_check).
    """
    _reset_ref_lookup_cache()  # per-run кеш довідкових лукапів (свіжий на кожен прогін)
    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)

    # Layer C: skip the whole run if the file is unchanged since last products parse
    gated, file_lut = _file_gate_check(session, sh, JOURNAL_ID, f"products_{mode}", force=force)
    if gated:
        logger.info(f"[products] Журнал без змін з останнього {mode}-парсингу — пропуск")
        if progress_cb:
            progress_cb(100, "Журнал без змін — пропуск")
        return {"mode": mode, "sheets": 0, "added": 0, "updated": 0,
                "skipped": 0, "file_unchanged": True, "seen_product_ids": set()}

    # Phase 2a: snapshot user-locked field values before the parser may
    # overwrite them; restored after the run so in-app edits survive reparse.
    locked_snapshot = _snapshot_product_locks(session)

    all_sheets = sh.worksheets()

    batch_sheets = [ws for ws in all_sheets if not is_skip_sheet(ws.title)]
    if mode == "quick":
        batch_sheets = batch_sheets[:QUICK_SHEETS_COUNT]

    total_added = total_updated = total_skipped = 0
    total_orphans_deleted = 0
    orphan_scan: list = []   # (shipment_id, назва вкладки, номери з аркуша)
    total_sheets = len(batch_sheets)
    # Спільний лічильник появ product.id між усіма аркушами в цьому run.
    # Передається у _parse_products_sheet щоб quantity = кількість появ
    # по всьому журналу, а не лише в одному аркуші.
    seen_in_run: dict = {}

    shipment_ids = []
    for idx, ws, all_rows in _iter_sheets_with_rows(sh, batch_sheets):
        sheet_date = parse_date_from_sheet_title(ws.title)
        supplier_name = parse_supplier_from_sheet_title(ws.title)
        supplier_id = _get_or_create_supplier(session, supplier_name) if supplier_name else None

        # Rows fetched in batches by _iter_sheets_with_rows (used for both
        # financial parsing and product parsing).

        # Parse purchase/delivery costs from the info block on the right side of the sheet
        financials = _parse_delivery_financials(all_rows)
        logger.info(f"[products] Sheet '{ws.title}': purchase_cost={financials['purchase_cost']}, delivery_cost={financials['delivery_cost']}")

        shipment_id = _get_or_create_shipment(
            session, ws.title, sheet_date, supplier_id,
            purchase_cost=financials["purchase_cost"],
            delivery_cost=financials["delivery_cost"],
            sheet_gid=ws.id,
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

        # ── Орфани вкладки збираємо, але НЕ видаляємо тут ─────────────────
        # ⚠️ 19.08.2026: прибирання одразу після кожної вкладки знесло 135 живих
        # товарів. Причина — `deliveryid` товару НЕ дорівнює вкладці, де його рядок
        # лежить зараз: рядок міг переїхати в іншу вкладку, а прив'язка лишитись
        # старою. Reconcile вкладки D бачив «номера нема в D» і видаляв, хоча далі
        # в цьому ж прогоні той номер знаходився у вкладці A (парсер потім створював
        # його наново, вже іншим id). Тому рішення відкладаємо до кінця прогону,
        # коли відомо ВСЕ, що журнал реально бачив (`seen_in_run`).
        if shipment_id:
            orphan_scan.append((shipment_id, ws.title, _sheet_numbers(all_rows)))

    if shipment_ids:
        session.commit()

    # ── Орфани: одне рішення на весь прогін, коли відомо все ──────────────
    # Видаляємо лише те, що: (а) не з'явилось у ЖОДНІЙ розібраній вкладці,
    # (б) парсер не торкався цього прогону, (в) не має продажів, (г) не сміттєвий
    # номер ('???'/'__tmp_rename'), (д) не суфіксна ростовка живого номера.
    # Плюс бекап (без нього — не видаляємо) і стеля частки завозу.
    if PARSE_ORPHAN_AUTO and orphan_scan:
        all_sheet_nums = set().union(*(nums for _, _, nums in orphan_scan))
        seen_ids = set(seen_in_run.keys())
        for shipment_id, title, nums in orphan_scan:
            try:
                deleted = _reconcile_delivery_orphans(
                    session, shipment_id, None, max_share=PARSE_ORPHAN_MAX_SHARE,
                    tag="products", sheet_nums=nums, seen_ids=seen_ids,
                    all_sheet_nums=all_sheet_nums,
                )
                if deleted:
                    session.commit()
                    total_orphans_deleted += deleted
            except Exception as e:  # noqa: BLE001 — парс не має падати через прибирання
                session.rollback()
                logger.warning(f"[products] orphan-reconcile '{title}' failed: {e}")

    # Phase 2a: restore user-locked fields the parser may have overwritten
    restored = _restore_product_locks(session, locked_snapshot)
    if restored:
        logger.info(f"[products] Restored {restored} user-locked field(s) after reparse")

    # Layer C: record file marker only after a full successful run
    if file_lut is not None:
        _record_file_state(session, JOURNAL_ID, f"products_{mode}", file_lut)
        session.commit()

    return {
        "mode":    mode,
        "sheets":  total_sheets,
        "added":   total_added,
        "updated": total_updated,
        "skipped": total_skipped,
        "locks_restored": restored,
        "orphans_deleted": total_orphans_deleted,
        "seen_product_ids": set(seen_in_run.keys()),
    }


def _reconcile_evolved_order_ghosts(session: Session, touched_ids: set) -> dict:
    """Прибрати ghost-замовлення, що лишились після зміни набору товарів у рядку
    аркуша (fingerprint змінився → парсер створив новий ордер, старий завис).

    БЕЗПЕЧНА сигнатура (ВСІ умови разом) — НЕ глобальний mark&sweep:
      • ордер НЕ touched цього прогону (жоден рядок аркуша його не матчив);
      • існує touched-сиблінг той самий client_id+order_date (ДОВОДИТЬ, що групу
        реально розпарсено цього прогону → не частковий прогін/hash-skip);
      • набір product_id у строгому containment із touched-сиблінгом (підмножина
        АБО надмножина) — підпис «додали/прибрали товари», не два різні замовлення;
      • source_fingerprint IS NOT NULL (парсер-походження; legacy/ручні не чіпаємо);
      • order_status_id IS NULL (рання версія рядка до статусу; фіналізовані — НЕ цей прохід).
    Перед видаленням — JSON-бекап. Помилки логуються, парс не валиться.
    """
    if not ORDER_GHOST_SWEEP or not touched_ids:
        return {"removed": 0}
    try:
        from backend.models.models import Order, OrderItem
    except ImportError:
        from models.models import Order, OrderItem
    try:
        # Групи (client_id, order_date), що мають touched-ордер, + їхні набори product_id.
        touched = session.query(Order).filter(Order.id.in_(touched_ids)).all()

        # Batch-запит на product_id-и ВСІХ touched-замовлень ОДНИМ SQL (замість
        # окремого запиту на кожне — на full-парсі touched_ids сягає тисяч ордерів,
        # і N+1 тут же тримав прогрес-бар «на місці» (~5+ хв без progress_cb).
        def _bulk_pidsets(order_ids) -> dict:
            if not order_ids:
                return {}
            out: dict = {}
            for oid, pid in (session.query(OrderItem.order_id, OrderItem.product_id)
                              .filter(OrderItem.order_id.in_(order_ids)).all()):
                out.setdefault(oid, set()).add(pid)
            return {oid: frozenset(pids) for oid, pids in out.items()}

        touched_pidsets = _bulk_pidsets([t.id for t in touched])

        # v8: групуємо і АНОНІМНІ (client_id IS NULL) — раніше їх пропускали
        # (`if t.client_id is None: continue`), тож evolved-set ghost анонімного
        # замовлення (як #Ф2261) не прибирався ні дедуп-рівнями, ні цим sweep.
        # Тепер None — повноправний ключ; для нього сиблінги шукаємо з IS NULL.
        groups: dict = {}  # (client_id|None, order_date) -> [frozenset]
        for t in touched:
            groups.setdefault((t.client_id, t.order_date), []).append(
                touched_pidsets.get(t.id, frozenset()))

        # Кандидати-сиблінги: РАНІШЕ окремий запит НА КОЖНУ групу (client_id,
        # order_date) — на full-парсі це тисячі дрібних round-trip (сам домінуючий
        # фактор зависання, більший за pidset N+1 вище). Тепер ОДИН запит по ВСІХ
        # датах одразу, а розподіл по (client_id, order_date) — у Python (None
        # порівнюється як звичайний ключ tuple, тож NULL-клієнти теж коректно
        # групуються без окремого is_(None)-філтра).
        distinct_dates = {odate for (_cid, odate) in groups.keys()}
        candidates = (
            session.query(Order).filter(
                Order.order_date.in_(distinct_dates),
                Order.id.notin_(touched_ids),
                Order.order_status_id.is_(None),
                Order.source_fingerprint.isnot(None),
            ).all()
            if distinct_dates else []
        )
        group_siblings: dict = {}  # (cid, odate) -> [Order]
        for c in candidates:
            group_siblings.setdefault((c.client_id, c.order_date), []).append(c)

        sibling_pidsets = _bulk_pidsets([c.id for c in candidates])

        ghost_ids = []
        for (cid, odate), tsets_all in groups.items():
            tsets = [s for s in tsets_all if s]
            if not tsets:
                continue
            for s in group_siblings.get((cid, odate), []):
                sset = sibling_pidsets.get(s.id, frozenset())
                if not sset:
                    continue
                # строгий containment із будь-яким touched-сиблінгом групи
                if any((sset < tset or tset < sset) for tset in tsets):
                    ghost_ids.append(s.id)

        if not ghost_ids:
            return {"removed": 0}

        # Бекап перед видаленням
        try:
            import json
            from datetime import datetime as _dt
            os.makedirs(_GHOST_SWEEP_BACKUP_DIR, exist_ok=True)
            orders_dump = [
                {c.name: (str(getattr(o, c.name)) if getattr(o, c.name) is not None else None)
                 for c in Order.__table__.columns}
                for o in session.query(Order).filter(Order.id.in_(ghost_ids)).all()
            ]
            items_dump = [
                {c.name: (str(getattr(it, c.name)) if getattr(it, c.name) is not None else None)
                 for c in OrderItem.__table__.columns}
                for it in session.query(OrderItem).filter(OrderItem.order_id.in_(ghost_ids)).all()
            ]
            path = os.path.join(_GHOST_SWEEP_BACKUP_DIR, f"{_dt.now():%Y%m%d_%H%M%S}_ghosts.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ghost_ids": ghost_ids, "orders": orders_dump, "order_items": items_dump},
                          f, ensure_ascii=False, indent=2)
            logger.warning(f"[ghost-sweep] backup {len(ghost_ids)} ghost order(s) → {path}")
        except Exception as be:
            logger.error(f"[ghost-sweep] backup failed ({be}); ABORT delete (safety)")
            return {"removed": 0}

        # Видалення: order_items (FK NO ACTION) → orders (решта CASCADE/SET NULL)
        session.query(OrderItem).filter(OrderItem.order_id.in_(ghost_ids)).delete(synchronize_session=False)
        session.query(Order).filter(Order.id.in_(ghost_ids)).delete(synchronize_session=False)
        session.commit()
        logger.warning(f"[ghost-sweep] removed {len(ghost_ids)} ghost order(s): {ghost_ids}")
        return {"removed": len(ghost_ids)}
    except Exception as e:
        session.rollback()
        logger.error(f"[ghost-sweep] failed (no orders removed): {e}")
        return {"removed": 0}


def _reconcile_orders_by_sheet_gid(session: Session, parsed_gids: set, touched_ids: set) -> dict:
    """Scoped-sweep привидів за gid вкладки-джерела.

    Видаляє ордери, у яких `source_sheet_gid` належить до вкладок, РЕАЛЬНО розпарсених
    цього прогону (parsed_gids), але які НЕ матчили жодного живого рядка (id ∉ touched)
    і є parser-origin (`source_fingerprint IS NOT NULL`). Вкладка = повна істина для
    своїх ордерів → відсутній рядок означає, що замовлення видалили/змінили в аркуші.

    Безпека:
      • parsed_gids містить ЛИШЕ вкладки, що пройшли парс без винятку (НЕ hash-skip,
        НЕ gated) — інакше частковий прогін не призведе до хибних видалень;
      • NULL gid (legacy, до фічі) НІКОЛИ не чіпаємо;
      • тільки parser-origin (ручні/legacy без fingerprint захищені);
      • JSON-бекап перед видаленням; помилка → нічого не видаляємо.
    """
    if not ORDER_GID_SWEEP or not parsed_gids:
        return {"removed": 0}
    try:
        from backend.models.models import Order, OrderItem
    except ImportError:
        from models.models import Order, OrderItem
    try:
        q = session.query(Order).filter(
            Order.source_sheet_gid.in_(list(parsed_gids)),
            Order.source_fingerprint.isnot(None),
        )
        if touched_ids:
            q = q.filter(Order.id.notin_(list(touched_ids)))
        ghosts = q.all()
        ghost_ids = [o.id for o in ghosts]
        if not ghost_ids:
            return {"removed": 0}

        # Бекап перед видаленням
        try:
            import json
            from datetime import datetime as _dt
            os.makedirs(_GHOST_SWEEP_BACKUP_DIR, exist_ok=True)
            orders_dump = [
                {c.name: (str(getattr(o, c.name)) if getattr(o, c.name) is not None else None)
                 for c in Order.__table__.columns}
                for o in ghosts
            ]
            items_dump = [
                {c.name: (str(getattr(it, c.name)) if getattr(it, c.name) is not None else None)
                 for c in OrderItem.__table__.columns}
                for it in session.query(OrderItem).filter(OrderItem.order_id.in_(ghost_ids)).all()
            ]
            path = os.path.join(_GHOST_SWEEP_BACKUP_DIR, f"{_dt.now():%Y%m%d_%H%M%S}_gid_ghosts.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ghost_ids": ghost_ids, "parsed_gids": list(parsed_gids),
                           "orders": orders_dump, "order_items": items_dump},
                          f, ensure_ascii=False, indent=2)
            logger.warning(f"[gid-sweep] backup {len(ghost_ids)} ghost order(s) → {path}")
        except Exception as be:
            logger.error(f"[gid-sweep] backup failed ({be}); ABORT delete (safety)")
            return {"removed": 0}

        session.query(OrderItem).filter(OrderItem.order_id.in_(ghost_ids)).delete(synchronize_session=False)
        session.query(Order).filter(Order.id.in_(ghost_ids)).delete(synchronize_session=False)
        session.commit()
        logger.warning(f"[gid-sweep] removed {len(ghost_ids)} ghost order(s): {ghost_ids}")
        return {"removed": len(ghost_ids)}
    except Exception as e:
        session.rollback()
        logger.error(f"[gid-sweep] failed (no orders removed): {e}")
        return {"removed": 0}


def run_orders_parsing(
    session: Session,
    mode: str = "quick",
    progress_cb: Optional[Callable] = None,
    force: bool = False,
) -> dict:
    """
    Parse Замовлення sheets → orders + order_items + clients.
    mode: 'quick' = last QUICK_SHEETS_COUNT date sheets
          'full'  = all date sheets
    force: True → обійти file-gate (manual-тригер завжди читає; див. _file_gate_check).
    """
    _reset_ref_lookup_cache()  # свіжий per-run кеш довідкових лукапів
    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)

    # Layer C: skip the whole run if the file is unchanged since last orders parse
    gated, file_lut = _file_gate_check(session, sh, ORDERS_ID, f"orders_{mode}", force=force)
    if gated:
        logger.info(f"[orders] Замовлення без змін з останнього {mode}-парсингу — пропуск")
        if progress_cb:
            progress_cb(100, "Замовлення без змін — пропуск")
        return {"mode": mode, "sheets": 0, "orders": 0, "items": 0,
                "updated": 0, "skipped": 0, "file_unchanged": True,
                "sheets_skipped_unchanged": 0}

    all_sheets = sh.worksheets()

    order_sheets = [ws for ws in all_sheets if not is_skip_sheet(ws.title)]
    if mode == "quick":
        order_sheets = order_sheets[:QUICK_SHEETS_COUNT]

    total_orders = total_items = total_updated = total_skipped = 0
    all_touched: set = set()  # ордери з живим рядком цього прогону (для ghost-reconcile)
    parsed_gids: set = set()  # gid вкладок, РЕАЛЬНО розпарсених цього прогону (для gid-sweep)
    sheets_skipped_unchanged = 0
    total_sheets = len(order_sheets)
    # Hash-skip only in 'quick' mode (see PARSER_VERSION / HASH_SKIP_ENABLED notes).
    use_hash_skip = HASH_SKIP_ENABLED and mode == "quick"

    # Order editing Phase A: snapshot user-locked order fields before the parser
    # may overwrite them; restored after the run so in-app edits survive reparse.
    order_locks_snapshot = _snapshot_order_locks(session)

    # Обчислюємо дати кожної вкладки (вкладки йдуть найновіша → найстаріша)
    sheet_dates = [
        parse_date_from_sheet_title(ws.title) or date.today()
        for ws in order_sheets
    ]

    for idx, ws, all_rows in _iter_sheets_with_rows(sh, order_sheets):
        sheet_date = sheet_dates[idx]

        # ── Layer B: skip sheets whose content is unchanged since last parse ──
        if use_hash_skip:
            content_hash = _compute_sheet_hash(all_rows)
            if _sheet_unchanged(session, ORDERS_ID, ws.id, content_hash):
                sheets_skipped_unchanged += 1
                logger.info(f"[orders] Skip unchanged sheet {idx+1}/{total_sheets}: {ws.title}")
                if progress_cb:
                    progress_cb(int((idx + 1) / total_sheets * 100), f"{ws.title}: unchanged (skip)")
                continue
        else:
            content_hash = None

        # cutoff: дата наступної (старішої) вкладки — замовлення з order_date <= cutoff
        # є carried-over і вже парсились з їх "рідної" вкладки → пропускаємо
        cutoff_date = sheet_dates[idx + 1] if idx + 1 < len(sheet_dates) else date.min
        logger.info(f"[orders] Parsing sheet {idx+1}/{total_sheets}: {ws.title} (cutoff={cutoff_date})")

        def _cb(done, total, _ws=ws, _idx=idx):
            if progress_cb:
                # Cap at 95: лишаємо 95→100 на пост-обробку (reconcile/sweep), щоб
                # бар не «застопорювався» на стелі фази поки йдуть тихі фінальні кроки.
                overall = min(95, int((_idx / total_sheets + done / total / total_sheets) * 100))
                progress_cb(overall, f"{_ws.title}: {done}/{total}")

        result = _parse_orders_sheet(ws, sheet_date, session, _cb, cutoff_date, prefetched_rows=all_rows)
        total_orders  += result["orders"]
        total_items   += result["items"]
        total_updated += result.get("updated", 0)
        total_skipped += result["skipped"]
        all_touched   |= result.get("touched_order_ids", set())
        # Вкладку успішно розпарсено повністю → її gid придатний для scoped-sweep.
        # (Hash-skip робить `continue` вище й сюди не доходить; виняток у парсі
        #  пробросився б і перервав прогін до sweep — отже часткового sweep не буде.)
        parsed_gids.add(ws.id)

        # Record sync marker only after the sheet parsed successfully, so a
        # mid-run failure leaves later sheets unmarked → they reparse next time.
        if use_hash_skip and content_hash is not None:
            _record_sheet_state(session, ORDERS_ID, ws.id, ws.title, content_hash)
            session.commit()

    # Прибрати ghost-замовлення, що лишились від зміни набору товарів у рядку
    # (вузька сигнатура + бекап; не глобальний sweep). Після повного циклу, коли
    # відомо які ордери мають живий рядок (all_touched).
    if progress_cb:
        progress_cb(96, "Прибирання привидів-замовлень…")
    ghost_result = _reconcile_evolved_order_ghosts(session, all_touched)

    # Scoped-sweep за gid вкладки: прибирає привидів навіть з реальним статусом і
    # disjoint-набором (як #64752/#64803), яких containment-сигнатура не бачить.
    if progress_cb:
        progress_cb(98, "Звірка дублів по вкладках…")
    gid_ghost_result = _reconcile_orders_by_sheet_gid(session, parsed_gids, all_touched)

    # Order editing Phase A: restore user-locked order fields the parser overwrote
    if progress_cb:
        progress_cb(100, "Відновлення локів і фіналізація…")
    orders_locks_restored = _restore_order_locks(session, order_locks_snapshot)
    if orders_locks_restored:
        logger.info(f"[orders] Restored {orders_locks_restored} user-locked order field(s) after reparse")

    # Layer C: record file marker only after a full successful run
    if file_lut is not None:
        _record_file_state(session, ORDERS_ID, f"orders_{mode}", file_lut)
        session.commit()

    return {
        "mode":    mode,
        "sheets":  total_sheets,
        "orders":  total_orders,
        "items":   total_items,
        "updated": total_updated,
        "skipped": total_skipped,
        "sheets_skipped_unchanged": sheets_skipped_unchanged,
        "order_locks_restored": orders_locks_restored,
        "ghosts_removed": ghost_result.get("removed", 0),
        "gid_ghosts_removed": gid_ghost_result.get("removed", 0),
    }


def run_workspace_parsing(
    session: Session,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Parse Воркспейс1 → merge into products or add as new.
    Always processes the single sheet (no mode/pagination needed).
    """
    _reset_ref_lookup_cache()  # свіжий per-run кеш довідкових лукапів
    gc = get_gc()
    sh = gc.open_by_key(WORKSPACE_ID)
    ws = sh.worksheet(WORKSPACE_SHEET)
    logger.info("[workspace] Parsing sheet '%s'", WORKSPACE_SHEET)

    def _cb(done, total):
        if progress_cb:
            pct = int(done / total * 100) if total else 0
            progress_cb(pct, f"{WORKSPACE_SHEET}: {done}/{total}")

    result = _parse_workspace_sheet(ws, session, _cb)

    # Уніфікація лічильника «Збіги»: парсинг вставляє кандидатів СТРОГИМ скорером,
    # а кнопка «Сканувати» — ЗВАЖЕНИМ. Через це число стрибало після кожного
    # парсингу. Тому одразу після парсингу перезапускаємо ЗВАЖЕНИЙ скан з
    # reset=True — він видаляє всі pending-кандидати й перезаписує єдиним
    # детермінованим скорером. Тепер: та сама БД → те саме число (і після рестарту).
    scan_summary = None
    if progress_cb:
        progress_cb(100, "Індексація збігів (lost-scan)…")
    try:
        from services.match_finder import scan_lost_products as _scan
    except ImportError:
        from backend.services.match_finder import scan_lost_products as _scan
    try:
        scan_summary = _scan(session, reset=True)
        logger.info(f"[workspace] auto-scan (weighted, unified count): {scan_summary}")
    except Exception as e:
        logger.warning(f"[workspace] auto-scan failed: {e}")

    return {
        "sheet":  WORKSPACE_SHEET,
        "merged": result["merged"],
        "added":  result["added"],
        "skipped":result["skipped"],
        "touched_product_ids": result["touched_product_ids"],
        "scan": scan_summary,
    }


def _canon_sheet_num(s) -> str:
    """Номер у порівнянному вигляді: без '#', без ';', UPPER."""
    return (s or "").strip().lstrip("#").rstrip(";").strip().upper()


def _num_base(n: str) -> str:
    """Базовий номер без парсерських суфіксів: 'Ф3477-2' → 'Ф3477', '0738(Л5)' → '0738'.

    Суфікси створює САМ парсер для повторів рядка з одним номером, і в аркуші їх
    нема — тож без цього зрізу кожна така ростовка виглядає «зниклою з вкладки».
    """
    n = re.sub(r"\([^)]*\)$", "", n or "")
    n = re.sub(r"\s*-\s*\d+$", "", n)
    return n.strip()


def _is_placeholder_num(n: str) -> bool:
    """'???', '???_123', '__tmp_rename_123', порожньо — сміттєві номери, які веде
    вручну людина у «проблемних». Автоматика їх НЕ прибирає."""
    n = (n or "").strip().lstrip("#")
    return (not n) or n.startswith("???") or n.startswith("__TMP_RENAME") or n.upper().startswith("__TMP_RENAME")


def _sheet_numbers(all_rows: list) -> set:
    """Канонічні номери з колонки «Номер» вкладки. Порожньо → порожня множина."""
    if not all_rows:
        return set()
    try:
        num_idx = all_rows[0].index("Номер")
    except (ValueError, AttributeError):
        return set()
    return {_canon_sheet_num(r[num_idx]) for r in all_rows[1:]
            if num_idx < len(r) and _canon_sheet_num(r[num_idx])}


def _reconcile_delivery_orphans(session: Session, delivery_id: int, all_rows: Optional[list],
                                max_share: Optional[float] = None, tag: str = "sync",
                                sheet_nums: Optional[set] = None,
                                seen_ids: Optional[set] = None,
                                all_sheet_nums: Optional[set] = None) -> int:
    """Point-wise видалення: товари БД(deliveryid), чий номер зник із вкладки аркуша
    і БЕЗ продажів. Scoped + protect-sold + JSON-бекап. Повертає к-сть видалених.

    `max_share` — запобіжник: якщо орфанами виявилась більша частка завозу, це
    ознака не перейменування, а кривого читання вкладки → лише лог. Ручний синк
    картки завозу передає None: там людина бачить дифф і тисне сама.

    `seen_ids` / `all_sheet_nums` — контекст ПРОГОНУ парсера (id, які журнал реально
    бачив, і всі номери з усіх розібраних вкладок). Без них вкладка судить наодинці
    й помиляється: `deliveryid` товару НЕ означає, що його рядок і досі в ЦІЙ вкладці.
    Саме на цьому 19.08.2026 згоріло 135 живих товарів.
    """
    if sheet_nums is None:
        sheet_nums = _sheet_numbers(all_rows)
    _canon = _canon_sheet_num
    if not sheet_nums:
        # Порожня/недочитана вкладка ≠ «завіз зник». Без цього гарду весь завіз
        # пішов би під ніж на першому ж збої читання.
        logger.warning(f"[{tag}] delivery {delivery_id}: у вкладці 0 номерів — reconcile пропущено")
        return 0

    rows = session.execute(
        text("""SELECT p.id, p.productnumber,
                       EXISTS(SELECT 1 FROM order_items oi WHERE oi.product_id = p.id) AS has_sales
                FROM products p WHERE p.deliveryid = :d"""),
        {"d": delivery_id},
    ).fetchall()

    def _is_orphan(pid, pnum) -> bool:
        if _is_placeholder_num(pnum):
            return False                      # «проблемні» веде людина, не автоматика
        if seen_ids is not None and pid in seen_ids:
            return False                      # журнал бачив цей рядок у цьому прогоні
        c = _canon(pnum)
        if c in sheet_nums or _num_base(c) in sheet_nums:
            return False                      # є у вкладці (з суфіксом чи без)
        if all_sheet_nums is not None and (c in all_sheet_nums or _num_base(c) in all_sheet_nums):
            return False                      # рядок переїхав в ІНШУ вкладку журналу
        return True

    orphan_ids = [r[0] for r in rows if not r[2] and _is_orphan(r[0], r[1])]

    # Продані під старим номером — не чіпаємо (історія продажу реальна), але
    # мовчати не можна: це рівно та пара-двійник, яку далі зливають руками.
    protected = [(r[0], r[1]) for r in rows if r[2] and _is_orphan(r[0], r[1])]
    if protected:
        logger.warning(
            f"[{tag}] delivery {delivery_id}: {len(protected)} товар(ів) зникли з вкладки, "
            f"але мають продажі — лишаю: {protected[:10]}"
        )

    if not orphan_ids:
        return 0
    if (max_share is not None and len(rows)
            and len(orphan_ids) > PARSE_ORPHAN_MIN_ABS
            and len(orphan_ids) / len(rows) > max_share):
        logger.warning(
            f"[{tag}] delivery {delivery_id}: орфанів {len(orphan_ids)}/{len(rows)} "
            f"(> {max_share:.0%}) — схоже на збій читання вкладки, видалення пропущено"
        )
        return 0
    # Бекап — умова видалення, а не побажання: не записався → не видаляємо.
    # (Раніше тут стояв `Product`, якого в цьому модулі нема на рівні модуля —
    # NameError мовчки з'їдався except'ом, і рядки зникали БЕЗ бекапу. Тому тепер
    # дамп читається сирим SQL, без ORM, і провал зупиняє прибирання.)
    try:
        import json as _json
        from datetime import datetime as _dt2
        os.makedirs(_GHOST_SWEEP_BACKUP_DIR, exist_ok=True)
        dump = [
            {k: (str(v) if v is not None else None) for k, v in row.items()}
            for row in session.execute(
                text("SELECT * FROM products WHERE id = ANY(:ids)"), {"ids": orphan_ids}
            ).mappings().all()
        ]
        if len(dump) != len(orphan_ids):
            raise RuntimeError(f"дамп {len(dump)} рядків замість {len(orphan_ids)}")
        path = os.path.join(_GHOST_SWEEP_BACKUP_DIR,
                            f"{_dt2.now():%Y%m%d_%H%M%S}_{tag}_orphans_d{delivery_id}.json")
        with open(path, "w") as f:
            _json.dump(dump, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.error(f"[{tag}] delivery {delivery_id}: бекап орфанів не вдався ({e}) — "
                     f"видалення СКАСОВАНО")
        return 0
    session.execute(text("DELETE FROM order_items WHERE product_id = ANY(:ids)"), {"ids": orphan_ids})
    session.execute(text("DELETE FROM products WHERE id = ANY(:ids)"), {"ids": orphan_ids})
    logger.info(f"[{tag}] delivery {delivery_id}: видалено {len(orphan_ids)} орфан-товар(ів)")
    return len(orphan_ids)


def sync_one_delivery_tab(session: Session, deliveryname: str) -> dict:
    """⚡ Точкова синхронізація ОДНІЄЇ вкладки завозу з аркушем → БД.

    Адди/правки: upsert через `_parse_products_sheet` (та сама логіка, що повний парс,
    але лише ця вкладка). Видалення: point-wise orphan-reconcile (scoped+protect-sold+
    бекап). Аркуш = джерело правди (writeback тримає його синхронним з in-app правками).
    Для loading-on-open у Картці завозу — поки не пройде, картка вантажиться.
    """
    # Анти-конфлікт: якщо фоновий повний парс уже йде — НЕ паримо ту саму вкладку
    # паралельно (той парс її все одно освіжить). Картка просто покаже БД.
    # ⚠️ Враховуємо ЛИШЕ ЖИВИЙ парс (heartbeat ≤120с). Інакше вбитий процес лишає
    # job у 'running' назавжди → синк вічно пропускається, ручні рядки не зʼявляються.
    busy = session.execute(
        text("""SELECT 1 FROM parsing_jobs
                WHERE status IN ('queued','running')
                  AND COALESCE(last_heartbeat_at, updated_at, started_at) > NOW() - INTERVAL '120 seconds'
                LIMIT 1""")
    ).first()
    if busy:
        return {"skipped": True, "reason": "parse_in_progress", "added": 0, "updated": 0, "deleted": 0}

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    try:
        ws = sh.worksheet(deliveryname)
    except Exception as e:
        raise RuntimeError(f"Вкладка '{deliveryname}' не знайдена: {e}")
    all_rows = ws.get_all_values()
    sheet_date = parse_date_from_sheet_title(ws.title)
    supplier_name = parse_supplier_from_sheet_title(ws.title)
    supplier_id = _get_or_create_supplier(session, supplier_name) if supplier_name else None
    financials = _parse_delivery_financials(all_rows)
    shipment_id = _get_or_create_shipment(
        session, ws.title, sheet_date, supplier_id,
        purchase_cost=financials["purchase_cost"], delivery_cost=financials["delivery_cost"],
        sheet_gid=ws.id,
    )
    res = _parse_products_sheet(ws, session, sheet_date, None, {}, supplier_id, shipment_id, prefetched_rows=all_rows)
    session.flush()
    deleted = _reconcile_delivery_orphans(session, shipment_id, all_rows) if shipment_id else 0
    session.commit()
    return {"shipment_id": shipment_id, "added": res.get("added", 0),
            "updated": res.get("updated", 0), "deleted": deleted}


def run_full_parsing(
    session: Session,
    mode: str = "quick",
    progress_cb: Optional[Callable] = None,
    force: bool = False,
) -> dict:
    """Run products → orders → workspace parsing sequentially.
    force: True → обійти file-gate у products+orders (manual-тригер завжди читає)."""
    def products_cb(pct, msg):
        if progress_cb:
            progress_cb(pct // 3, f"[Товари] {msg}")

    def orders_cb(pct, msg):
        if progress_cb:
            progress_cb(33 + pct // 3, f"[Замовлення] {msg}")

    def workspace_cb(pct, msg):
        if progress_cb:
            progress_cb(66 + pct // 3, f"[Воркспейс] {msg}")

    products_result  = run_products_parsing(session, mode=mode, progress_cb=products_cb, force=force)
    orders_result    = run_orders_parsing(session, mode=mode, progress_cb=orders_cb, force=force)
    workspace_result = run_workspace_parsing(session, progress_cb=workspace_cb)

    # ── Mark & Sweep: delete orphan products after full parse ────────────
    # ⚠️ DISABLED BY DEFAULT (2026-06-02): this deleted ALL products not "seen"
    # this run, using the same unreliable touched-heuristic the prune dry-run
    # proved produces false positives (legit rostovka variants not touched).
    # It silently removed ~2400 real-delivery products. Enable only with
    # PARSER_SWEEP=1 after the heuristic is made reliable / replaced by the
    # manual review tool. Sold products were protected (NOT EXISTS order_items).
    from sqlalchemy import text
    sweep_deleted = 0
    if mode == "full" and os.getenv("PARSER_SWEEP", "0") != "0":
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

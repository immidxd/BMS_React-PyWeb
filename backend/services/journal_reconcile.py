"""Звірка журналу з базою: що в картках уже нове, а в аркуші ще старе.

Навіщо окремо від черги
───────────────────────
Черга (``journal_sync``) страхує МАЙБУТНІ правки. Але за час, поки запис у
журнал працював «вистрелив і забув», частина правок туди так і не доїхала —
і черга про них не знає, бо її тоді не було. Цей модуль знаходить такий борг:
для кожного товару із залоченими полями (правка з картки) порівнює значення в
БД зі значенням у клітинці аркуша.

Чому лише залочені поля: журнал — джерело правди для всього іншого. Якщо
переписувати з БД усе підряд, ми затерли б у аркуші ручні правки людини, які
парсер ще не забрав. Лок — це якраз позначка «тут головна БД, бо правив
користувач у застосунку».

Читання аркуша дороге (весь аркуш за раз), тож звірка йде ПО ВКЛАДКАХ: одне
читання на вкладку, а не одне на кожне поле кожного товару.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import logging
import os
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Sheets API дає ~60 читань за хвилину на користувача. 111 вкладок поспіль
# з'їдають її за півхвилини, і решта вкладок падає з 429 — на першому прогоні
# так і сталося: 50 вкладок прочитано, 61 відкинуто квотою. Тому між вкладками
# тримаємо паузу, а на 429 чекаємо й пробуємо ще раз.
_MIN_INTERVAL_SEC = 1.3
_QUOTA_BACKOFF = [10, 30, 60]


def _read_all_values(ws):
    """ws.get_all_values() із повторами на 429 (вичерпана квота читань)."""
    for i, wait in enumerate([0] + _QUOTA_BACKOFF):
        if wait:
            time.sleep(wait)
        try:
            return ws.get_all_values()
        except Exception as e:  # gspread APIError
            if "429" not in str(e) or i == len(_QUOTA_BACKOFF):
                raise
            logger.info("[reconcile] квота вичерпана, чекаю %ss", _QUOTA_BACKOFF[i])
    return []


def _sp():
    try:
        from backend.scripts import sheets_parser as sp
    except ImportError:
        from scripts import sheets_parser as sp
    return sp


def _full_sheet_map(detail: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from backend.routers.deliveries import _product_full_sheet_map
    except ImportError:
        from routers.deliveries import _product_full_sheet_map
    return _product_full_sheet_map(detail)


def _product_service():
    try:
        from backend.services import product_service as ps
    except ImportError:
        from services import product_service as ps
    return ps


def _cell_str(field: str, value: Any) -> str:
    """Значення → рядок клітинки. Дзеркалить нормалізацію writeback."""
    if value is None:
        return ""
    if field in ("price", "oldprice"):
        try:
            fv = float(value)
            return str(int(fv)) if fv == int(fv) else str(fv)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _locked_fields(raw: Optional[str]) -> List[str]:
    return [f.strip() for f in (raw or "").split(",") if f.strip()]


def reconcile(db: Session, apply: bool = False,
              sheet_titles: Optional[List[str]] = None,
              max_sheets: Optional[int] = None,
              mode: str = "locked",
              numbers: Optional[List[str]] = None) -> Dict[str, Any]:
    """Знайти (і за apply=True — виправити) розбіжності картка→аркуш.

    Два режими:

    ``locked`` (типовий) — синхронізує лише ЗАЛОЧЕНІ поля, тобто ті, які людина
    правила в застосунку. Решта журналу лишається джерелом правди.

    ``fill_empty`` — заповнює ПОРОЖНІ клітинки з картки, незалежно від локів, і
    ніколи не чіпає клітинку, де вже щось є. Для випадку «рядок у журналі
    недозаповнений, хоча в картці все є»: дані з БД не губляться, а порожнеча
    в аркуші не є чиєюсь думкою, яку можна затерти.

    ``numbers`` звужує роботу до конкретних номерів товарів.

    Повертає звіт. Нічого не пише, поки apply не True.
    """
    if mode not in ("locked", "fill_empty"):
        raise ValueError("mode має бути 'locked' або 'fill_empty'")
    sp = _sp()
    ps = _product_service()

    where = ["TRUE"]
    params: Dict[str, Any] = {}
    if mode == "locked":
        where.append("COALESCE(p.manually_edited_fields, '') <> ''")
    if numbers:
        where.append("REPLACE(UPPER(p.productnumber), '#', '') = ANY(:nums)")
        params["nums"] = [n.replace('#', '').upper() for n in numbers]
    rows = db.execute(text(f"""
        SELECT p.id, p.productnumber, p.manually_edited_fields, d.deliveryname
        FROM products p
        JOIN deliveries d ON d.id = p.deliveryid
        WHERE {' AND '.join(where)}
        ORDER BY d.deliveryname, p.productnumber
    """), params).fetchall()

    by_sheet: Dict[str, List[Any]] = {}
    for r in rows:
        if sheet_titles and r.deliveryname not in sheet_titles:
            continue
        by_sheet.setdefault(r.deliveryname, []).append(r)

    report: Dict[str, Any] = {
        "sheets_total": len(by_sheet), "sheets_done": 0,
        "products": 0, "diffs": 0, "applied": 0,
        "skipped_per_item": 0, "no_column": 0, "row_not_found": 0,
        "by_field": {}, "examples": [], "errors": [],
    }

    gc = sp.get_gc()
    sh = gc.open_by_key(sp.JOURNAL_ID)

    for s_i, (sheet_title, prods) in enumerate(by_sheet.items()):
        if max_sheets is not None and s_i >= max_sheets:
            break
        if s_i:
            time.sleep(_MIN_INTERVAL_SEC)   # тримаємось у межах квоти читань
        try:
            ws = sh.worksheet(sheet_title)
            all_values = _read_all_values(ws)
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{sheet_title}: {e}")
            continue
        if not all_values:
            report["errors"].append(f"{sheet_title}: порожня вкладка")
            continue

        header = [h.strip() for h in all_values[0]]
        if "Номер" not in header:
            report["errors"].append(f"{sheet_title}: нема колонки «Номер»")
            continue
        num_idx = header.index("Номер")

        # номер → індекси рядків аркуша (ростовка може займати кілька)
        rows_by_num: Dict[str, List[int]] = {}
        for r_i, row in enumerate(all_values[1:], start=2):
            key = sp._canon_pnum_for_match(row[num_idx] if num_idx < len(row) else "")
            if key:
                rows_by_num.setdefault(key, []).append(r_i)

        updates, backups = [], []
        for pr in prods:
            report["products"] += 1
            detail = ps.get_product_with_relations(db, pr.id)
            if not detail:
                continue
            expected_row = _full_sheet_map(detail)
            target = sp._canon_pnum_for_match(pr.productnumber)
            sheet_rows = rows_by_num.get(target, [])
            if not sheet_rows:
                report["row_not_found"] += 1
                continue

            fields = (_locked_fields(pr.manually_edited_fields) if mode == "locked"
                      else list(sp.WRITEBACK_FIELD_HEADERS.keys()))
            for field in fields:
                header_name = sp.WRITEBACK_FIELD_HEADERS.get(field)
                if not header_name or header_name not in header:
                    report["no_column"] += 1
                    continue
                if field in sp.PER_ITEM_WRITEBACK_FIELDS and len(sheet_rows) > 1:
                    # Писати в усі рядки ростовки = затерти сусідні розміри.
                    report["skipped_per_item"] += 1
                    continue
                if header_name not in expected_row:
                    # Порожнє значення в БД: НЕ чистимо клітинку — порожнеча в
                    # застосунку частіше означає «ще не заповнили», ніж «зітри».
                    continue
                col_idx = header.index(header_name)
                new_str = _cell_str(field, expected_row.get(header_name))
                for r_i in sheet_rows:
                    row = all_values[r_i - 1]
                    old = row[col_idx] if col_idx < len(row) else ""
                    if old.strip() == new_str.strip():
                        continue
                    if mode == "fill_empty" and old.strip():
                        continue   # у клітинці вже щось є — не наша справа
                    if not new_str.strip():
                        continue   # нема чим заповнювати
                    import gspread as _gspread
                    a1 = _gspread.utils.rowcol_to_a1(r_i, col_idx + 1)
                    # Числові поля (Ціна/Стара ціна/Рік) мусять лягти ЧИСЛОМ, а не
                    # текстом: журнал рахує по них суми. Той самий поділ, що й у
                    # writeback_field_to_journal — RAW лише для текстових полів.
                    bucket = ("raw" if field in sp.WRITEBACK_TEXT_FIELDS else "user")
                    updates.append({"range": a1, "values": [[new_str]], "_bucket": bucket})
                    backups.append({"sheet": sheet_title, "a1": a1, "number": pr.productnumber,
                                    "field": field, "header": header_name,
                                    "old": old, "new": new_str})
                    report["diffs"] += 1
                    report["by_field"][field] = report["by_field"].get(field, 0) + 1
                    if len(report["examples"]) < 25:
                        report["examples"].append({
                            "sheet": sheet_title, "number": pr.productnumber,
                            "column": header_name, "sheet_value": old, "card_value": new_str,
                        })

        if apply and updates:
            backup_dir = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "scripts", "writeback_backups")
            os.makedirs(backup_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = "".join(ch for ch in sheet_title if ch.isalnum() or ch in "._-()")[:60]
            path = os.path.join(backup_dir, f"{stamp}_reconcile_{safe}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(backups, fh, ensure_ascii=False, indent=1)
            # Пишемо порціями: один batch_update на кілька сотень клітинок,
            # окремо текстові й числові (різний value_input_option).
            for bucket, opt in (("raw", "RAW"), ("user", "USER_ENTERED")):
                part = [{k: v for k, v in u.items() if k != "_bucket"}
                        for u in updates if u["_bucket"] == bucket]
                for i in range(0, len(part), 400):
                    if i or bucket == "user":
                        time.sleep(_MIN_INTERVAL_SEC)
                    ws.batch_update(part[i:i + 400], value_input_option=opt)
            report["applied"] += len(updates)
            logger.info("[reconcile] %s: %d клітинок оновлено, backup=%s",
                        sheet_title, len(updates), path)

        report["sheets_done"] += 1

    return report

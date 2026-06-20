"""Journal WRITE-layer — створення завозів (вкладок) + додавання/видалення рядків товару.

⚠️ Пише у ЖИВИЙ журнал (JOURNAL_ID). За фіча-флагом PARSER_ADD_PRODUCT (default OFF).
Наявний парсер/writeback — UPDATE-only; це ADD-сторона (Фаза 0 фічі «Додати товар»).
Дизайн і рішення → memory [[project_add_product_feature]].

Безпека:
- create_delivery_tab клонує шаблон 'New' (валідуємо, що існує — його легко видалити).
- append_product_row пише cols 1..MAIN_COLS за НАЗВОЮ заголовка (ніколи за позицією),
  у перший вільний рядок за колонкою «Номер» (у кінець).
- delete_product_row ОЧИЩАЄ cols 1..MAIN_COLS рядка — НІКОЛИ delete_rows (знищив би
  блок «Інформація про завоз» праворуч, col56+).
- бекап вкладки перед append/delete у journal_add_backups/.
"""
import os
import json
import logging
from datetime import datetime as _dt
from typing import Optional, Dict, Any

import gspread.utils as _gsu

logger = logging.getLogger(__name__)

# Єдине джерело доступу до журналу — парсер (той самий gc/JOURNAL_ID).
try:
    from scripts.sheets_parser import get_gc, JOURNAL_ID
except ImportError:
    from backend.scripts.sheets_parser import get_gc, JOURNAL_ID

# Default ON (2026-06-19): фіча перевірена end-to-end, користувач активно користується.
# Вимкнути за потреби: PARSER_ADD_PRODUCT=0.
ADD_PRODUCT_ENABLED = os.getenv("PARSER_ADD_PRODUCT", "1") != "0"

TEMPLATE_TITLE = "New"        # клон-джерело для нового завозу
MAIN_COLS = 52                # колонки полів товару (1..52); блок «Завоз» = col56+
HEADER_ROW = 1
NEW_DELIVERY_INDEX = 2        # нові вкладки після Publications/Suppliers (зверху)

# Блок «Завоз»: мітки в col63, значення в col64 (з обстеження живої вкладки).
_BLOCK_VALUE_COL = 64
_BLOCK_DATE_ROW = 2           # «Дата завозу»
_BLOCK_SUM_ROW = 3            # «Сума» (purchase_cost)
_BLOCK_DELIVERY_ROW = 4       # «Сума доставки» (delivery_cost)

_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal_add_backups")


def _guard():
    if not ADD_PRODUCT_ENABLED:
        raise RuntimeError("Додавання у журнал вимкнено (PARSER_ADD_PRODUCT=0)")


def _open_journal():
    return get_gc().open_by_key(JOURNAL_ID)


def get_template_ws(sh):
    """Знайти й валідувати шаблон 'New'. Помилка якщо відсутній (його легко видалити)."""
    for ws in sh.worksheets():
        if ws.title.strip().lower() == TEMPLATE_TITLE.lower():
            return ws
    raise RuntimeError(
        f"Шаблонна вкладка '{TEMPLATE_TITLE}' відсутня у журналі — "
        f"відновіть її перед створенням завозу"
    )


def _header_columns(ws) -> Dict[str, int]:
    """{назва_заголовка: 1-based індекс колонки} з рядка заголовка."""
    out: Dict[str, int] = {}
    for i, h in enumerate(ws.row_values(HEADER_ROW), 1):
        h = (h or "").strip()
        if h and h not in out:
            out[h] = i
    return out


def _backup_tab(ws, tag: str) -> str:
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    path = os.path.join(_BACKUP_DIR, f"{_dt.now():%Y%m%d_%H%M%S}_{tag}_{ws.id}.json")
    with open(path, "w") as f:
        json.dump({"title": ws.title, "gid": ws.id, "values": ws.get_all_values()},
                  f, ensure_ascii=False)
    return path


def _first_free_product_row(ws, num_col: int) -> int:
    """Перший вільний рядок за колонкою «Номер» — додаємо В КІНЕЦЬ (після останнього)."""
    col_vals = ws.col_values(num_col)
    last = HEADER_ROW
    for i, v in enumerate(col_vals, 1):
        if i > HEADER_ROW and v and v.strip():
            last = i
    return last + 1


def create_delivery_tab(deliveryname: str, deliverydate=None, purchase_cost=None,
                        delivery_cost=None, dry_run: bool = False) -> Dict[str, Any]:
    """Клон 'New' → rename `deliveryname` → заповнити блок «Завоз» → {title, gid}.

    dry_run=True → клон + перевірка + ВИДАЛЕННЯ тимчасової вкладки (нічого не лишає).
    БД (deliveries row) пише викликач (роутер) — тут лише аркуш.
    """
    _guard()
    sh = _open_journal()
    tmpl = get_template_ws(sh)
    title = (deliveryname or "").strip()
    if not title:
        raise RuntimeError("Порожня назва завозу")

    existing = {w.title.strip().lower() for w in sh.worksheets()}
    if title.lower() in existing and not dry_run:
        raise RuntimeError(f"Вкладка '{title}' вже існує")

    dup_title = f"__dryrun_{_dt.now():%H%M%S%f}" if dry_run else title
    new_ws = sh.duplicate_sheet(
        tmpl.id, insert_sheet_index=NEW_DELIVERY_INDEX, new_sheet_name=dup_title
    )
    try:
        # Заповнюємо лише 3 значення блоку (Всього/К-сть — авто-формули шаблону).
        block = []
        if deliverydate is not None:
            block.append({"range": _gsu.rowcol_to_a1(_BLOCK_DATE_ROW, _BLOCK_VALUE_COL),
                          "values": [[str(deliverydate)]]})
        if purchase_cost is not None:
            block.append({"range": _gsu.rowcol_to_a1(_BLOCK_SUM_ROW, _BLOCK_VALUE_COL),
                          "values": [[str(purchase_cost)]]})
        if delivery_cost is not None:
            block.append({"range": _gsu.rowcol_to_a1(_BLOCK_DELIVERY_ROW, _BLOCK_VALUE_COL),
                          "values": [[str(delivery_cost)]]})
        if block:
            new_ws.batch_update(block)

        result = {"title": new_ws.title, "gid": new_ws.id, "dry_run": dry_run}
        if dry_run:
            sh.del_worksheet(new_ws)
        return result
    except Exception:
        # пів-створену вкладку прибираємо, щоб не лишати сироту
        try:
            sh.del_worksheet(new_ws)
        except Exception:
            pass
        raise


def delete_delivery_tab(gid: int, dry_run: bool = False) -> Dict[str, Any]:
    """⚠️ ВИДАЛИТИ ЦІЛУ вкладку завозу за gid (deletes ALL products in it).

    Використання: (1) відкат напів-створеного завозу при збої БД-вставки;
    (2) майбутнє «видалити завіз» (з підтвердженням + чисткою deliveries/products).
    dry_run=True → лише знайти й повернути title, не видаляти.
    """
    _guard()
    sh = _open_journal()
    ws = next((w for w in sh.worksheets() if w.id == gid), None)
    if ws is None:
        return {"deleted": False, "reason": "not found", "gid": gid}
    title = ws.title
    if dry_run:
        return {"deleted": False, "title": title, "gid": gid, "dry_run": True}
    sh.del_worksheet(ws)
    return {"deleted": True, "title": title, "gid": gid}


def append_product_row(delivery_title: str, field_values: Dict[str, Any],
                       dry_run: bool = False) -> Dict[str, Any]:
    """Дописати рядок товару у вкладку завозу. field_values keyed за НАЗВОЮ заголовка.

    Пише лише cols 1..MAIN_COLS (блок «Завоз» праворуч недоторканий). У КІНЕЦЬ.
    Повертає {row, gid, written:{col->val}}.
    """
    _guard()
    sh = _open_journal()
    ws = sh.worksheet(delivery_title)
    headers = _header_columns(ws)
    if "Номер" not in headers:
        raise RuntimeError(f"У вкладці '{delivery_title}' немає колонки «Номер»")
    num_col = headers["Номер"]

    # Зіставляємо значення з колонками за назвою (невідомі назви ігноруємо з логом).
    row_idx = _first_free_product_row(ws, num_col)
    row_vec = [""] * MAIN_COLS
    written = {}
    for name, val in field_values.items():
        col = headers.get(name)
        if not col:
            logger.warning("[add] невідома колонка '%s' у '%s' — пропуск", name, delivery_title)
            continue
        if col > MAIN_COLS:
            logger.warning("[add] колонка '%s' (col%d) поза товарним блоком — пропуск", name, col)
            continue
        row_vec[col - 1] = "" if val is None else str(val)
        written[name] = row_vec[col - 1]

    if dry_run:
        return {"row": row_idx, "gid": ws.id, "written": written, "dry_run": True}

    _backup_tab(ws, "append")
    rng = f"{_gsu.rowcol_to_a1(row_idx, 1)}:{_gsu.rowcol_to_a1(row_idx, MAIN_COLS)}"
    ws.batch_update([{"range": rng, "values": [row_vec]}])
    return {"row": row_idx, "gid": ws.id, "written": written, "dry_run": False}


def read_delivery_productnumbers(delivery_title: str) -> set:
    """Канонічні номери (без #/;, UPPER) з колонки «Номер» вкладки завозу.

    Read-only (БЕЗ флага). Для deletion-reconcile: порівняти з products(deliveryid)
    → знайти товари, що зникли з аркуша (видалені вручну в журналі).
    """
    sh = _open_journal()
    ws = sh.worksheet(delivery_title)
    headers = _header_columns(ws)
    num_col = headers.get("Номер")
    if not num_col:
        raise RuntimeError(f"Вкладка '{delivery_title}' без колонки «Номер»")
    out = set()
    for v in ws.col_values(num_col)[HEADER_ROW:]:  # пропустити заголовок
        c = (v or "").strip().lstrip("#").rstrip(";").strip().upper()
        if c:
            out.add(c)
    return out


def delete_product_row(delivery_title: str, productnumber: str,
                       dry_run: bool = False) -> Dict[str, Any]:
    """ОЧИСТИТИ товарні клітинки (cols 1..MAIN_COLS) рядка з цим номером.

    ⚠️ НЕ delete_rows — це знищило б блок «Завоз» праворуч. Матч за «Номер»
    (canonical: без #/;, регістронезалежно). Повертає {rows_cleared}.
    """
    _guard()
    sh = _open_journal()
    ws = sh.worksheet(delivery_title)
    headers = _header_columns(ws)
    num_col = headers.get("Номер")
    if not num_col:
        raise RuntimeError(f"У вкладці '{delivery_title}' немає колонки «Номер»")

    def _canon(s: str) -> str:
        return (s or "").strip().lstrip("#").rstrip(";").strip().upper()

    target = _canon(productnumber)
    col_vals = ws.col_values(num_col)
    rows = [i for i, v in enumerate(col_vals, 1) if i > HEADER_ROW and _canon(v) == target]
    if not rows:
        # Рядка вже нема в аркуші (орфан — видалили вручну в журналі). Це НЕ помилка:
        # викликач (delete endpoint) однаково прибере товар із БД.
        return {"rows": [], "gid": ws.id, "not_found": True}

    if dry_run:
        return {"rows": rows, "gid": ws.id, "dry_run": True}

    _backup_tab(ws, "delete")
    ranges = [f"{_gsu.rowcol_to_a1(r, 1)}:{_gsu.rowcol_to_a1(r, MAIN_COLS)}" for r in rows]
    ws.batch_clear(ranges)
    return {"rows": rows, "gid": ws.id, "dry_run": False}

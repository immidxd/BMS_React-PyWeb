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
import time
import logging
from datetime import datetime as _dt
from typing import Optional, Dict, Any, Callable

import gspread.utils as _gsu

logger = logging.getLogger(__name__)


class JournalTransientError(Exception):
    """Транзієнтна помилка зв'язку з Google (SSL/мережа/429/5xx) — варто повторити."""


# Підрядки в тексті помилки, що сигналізують про транзієнтний (мережевий) збій.
_TRANSIENT_MARKERS = (
    "ssl", "certificate", "max retries", "connection", "timed out", "timeout",
    "temporarily", "503", "502", "500", "429", "rate limit", "ratelimit",
    "broken pipe", "reset by peer", "eof occurred", "handshake",
)


def _is_transient(exc: Exception) -> bool:
    s = f"{type(exc).__name__}: {exc}".lower()
    return any(m in s for m in _TRANSIENT_MARKERS)


def _with_retry(fn: Callable, *, attempts: int = 4, base_delay: float = 0.8, what: str = "журнал"):
    """Виконати мережеву операцію з ретраями на транзієнтних збоях (експон. backoff).
    Перманентні помилки (нема вкладки, нема колонки тощо) НЕ ретраяться — кидаються одразу."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if not _is_transient(e):
                raise
            if i < attempts - 1:
                delay = base_delay * (2 ** i)
                logger.warning("[add] транзієнтний збій (%s), спроба %d/%d, чекаю %.1fс: %s",
                               what, i + 1, attempts, delay, e)
                time.sleep(delay)
    raise JournalTransientError(
        f"Не вдалося зв'язатися з Google Sheets після {attempts} спроб ({what}). "
        f"Це тимчасова проблема мережі/з'єднання — спробуйте ще раз. Деталі: {last}"
    )

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
    title = (deliveryname or "").strip()
    if not title:
        raise RuntimeError("Порожня назва завозу")

    # Прелюдія (auth/open/template/список вкладок) — read-only, ретраїмо на SSL/мережі.
    def _prelude():
        sh = _open_journal()
        tmpl = get_template_ws(sh)
        existing = {w.title.strip().lower() for w in sh.worksheets()}
        return sh, tmpl, existing
    sh, tmpl, existing = _with_retry(_prelude, what="створення вкладки завозу")
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

    def _do() -> Dict[str, Any]:
        sh = _open_journal()
        ws = sh.worksheet(delivery_title)
        headers = _header_columns(ws)
        if "Номер" not in headers:
            # Перманентна помилка структури — не ретраїмо.
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

    return _with_retry(_do, what=f"запис рядка у «{delivery_title}»")


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


# ── Сортування рядків завозу за номером (Фіча 1) ──────────────────────────────
import re as _re


def _pn_sort_key(pnum: str, original_idx: int):
    """Ключ сортування номера: (prefix, base_int, suffix_int, original_idx).
    Узгоджено з get_next_product_number: `<prefix><base>(-<suffix>)?`. Ростовка-суфікс
    тримає пари поряд (Ф1810 перед Ф1810-2). Непарсовані → в кінець (стабільно)."""
    s = (pnum or "").strip().lstrip("#").rstrip(";").strip()
    m = _re.match(r"^(\D*)(\d+)(?:-(\d+))?$", s)
    if not m:
        return (1, "￿", 0, 0, original_idx)  # непарсовані — в кінець
    prefix = (m.group(1) or "").upper()
    base = int(m.group(2))
    suffix = int(m.group(3)) if m.group(3) else 0
    return (0, prefix, base, suffix, original_idx)


def reorder_delivery_rows(delivery_title: str, dry_run: bool = False) -> Dict[str, Any]:
    """Впорядкувати ТОВАРНІ рядки вкладки за номером (зростання) — переписує лише
    cols 1..MAIN_COLS у відсортованому порядку. Блок «Завоз» (col56+) НЕ чіпає
    (інші колонки). НІКОЛИ delete_rows. Бекап + retry. Повертає {reordered, gid}."""
    _guard()

    def _do() -> Dict[str, Any]:
        sh = _open_journal()
        ws = sh.worksheet(delivery_title)
        headers = _header_columns(ws)
        num_col = headers.get("Номер")
        if not num_col:
            raise RuntimeError(f"У вкладці '{delivery_title}' немає колонки «Номер»")

        all_vals = ws.get_all_values()
        # Товарні рядки = рядки > HEADER_ROW із непорожнім «Номер». Зберігаємо cols 1..52.
        product_rows = []  # (original_idx, row_vec[0..51])
        occupied_rows = []  # фактичні номери рядків аркуша, що були зайняті
        for r_i, row in enumerate(all_vals[HEADER_ROW:], start=HEADER_ROW + 1):
            cell = row[num_col - 1] if num_col - 1 < len(row) else ""
            if cell and cell.strip():
                vec = [(row[c] if c < len(row) else "") for c in range(MAIN_COLS)]
                product_rows.append((cell, vec, len(product_rows)))
                occupied_rows.append(r_i)

        if len(product_rows) <= 1:
            return {"reordered": len(product_rows), "gid": ws.id, "noop": True}

        ordered = sorted(product_rows, key=lambda t: _pn_sort_key(t[0], t[2]))
        if [t[2] for t in ordered] == list(range(len(product_rows))):
            return {"reordered": len(product_rows), "gid": ws.id, "noop": True}  # вже відсортовано

        if dry_run:
            return {"reordered": len(product_rows), "gid": ws.id,
                    "order": [t[0] for t in ordered], "dry_run": True}

        _backup_tab(ws, "reorder")
        # Пишемо відсортовані cols1-52 у рядки 2..(2+P-1) одним батчем.
        start = HEADER_ROW + 1
        end = start + len(ordered) - 1
        rng = f"{_gsu.rowcol_to_a1(start, 1)}:{_gsu.rowcol_to_a1(end, MAIN_COLS)}"
        values = [t[1] for t in ordered]
        updates = [{"range": rng, "values": values}]
        ws.batch_update(updates)
        # Очистити «хвіст» (рядки, що були зайняті, але тепер за межами P) — cols1-52.
        tail = [r for r in occupied_rows if r > end]
        if tail:
            ws.batch_clear([f"{_gsu.rowcol_to_a1(r,1)}:{_gsu.rowcol_to_a1(r,MAIN_COLS)}" for r in tail])
        return {"reordered": len(ordered), "gid": ws.id, "dry_run": False}

    return _with_retry(_do, what=f"сортування «{delivery_title}»")


# ── Інфо-блок «Інформація про завоз» (Фіча 4) ─────────────────────────────────
# Редаговані поля (allowlist): лише користувацький ввід. Формули/статистику не чіпаємо.
INFO_EDITABLE_LABELS = [
    "Дата завозу", "Сума", "Сума доставки", "Промокод", "Коментар",
    "Очікувана к-сть речей", "Статус", "Писав", "Дата початку", "Дата завершення",
]
# Лейбли, які можна СТВОРИТИ у блоці, якщо їх там ще нема (нові поля — пишемо у вільний
# рядок колонки-лейблів блоку). Решта — лише якщо лейбл уже існує в шаблоні.
INFO_CREATABLE_LABELS = {"Дата початку", "Дата завершення"}


def _find_label_cell(all_vals, label: str, min_col: int = MAIN_COLS):
    """(row_idx, col_idx) 1-based комірки з ТОЧНОЮ назвою label (перше входження).

    ⚠️ Шукаємо ЛИШЕ у зоні блоку «Завоз» (col > min_col=MAIN_COLS=52). Інакше лейбли
    на кшталт «Статус»/«Коментар» збіглися б із ЗАГОЛОВКАМИ товарних колонок (рядок 1,
    cols 1..52) → читали/писали б не ту комірку (зіпсувало б заголовок товару)."""
    lab = label.strip().lower()
    for r_i, row in enumerate(all_vals, start=1):
        for c_i, cell in enumerate(row, start=1):
            if c_i <= min_col:
                continue
            if str(cell).strip().lower() == lab:
                return r_i, c_i
    return None, None


def rename_product_row(delivery_title: str, old_number: str, new_number: str,
                       size_hint: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Перейменувати «Номер» рядка товару в журналі (old→new).
    Single рядок → перейменувати. Ростовка (кілька рядків одного номера) → звузити за
    розміром (size_hint проти «Розмір»/«Буквений»/«СМ»); якщо все одно неоднозначно —
    {ambiguous}. Бекап + retry. НІКОЛИ не чіпає інші рядки/блок."""
    _guard()

    def _canon(s: str) -> str:
        return (s or "").strip().lstrip("#").rstrip(";").strip().upper()

    def _do() -> Dict[str, Any]:
        sh = _open_journal()
        ws = sh.worksheet(delivery_title)
        headers = _header_columns(ws)
        num_col = headers.get("Номер")
        if not num_col:
            raise RuntimeError(f"У вкладці '{delivery_title}' немає колонки «Номер»")
        all_vals = ws.get_all_values()
        target = _canon(old_number)
        rows = [r_i for r_i, row in enumerate(all_vals[HEADER_ROW:], start=HEADER_ROW + 1)
                if _canon(row[num_col - 1] if num_col - 1 < len(row) else "") == target]
        if not rows:
            return {"renamed": 0, "not_found": True, "gid": ws.id}
        if len(rows) > 1 and size_hint:
            size_cols = [headers[h] for h in ("Розмір", "Буквений", "СМ") if h in headers]
            sh_c = str(size_hint).strip().lower()
            narrowed = [r for r in rows if any(
                (all_vals[r - 1][c - 1].strip().lower() if c - 1 < len(all_vals[r - 1]) else "") == sh_c
                for c in size_cols)]
            if len(narrowed) == 1:
                rows = narrowed
        if len(rows) > 1:
            return {"renamed": 0, "ambiguous": True, "rows": rows, "gid": ws.id}
        if dry_run:
            return {"renamed": 1, "row": rows[0], "gid": ws.id, "dry_run": True}
        _backup_tab(ws, "rename")
        a1 = _gsu.rowcol_to_a1(rows[0], num_col)
        ws.batch_update([{"range": a1, "values": [[new_number]]}], value_input_option="USER_ENTERED")
        return {"renamed": 1, "row": rows[0], "gid": ws.id}

    return _with_retry(_do, what=f"перейменування номера у «{delivery_title}»")


def _block_label_col(all_vals) -> Optional[int]:
    """Колонка-лейблів блоку «Завоз» (де живуть «Дата завозу»/«Сума»/«Статус»…) — 1-based.
    Визначаємо за наявним лейблом, щоб не хардкодити col63."""
    for anchor in ("Дата завозу", "Сума", "Статус", "Промокод"):
        r, c = _find_label_cell(all_vals, anchor)
        if c is not None:
            return c
    return None


def _first_free_block_row(all_vals, label_col: int) -> Optional[int]:
    """Перший вільний рядок у колонці-лейблів блоку (де і лейбл, і значення-праворуч порожні).
    Шукаємо в межах ~30 рядків (блок невеликий)."""
    max_r = min(len(all_vals), 30)
    for r in range(2, max_r + 1):
        row = all_vals[r - 1] if r - 1 < len(all_vals) else []
        lab = (row[label_col - 1].strip() if label_col - 1 < len(row) else "")
        val = (row[label_col].strip() if label_col < len(row) else "")
        if not lab and not val:
            return r
    # якщо все зайнято в межах 30 — додати після
    return min(len(all_vals), 30) + 1


def read_delivery_info_block(delivery_title: str) -> Dict[str, Any]:
    """Прочитати редаговані поля блоку «Інформація про завоз» (label→значення-праворуч).
    Формула-комірки → editable=False (захист від клоберу `Всього`/`К-сть речей`).
    Поля, яких ще нема в блоці (Дата початку/завершення) — повертаємо з value='' editable=True."""
    sh = _open_journal()
    ws = sh.worksheet(delivery_title)
    all_vals = ws.get_all_values()
    formulas = ws.get_all_values(value_render_option="FORMULA")

    def _val_at(rows, r, c):
        if r is None:
            return ""
        rr = rows[r - 1] if r - 1 < len(rows) else []
        return rr[c - 1] if c - 1 < len(rr) else ""

    fields = []
    for label in INFO_EDITABLE_LABELS:
        r, c = _find_label_cell(all_vals, label)
        if r is None:
            # Нове поле (Дата початку/завершення) — ще нема в блоці: показуємо порожнім.
            if label in INFO_CREATABLE_LABELS:
                fields.append({"label": label, "value": "", "editable": True})
            continue
        value = _val_at(all_vals, r, c + 1)
        raw = _val_at(formulas, r, c + 1)
        is_formula = isinstance(raw, str) and raw.startswith("=")
        fields.append({"label": label, "value": value, "editable": not is_formula})
    return {"title": delivery_title, "gid": ws.id, "fields": fields}


def update_delivery_info_block(delivery_title: str, changes: Dict[str, Any],
                               dry_run: bool = False) -> Dict[str, Any]:
    """Записати значення (label→cell-праворуч) для полів з allowlist. Пропускає
    формула-комірки. Бекап + retry. changes = {label: value}."""
    _guard()
    allowed = {l for l in INFO_EDITABLE_LABELS}

    def _do() -> Dict[str, Any]:
        sh = _open_journal()
        ws = sh.worksheet(delivery_title)
        all_vals = ws.get_all_values()
        formulas = ws.get_all_values(value_render_option="FORMULA")
        updates, written, skipped = [], {}, []
        for label, value in changes.items():
            if label not in allowed:
                skipped.append({"label": label, "reason": "not_editable"})
                continue
            r, c = _find_label_cell(all_vals, label)
            if r is None:
                # Нове поле — створюємо лейбл у вільному рядку блоку (тільки creatable).
                if label not in INFO_CREATABLE_LABELS:
                    skipped.append({"label": label, "reason": "not_found"})
                    continue
                lc = _block_label_col(all_vals)
                fr = _first_free_block_row(all_vals, lc) if lc else None
                if not lc or not fr:
                    skipped.append({"label": label, "reason": "no_free_block_row"})
                    continue
                # лейбл у колонку-лейблів + значення праворуч
                updates.append({"range": _gsu.rowcol_to_a1(fr, lc), "values": [[label]]})
                new_str = "" if value is None else str(value)
                updates.append({"range": _gsu.rowcol_to_a1(fr, lc + 1), "values": [[new_str]]})
                written[label] = new_str
                # позначимо рядок зайнятим у локальній копії (на випадок 2-х нових за раз)
                while len(all_vals) < fr:
                    all_vals.append([])
                while len(all_vals[fr - 1]) <= lc:
                    all_vals[fr - 1].append("")
                all_vals[fr - 1][lc - 1] = label
                continue
            frow = formulas[r - 1] if r - 1 < len(formulas) else []
            raw = frow[c] if c < len(frow) else ""  # комірка праворуч (0-based c == col c+1)
            if isinstance(raw, str) and raw.startswith("="):
                skipped.append({"label": label, "reason": "formula"})
                continue
            a1 = _gsu.rowcol_to_a1(r, c + 1)
            new_str = "" if value is None else str(value)
            updates.append({"range": a1, "values": [[new_str]]})
            written[label] = new_str
        if dry_run:
            return {"written": written, "skipped": skipped, "gid": ws.id, "dry_run": True}
        if updates:
            _backup_tab(ws, "info")
            ws.batch_update(updates, value_input_option="USER_ENTERED")
        return {"written": written, "skipped": skipped, "gid": ws.id, "dry_run": False}

    return _with_retry(_do, what=f"інфо-блок «{delivery_title}»")

# -*- coding: utf-8 -*-
"""Списання Meta → комірка «Витрати на рекламу» в аркуші ефіру.

Куди саме пишемо
────────────────
Адреса комірки НЕ зашита. Парсер знаходить підпис «Витрати на рекламу» ТЕКСТОМ
і читає клітинку під ним, і в базі реально трапляються різні адреси (AB46 в
одних аркушах, AB45 в інших). Тому ми користуємось тим самим пошуком
(`_extract_advertising_expense`), а не «AB46» — інакше на частині аркушів
значення лягло б у сусідню клітинку.

Спільна комірка (з 01.09.2026)
──────────────────────────────
У комірці лежить ВСЯ реклама ефіру: наші списання Meta — лише складова, решту
(Telegram, блогери) власник знає сам і дописує руками. Тому з дати
`additive_from` комірка спільна: програма не пропускає заповнену, а ДОДАЄ до неї
свою частку.

Повторний прогін нічого не подвоїть, і тримає це НЕ значення комірки, а статус
кожного списання (`meta_ad_charges.write_status`). Додається рівно те, що ще не
записано; вже записане до суми не потрапляє вдруге навіть якщо число в комірці
хтось змінив.

Що НЕДОТОРКАННЕ
───────────────
1. Аркуші ДО `additive_from` не міняються ніколи: там ручні числа власника за
   період, коли програми ще не було.
2. Комірка з текстом, який не розбирається в число, не чіпається взагалі.
   `amount` дорівнює None і для порожньої, і для нечитабельної комірки, тож
   рішення ухвалюється за СИРИМ значенням — інакше «уточнити у Жені»
   перетворилось би на число.
3. Аркуш без блоку «Витрати на рекламу» пропускаємо, а не створюємо структуру:
   змінювати чужу таблицю навмання небезпечніше, ніж пропустити рядок і сказати.
4. Сухий прогін за замовчуванням. У Google Sheets нічого не потрапляє, доки
   людина не подивилась план.

Після запису рядок приїде назад у `advertising_expenses` НАЯВНИМ парсером —
напрямок аркуш → база лишається єдиним, нічого не роздвоюється, і «Статистика»
оновлюється сама, бо вона вже читає саме цю таблицю.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("bms.meta_ads_writeback")

PLANNED = "planned"          # комірка порожня — кладемо свою суму
PLANNED_ADD = "planned_add"  # комірка спільна — ДОДАЄМО свою частку до наявного
SKIPPED_MANUAL = "skipped_manual"
UNREADABLE = "unreadable"    # у комірці текст, який ми не сміємо чіпати
NO_BLOCK = "no_block"
NO_AIR = "no_air"
WRITTEN = "written"


def load_config(db: Session) -> dict:
    try:
        from services.meta_ads import load_config as _load
    except ImportError:
        from backend.services.meta_ads import load_config as _load
    return _load(db)


def _sp():
    try:
        from backend.scripts import sheets_parser
    except ImportError:
        from scripts import sheets_parser
    return sheets_parser


def air_sheets(sh) -> List[dict]:
    """Датовані вкладки книги «Замовлення»: `{date, title, gid, ws}`.

    Джерело правди про ефіри — саме НАЗВА вкладки («23.08.2026»), а не
    `orders.source_sheet_gid`: у базі 5 gid мають кілька дат і 5 дат мають
    кілька gid, тож зв'язок там не однозначний.
    """
    sp = _sp()
    out = []
    for ws in sh.worksheets():
        title = str(ws.title or "").strip()
        if sp.is_skip_sheet(title):
            continue
        day = sp.parse_date_from_sheet_title(title)
        if not day:
            continue
        out.append({"date": day, "title": title, "gid": int(ws.id), "ws": ws})
    out.sort(key=lambda row: row["date"])
    return out


def pending_charges(db: Session) -> List[dict]:
    rows = db.execute(text("""
        SELECT id, transaction_id, receipt_id, charge_date, amount_uah,
               operation_amount, operation_currency, description
        FROM meta_ad_charges
        WHERE write_status = 'pending'
        ORDER BY charge_date, id
    """)).mappings().all()
    return [dict(r) for r in rows]


def build_plan(db: Session, sh, *, only_air: Optional[List[date]] = None,
               reader: Optional[Callable] = None,
               additive_from: Optional[date] = None) -> dict:
    """Що куди пішло б. Нічого не змінює.

    Читає лише ті вкладки, куди справді щось лягає: у книзі сотні аркушів, а
    списань — десятки, тож повне вичитування було б і повільним, і марним.
    """
    try:
        from services.meta_ads import group_by_air
    except ImportError:
        from backend.services.meta_ads import group_by_air
    sp = _sp()
    reader = reader or (lambda ws: ws.get_all_values())

    sheets = air_sheets(sh)
    by_date = {row["date"]: row for row in sheets}
    air_dates = [row["date"] for row in sheets]

    charges = pending_charges(db)
    grouped, orphans = group_by_air(charges, air_dates)
    if only_air:
        grouped = {d: v for d, v in grouped.items() if d in set(only_air)}

    if additive_from is None:
        additive_from = load_config(db).get("additive_from")

    targets, skipped, blocked, unreadable = [], [], [], []
    for air in sorted(grouped):
        sheet = by_date[air]
        rows = reader(sheet["ws"])
        found = sp._extract_advertising_expense(rows)
        total = sum((Decimal(str(c["amount_uah"])) for c in grouped[air]), Decimal("0"))
        raw = str(found.get("raw") or "").strip()
        entry = {
            "air_date": air, "title": sheet["title"], "gid": sheet["gid"],
            "charges": grouped[air], "total_uah": total.quantize(Decimal("0.01")),
            "value_cell": found.get("value_cell"),
            "existing": found.get("amount"),
            "raw": raw,
        }
        if not found.get("found"):
            entry["status"] = NO_BLOCK
            blocked.append(entry)
        elif not raw:
            # Порожня комірка — кладемо свою суму як є.
            entry["status"] = PLANNED
            entry["new_value"] = entry["total_uah"]
            targets.append(entry)
        elif found.get("amount") is None:
            # Непорожня, але не число. Чіпати такий текст ми не маємо права.
            entry["status"] = UNREADABLE
            unreadable.append(entry)
        elif additive_from is not None and air >= additive_from:
            # Спільна комірка: додаємо СВОЮ частку до того, що вже стоїть.
            entry["status"] = PLANNED_ADD
            entry["new_value"] = (Decimal(str(found["amount"])) + entry["total_uah"]
                                  ).quantize(Decimal("0.01"))
            targets.append(entry)
        else:
            # Аркуш до дати спільного режиму — історія власника, недоторканна.
            entry["status"] = SKIPPED_MANUAL
            skipped.append(entry)

    return {
        "sheets_total": len(sheets),
        "charges_pending": len(charges),
        "additive_from": additive_from,
        "planned": targets,
        "skipped_manual": skipped,
        "no_block": blocked,
        "unreadable": unreadable,
        "no_air": orphans,
    }


def format_plan(plan: dict) -> str:
    """План у вигляді, придатному для читання людиною перед рішенням."""
    lines = [
        f"Аркушів ефірів у книзі: {plan['sheets_total']}",
        f"Списань в очікуванні:   {plan['charges_pending']}",
        "",
    ]
    if plan.get("additive_from"):
        lines.append(f"Спільна комірка з {plan['additive_from']:%d.%m.%Y}: "
                     f"до наявного значення ДОДАЄМО свою частку.\n")
    if plan["planned"]:
        total = sum((e["total_uah"] for e in plan["planned"]), Decimal("0"))
        lines.append(f"── ЗАПИСАТИ: {len(plan['planned'])} аркуш(ів), наша частка {total} ₴")
        for e in plan["planned"]:
            if e["status"] == PLANNED_ADD:
                head = (f"   {e['title']:16} {e['value_cell']:>6}   "
                        f"{e['existing']} + {e['total_uah']} = {e['new_value']} ₴")
            else:
                head = (f"   {e['title']:16} {e['value_cell']:>6} ← {e['total_uah']:>12} ₴")
            lines.append(f"{head}   ({len(e['charges'])} списан.)")
            for c in e["charges"]:
                op = (f"{c['operation_amount']} {c['operation_currency']}"
                      if c.get("operation_amount") is not None else "")
                lines.append(f"        {c['charge_date']}  {c['amount_uah']:>10} ₴  {op}")
        lines.append("")
    if plan["skipped_manual"]:
        lines.append(f"── НЕ ЧІПАЮ (заповнено раніше вручну): {len(plan['skipped_manual'])}")
        for e in plan["skipped_manual"]:
            lines.append(f"   {e['title']:16} у комірці вже {e['existing']} ₴"
                         f"   (наш розрахунок був би {e['total_uah']} ₴)")
        lines.append("")
    if plan.get("unreadable"):
        lines.append(f"── НЕЧИТАБЕЛЬНА КОМІРКА (не чіпаю): {len(plan['unreadable'])}")
        for e in plan["unreadable"]:
            lines.append(f"   {e['title']:16} там {e['raw']!r}, наша частка {e['total_uah']} ₴")
        lines.append("")
    if plan["no_block"]:
        lines.append(f"── НЕМАЄ БЛОКУ «Витрати на рекламу»: {len(plan['no_block'])}")
        for e in plan["no_block"]:
            lines.append(f"   {e['title']:16} {e['total_uah']} ₴ нікуди покласти")
        lines.append("")
    if plan["no_air"]:
        total = sum((Decimal(str(c["amount_uah"])) for c in plan["no_air"]), Decimal("0"))
        lines.append(f"── ЧЕКАЮТЬ НА ЕФІР (списано після останнього аркуша): "
                     f"{len(plan['no_air'])} на {total} ₴")
        for c in plan["no_air"]:
            lines.append(f"   {c['charge_date']}  {c['amount_uah']} ₴")
    return "\n".join(lines)


def apply_plan(db: Session, plan: dict, *, writer: Optional[Callable] = None,
               sh=None) -> dict:
    """Записати заплановане. Викликати ЛИШЕ після перегляду плану людиною.

    Перед кожним записом комірка перечитується: між побудовою плану й
    застосуванням власник міг заповнити її вручну, і затерти це було б
    найгіршим результатом усієї роботи.
    """
    sp = _sp()
    written, raced = [], []
    for entry in plan["planned"]:
        ws = None
        for row in air_sheets(sh):
            if row["gid"] == entry["gid"]:
                ws = row["ws"]
                break
        if ws is None:
            continue
        fresh = sp._extract_advertising_expense(ws.get_all_values())
        fresh_raw = str(fresh.get("raw") or "").strip()
        if not fresh.get("found"):
            _mark(db, entry, NO_BLOCK, "блок зник між планом і записом")
            continue
        if fresh_raw and fresh.get("amount") is None:
            raced.append({**entry, "status": UNREADABLE, "existing": fresh_raw})
            _mark(db, entry, UNREADABLE, "у комірці нечитабельний текст")
            continue

        # Сума рахується від СВІЖОГО значення, а не від того, що було в плані:
        # власник міг дописати щось між переглядом і застосуванням, і його
        # число має вціліти. Наша частка при цьому та сама — вона визначається
        # списаннями, а не вмістом комірки.
        if not fresh_raw:
            new_value = entry["total_uah"]
        elif entry["status"] == PLANNED_ADD:
            new_value = (Decimal(str(fresh["amount"])) + entry["total_uah"]
                         ).quantize(Decimal("0.01"))
        else:
            # План казав «порожня», а там уже число і аркуш не в спільному
            # режимі — це історія власника, відступаємо.
            raced.append({**entry, "status": SKIPPED_MANUAL,
                          "existing": fresh.get("amount")})
            _mark(db, entry, SKIPPED_MANUAL, "заповнено вручну між планом і записом")
            continue

        cell = fresh["value_cell"]
        (writer or (lambda w, c, v: w.update_acell(c, v)))(ws, cell, float(new_value))
        _mark(db, entry, WRITTEN, None, cell=cell)
        written.append({**entry, "value_cell": cell, "new_value": new_value,
                        "existing": fresh.get("amount")})
    # Рішення по МИНУЛИХ аркушах фіксуємо в базі. Вони не зміняться ніколи
    # («минулі не трогаємо»), тож лишати їх у черзі означало б перечитувати ті
    # самі 22 аркуші при кожному плані — і показувати людині рядки, за якими
    # вона вже все вирішила. Аркуші БЕЗ блоку й нечитабельні лишаються в черзі
    # навмисно: їх ще можуть полагодити руками.
    settled = 0
    for entry in plan.get("skipped_manual", []):
        _mark(db, entry, SKIPPED_MANUAL, "минулий аркуш, значення власника")
        settled += 1

    db.commit()
    return {"written": written, "skipped_manual": raced, "settled": settled}


def _mark(db: Session, entry: dict, status: str, note: Optional[str],
          cell: Optional[str] = None) -> None:
    db.execute(text("""
        UPDATE meta_ad_charges
        SET write_status = :status, write_note = :note, air_date = :air,
            sheet_gid = :gid, written_at = CASE WHEN :status = 'written'
                                                THEN now() ELSE written_at END,
            updated_at = now()
        WHERE id = ANY(:ids)
    """), {
        "status": status, "note": note or cell, "air": entry["air_date"],
        "gid": entry["gid"], "ids": [c["id"] for c in entry["charges"]],
    })

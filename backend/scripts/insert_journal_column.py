#!/usr/bin/env python3
"""Вставка ОДНІЄЇ колонки в Журнал (і, окремо, у Воркспейс) — узагальнено.

Це третій скрипт такого роду поспіль, тому параметризований: колонка й якір
задаються аргументами, а не вшиті в код.

    --column "Протектор" --after "Тип підошви"

ПОЗИЦІЇ БЕРУТЬСЯ З РЕАЛЬНОГО ЗАГОЛОВКА кожної вкладки, а не з фіксованої мапи.
Причина знайшлась на живих даних: вкладка '21.04.2024(Андрій)' має ту саму
шапку в ІНШОМУ порядку. Парсеру байдуже (він резолвить header.index(name)), а
позиційна перевірка таку вкладку мовчки пропускала.

ПОРЯДОК: КОЛОНКИ ПЕРШИМИ, КОД ПОТІМ. Зайва колонка, якої код не знає,
нешкідлива — невідомий заголовок просто ігнорується. Зворотний порядок гірший і
тихий: writeback поверне "missing in sheet", а цей рядок є в
journal_sync._PERMANENT_MARKERS → задача стане 'skipped' БЕЗ повтору й без
видимої помилки, і аркуш розійдеться з базою непомітно.

Usage:
    ./venv/bin/python backend/scripts/insert_journal_column.py \
        --column "Протектор" --after "Тип підошви"                 # dry-run
    ./venv/bin/python backend/scripts/insert_journal_column.py \
        --column "Протектор" --after "Тип підошви" --execute
    # Воркспейс — окремий документ, сам він туди не піде:
    ./venv/bin/python backend/scripts/insert_journal_column.py \
        --column "Протектор" --after "Тип підошви" \
        --spreadsheet-id <WORKSPACE_ID> --all-tabs --execute
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⚠️ sheets_parser імпортується всередині main(): він тягне gspread і мережеві
# клієнти, а plan_for() — чиста арифметика, яку треба вміти покрити тестом.

THROTTLE_SEC = 1.1   # ~55 запитів/хв, під квотою 60/хв/користувача


def plan_for(header: list[str], column: str, after: str) -> int | None:
    """0-based позиція, куди вставляти нову колонку. None — якоря немає."""
    clean = [h.strip() for h in header]
    if column in clean:
        return None            # уже зроблено — обробляється окремо викликачем
    try:
        return clean.index(after) + 1
    except ValueError:
        return None


def build_requests(sheet_id: int, at: int, column: str) -> list:
    return [
        {
            "insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": at, "endIndex": at + 1},
                "inheritFromBefore": True,
            }
        },
        {
            "updateCells": {
                "rows": [{"values": [{"userEnteredValue": {"stringValue": column}}]}],
                "fields": "userEnteredValue",
                "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": at},
            }
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--column", required=True, help="назва нової колонки")
    ap.add_argument("--after", required=True, help="назва колонки-якоря, після якої вставляти")
    ap.add_argument("--execute", action="store_true", help="справді змінити живий документ")
    ap.add_argument("--dry-run", action="store_true", help="лише звіт (за замовчуванням)")
    ap.add_argument("--spreadsheet-id", default=None, help="інший документ (напр. Воркспейс)")
    ap.add_argument("--all-tabs", action="store_true", help="не застосовувати is_skip_sheet")
    args = ap.parse_args()
    execute = args.execute and not args.dry_run

    from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet

    gc = get_gc()
    sh = gc.open_by_key(args.spreadsheet_id or JOURNAL_ID)
    tabs = sh.worksheets() if args.all_tabs else [
        w for w in sh.worksheets() if not is_skip_sheet(w.title)
    ]
    titles = [w.title for w in tabs]

    ranges = ["'%s'!1:1" % t.replace("'", "''") for t in titles]
    hdrs: dict[str, list] = {}
    for i in range(0, len(ranges), 80):
        res = sh.values_batch_get(ranges[i:i + 80])
        for t, vr in zip(titles[i:i + 80], res["valueRanges"]):
            vals = vr.get("values", [[]])
            hdrs[t] = vals[0] if vals else []

    # ⚠️ is_skip_sheet — фільтр ПАРСЕРА (не читати службові вкладки як завози).
    # Для вставки колонок він хибний: «New» — ШАБЛОН нових завозів, і колонки
    # там потрібні найбільше. Один раз це вже коштувало трьох колонок, яких у
    # шаблоні не було, і кожен новий завіз народжувався б без них.
    if not args.all_tabs:
        skipped_with_anchor = []
        for w in sh.worksheets():
            if not is_skip_sheet(w.title):
                continue
            h = [x.strip() for x in (w.row_values(1) or [])]
            if args.after in h:
                skipped_with_anchor.append(w.title)
        if skipped_with_anchor:
            print(f"\n⚠️ УВАГА: вкладки {skipped_with_anchor} мають потрібні колонки-якорі,")
            print("   але відфільтровані is_skip_sheet і НЕ будуть оброблені.")
            print("   Серед них може бути ШАБЛОН нових завозів. Додайте --all-tabs.\n")

    todo, skip_done, skip_bad = [], [], []
    for w in tabs:
        clean = [x.strip() for x in hdrs.get(w.title, [])]
        if args.column in clean:
            skip_done.append(w.title)
            continue
        at = plan_for(hdrs.get(w.title, []), args.column, args.after)
        if at is None:
            skip_bad.append(w.title)
            continue
        todo.append((w, at))

    print(f"колонка {args.column!r} після {args.after!r}")
    print(f"вкладок усього: {len(tabs)}")
    print(f"  → до обробки  : {len(todo)}")
    print(f"  → вже зроблено: {len(skip_done)} {skip_done[:5]}")
    print(f"  → без якоря   : {len(skip_bad)} {skip_bad[:10]}")

    if not execute:
        print("\nDRY-RUN. Для застосування додайте --execute.")
        seen = set()
        for w, at in todo:
            if at in seen:
                continue
            seen.add(at)
            h = [x.strip() for x in hdrs[w.title]]
            after_h = h[:at] + [args.column] + h[at:]
            lo = max(0, at - 2)
            print(f"\n  вкладка {w.title!r}  ({args.after}@{at} у 1-based)")
            print(f"   було : {h[lo:at + 2]}")
            print(f"   стане: {after_h[lo:at + 3]}")
            if len(seen) >= 4:
                break
        return 0

    done, failed = 0, []
    for w, at in todo:
        body = {"requests": build_requests(w.id, at, args.column)}
        for attempt in range(4):
            try:
                sh.batch_update(body)
                done += 1
                break
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "429" in msg or "Quota" in msg or "rate" in msg.lower():
                    wait = 10 * (attempt + 1)
                    print(f"   [429] {w.title} — пауза {wait}с")
                    time.sleep(wait)
                else:
                    print(f"   [FAIL] {w.title}: {msg[:120]}")
                    failed.append(w.title)
                    break
        if done and done % 25 == 0:
            print(f"   ...{done}/{len(todo)} вкладок", flush=True)
        time.sleep(THROTTLE_SEC)

    print(f"\nГОТОВО. змінено {done}/{len(todo)}. невдалих={len(failed)} {failed[:10]}")
    print("Перезапуск безпечний: зроблені вкладки пропускаються.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

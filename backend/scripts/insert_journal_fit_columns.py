#!/usr/bin/env python3
"""Вставка двох замірних колонок у Журнал (і, окремо, у Воркспейс).

    • «Ширина устілки»  — одразу після «СМ» (довжина устілки)
    • «Обхват халяви»   — одразу після «Висота» (висота халяви)

Порядок саме такий, бо так їх і міряють: довжина й ширина однієї устілки —
парою, висота й обхват халяви — парою на тому самому чоботі.

ЧОМУ КОЛОНКИ ПЕРШІ, А КОД ПОТІМ. Зайва колонка, якої код ще не знає, цілком
нешкідлива: парсер резолвить заголовки точним header.index(), тож невідоме
просто ігнорує. Зворотний порядок гірший і тихий: writeback поверне
"missing in sheet", а цей рядок є в journal_sync._PERMANENT_MARKERS — тобто
задача отримає статус 'skipped', БЕЗ повтору й без помітної помилки. Правка
лишиться в БД, аркуш розійдеться, і ніхто цього не побачить.

Модель — insert_journal_shoe_columns.py, який уже вставив п'ять колонок у 411
вкладок: одна batchUpdate на вкладку, ідемпотентність, пропуск вкладок із
несподіваними якорями, дроселювання під квоту, перезапускність.

Usage:
    ./venv/bin/python backend/scripts/insert_journal_fit_columns.py                  # dry-run
    ./venv/bin/python backend/scripts/insert_journal_fit_columns.py --execute
    ./venv/bin/python backend/scripts/insert_journal_fit_columns.py \
        --spreadsheet-id <WORKSPACE_ID> --all-tabs           # Воркспейс, dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⚠️ sheets_parser імпортується всередині main(), а не тут: він тягне gspread і
# мережеві клієнти, а plan_for() — чиста арифметика, яку треба вміти покрити
# тестом без жодного оточення. Саме ця арифметика поїде по 430 вкладках.

# нова колонка → одразу після цього якоря
PLACEMENT = {
    "Ширина устілки": "СМ",       # довжина й ширина однієї устілки — парою
    "Обхват халяви":  "Висота",   # висота й обхват халяви — парою на чоботі
}
NEW_COLS = list(PLACEMENT)
ANCHORS = set(PLACEMENT.values())

THROTTLE_SEC = 1.1   # ~55 запитів/хв, під квотою 60/хв/користувача


def plan_for(header: list[str]) -> list[tuple[int, str]] | None:
    """[(0-based позиція нової колонки ПІСЛЯ всіх вставок, заголовок)] або None.

    ⚠️ Позиції рахуються з РЕАЛЬНОГО заголовка вкладки, а не з фіксованої мапи.
    Порядок колонок у Журналі не всюди однаковий: вкладка '21.04.2024(Андрій)'
    має ту саму 56-колонкову шапку в іншому порядку. Парсеру це байдуже — він
    резолвить header.index(name) — тож і ми мусимо орієнтуватись на назви.
    """
    clean = [h.strip() for h in header]
    try:
        anchors = {a: clean.index(a) for a in ANCHORS}
    except ValueError:
        return None                      # вкладка без замірного блоку

    # Вставляємо за СПАДНИМ startIndex, щоб кожна вставка зсувала лише праве.
    # Колонка після меншого якоря лишається на min+1; після більшого — їде на
    # max+2, бо ліворуч від неї встигла з'явитись ще одна.
    ordered = sorted(PLACEMENT.items(), key=lambda kv: anchors[kv[1]])
    (low_label, low_a), (high_label, high_a) = ordered
    return [
        (anchors[low_a] + 1, low_label),
        (anchors[high_a] + 2, high_label),
    ]


def build_requests(sheet_id: int, header: list[str]) -> list | None:
    plan = plan_for(header)
    if plan is None:
        return None
    reqs = []
    # startIndex вставок — спадно; для меншого це final, для більшого final-1
    # (його ще не зсунула сусідня вставка ліворуч).
    starts = sorted({plan[0][0], plan[1][0] - 1}, reverse=True)
    for start in starts:
        reqs.append({
            "insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": start, "endIndex": start + 1},
                "inheritFromBefore": True,
            }
        })
    for col0, text in plan:
        reqs.append({
            "updateCells": {
                "rows": [{"values": [{"userEnteredValue": {"stringValue": text}}]}],
                "fields": "userEnteredValue",
                "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": col0},
            }
        })
    return reqs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="справді змінити живий документ")
    ap.add_argument("--dry-run", action="store_true", help="лише звіт (за замовчуванням)")
    ap.add_argument("--spreadsheet-id", default=None,
                    help="інший документ (напр. Воркспейс); за замовчуванням — Журнал")
    ap.add_argument("--all-tabs", action="store_true",
                    help="не застосовувати is_skip_sheet (для Воркспейсу)")
    args = ap.parse_args()
    execute = args.execute and not args.dry_run

    from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet

    gc = get_gc()
    sh = gc.open_by_key(args.spreadsheet_id or JOURNAL_ID)
    tabs = sh.worksheets() if args.all_tabs else [
        w for w in sh.worksheets() if not is_skip_sheet(w.title)
    ]
    titles = [w.title for w in tabs]

    # Один пакетний читальний прохід по всіх рядках заголовків.
    ranges = ["'%s'!1:1" % t.replace("'", "''") for t in titles]
    hdrs: dict[str, list] = {}
    CH = 80
    for i in range(0, len(ranges), CH):
        res = sh.values_batch_get(ranges[i:i + CH])
        for t, vr in zip(titles[i:i + CH], res["valueRanges"]):
            vals = vr.get("values", [[]])
            hdrs[t] = vals[0] if vals else []

    todo, skip_done, skip_bad = [], [], []
    for w in tabs:
        h = hdrs.get(w.title, [])
        if any(nc in [x.strip() for x in h] for nc in NEW_COLS):
            skip_done.append(w.title)
            continue
        if plan_for(h) is None:
            skip_bad.append(w.title)
            continue
        todo.append(w)

    print(f"вкладок усього: {len(tabs)}")
    print(f"  → до обробки  : {len(todo)}")
    print(f"  → вже зроблено: {len(skip_done)} {skip_done[:5]}")
    print(f"  → без якорів  : {len(skip_bad)} {skip_bad[:10]}")

    if skip_bad:
        print("\n  Вкладки без замірного блоку НЕ чіпаються — це навмисно.")
        for t in skip_bad[:5]:
            print(f"    {t!r}: колонок {len(hdrs.get(t, []))}")

    if not execute:
        print("\nDRY-RUN. Для застосування додайте --execute.")
        # Показуємо і типову вкладку, і кожну з нетиповою розкладкою: саме там
        # арифметика позицій відрізняється, і саме там її треба побачити очима.
        shown, seen_layouts = 0, set()
        for w in todo:
            h = [x.strip() for x in hdrs[w.title]]
            key = (h.index("СМ"), h.index("Висота"))
            if key in seen_layouts:
                continue
            seen_layouts.add(key)
            plan = plan_for(h)
            lo = min(key)
            print(f"\n  вкладка {w.title!r}  (СМ@{key[0]+1}, Висота@{key[1]+1} у 1-based)")
            print(f"   було : {h[lo:lo+4]}")
            after = h[:]
            for pos, label in sorted(plan, key=lambda p: p[0]):
                after.insert(pos, label)
            print(f"   стане: {after[lo:lo+6]}")
            shown += 1
            if shown >= 4:
                break
        return 0

    done, failed = 0, []
    for w in todo:
        reqs = build_requests(w.id, hdrs[w.title])
        if reqs is None:
            failed.append(w.title)
            continue
        body = {"requests": reqs}
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
            print(f"   ...{done}/{len(todo)} вкладок")
        time.sleep(THROTTLE_SEC)

    print(f"\nГОТОВО. змінено {done}/{len(todo)}. невдалих={len(failed)} {failed[:10]}")
    print("Перезапуск безпечний: зроблені вкладки пропускаються.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

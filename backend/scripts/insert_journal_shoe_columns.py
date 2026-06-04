#!/usr/bin/env python3
"""
Insert 5 new shoe columns into EVERY product batch tab of the live Журнал.

Layout (verified uniform across 411/412 tabs; 'Suppliers' has no anchors → skipped):
    18 Мембрана | 19 Підошва | 20 Тип підошви | 21 ... | 22 Застібка   (1-based, current)

Inserts (each "after <anchor>", per user spec):
    • Технології    after Мембрана(18)
    • Колір підошви after Підошва(19)
    • Тип каблука   after Тип підошви(20)
    • Тип шнурівки  after Застібка(22)         (before Пакування)
    • Пакування     after Тип шнурівки

Implementation per tab = ONE spreadsheets.batchUpdate:
    5× insertDimension (right→left so indices never interfere)
  + 5× updateCells     (write header text at final absolute positions)

Idempotent: a tab already containing any new column header is skipped.
Safe: a tab whose anchors are not exactly where expected is skipped + reported.
Throttled to respect Sheets write quota. Resumable (just re-run).

Usage:
    python3 scripts/insert_journal_shoe_columns.py --dry-run
    python3 scripts/insert_journal_shoe_columns.py --execute
"""
import sys, os, time, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet

# 0-based expected anchor column indices (current live layout)
EXP = {"Мембрана": 17, "Підошва": 18, "Тип підошви": 19, "Застібка": 21}
NEW_COLS = ["Технології", "Колір підошви", "Тип каблука", "Тип шнурівки", "Пакування"]

# insertDimension requests, ordered by DESCENDING 0-based startIndex so each
# insert only shifts columns to its right (leftward anchors stay put).
#   (startIndex, label)   — endIndex = startIndex+1
INSERTS = [
    (22, "Пакування"),     # after Застібка → ends up rightmost of the Застібка pair
    (22, "Тип шнурівки"),  # after Застібка → pushes Пакування one right
    (20, "Тип каблука"),   # after Тип підошви
    (19, "Колір підошви"), # after Підошва
    (18, "Технології"),    # after Мембрана
]
# Final 0-based column indices where each header text must land after all inserts.
FINAL_HEADER = {
    18: "Технології",     # col 19
    20: "Колір підошви",  # col 21
    22: "Тип каблука",    # col 23
    25: "Тип шнурівки",   # col 26
    26: "Пакування",      # col 27
}

THROTTLE_SEC = 1.1   # ~55 write req/min, under 60/min/user quota


def build_requests(sheet_id: int) -> list:
    reqs = []
    for start, _label in INSERTS:
        reqs.append({
            "insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": start, "endIndex": start + 1},
                "inheritFromBefore": True,
            }
        })
    for col0, text in FINAL_HEADER.items():
        reqs.append({
            "updateCells": {
                "rows": [{"values": [{"userEnteredValue": {"stringValue": text}}]}],
                "fields": "userEnteredValue",
                "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": col0},
            }
        })
    return reqs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually mutate the live sheet")
    ap.add_argument("--dry-run", action="store_true", help="report only (default)")
    args = ap.parse_args()
    execute = args.execute and not args.dry_run

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    tabs = [w for w in sh.worksheets() if not is_skip_sheet(w.title)]
    titles = [w.title for w in tabs]

    # one batched read of every header row
    ranges = ["'%s'!1:1" % t.replace("'", "''") for t in titles]
    hdrs = {}
    CH = 80
    for i in range(0, len(ranges), CH):
        res = sh.values_batch_get(ranges[i:i + CH])
        for t, vr in zip(titles[i:i + CH], res["valueRanges"]):
            vals = vr.get("values", [[]])
            hdrs[t] = vals[0] if vals else []

    todo, skip_done, skip_bad = [], [], []
    for w in tabs:
        h = hdrs.get(w.title, [])
        if any(nc in h for nc in NEW_COLS):
            skip_done.append(w.title); continue
        if any(len(h) <= idx or h[idx] != name for name, idx in EXP.items()):
            skip_bad.append(w.title); continue
        todo.append(w)

    print(f"tabs total(non-skip): {len(tabs)}")
    print(f"  → to process : {len(todo)}")
    print(f"  → already done: {len(skip_done)} {skip_done[:5]}")
    print(f"  → anchors off : {len(skip_bad)} {skip_bad[:10]}")

    if not execute:
        print("\nDRY-RUN. Re-run with --execute to apply. Sample plan (first tab):")
        if todo:
            for r in build_requests(todo[0].id):
                k = list(r.keys())[0]
                if k == "insertDimension":
                    rg = r[k]["range"]; print(f"   insert col @0-based {rg['startIndex']}")
                else:
                    s = r[k]["start"]; t = r[k]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
                    print(f"   header @0-based col {s['columnIndex']} = {t!r}")
        return

    done = 0; failed = []
    for w in todo:
        body = {"requests": build_requests(w.id)}
        for attempt in range(4):
            try:
                sh.batch_update(body)
                done += 1
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg or "Quota" in msg or "rate" in msg.lower():
                    wait = 10 * (attempt + 1)
                    print(f"   [429] {w.title} — backoff {wait}s"); time.sleep(wait)
                else:
                    print(f"   [FAIL] {w.title}: {msg[:120]}"); failed.append(w.title); break
        if done % 25 == 0:
            print(f"   ...{done}/{len(todo)} tabs done")
        time.sleep(THROTTLE_SEC)

    print(f"\nDONE. mutated {done}/{len(todo)} tabs. failed={len(failed)} {failed[:10]}")
    print("Failed/partial tabs are safe to re-run (idempotent skip of completed tabs).")


if __name__ == "__main__":
    main()

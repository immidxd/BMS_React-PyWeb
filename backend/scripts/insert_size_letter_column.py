"""
Insert single new column "Буквений" immediately after "Розмір" in journal batch sheets.

Properties:
  • Idempotent — skips sheets where "Буквений" already exists.
  • Skips Suppliers + all sheets matched by is_skip_sheet() (Publications, Старі, New, ...).
  • Single batchUpdate per sheet (InsertDimension + UpdateCells header + UpdateDimensionProperties width).
  • Header formatting inherited from "Розмір" (bold/colour carry over).
  • Width: same NEW_COL_WIDTH_PX (90px) as previous measurement columns.

Usage:
  PYTHONPATH=. ./venv/bin/python3 backend/scripts/insert_size_letter_column.py
        # dry-run on ALL non-skip sheets (no writes)
  PYTHONPATH=. ./venv/bin/python3 backend/scripts/insert_size_letter_column.py --sheet "23.05.2026(Лісоводи)" --apply
        # really insert into ONE sheet (recommended first step)
  PYTHONPATH=. ./venv/bin/python3 backend/scripts/insert_size_letter_column.py --all --apply
        # really insert into ALL non-skip batch sheets

ALWAYS run --dry-run on a single sheet first, then --apply on that one sheet,
verify visually in Google Sheets, only then --all --apply.
"""
import argparse
import sys
import time
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet


EXTRA_SKIP_TITLES = {"Suppliers"}

ANCHOR_NAME = "Розмір"
NEW_COL_TITLE = "Буквений"
NEW_COL_WIDTH_PX = 90  # match previous-batch widths
PAUSE_BETWEEN_SHEETS_SEC = 1.2


def build_requests(sheet_id: int, anchor_idx_1based: int) -> list[dict]:
    """Insert one column immediately after the anchor (1-indexed)."""
    insert_at_0based = anchor_idx_1based   # 0-indexed insertion point AFTER anchor
    return [
        {
            "insertDimension": {
                "range": {
                    "sheetId":    sheet_id,
                    "dimension":  "COLUMNS",
                    "startIndex": insert_at_0based,
                    "endIndex":   insert_at_0based + 1,
                },
                "inheritFromBefore": True,  # bold header etc. from "Розмір"
            }
        },
        {
            "updateCells": {
                "rows": [{"values": [{"userEnteredValue": {"stringValue": NEW_COL_TITLE}}]}],
                "fields": "userEnteredValue",
                "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": insert_at_0based},
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId":    sheet_id,
                    "dimension":  "COLUMNS",
                    "startIndex": insert_at_0based,
                    "endIndex":   insert_at_0based + 1,
                },
                "properties": {"pixelSize": NEW_COL_WIDTH_PX},
                "fields": "pixelSize",
            }
        },
    ]


def batch_fetch_headers(sh, worksheets: list) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    CHUNK = 80
    for i in range(0, len(worksheets), CHUNK):
        chunk = worksheets[i:i + CHUNK]
        ranges = [f"'{ws.title}'!1:1" for ws in chunk]
        result = sh.values_batch_get(ranges=ranges)
        for ws, value_range in zip(chunk, result.get("valueRanges", [])):
            rows = value_range.get("values", [])
            headers[ws.title] = rows[0] if rows else []
        if i + CHUNK < len(worksheets):
            time.sleep(1.0)
    return headers


def process_sheet(ws, *, header: list[str], apply: bool, verbose: bool = True) -> dict:
    title = ws.title
    if NEW_COL_TITLE in header:
        if verbose:
            print(f"[{title}] already has '{NEW_COL_TITLE}' — skip")
        return {"sheet": title, "status": "already-present"}
    if ANCHOR_NAME not in header:
        if verbose:
            print(f"[{title}] no '{ANCHOR_NAME}' anchor — skip")
        return {"sheet": title, "status": "no-anchor"}

    anchor_idx_1based = header.index(ANCHOR_NAME) + 1
    if verbose:
        print(f"[{title}] insert '{NEW_COL_TITLE}' after '{ANCHOR_NAME}' (col {anchor_idx_1based})")

    if not apply:
        return {"sheet": title, "status": "dry-run"}

    requests = build_requests(ws.id, anchor_idx_1based)
    try:
        ws.spreadsheet.batch_update({"requests": requests})
        return {"sheet": title, "status": "applied", "n_requests": len(requests)}
    except Exception as e:
        return {"sheet": title, "status": f"ERROR: {type(e).__name__}: {e}"}


def iter_target_sheets(sh, only_title: str | None) -> Iterable:
    for ws in sh.worksheets():
        if only_title:
            if ws.title == only_title:
                yield ws
            continue
        if is_skip_sheet(ws.title):
            continue
        if ws.title in EXTRA_SKIP_TITLES:
            continue
        yield ws


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="Process all non-skip batch sheets")
    g.add_argument("--sheet", metavar="TITLE", help="Process exactly one sheet by title")
    ap.add_argument("--apply", action="store_true",
                    help="ACTUALLY perform inserts. Without this, dry-run only.")
    args = ap.parse_args()

    if not args.all and not args.sheet:
        args.all = True   # default: dry-run all

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    print(f"Spreadsheet: {sh.title!r}  ({JOURNAL_ID})")
    print(f"Mode: {'APPLY (writes)' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"Target: {'sheet=' + args.sheet if args.sheet else 'ALL non-skip batches'}")
    print(f"Insert '{NEW_COL_TITLE}' after '{ANCHOR_NAME}', width={NEW_COL_WIDTH_PX}px")
    print(f"Extra skipped sheets: {sorted(EXTRA_SKIP_TITLES)}")
    print("=" * 70)

    targets = list(iter_target_sheets(sh, args.sheet))
    if not targets:
        print(f"[ERR] No target sheets matched.")
        sys.exit(2)
    print(f"Target sheet count: {len(targets)}")

    print(f"\nBatch-fetching headers for {len(targets)} sheets …")
    headers_by_title = batch_fetch_headers(sh, targets)
    print(f"Got {len(headers_by_title)} headers.\n")

    results = []
    for i, ws in enumerate(targets, start=1):
        hdr = headers_by_title.get(ws.title, [])
        r = process_sheet(ws, header=hdr, apply=args.apply, verbose=True)
        results.append(r)
        if args.apply and i < len(targets):
            time.sleep(PAUSE_BETWEEN_SHEETS_SEC)
        if i % 25 == 0:
            print(f"  ...progress {i}/{len(targets)}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    statuses: dict[str, int] = {}
    for r in results:
        s = r["status"] if not r["status"].startswith("ERROR") else "ERROR"
        statuses[s] = statuses.get(s, 0) + 1
    for s, n in statuses.items():
        print(f"   {s:25} {n}")
    errors = [r for r in results if r["status"].startswith("ERROR")]
    if errors:
        print("\nERRORS:")
        for r in errors:
            print(f"   - {r['sheet']!r}: {r['status']}")


if __name__ == "__main__":
    main()

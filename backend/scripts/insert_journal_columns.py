"""
Insert new product-characteristic columns into Журнал batch sheets.

Three target groups (anchors are CURRENT column titles in each sheet):
  • After "Опис"   → Верх, Середина, Підкладка, Устілка, Мембрана, Підошва,
                    Тип підошви, Форма носка, Застібка
  • After "Розмір" → Груди (н/о), Талія (н/о), Бедра (н/о), Рукав, Довжина
  • After "СМ"     → Висота, Товщина підошви, Підбор

Properties:
  • Idempotent — never inserts a column whose title already exists.
  • Skips Suppliers + all sheets matched by is_skip_sheet() (Publications, Старі, New, ...).
  • Processes anchors right→left per sheet so left-side inserts don't shift right anchors.
  • Within each anchor group, inserts columns one-by-one in REVERSE so final visual
    order matches the spec.
  • Single batchUpdate per sheet (InsertDimension + UpdateCells header text combined).
  • Header formatting copied from anchor column (bold/color carry over from neighbours).

Usage:
  PYTHONPATH=. ./venv/bin/python3 backend/scripts/insert_journal_columns.py
        # dry-run on ALL non-skip sheets (no writes)
  PYTHONPATH=. ./venv/bin/python3 backend/scripts/insert_journal_columns.py --sheet "23.05.2026(Лісоводи)" --apply
        # really insert into one sheet
  PYTHONPATH=. ./venv/bin/python3 backend/scripts/insert_journal_columns.py --all --apply
        # really insert into ALL non-skip batch sheets

Always run --dry-run first.
"""
import argparse
import sys
import time
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet


# Sheets to explicitly skip in addition to the parser's built-in is_skip_sheet().
EXTRA_SKIP_TITLES = {"Suppliers"}

# Anchor → list of NEW column titles to insert immediately after it, in the
# left-to-right visual order the user agreed on. Ordered RIGHT→LEFT (rightmost
# anchor first) so each insertion doesn't shift the column index of unprocessed
# anchors.
ANCHOR_GROUPS: list[tuple[str, list[str]]] = [
    ("СМ",     ["Висота", "Товщина підошви", "Підбор"]),
    ("Розмір", ["Груди (н/о)", "Талія (н/о)", "Бедра (н/о)", "Рукав", "Довжина"]),
    ("Опис",   ["Верх", "Середина", "Підкладка", "Устілка", "Мембрана",
                "Підошва", "Тип підошви", "Форма носка", "Застібка"]),
]

# Rate-limit: short pause between sheets (60 writes/min default Google quota).
PAUSE_BETWEEN_SHEETS_SEC = 1.2

# Width in pixels for newly inserted columns. Google default ~100. The user
# asked for narrower; 90 fits short headers ("Рукав"/"Підбор") tightly while
# still showing "Товщина підошви" / "Груди (н/о)" without ellipsis at zoom 100%.
NEW_COL_WIDTH_PX = 90


def plan_for_sheet(header: list[str]) -> list[tuple[str, int, list[str]]]:
    """
    For each anchor group, compute (anchor_name, anchor_col_1indexed, [cols_to_insert]).
    Skips anchors not found in the header.  Skips columns already present.
    Returns plan in the same RIGHT→LEFT order as ANCHOR_GROUPS, suitable for
    converting to API requests where higher-indexed inserts are processed first.
    """
    plan: list[tuple[str, int, list[str]]] = []
    for anchor_name, new_cols in ANCHOR_GROUPS:
        if anchor_name not in header:
            continue
        anchor_idx = header.index(anchor_name) + 1   # 1-indexed
        missing = [c for c in new_cols if c not in header]
        if missing:
            plan.append((anchor_name, anchor_idx, missing))
    return plan


def compute_final_group_ranges(header: list[str]) -> list[tuple[str, int, int]]:
    """
    Compute the FINAL 0-indexed (start, end) ranges (end exclusive) of each new
    column group after ALL inserts complete. Used both for addDimensionGroup
    requests after fresh inserts and for the fixup path on already-inserted sheets.

    Returns [(anchor_name, start_0idx, end_0idx), ...] in left-to-right order.
    Skips anchors not present in header. Assumes a FULL insert (all cols missing
    for present anchors) — caller must guard.
    """
    # Sort by original anchor position left-to-right.
    anchors_with_pos = [(name, header.index(name) + 1, cols)
                        for name, cols in ANCHOR_GROUPS if name in header]
    anchors_with_pos.sort(key=lambda x: x[1])

    out: list[tuple[str, int, int]] = []
    cumulative_shift = 0
    for name, orig_1idx, cols in anchors_with_pos:
        final_anchor_1idx = orig_1idx + cumulative_shift
        # First new col after anchor: 1-idx = final_anchor_1idx + 1 → 0-idx = final_anchor_1idx
        start_0idx = final_anchor_1idx
        end_0idx = start_0idx + len(cols)
        out.append((name, start_0idx, end_0idx))
        cumulative_shift += len(cols)
    return out


def build_batch_requests(sheet_id: int, plan: list[tuple[str, int, list[str]]],
                          header: list[str]) -> list[dict]:
    """
    Convert a plan into Google Sheets API batchUpdate requests.

    For each anchor group, we insert columns one-by-one all at position
    (anchor_idx + 1) in REVERSE order — that way each new insert pushes the
    previous one right, giving final order = [cols[0], cols[1], ..., cols[-1]].

    Anchor groups are processed in ANCHOR_GROUPS order (right→left), so
    left-anchor inserts performed later won't shift the column indexes of
    inserts already queued for right anchors.

    Note: each insert is "Insert empty dimension" + "Write header text into row 0"
          + "Force narrow width". After all inserts, group requests bundle each
          set of new cols into a collapsible Google Sheets column-group.
    """
    requests: list[dict] = []
    for anchor_name, anchor_idx, new_cols in plan:
        insert_at_zero_based = anchor_idx   # 1-indexed anchor → 0-indexed insertion point AFTER it
        for col_title in reversed(new_cols):
            # 1. Make space for one column at insert_at_zero_based
            requests.append({
                "insertDimension": {
                    "range": {
                        "sheetId":    sheet_id,
                        "dimension":  "COLUMNS",
                        "startIndex": insert_at_zero_based,
                        "endIndex":   insert_at_zero_based + 1,
                    },
                    "inheritFromBefore": True,   # copy formatting (bold header etc) from anchor side
                }
            })
            # 2. Write header text into row 0 of the newly inserted column
            requests.append({
                "updateCells": {
                    "rows": [{
                        "values": [{
                            "userEnteredValue": {"stringValue": col_title}
                        }]
                    }],
                    "fields": "userEnteredValue",
                    "start": {
                        "sheetId":     sheet_id,
                        "rowIndex":    0,
                        "columnIndex": insert_at_zero_based,
                    },
                }
            })
            # 3. Force narrow width (override whatever was inherited from anchor side)
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId":    sheet_id,
                        "dimension":  "COLUMNS",
                        "startIndex": insert_at_zero_based,
                        "endIndex":   insert_at_zero_based + 1,
                    },
                    "properties": {"pixelSize": NEW_COL_WIDTH_PX},
                    "fields": "pixelSize",
                }
            })

    # After all inserts: bundle each new-column block into a collapsible group.
    # Indexes here refer to the FINAL state (post-insert) — Sheets API processes
    # requests sequentially within one batchUpdate, so this is consistent.
    for _name, start_0idx, end_0idx in compute_final_group_ranges(header):
        requests.append({
            "addDimensionGroup": {
                "range": {
                    "sheetId":    sheet_id,
                    "dimension":  "COLUMNS",
                    "startIndex": start_0idx,
                    "endIndex":   end_0idx,
                }
            }
        })
    return requests


def build_fixup_requests(sheet_id: int, header: list[str]) -> list[dict]:
    """
    For a sheet where new columns ALREADY exist but lack the proper width and
    column-grouping (e.g. sheets processed before width/group support landed).

    Resizes each new column to NEW_COL_WIDTH_PX and groups each contiguous
    new-column run. Idempotent on width; addDimensionGroup may error if a group
    already covers the same range (we wrap caller in try/except).
    """
    # Each (first_header, last_header) defines a contiguous group expected in the sheet.
    GROUP_BOUNDS = [
        ("Верх",        "Застібка"),    # after Опис
        ("Груди (н/о)", "Довжина"),     # after Розмір
        ("Висота",      "Підбор"),      # after СМ
    ]
    requests: list[dict] = []
    for first, last in GROUP_BOUNDS:
        if first not in header or last not in header:
            continue
        s = header.index(first)        # 0-idx start
        e = header.index(last) + 1     # 0-idx end exclusive
        # Resize all cols in this range
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id, "dimension": "COLUMNS",
                    "startIndex": s, "endIndex": e,
                },
                "properties": {"pixelSize": NEW_COL_WIDTH_PX},
                "fields": "pixelSize",
            }
        })
        # Group them
        requests.append({
            "addDimensionGroup": {
                "range": {
                    "sheetId": sheet_id, "dimension": "COLUMNS",
                    "startIndex": s, "endIndex": e,
                }
            }
        })
    return requests


def batch_fetch_headers(sh, worksheets: list) -> dict[str, list[str]]:
    """
    Read row 1 of each worksheet using ONE values.batchGet call (well, batched).
    Returns {sheet_title: [header_cells]}. Massively under quota vs per-sheet reads.
    """
    headers: dict[str, list[str]] = {}
    # Chunk into batches of 80 ranges per call (well under per-request limit, fast).
    CHUNK = 80
    for i in range(0, len(worksheets), CHUNK):
        chunk = worksheets[i:i + CHUNK]
        ranges = [f"'{ws.title}'!1:1" for ws in chunk]
        result = sh.values_batch_get(ranges=ranges)
        for ws, value_range in zip(chunk, result.get("valueRanges", [])):
            rows = value_range.get("values", [])
            headers[ws.title] = rows[0] if rows else []
        if i + CHUNK < len(worksheets):
            time.sleep(1.0)   # gentle pause between read-batches
    return headers


def process_sheet_fixup(ws, *, header: list[str], apply: bool, verbose: bool = True) -> dict:
    """Apply width + grouping to a sheet where new cols already exist."""
    title = ws.title
    requests = build_fixup_requests(ws.id, header)
    if verbose:
        print(f"\n[{title}] FIXUP: {len(requests)} requests (width + group for each present block)")
    if not requests:
        return {"sheet": title, "status": "nothing-to-fixup", "inserted": [],
                "skipped_existing": [], "missing_anchors": []}
    if not apply:
        return {"sheet": title, "status": "dry-run-fixup", "inserted": [],
                "skipped_existing": [], "missing_anchors": []}
    try:
        ws.spreadsheet.batch_update({"requests": requests})
        return {"sheet": title, "status": "fixed-up", "inserted": [],
                "skipped_existing": [], "missing_anchors": [], "n_requests": len(requests)}
    except Exception as e:
        # Group requests may fail if a group already covers that range — that's fine
        # for idempotency. Try width-only as a fallback.
        msg = str(e)
        if "already" in msg.lower() or "exist" in msg.lower():
            try:
                width_only = [r for r in requests if "updateDimensionProperties" in r]
                ws.spreadsheet.batch_update({"requests": width_only})
                return {"sheet": title, "status": "fixed-up (width only — group existed)",
                        "inserted": [], "skipped_existing": [], "missing_anchors": [],
                        "n_requests": len(width_only)}
            except Exception as e2:
                return {"sheet": title, "status": f"ERROR (fixup-fallback): {type(e2).__name__}: {e2}",
                        "inserted": [], "skipped_existing": [], "missing_anchors": []}
        return {"sheet": title, "status": f"ERROR (fixup): {type(e).__name__}: {e}",
                "inserted": [], "skipped_existing": [], "missing_anchors": []}


def process_sheet(ws, *, header: list[str], apply: bool, verbose: bool = True) -> dict:
    """Returns {'sheet','status','inserted','skipped_existing','missing_anchors','requests'}."""
    title = ws.title
    plan = plan_for_sheet(header)

    inserted_cols: list[str] = []
    for _, _, cols in plan:
        inserted_cols.extend(cols)

    # Count which expected new columns are skipped because already present
    all_expected = [c for _, cs in ANCHOR_GROUPS for c in cs]
    already_present = [c for c in all_expected if c in header]
    missing_anchors = [a for a, _ in ANCHOR_GROUPS if a not in header]

    if verbose:
        print(f"\n[{title}] header_cols={len(header)}  "
              f"to_insert={len(inserted_cols)}  "
              f"already_present={len(already_present)}  "
              f"missing_anchors={missing_anchors or '—'}")
        for anchor_name, anchor_idx, cols in plan:
            print(f"   after '{anchor_name}' (col {anchor_idx}): {cols}")

    if not inserted_cols:
        return {"sheet": title, "status": "nothing-to-do", "inserted": [],
                "skipped_existing": already_present, "missing_anchors": missing_anchors}

    if not apply:
        return {"sheet": title, "status": "dry-run", "inserted": inserted_cols,
                "skipped_existing": already_present, "missing_anchors": missing_anchors}

    # Apply
    requests = build_batch_requests(ws.id, plan, header)
    try:
        ws.spreadsheet.batch_update({"requests": requests})
        return {"sheet": title, "status": "applied", "inserted": inserted_cols,
                "skipped_existing": already_present, "missing_anchors": missing_anchors,
                "n_requests": len(requests)}
    except Exception as e:
        return {"sheet": title, "status": f"ERROR: {type(e).__name__}: {e}",
                "inserted": [], "skipped_existing": already_present,
                "missing_anchors": missing_anchors}


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
    ap.add_argument("--fixup", action="store_true",
                    help="Don't insert anything; apply width + grouping to sheets where new cols already exist.")
    args = ap.parse_args()

    if not args.all and not args.sheet:
        # Default: dry-run all
        args.all = True

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    print(f"Spreadsheet: {sh.title!r}  ({JOURNAL_ID})")
    print(f"Mode: {'APPLY (writes)' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"Target: {'sheet=' + args.sheet if args.sheet else 'ALL non-skip batches'}")
    print(f"Anchors→cols:")
    for a, cs in ANCHOR_GROUPS:
        print(f"   {a} → {cs}")
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
        if args.fixup:
            r = process_sheet_fixup(ws, header=hdr, apply=args.apply, verbose=True)
        else:
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
    total_new = sum(len(r["inserted"]) for r in results)
    print(f"\nTotal columns inserted (or planned): {total_new}")


if __name__ == "__main__":
    main()

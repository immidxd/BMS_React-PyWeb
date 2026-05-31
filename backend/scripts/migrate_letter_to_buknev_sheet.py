"""
One-off Google Sheets migration: move letter-size values from "Розмір" (and "Опис")
columns into the new "Буквений" column.

Rules per row:
  • Skip if "Буквений" cell is already non-empty (idempotent).
  • If "Розмір" cell is a PURE canonical letter (L / XL / 3XL / xs / м / etc.)
       → write canonical letter into "Буквений", CLEAR "Розмір".
  • If "Розмір" cell matches "EU 38" / "FR 42" / "UA 40" pattern (system prefix + digits)
       → strip prefix, leave only digits in "Розмір". NO letter migration.
  • If "Розмір" cell is mixed (letter + digits, e.g. "L 42/44 FR / M 40/42 EUR")
       → ONLY report. Do not auto-modify. User said these are already cleaned;
         any remaining ones deserve manual review.
  • If "Розмір" cell is empty AND "Опис" contains "Розмір: L" / "Size: XL" keyword
       → extract letter into "Буквений". Опис cell unchanged.

Skips Suppliers + Publications/Старі/New via is_skip_sheet().

Usage:
    PYTHONPATH=. ./venv/bin/python3 backend/scripts/migrate_letter_to_buknev_sheet.py
        # dry-run on ALL non-skip sheets
    PYTHONPATH=. ./venv/bin/python3 backend/scripts/migrate_letter_to_buknev_sheet.py --sheet "23.05.2026(Лісоводи)"
        # dry-run on one sheet
    PYTHONPATH=. ./venv/bin/python3 backend/scripts/migrate_letter_to_buknev_sheet.py --sheet "23.05.2026(Лісоводи)" --apply
        # really write to one sheet
    PYTHONPATH=. ./venv/bin/python3 backend/scripts/migrate_letter_to_buknev_sheet.py --all --apply
        # really write to all
"""
import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sheets_parser import (
    get_gc, JOURNAL_ID, is_skip_sheet,
    _normalize_size_letter,
)


EXTRA_SKIP_TITLES = {"Suppliers"}
PAUSE_BETWEEN_SHEETS_SEC = 2.0
# Google Sheets API: 60 read / 60 write per minute per user.
# With 1 read/sheet + dry-run pause 1.2s, we run ~50/min — safe.
# Apply mode: 1 read + 1 write per sheet = need slightly larger gap.
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_BASE = 30  # seconds; 30, 60, 120, 240, 480

PURE_LETTER_RE = re.compile(
    r"^(XS|S|M|L|XL|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL|3/L|4/XL|М)$",
    re.IGNORECASE,
)
# "EU 38", "FR 42", "UA 40", "USA 8", "UK 7" — system prefix + digits
SYSTEM_PREFIX_RE = re.compile(
    r"^\s*(EU|EUR|FR|UA|UK|USA|US|IT|ES|PT|JP|CN)\s+([\d\.,\-/]+)\s*$",
    re.IGNORECASE,
)
# Desc keyword: Розмір/Size + letter
DESC_RE = re.compile(
    r"(?:розм[іе]р|размер|розм\.?|size)\s*:?\s*"
    r"(XS|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL|XL|L|M|S)\b",
    re.IGNORECASE,
)
# Mixed compound: contains both letter and digit
MIXED_RE = re.compile(
    r"(?:^|[\s/(])(XS|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL|XL|S|M|L)(?=\s|/|$)",
    re.IGNORECASE,
)


def col_letter(idx_0based: int) -> str:
    """Convert 0-based column index to A/B/.../AA/AB/... letter notation."""
    result = ""
    n = idx_0based + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def classify_cell(rozmir: str, opys: str) -> dict:
    """
    Decide action for one row.
    Returns {action, letter, new_rozmir, source, raw_rozmir, raw_opys}.
    action ∈ {"none", "pure_letter", "system_prefix", "desc_keyword", "mixed_report"}
    """
    raw_r = (rozmir or "").strip()
    raw_o = (opys or "").strip()

    if raw_r:
        if PURE_LETTER_RE.match(raw_r):
            letter = _normalize_size_letter(raw_r)
            if letter:
                return {"action": "pure_letter", "letter": letter, "new_rozmir": "",
                        "source": "rozmir_pure", "raw_rozmir": raw_r, "raw_opys": raw_o}
        m = SYSTEM_PREFIX_RE.match(raw_r)
        if m:
            return {"action": "system_prefix", "letter": "", "new_rozmir": m.group(2).strip(),
                    "source": "rozmir_prefix", "raw_rozmir": raw_r, "raw_opys": raw_o}
        # Mixed (digits + letters, but not system prefix)
        if re.search(r"\d", raw_r) and MIXED_RE.search(raw_r):
            letter = _normalize_size_letter(raw_r)
            return {"action": "mixed_report", "letter": letter, "new_rozmir": raw_r,
                    "source": "rozmir_mixed", "raw_rozmir": raw_r, "raw_opys": raw_o}

    # Empty/non-letter Розмір — try description keyword
    if not raw_r or not re.search(r"[A-Za-zА-Яа-я]", raw_r):
        m = DESC_RE.search(raw_o)
        if m:
            letter = _normalize_size_letter(m.group(1))
            if letter:
                return {"action": "desc_keyword", "letter": letter, "new_rozmir": raw_r,
                        "source": "opys_keyword", "raw_rozmir": raw_r, "raw_opys": raw_o}

    return {"action": "none", "letter": "", "new_rozmir": raw_r,
            "source": "", "raw_rozmir": raw_r, "raw_opys": raw_o}


def _retry_on_429(fn, *args, label="op", **kwargs):
    """Call fn(*args, **kwargs); on 429 sleep+retry with exponential backoff."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "429" not in msg and "Quota" not in msg:
                raise
            if attempt == RATE_LIMIT_RETRIES:
                raise
            wait = RATE_LIMIT_BACKOFF_BASE * (2 ** attempt)
            print(f"   ⏳ 429 on {label}, sleeping {wait}s (attempt {attempt+1}/{RATE_LIMIT_RETRIES}) …")
            time.sleep(wait)


def process_sheet(ws, *, apply: bool, verbose: bool = True) -> dict:
    title = ws.title
    # SINGLE read call: get_all_values gives header (row 0) + data rows.
    all_rows = _retry_on_429(ws.get_all_values, label=f"read {title}")
    if not all_rows:
        return {"sheet": title, "status": "no-data", "changes": 0, "mixed": []}
    header = all_rows[0]
    if "Розмір" not in header:
        return {"sheet": title, "status": "no-розмір", "changes": 0, "mixed": []}
    if "Буквений" not in header:
        return {"sheet": title, "status": "no-буквений", "changes": 0, "mixed": []}

    rozmir_idx = header.index("Розмір")        # 0-based
    bukven_idx = header.index("Буквений")
    opys_idx   = header.index("Опис") if "Опис" in header else -1

    if len(all_rows) <= 1:
        return {"sheet": title, "status": "no-data", "changes": 0, "mixed": []}

    # 3. Per-row classify
    cell_updates = []   # list of (a1_notation, new_value)
    mixed_reports = []
    stats = Counter()

    for row_idx, row in enumerate(all_rows[1:], start=2):  # spreadsheet rows are 1-based, header=1
        # pad row to header length
        while len(row) < len(header):
            row.append("")
        bukven_cell  = row[bukven_idx] if bukven_idx < len(row) else ""
        if bukven_cell.strip():
            stats["skip_buknev_filled"] += 1
            continue
        rozmir_cell = row[rozmir_idx] if rozmir_idx < len(row) else ""
        opys_cell   = row[opys_idx]   if opys_idx >= 0 and opys_idx < len(row) else ""
        decision = classify_cell(rozmir_cell, opys_cell)
        act = decision["action"]
        stats[act] += 1
        if act == "none":
            continue

        # Build planned changes
        a1_bukven  = f"{col_letter(bukven_idx)}{row_idx}"
        a1_rozmir  = f"{col_letter(rozmir_idx)}{row_idx}"

        if act == "pure_letter":
            cell_updates.append((a1_bukven, decision["letter"]))
            cell_updates.append((a1_rozmir, ""))
        elif act == "system_prefix":
            cell_updates.append((a1_rozmir, decision["new_rozmir"]))
        elif act == "desc_keyword":
            cell_updates.append((a1_bukven, decision["letter"]))
        elif act == "mixed_report":
            mixed_reports.append({
                "row": row_idx, "rozmir": decision["raw_rozmir"], "letter": decision["letter"],
            })

    if verbose:
        print(f"[{title}] header_cols={len(header)} rozmir_col={col_letter(rozmir_idx)} "
              f"bukven_col={col_letter(bukven_idx)} opys_col={col_letter(opys_idx) if opys_idx>=0 else '—'}")
        actions_summary = ", ".join(f"{k}={v}" for k, v in sorted(stats.items()) if v)
        print(f"   actions: {actions_summary}")
        if mixed_reports:
            print(f"   ⚠ MIXED compounds found (manual review): {len(mixed_reports)}")
            for mr in mixed_reports[:5]:
                print(f"      row {mr['row']}: Розмір={mr['rozmir']!r} (letter hint: {mr['letter']})")
            if len(mixed_reports) > 5:
                print(f"      ... and {len(mixed_reports) - 5} more")

    if not cell_updates:
        return {"sheet": title, "status": "nothing-to-do", "changes": 0, "mixed": mixed_reports}

    if not apply:
        return {"sheet": title, "status": "dry-run", "changes": len(cell_updates),
                "mixed": mixed_reports, "sample": cell_updates[:5]}

    # 4. Batch-apply via batch_update (values.batchUpdate). gspread Worksheet.batch_update
    #    expects [{"range": "A2", "values": [["L"]]}, ...]
    payload = [{"range": a1, "values": [[val]]} for a1, val in cell_updates]
    try:
        _retry_on_429(ws.batch_update, payload, value_input_option="USER_ENTERED",
                      label=f"write {title}")
        return {"sheet": title, "status": "applied", "changes": len(cell_updates),
                "mixed": mixed_reports}
    except Exception as e:
        return {"sheet": title, "status": f"ERROR: {type(e).__name__}: {e}",
                "changes": 0, "mixed": mixed_reports}


def iter_target_sheets(sh, only_title):
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
    g.add_argument("--all", action="store_true")
    g.add_argument("--sheet", metavar="TITLE")
    ap.add_argument("--apply", action="store_true",
                    help="ACTUALLY write to sheets. Without this, dry-run only.")
    args = ap.parse_args()

    if not args.all and not args.sheet:
        args.all = True

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    print(f"Spreadsheet: {sh.title!r}  ({JOURNAL_ID})")
    print(f"Mode: {'APPLY (writes)' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"Target: {'sheet=' + args.sheet if args.sheet else 'ALL non-skip batches'}")
    print("=" * 70)

    targets = list(iter_target_sheets(sh, args.sheet))
    if not targets:
        print("[ERR] No target sheets matched.")
        sys.exit(2)
    print(f"Target sheet count: {len(targets)}\n")

    results = []
    grand_changes = 0
    grand_mixed = 0
    for i, ws in enumerate(targets, start=1):
        try:
            r = process_sheet(ws, apply=args.apply, verbose=True)
        except Exception as e:
            r = {"sheet": ws.title, "status": f"ERROR (outer): {type(e).__name__}: {e}",
                 "changes": 0, "mixed": []}
        results.append(r)
        grand_changes += r.get("changes", 0)
        grand_mixed += len(r.get("mixed", []))
        if args.apply and i < len(targets):
            time.sleep(PAUSE_BETWEEN_SHEETS_SEC)
        if i % 25 == 0:
            print(f"  ...progress {i}/{len(targets)}  (cumulative changes: {grand_changes})")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    statuses = Counter()
    for r in results:
        s = r["status"] if not str(r["status"]).startswith("ERROR") else "ERROR"
        statuses[s] += 1
    for s, n in statuses.most_common():
        print(f"   {s:25} {n}")
    errors = [r for r in results if str(r["status"]).startswith("ERROR")]
    if errors:
        print("\nERRORS:")
        for r in errors:
            print(f"   - {r['sheet']!r}: {r['status']}")
    print(f"\nTotal cell changes {'applied' if args.apply else 'planned'}: {grand_changes}")
    print(f"Mixed compounds reported (manual review needed): {grand_mixed}")


if __name__ == "__main__":
    main()

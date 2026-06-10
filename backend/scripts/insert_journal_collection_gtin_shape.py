#!/usr/bin/env python3
"""
Insert 3 new columns into EVERY product batch tab of the live Журнал
+ normalize the collapsible column group to «Модель…GTIN».

New columns (per user spec, each "right after <anchor>"):
    • Колекція          after Модель
    • GTIN              after Маркування
    • Геометрична форма after Габарити

Column group handling:
    Target: ONE depth-1 COLUMNS group covering [Модель .. GTIN] (4 columns).
    Existing groups intersecting that zone (e.g. the old «Маркування+Рік» group
    on ~231 tabs) are deleted first; their columns are un-hidden so nothing is
    left stranded-hidden outside the new group. If the old group was collapsed,
    the new group is re-collapsed (columns hidden + collapsed flag) to preserve
    the tab's visual state. Tabs without a group in the zone get an expanded one.

Anchors are resolved BY HEADER NAME per tab (rename/position-safe). A tab is
skipped + reported if: any new header already exists (idempotent), or anchors
are missing, or Маркування is not directly after Модель (group would not be
contiguous), or a depth>1 group touches the zone (manual review).

Implementation per tab = ONE spreadsheets.batchUpdate, requests in order:
    unhide old zone cols → deleteDimensionGroup (old zone groups)
    → 3× insertDimension (right→left) → 3× updateCells (headers)
    → addDimensionGroup [Модель..GTIN] → (optional) re-collapse.

Backup: headers + columnGroups of every non-skip tab → JSON before any write.
Throttled under the 60 write-req/min quota. Resumable (just re-run).

Usage:
    python3 scripts/insert_journal_collection_gtin_shape.py --dry-run
    python3 scripts/insert_journal_collection_gtin_shape.py --execute
    python3 scripts/insert_journal_collection_gtin_shape.py --verify
    # Інші цілі: явні вкладки (обходять skip-list) та інший spreadsheet:
    python3 scripts/insert_journal_collection_gtin_shape.py --tabs "New" --execute
    python3 scripts/insert_journal_collection_gtin_shape.py --spreadsheet-id <WORKSPACE_ID> --all-tabs --execute
"""
import sys, os, time, json, argparse
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet

NEW_COLS = ["Колекція", "GTIN", "Геометрична форма"]
ANCHORS  = ["Модель", "Маркування", "Габарити"]
THROTTLE_SEC = 1.1   # ~55 write req/min, under 60/min/user quota
BACKUP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    f"journal_header_groups_backup_{datetime.now():%Y%m%d_%H%M%S}.json",
)


def col_range(sheet_id: int, start: int, end: int) -> dict:
    return {"sheetId": sheet_id, "dimension": "COLUMNS",
            "startIndex": start, "endIndex": end}


def plan_tab(header: list, groups: list, sheet_id: int):
    """Return (requests, info) for one tab, or (None, reason) to skip."""
    if any(nc in header for nc in NEW_COLS):
        return None, "already-done"
    try:
        mod  = header.index("Модель")
        mark = header.index("Маркування")
        gab  = header.index("Габарити")
    except ValueError:
        return None, "anchors-missing"
    if mark != mod + 1:
        return None, "markup-not-adjacent"
    if gab <= mark:
        return None, "gabarity-left-of-marking"

    # Final zone after inserts: Модель@mod, Колекція@mod+1, Маркування@mod+2, GTIN@mod+3
    zone_pre  = (mod, mark + 2)        # pre-insert columns Модель..Рік(виключно+1) area
    zone_post = (mod, mod + 4)

    col_groups = [g for g in (groups or [])
                  if g.get("range", {}).get("dimension") == "COLUMNS"]
    inter = [g for g in col_groups
             if g["range"].get("startIndex", 0) < zone_pre[1]
             and g["range"].get("endIndex", 0) > zone_pre[0]]
    if any(g.get("depth", 1) > 1 for g in inter):
        return None, "nested-group-in-zone"

    was_collapsed = any(g.get("collapsed") for g in inter)
    outside = [g for g in inter
               if g["range"]["startIndex"] < zone_pre[0] or g["range"]["endIndex"] > zone_pre[1]]

    reqs = []
    # 1. Un-hide + delete old intersecting groups (pre-insert coordinates).
    for g in inter:
        r = g["range"]
        reqs.append({"updateDimensionProperties": {
            "range": col_range(sheet_id, r["startIndex"], r["endIndex"]),
            "properties": {"hiddenByUser": False},
            "fields": "hiddenByUser",
        }})
        reqs.append({"deleteDimensionGroup": {
            "range": col_range(sheet_id, r["startIndex"], r["endIndex"]),
        }})
    # 2. Inserts, DESCENDING start index so leftward anchors never shift.
    for start in (gab + 1, mark + 1, mod + 1):
        reqs.append({"insertDimension": {
            "range": col_range(sheet_id, start, start + 1),
            "inheritFromBefore": True,
        }})
    # 3. Headers at FINAL absolute positions.
    for col0, text_val in ((mod + 1, "Колекція"),
                           (mark + 2, "GTIN"),
                           (gab + 3, "Геометрична форма")):
        reqs.append({"updateCells": {
            "rows": [{"values": [{"userEnteredValue": {"stringValue": text_val}}]}],
            "fields": "userEnteredValue",
            "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": col0},
        }})
    # 4. The one canonical group «Модель..GTIN».
    reqs.append({"addDimensionGroup": {"range": col_range(sheet_id, *zone_post)}})
    # 5. Preserve collapsed state of the replaced group.
    if was_collapsed:
        reqs.append({"updateDimensionProperties": {
            "range": col_range(sheet_id, *zone_post),
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }})
        reqs.append({"updateDimensionGroup": {
            "dimensionGroup": {"range": col_range(sheet_id, *zone_post),
                               "depth": 1, "collapsed": True},
            "fields": "collapsed",
        }})
    info = {"mod": mod, "mark": mark, "gab": gab,
            "deleted_groups": [g["range"] for g in inter],
            "was_collapsed": was_collapsed,
            "group_extends_outside_zone": [g["range"] for g in outside]}
    return reqs, info


def fetch_state(sh, want_groups=True, only_tabs=None, all_tabs=False):
    """One metadata fetch + batched header reads.

    Default: all non-skip tabs. only_tabs=[...] → exactly these titles
    (bypasses the skip-list, e.g. 'New'). all_tabs=True → every tab.
    """
    fields = "sheets(properties(sheetId,title)" + (",columnGroups)" if want_groups else ")")
    meta = sh.fetch_sheet_metadata({"fields": fields})
    if only_tabs is not None:
        wanted = set(only_tabs)
        tabs = [s for s in meta["sheets"] if s["properties"]["title"] in wanted]
        missing = wanted - {s["properties"]["title"] for s in tabs}
        if missing:
            raise SystemExit(f"tabs not found in spreadsheet: {sorted(missing)}")
    elif all_tabs:
        tabs = meta["sheets"]
    else:
        tabs = [s for s in meta["sheets"] if not is_skip_sheet(s["properties"]["title"])]
    titles = [s["properties"]["title"] for s in tabs]
    hdrs = {}
    CH = 80
    ranges = ["'%s'!1:1" % t.replace("'", "''") for t in titles]
    for i in range(0, len(ranges), CH):
        res = sh.values_batch_get(ranges[i:i + CH])
        for t, vr in zip(titles[i:i + CH], res["valueRanges"]):
            vals = vr.get("values", [[]])
            hdrs[t] = vals[0] if vals else []
    return tabs, hdrs


def verify(sh, only_tabs=None, all_tabs=False):
    tabs, hdrs = fetch_state(sh, only_tabs=only_tabs, all_tabs=all_tabs)
    ok, bad = 0, []
    for s in tabs:
        t = s["properties"]["title"]
        h = hdrs.get(t, [])
        if "Модель" not in h:
            continue  # non-product tab (e.g. Suppliers)
        mod = h.index("Модель")
        layout_ok = (len(h) > mod + 3 and h[mod + 1] == "Колекція"
                     and h[mod + 2] == "Маркування" and h[mod + 3] == "GTIN"
                     and "Габарити" in h and "Геометрична форма" in h
                     and h.index("Геометрична форма") == h.index("Габарити") + 1)
        zone_groups = [g for g in (s.get("columnGroups") or [])
                       if g["range"].get("dimension") == "COLUMNS"
                       and g["range"].get("startIndex", -1) == mod
                       and g["range"].get("endIndex", -1) == mod + 4]
        overlapping = [g for g in (s.get("columnGroups") or [])
                       if g["range"].get("dimension") == "COLUMNS"
                       and g["range"].get("startIndex", 0) < mod + 4
                       and g["range"].get("endIndex", 0) > mod
                       and g not in zone_groups]
        if layout_ok and len(zone_groups) == 1 and not overlapping:
            ok += 1
        else:
            bad.append((t, "layout" if not layout_ok else
                        f"groups exact={len(zone_groups)} overlap={len(overlapping)}"))
    print(f"VERIFY: ok={ok}, bad={len(bad)}")
    for t, why in bad[:30]:
        print(f"   [BAD] {t}: {why}")
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--spreadsheet-id", default=JOURNAL_ID,
                    help="target spreadsheet (default: production Журнал)")
    ap.add_argument("--tabs", default=None,
                    help="comma-separated exact tab titles (bypasses skip-list), e.g. 'New'")
    ap.add_argument("--all-tabs", action="store_true",
                    help="process every tab of the spreadsheet (e.g. Воркспейс)")
    args = ap.parse_args()
    only_tabs = [t.strip() for t in args.tabs.split(",")] if args.tabs else None

    gc = get_gc()
    sh = gc.open_by_key(args.spreadsheet_id)

    if args.verify:
        sys.exit(0 if verify(sh, only_tabs=only_tabs, all_tabs=args.all_tabs) else 1)

    execute = args.execute and not args.dry_run
    tabs, hdrs = fetch_state(sh, only_tabs=only_tabs, all_tabs=args.all_tabs)
    print(f"target tabs: {len(tabs)}")

    # ── Backup BEFORE any mutation ────────────────────────────────────────
    backup = {s["properties"]["title"]: {
        "sheetId": s["properties"]["sheetId"],
        "header": hdrs.get(s["properties"]["title"], []),
        "columnGroups": s.get("columnGroups", []),
    } for s in tabs}
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print(f"backup → {BACKUP_PATH}")

    todo, skips = [], {}
    review_outside, review_pos = [], {}
    for s in tabs:
        t = s["properties"]["title"]
        reqs, info = plan_tab(hdrs.get(t, []), s.get("columnGroups"), s["properties"]["sheetId"])
        if reqs is None:
            skips.setdefault(info, []).append(t)
            continue
        todo.append((t, reqs))
        review_pos.setdefault((info["mod"], info["mark"], info["gab"]), []).append(t)
        if info["group_extends_outside_zone"]:
            review_outside.append((t, info["group_extends_outside_zone"]))

    print(f"  → to process : {len(todo)}")
    for why, ts in skips.items():
        print(f"  → skip[{why}]: {len(ts)} {ts[:8]}")
    print(f"  anchor-position variants: "
          f"{ {k: len(v) for k, v in review_pos.items()} }")
    if review_outside:
        print(f"  ⚠ groups extending outside Модель..Рік zone (will shrink to zone): "
              f"{review_outside[:10]}")

    if not execute:
        print("\nDRY-RUN. Re-run with --execute to apply. Plan for first tab:")
        if todo:
            print(f"  [{todo[0][0]}]")
            for r in todo[0][1]:
                print("   ", json.dumps(r, ensure_ascii=False)[:160])
        return

    done, failed = 0, []
    for t, reqs in todo:
        body = {"requests": reqs}
        for attempt in range(5):
            try:
                sh.batch_update(body)
                done += 1
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg or "Quota" in msg or "rate" in msg.lower():
                    wait = 15 * (attempt + 1)
                    print(f"   [429] {t} — backoff {wait}s"); time.sleep(wait)
                else:
                    print(f"   [FAIL] {t}: {msg[:140]}"); failed.append(t); break
        else:
            failed.append(t)
        if done and done % 25 == 0:
            print(f"   ...{done}/{len(todo)} tabs done", flush=True)
        time.sleep(THROTTLE_SEC)

    print(f"\nDONE. mutated {done}/{len(todo)} tabs. failed={len(failed)} {failed[:10]}")
    print("Re-run safe: completed tabs are skipped (idempotent). Now run --verify.")


if __name__ == "__main__":
    main()

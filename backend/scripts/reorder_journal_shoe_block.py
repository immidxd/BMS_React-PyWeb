#!/usr/bin/env python3
"""
Reorder/insert the shoe-construction columns in every product tab + normalize
the collapsible group to «Верх…Технології».

Per user spec 2026-06-10 (final layout of the affected columns):
    …Колір | Колір підошви | Опис…
    …Верх | Середина | Підкладка | Устілка | Мембрана | Підошва |
      Проміжна підошва | Тип підошви | Форма носка | Тип шнурівки |
      Застібка | Тип каблука | Технології | Країна-виробник…
    …Поточний стан | Пакування | Екстра примітка…

Ops (applied in this order; indices recomputed against a SIMULATED header
after each op, so the emitted batch is internally consistent):
    1. Колір підошви      → right after «Колір»            (move | insert)
    2. Проміжна підошва   → right after «Підошва»          (insert; NEW column)
    3. Тип шнурівки       → right after «Форма носка»      (move | insert)
    4. Тип каблука        → right after «Застібка»         (move | insert)
    5. Технології         → right before «Країна-виробник» (move | insert)
    6. Пакування          → right before «Екстра примітка» (move | insert)

In the production Журнал tabs ops 1,3,4,5,6 are MOVES of existing data
columns (moveDimension keeps data+format with the column; parser reads by
header name, so position is irrelevant to it). In «New»/«Воркспейс1» (old
layout without the 5 shoe columns) they are INSERTS.

Group: existing groups intersecting [Верх .. Країна-виробник) are deleted
first (collapsed ones get un-hidden so no column is left stranded-hidden);
after the ops ONE depth-1 group [Верх .. Технології] is added; the collapsed
state of the replaced group is preserved.

All-or-nothing per tab: every op's anchor must exist and the simulated final
header must pass all adjacency assertions, otherwise the tab is skipped and
reported. Idempotent: a tab already in the target layout is skipped.

Usage:
    python3 scripts/reorder_journal_shoe_block.py --dry-run
    python3 scripts/reorder_journal_shoe_block.py --execute
    python3 scripts/reorder_journal_shoe_block.py --verify
    python3 scripts/reorder_journal_shoe_block.py --spreadsheet-id <WORKSPACE_ID> --all-tabs --execute
"""
import sys, os, time, json, argparse
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sheets_parser import get_gc, JOURNAL_ID, is_skip_sheet

#            (column,              mode,     anchor)
OPS = [
    ("Колір підошви",      "after",  "Колір"),
    ("Проміжна підошва",   "after",  "Підошва"),
    ("Тип шнурівки",       "after",  "Форма носка"),
    ("Тип каблука",        "after",  "Застібка"),
    ("Технології",         "before", "Країна-виробник"),
    ("Пакування",          "before", "Екстра примітка"),
]
GROUP_FROM, GROUP_TO = "Верх", "Технології"
# Кінець зони конструкції в ПОТОЧНИХ координатах (для пошуку старої групи).
ZONE_END_ANCHOR = "Країна-виробник"
THROTTLE_SEC = 1.1
BACKUP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    f"journal_reorder_backup_{datetime.now():%Y%m%d_%H%M%S}.json",
)


def col_range(sheet_id: int, start: int, end: int) -> dict:
    return {"sheetId": sheet_id, "dimension": "COLUMNS",
            "startIndex": start, "endIndex": end}


def target_predicates(h: list) -> bool:
    """True if header already matches the target layout (all adjacencies)."""
    try:
        for name, mode, anchor in OPS:
            a = h.index(anchor)
            if mode == "after" and h.index(name) != a + 1:
                return False
            if mode == "before" and h.index(name) != a - 1:
                return False
        return h.index(GROUP_FROM) < h.index(GROUP_TO)
    except ValueError:
        return False


def plan_tab(header: list, groups: list, sheet_id: int):
    """Return (requests, info) or (None, reason)."""
    h = list(header)
    # Валідація якорів (all-or-nothing).
    missing = [a for a in {anchor for _, _, anchor in OPS} | {GROUP_FROM, ZONE_END_ANCHOR}
               if a not in h]
    if missing:
        return None, f"anchors-missing:{','.join(missing)}"
    if target_predicates(h):
        return None, "already-done"

    reqs = []
    # 1. Старі групи, що перетинають зону конструкції — un-hide + delete
    #    (у ПОТОЧНИХ координатах, ДО будь-яких рухів).
    zone = (h.index(GROUP_FROM), h.index(ZONE_END_ANCHOR))
    col_groups = [g for g in (groups or [])
                  if g.get("range", {}).get("dimension") == "COLUMNS"]
    inter = [g for g in col_groups
             if g["range"].get("startIndex", 0) < zone[1]
             and g["range"].get("endIndex", 0) > zone[0]]
    if any(g.get("depth", 1) > 1 for g in inter):
        return None, "nested-group-in-zone"
    was_collapsed = any(g.get("collapsed") for g in inter)
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

    # 2. Ops проти симульованого заголовка.
    moved, inserted = [], []
    for name, mode, anchor in OPS:
        a = h.index(anchor)
        dest = a + 1 if mode == "after" else a
        if name in h:
            src = h.index(name)
            if dest in (src, src + 1):
                continue  # вже на місці
            reqs.append({"moveDimension": {
                "source": col_range(sheet_id, src, src + 1),
                "destinationIndex": dest,
            }})
            x = h.pop(src)
            h.insert(dest - 1 if dest > src else dest, x)
            moved.append(name)
        else:
            reqs.append({"insertDimension": {
                "range": col_range(sheet_id, dest, dest + 1),
                "inheritFromBefore": True,
            }})
            reqs.append({"updateCells": {
                "rows": [{"values": [{"userEnteredValue": {"stringValue": name}}]}],
                "fields": "userEnteredValue",
                "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": dest},
            }})
            h.insert(dest, name)
            inserted.append(name)

    # 3. Пост-симуляційні assert-и фінальної розкладки.
    if not target_predicates(h):
        return None, "simulation-failed"
    g_start, g_end = h.index(GROUP_FROM), h.index(GROUP_TO) + 1
    for outsider in ("Колір підошви", "Пакування"):
        if g_start <= h.index(outsider) < g_end:
            return None, "outsider-in-group"

    # 4. Нова група [Верх..Технології] (+ збереження collapsed-стану).
    reqs.append({"addDimensionGroup": {"range": col_range(sheet_id, g_start, g_end)}})
    if was_collapsed:
        reqs.append({"updateDimensionProperties": {
            "range": col_range(sheet_id, g_start, g_end),
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }})
        reqs.append({"updateDimensionGroup": {
            "dimensionGroup": {"range": col_range(sheet_id, g_start, g_end),
                               "depth": 1, "collapsed": True},
            "fields": "collapsed",
        }})
    info = {"moved": moved, "inserted": inserted,
            "group": (g_start, g_end), "was_collapsed": was_collapsed,
            "deleted_groups": [g["range"] for g in inter],
            "final_header_zone": h[max(0, g_start - 2): g_end + 2]}
    return reqs, info


def fetch_state(sh, only_tabs=None, all_tabs=False, include=()):
    fields = "sheets(properties(sheetId,title),columnGroups)"
    meta = sh.fetch_sheet_metadata({"fields": fields})
    if only_tabs is not None:
        wanted = set(only_tabs)
        tabs = [s for s in meta["sheets"] if s["properties"]["title"] in wanted]
        missing = wanted - {s["properties"]["title"] for s in tabs}
        if missing:
            raise SystemExit(f"tabs not found: {sorted(missing)}")
    elif all_tabs:
        tabs = meta["sheets"]
    else:
        tabs = [s for s in meta["sheets"]
                if not is_skip_sheet(s["properties"]["title"])
                or s["properties"]["title"] in include]
    # Явні виключення користувача.
    tabs = [s for s in tabs if s["properties"]["title"] not in ("Publications", "Suppliers")]
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


def verify(sh, only_tabs=None, all_tabs=False, include=()):
    tabs, hdrs = fetch_state(sh, only_tabs=only_tabs, all_tabs=all_tabs, include=include)
    ok, bad = 0, []
    for s in tabs:
        t = s["properties"]["title"]
        h = hdrs.get(t, [])
        if GROUP_FROM not in h:
            continue  # non-product tab
        layout_ok = target_predicates(h)
        gs, ge = (h.index(GROUP_FROM), h.index(GROUP_TO) + 1) if layout_ok else (-1, -1)
        zone_groups = [g for g in (s.get("columnGroups") or [])
                       if g["range"].get("dimension") == "COLUMNS"
                       and g["range"].get("startIndex", -1) == gs
                       and g["range"].get("endIndex", -1) == ge]
        overlapping = [g for g in (s.get("columnGroups") or [])
                       if g["range"].get("dimension") == "COLUMNS"
                       and g["range"].get("startIndex", 0) < ge
                       and g["range"].get("endIndex", 0) > gs
                       and g not in zone_groups]
        # Група Модель..GTIN з попереднього етапу має лишитись.
        mg_ok = True
        if "Модель" in h and "GTIN" in h:
            m0 = h.index("Модель")
            mg_ok = any(g["range"].get("startIndex") == m0 and g["range"].get("endIndex") == m0 + 4
                        for g in (s.get("columnGroups") or [])
                        if g["range"].get("dimension") == "COLUMNS")
        if layout_ok and len(zone_groups) == 1 and not overlapping and mg_ok:
            ok += 1
        else:
            why = ("layout" if not layout_ok else
                   "model-gtin-group-lost" if not mg_ok else
                   f"groups exact={len(zone_groups)} overlap={len(overlapping)}")
            bad.append((t, why))
    print(f"VERIFY: ok={ok}, bad={len(bad)}")
    for t, why in bad[:30]:
        print(f"   [BAD] {t}: {why}")
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--spreadsheet-id", default=JOURNAL_ID)
    ap.add_argument("--tabs", default=None)
    ap.add_argument("--all-tabs", action="store_true")
    args = ap.parse_args()
    only_tabs = [t.strip() for t in args.tabs.split(",")] if args.tabs else None
    include = ("New",) if args.spreadsheet_id == JOURNAL_ID else ()

    gc = get_gc()
    sh = gc.open_by_key(args.spreadsheet_id)

    if args.verify:
        sys.exit(0 if verify(sh, only_tabs=only_tabs, all_tabs=args.all_tabs, include=include) else 1)

    execute = args.execute and not args.dry_run
    tabs, hdrs = fetch_state(sh, only_tabs=only_tabs, all_tabs=args.all_tabs, include=include)
    print(f"target tabs: {len(tabs)}")

    backup = {s["properties"]["title"]: {
        "sheetId": s["properties"]["sheetId"],
        "header": hdrs.get(s["properties"]["title"], []),
        "columnGroups": s.get("columnGroups", []),
    } for s in tabs}
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print(f"backup → {BACKUP_PATH}")

    todo, skips = [], {}
    plans = {}
    for s in tabs:
        t = s["properties"]["title"]
        reqs, info = plan_tab(hdrs.get(t, []), s.get("columnGroups"), s["properties"]["sheetId"])
        if reqs is None:
            skips.setdefault(info, []).append(t)
            continue
        todo.append((t, reqs))
        plans[t] = info

    print(f"  → to process : {len(todo)}")
    for why, ts in sorted(skips.items()):
        print(f"  → skip[{why}]: {len(ts)} {ts[:6]}")
    from collections import Counter
    shape = Counter((tuple(i["moved"]), tuple(i["inserted"])) for i in plans.values())
    for (mv, ins), n in shape.most_common(5):
        print(f"  plan-shape ×{n}: moved={list(mv)} inserted={list(ins)}")

    if not execute:
        print("\nDRY-RUN. Plan for first tab:")
        if todo:
            t = todo[0][0]
            print(f"  [{t}] final zone: {plans[t]['final_header_zone']}")
            for r in todo[0][1]:
                print("   ", json.dumps(r, ensure_ascii=False)[:150])
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
    print("Re-run safe (idempotent). Now run --verify.")


if __name__ == "__main__":
    main()

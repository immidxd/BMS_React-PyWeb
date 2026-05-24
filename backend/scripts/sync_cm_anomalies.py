"""
Surgical sync: re-read current СМ values from Журнал for the 40 productnumbers
that previously had unparseable measurementscm in DB. Update both the TEXT
column and the new min/max numeric columns.

No full re-parse — only touches measurementscm fields of these specific products.
Idempotent. Commits on success per-sheet.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import defaultdict
from sqlalchemy import text
from models.database import SessionLocal
from scripts.sheets_parser import get_gc, JOURNAL_ID, _parse_measurement_range, _normalize_size


def main():
    session = SessionLocal()

    # Pull current anomaly list from DB (any product with non-empty TEXT but NULL min)
    rows = session.execute(text("""
        SELECT p.id, p.productnumber, p.measurementscm, COALESCE(d.deliveryname, '') AS sheet
          FROM products p
          LEFT JOIN deliveries d ON d.id = p.deliveryid
         WHERE p.measurementscm IS NOT NULL
           AND p.measurementscm <> ''
           AND p.measurementscm_min IS NULL
    """)).fetchall()
    print(f"Anomalies in DB: {len(rows)}")
    if not rows:
        print("Nothing to sync — DB already clean.")
        return

    # Group by sheet for batched reads
    by_sheet: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        if not r.sheet:
            print(f"  [SKIP] {r.productnumber}: no sheet/delivery linked")
            continue
        by_sheet[r.sheet].append((r.id, r.productnumber, r.measurementscm))

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    ws_by_title = {w.title: w for w in sh.worksheets()}

    updated = cleared = unchanged = missing = 0

    for sheet_title, items in by_sheet.items():
        ws = ws_by_title.get(sheet_title)
        if not ws:
            print(f"  [WARN] sheet '{sheet_title}' not found — skipping {len(items)} items")
            continue

        print(f"\n=== {sheet_title} ({len(items)} items) ===")
        # Read whole sheet once (1 read call)
        all_rows = ws.get_all_values()
        if not all_rows:
            print(f"  [WARN] empty sheet")
            continue
        header = all_rows[0]
        try:
            num_col = header.index("Номер")
            cm_col  = header.index("СМ")
        except ValueError as e:
            print(f"  [WARN] missing required column: {e}")
            continue

        # Build pnum → cm value(s) map from sheet
        # Multiple rows may share same pnum (ростовка) — collect all, dedupe values
        sheet_pnum_to_cm: dict[str, set[str]] = defaultdict(set)
        for r in all_rows[1:]:
            if not r or len(r) <= max(num_col, cm_col):
                continue
            pn = (r[num_col] or "").strip()
            cm_raw = (r[cm_col]  or "").strip()
            if not pn:
                continue
            sheet_pnum_to_cm[pn].add(cm_raw)

        for prod_id, pnum, db_cm in items:
            sheet_cm_set = sheet_pnum_to_cm.get(pnum, set())
            if not sheet_cm_set:
                print(f"  [MISS] {pnum}: not found in sheet (DB cm={db_cm!r})")
                missing += 1
                continue

            # If sheet has multiple rows with different cm values, pick the FIRST non-empty
            # (the rostovka may have per-row cm). Most cleanups will give single value anyway.
            candidates = [c for c in sheet_cm_set if c]
            if not candidates:
                # Sheet now has empty СМ for this pnum → clear DB
                session.execute(
                    text("UPDATE products SET measurementscm = NULL, measurementscm_min = NULL, measurementscm_max = NULL WHERE id = :id"),
                    {"id": prod_id}
                )
                print(f"  [CLEAR] {pnum}: sheet empty, db was {db_cm!r}")
                cleared += 1
                continue

            new_cm_raw = candidates[0]
            # Normalize like the parser does
            new_cm_norm = _normalize_size(new_cm_raw)
            cm_min, cm_max = _parse_measurement_range(new_cm_norm)

            if new_cm_norm == (db_cm or "") and cm_min is not None:
                # Already in sync? shouldn't really happen because min was NULL, but defensive
                unchanged += 1
                continue

            session.execute(
                text("""
                    UPDATE products
                       SET measurementscm     = :cm_text,
                           measurementscm_min = :cm_min,
                           measurementscm_max = :cm_max
                     WHERE id = :id
                """),
                {"id": prod_id,
                 "cm_text": new_cm_norm or None,
                 "cm_min":  cm_min,
                 "cm_max":  cm_max}
            )
            print(f"  [UPD] {pnum}: {db_cm!r} → text={new_cm_norm!r}, min={cm_min}, max={cm_max}")
            updated += 1

        session.commit()
        print(f"  committed {sheet_title}")

    print(f"\nSUMMARY: updated={updated}, cleared={cleared}, missing={missing}, unchanged={unchanged}")

    # Final stat
    final = session.execute(text("""
        SELECT COUNT(*) FILTER (WHERE measurementscm IS NOT NULL AND measurementscm != '' AND measurementscm_min IS NULL) AS still_unparsed
          FROM products
    """)).first()
    print(f"Remaining unparsed in DB: {final.still_unparsed}")
    session.close()


if __name__ == "__main__":
    main()

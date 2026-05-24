"""
Dry-run: parse ONE sheet from Журнал through the full sheets_parser pipeline,
report results, then rollback (zero DB persistence).

Validates that the new measurement/material/lookup code paths work on real data.

Usage:
    ./venv/bin/python3 backend/scripts/dryrun_one_sheet.py [sheet_title_substring]

If no arg given — picks the most recent (first) non-skip sheet.
"""
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from models.database import SessionLocal
from scripts.sheets_parser import (
    get_gc, JOURNAL_ID, is_skip_sheet,
    _parse_products_sheet, _get_or_create_supplier, _get_or_create_shipment,
    _parse_delivery_financials, parse_date_from_sheet_title, parse_supplier_from_sheet_title,
)


def main():
    title_filter = sys.argv[1] if len(sys.argv) > 1 else None

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    all_sheets = sh.worksheets()
    batch = [ws for ws in all_sheets if not is_skip_sheet(ws.title)]

    if title_filter:
        match = [ws for ws in batch if title_filter.lower() in ws.title.lower()]
        if not match:
            print(f"[ERR] No sheet matches '{title_filter}'. Available:")
            for ws in batch[:10]:
                print(f"  - {ws.title}")
            sys.exit(1)
        ws = match[0]
    else:
        ws = batch[0]   # most recent

    print(f"=" * 70)
    print(f"DRY-RUN target sheet: {ws.title!r}")
    print(f"=" * 70)

    session = SessionLocal()

    # Baseline counts (will compare after parser; should NOT change because of rollback)
    def snapshot():
        return session.execute(text("""
            SELECT COUNT(*) AS products,
                   COUNT(measurements_pog_min) AS with_pog,
                   COUNT(measurements_heel_min) AS with_heel,
                   COUNT(soletypeid) AS with_soletype,
                   (SELECT COUNT(*) FROM product_materials) AS pm_rows,
                   (SELECT COUNT(*) FROM unmapped_materials) AS unmapped_rows
            FROM products
        """)).first()

    before = snapshot()
    print(f"BEFORE: products={before.products}, with_pog={before.with_pog}, "
          f"with_heel={before.with_heel}, with_soletype={before.with_soletype}, "
          f"pm_rows={before.pm_rows}, unmapped_rows={before.unmapped_rows}")

    try:
        sheet_date = parse_date_from_sheet_title(ws.title)
        supplier_name = parse_supplier_from_sheet_title(ws.title)
        supplier_id = _get_or_create_supplier(session, supplier_name) if supplier_name else None
        all_rows = ws.get_all_values()
        financials = _parse_delivery_financials(all_rows)
        shipment_id = _get_or_create_shipment(
            session, ws.title, sheet_date, supplier_id,
            purchase_cost=financials["purchase_cost"],
            delivery_cost=financials["delivery_cost"],
        )

        print(f"\nParsing {len(all_rows)} rows… (sheet_date={sheet_date}, supplier={supplier_name})")
        t0 = time.time()
        result = _parse_products_sheet(
            ws, session, sheet_date,
            progress_cb=None,
            seen_in_run={},
            supplier_id=supplier_id,
            shipment_id=shipment_id,
            prefetched_rows=all_rows,
        )
        elapsed = time.time() - t0

        print(f"\nRESULT (in-transaction, not committed):")
        print(f"  added   = {result.get('added')}")
        print(f"  updated = {result.get('updated')}")
        print(f"  skipped = {result.get('skipped')}")
        print(f"  elapsed = {elapsed:.1f}s")

        # In-transaction snapshot — to see what WOULD be persisted
        in_tx = snapshot()
        print(f"\nIN-TX deltas (vs before, rolled back after this):")
        print(f"  Δ products       = {in_tx.products - before.products}")
        print(f"  Δ with_pog       = {in_tx.with_pog - before.with_pog}")
        print(f"  Δ with_heel      = {in_tx.with_heel - before.with_heel}")
        print(f"  Δ with_soletype  = {in_tx.with_soletype - before.with_soletype}")
        print(f"  Δ pm_rows        = {in_tx.pm_rows - before.pm_rows}")
        print(f"  Δ unmapped_rows  = {in_tx.unmapped_rows - before.unmapped_rows}")

        # Show sample of unmapped materials (signals — what's in the wild that we haven't seeded)
        unmapped = session.execute(text("""
            SELECT raw_value, position, seen_count
              FROM unmapped_materials
             WHERE resolved = FALSE
             ORDER BY last_seen DESC
             LIMIT 20
        """)).fetchall()
        if unmapped:
            print(f"\nSample unmapped values (top 20):")
            for u in unmapped:
                print(f"  [{u.position}] {u.raw_value!r}  (seen {u.seen_count}×)")
        else:
            print("\nNo unmapped values — either all matched or the sheet had no material/lookup cells.")

    except Exception as e:
        print(f"\n[ERR] Parser raised: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        sys.exit(2)
    finally:
        # ROLLBACK — nothing is persisted
        session.rollback()
        after = snapshot()
        assert after.products == before.products, "Rollback didn't restore product count!"
        print(f"\nAFTER ROLLBACK: products={after.products} (matches baseline ✓)")
        session.close()


if __name__ == "__main__":
    main()

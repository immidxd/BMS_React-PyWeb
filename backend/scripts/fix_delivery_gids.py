"""One-time fix: backfill deliveries.sheet_gid from live journal tabs and
reattach orphaned products from a stale placeholder delivery to the renamed one.

DRY-RUN by default. Pass --apply to commit.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from backend.scripts.sheets_parser import get_gc, JOURNAL_ID
from backend.models.database import SessionLocal

APPLY = "--apply" in sys.argv


def main():
    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    # title -> gid (live, authoritative)
    title_to_gid = {ws.title: ws.id for ws in sh.worksheets()}
    print(f"Live tabs: {len(title_to_gid)}")

    s = SessionLocal()
    try:
        # ── 1. Backfill sheet_gid for NULL-gid deliveries matching a live tab ──
        rows = s.execute(text(
            "SELECT id, deliveryname FROM deliveries WHERE sheet_gid IS NULL ORDER BY id"
        )).fetchall()
        backfill, orphans, twin_merges = [], [], []
        for did, name in rows:
            gid = title_to_gid.get(name)
            if gid is None:
                orphans.append((did, name))
                continue
            # another delivery already holds this gid = stale twin (old placeholder
            # name kept the gid; products mostly went to the renamed canonical row).
            taken = s.execute(text("SELECT id FROM deliveries WHERE sheet_gid = :g"), {"g": gid}).scalar()
            if taken:
                twin_merges.append((taken, did, name, gid))  # (stale_twin, canonical, name, gid)
                continue
            backfill.append((did, name, gid))

        print(f"\n=== BACKFILL sheet_gid ({len(backfill)}) ===")
        for did, name, gid in backfill:
            print(f"  id={did:<5} '{name}' → gid={gid}")
            if APPLY:
                s.execute(text("UPDATE deliveries SET sheet_gid=:g WHERE id=:id"), {"g": gid, "id": did})

        print(f"\n=== TWIN MERGES (stale gid-holder → canonical by tab name) ({len(twin_merges)}) ===")
        for stale, canon, name, gid in twin_merges:
            c_stale = s.execute(text("SELECT COUNT(*) FROM products WHERE deliveryid=:id"), {"id": stale}).scalar()
            c_canon = s.execute(text("SELECT COUNT(*) FROM products WHERE deliveryid=:id"), {"id": canon}).scalar()
            sname = s.execute(text("SELECT deliveryname FROM deliveries WHERE id=:id"), {"id": stale}).scalar()
            print(f"  stale id={stale} '{sname}' products={c_stale}  →  canonical id={canon} '{name}' products={c_canon} (gid={gid})")
            if APPLY:
                s.execute(text("UPDATE products SET deliveryid=:c WHERE deliveryid=:s"), {"c": canon, "s": stale})
                s.execute(text(
                    "UPDATE deliveries t SET purchase_cost=GREATEST(t.purchase_cost, src.purchase_cost), "
                    "delivery_cost=GREATEST(t.delivery_cost, src.delivery_cost) "
                    "FROM deliveries src WHERE t.id=:c AND src.id=:s"
                ), {"c": canon, "s": stale})
                s.execute(text("DELETE FROM deliveries WHERE id=:s"), {"s": stale})
                s.execute(text("UPDATE deliveries SET sheet_gid=:g WHERE id=:c"), {"g": gid, "c": canon})
                print(f"    APPLIED: merged {stale}→{canon}, gid set")

        print(f"\n=== ORPHAN deliveries (NULL gid, no live tab by name) — {len(orphans)} ===")
        for did, name in orphans:
            cnt = s.execute(text("SELECT COUNT(*) FROM products WHERE deliveryid=:id"), {"id": did}).scalar()
            print(f"  id={did:<5} '{name}'  products={cnt}")

        # ── 2. Reattach 777 (xx.06.2026 Садовий, orphan) → 781 (14.06.2026 Садовий) ──
        print("\n=== REATTACH 777 → 781 ===")
        cnt777 = s.execute(text("SELECT COUNT(*) FROM products WHERE deliveryid=777")).scalar()
        cnt781 = s.execute(text("SELECT COUNT(*) FROM products WHERE deliveryid=781")).scalar()
        d777 = s.execute(text("SELECT deliveryname FROM deliveries WHERE id=777")).scalar()
        d781 = s.execute(text("SELECT deliveryname FROM deliveries WHERE id=781")).scalar()
        print(f"  777 '{d777}' products={cnt777}  →  781 '{d781}' products={cnt781}")
        if d777 and d781 and "Садов" in (d777 or "") and "14.06.2026" in (d781 or ""):
            if APPLY:
                s.execute(text("UPDATE products SET deliveryid=781 WHERE deliveryid=777"))
                # carry costs if 781 has none
                s.execute(text(
                    "UPDATE deliveries t SET purchase_cost=GREATEST(t.purchase_cost, src.purchase_cost), "
                    "delivery_cost=GREATEST(t.delivery_cost, src.delivery_cost) "
                    "FROM deliveries src WHERE t.id=781 AND src.id=777"
                ))
                s.execute(text("DELETE FROM deliveries WHERE id=777"))
                print("  APPLIED: products moved, 777 deleted")
            else:
                print("  (dry-run) would move products and delete 777")
        else:
            print("  GUARD FAILED — names don't match expectation, skipping")

        if APPLY:
            s.commit()
            print("\nCOMMITTED.")
        else:
            s.rollback()
            print("\nDRY-RUN only. Re-run with --apply to commit.")
    finally:
        s.close()


if __name__ == "__main__":
    main()

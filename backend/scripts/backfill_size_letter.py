"""
One-off DB cleanup: migrate letter sizes (XS/S/M/L/XL/XXL/...) that are currently
stored in products.sizeeu (and rarely in description) → products.size_letter.

Sources scanned (in priority order):
  1. sizeeu — pure letter token (e.g. "L", "XL", "3XL", "5XL")
     → size_letter = canonical letter, sizeeu = NULL
  2. sizeeu — mixed letter+number compound (e.g. "L 42/44 FR / M 40/42 EUR")
     → size_letter = first canonical letter, sizeeu UNCHANGED
       (numbers might still be useful; user can manually clean compound rows)
  3. description — explicit "Розмір: L" / "Size: XL" keyword
     → size_letter = canonical letter, description UNCHANGED

Idempotent: skips rows where size_letter already set.

Usage:
    PYTHONPATH=. ./venv/bin/python3 backend/scripts/backfill_size_letter.py
        # dry-run by default — prints what would change
    PYTHONPATH=. ./venv/bin/python3 backend/scripts/backfill_size_letter.py --apply
        # actually commit
"""
import argparse
import re
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from models.database import SessionLocal
from scripts.sheets_parser import _normalize_size_letter


PURE_LETTER_RE = re.compile(
    r"^(XS|S|M|L|XL|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL)$",
    re.IGNORECASE,
)

MIXED_RE = re.compile(
    r"(?:^|[\s/(])(XS|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL|XL|S|M|L)(?=\s|/|$)",
    re.IGNORECASE,
)

# Conservative description regex: must have explicit Розмір/Size keyword
DESC_RE = re.compile(
    r"(?:розм[іе]р|размер|розм\.?|size)\s*:?\s*(XS|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL|XL|L|M|S)\b",
    re.IGNORECASE,
)


def detect_letter(sizeeu: str | None, description: str | None) -> tuple[str, str]:
    """Return (canonical_letter, source). source ∈ {'sizeeu_pure', 'sizeeu_mixed', 'desc', ''}."""
    if sizeeu:
        s = sizeeu.strip()
        if PURE_LETTER_RE.match(s):
            return (_normalize_size_letter(s), "sizeeu_pure")
        # Mixed: contains both letter and digit
        if re.search(r"\d", s) and MIXED_RE.search(s):
            letter = _normalize_size_letter(s)
            if letter:
                return (letter, "sizeeu_mixed")
    if description:
        m = DESC_RE.search(description)
        if m:
            letter = _normalize_size_letter(m.group(1))
            if letter:
                return (letter, "desc")
    return ("", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually commit changes. Without this, dry-run only.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process at most N candidate rows (for testing).")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, productnumber, sizeeu, description, size_letter
            FROM products
            WHERE size_letter IS NULL
              AND (
                   sizeeu ~* '[A-Za-zА-Яа-я]'
                   OR description ~* '(розм[іе]р|размер|розм\\.|size)\\s*:?\\s*(XS|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL|XL|L|M|S)\\b'
              )
            ORDER BY id
        """)).fetchall()

        print(f"Mode: {'APPLY (will commit)' if args.apply else 'DRY-RUN (no writes)'}")
        print(f"Candidate rows: {len(rows)}")
        if args.limit:
            rows = rows[: args.limit]
            print(f"Processing first {len(rows)} (limit)")
        print("=" * 70)

        stats = Counter()
        updates = []   # list of (id, productnumber, letter, source, clear_sizeeu, old_sizeeu)

        for r in rows:
            pid, pnum, sizeeu, desc, _existing = r
            letter, source = detect_letter(sizeeu, desc)
            if not letter:
                stats["no_match"] += 1
                continue
            clear_sizeeu = (source == "sizeeu_pure")
            updates.append((pid, pnum, letter, source, clear_sizeeu, sizeeu))
            stats[source] += 1
            stats[f"letter:{letter}"] += 1

        print("\n=== STATS ===")
        for k in sorted(stats):
            if k.startswith("letter:"):
                continue
            print(f"  {k:20} {stats[k]}")
        print("\n  Distribution by letter:")
        for k in sorted(stats):
            if k.startswith("letter:"):
                print(f"    {k.split(':',1)[1]:8} {stats[k]}")

        print(f"\n=== UPDATES PLANNED: {len(updates)} ===")
        print("Showing first 30:")
        for u in updates[:30]:
            pid, pnum, letter, source, clear, old = u
            clr_note = "+ sizeeu→NULL" if clear else ""
            print(f"  id={pid:7}  pnum={pnum:12}  → size_letter={letter:8}  source={source:14} (was sizeeu={old!r}) {clr_note}")
        if len(updates) > 30:
            print(f"  ... and {len(updates) - 30} more")

        # Mixed cases — show all (only ~6 expected)
        mixed = [u for u in updates if u[3] == "sizeeu_mixed"]
        if mixed:
            print(f"\n=== MIXED CASES (kept sizeeu intact, only added size_letter): {len(mixed)} ===")
            for u in mixed:
                pid, pnum, letter, source, clear, old = u
                print(f"  id={pid:7}  pnum={pnum:12}  size_letter={letter}  sizeeu STAYS={old!r}")

        if not args.apply:
            print("\nDRY-RUN — no changes committed. Re-run with --apply to commit.")
            return

        # APPLY — commit one row at a time so a UNIQUE conflict on one orphan-twin
        # doesn't roll back the rest.
        print("\nApplying updates ...")
        n_ok = 0
        n_conflict = 0
        conflicts = []
        for pid, pnum, letter, source, clear_sizeeu, _old in updates:
            try:
                # Detect conflict pre-emptively for clear_sizeeu path: is there another row
                # with same productnumber+colorid and sizeeu=NULL (or "")?
                if clear_sizeeu:
                    conflict = db.execute(text("""
                        SELECT id FROM products
                        WHERE productnumber = (SELECT productnumber FROM products WHERE id = :id)
                          AND COALESCE(colorid, 0) = (SELECT COALESCE(colorid, 0) FROM products WHERE id = :id)
                          AND id != :id
                          AND (sizeeu IS NULL OR sizeeu = '')
                        LIMIT 1
                    """), {"id": pid}).fetchone()
                    if conflict:
                        n_conflict += 1
                        conflicts.append((pid, pnum, letter, conflict[0]))
                        # Still set size_letter; just don't NULL sizeeu (keep letter there for now)
                        db.execute(text("UPDATE products SET size_letter = :l WHERE id = :id"),
                                   {"l": letter, "id": pid})
                        db.commit()
                        n_ok += 1
                        continue
                    db.execute(text("UPDATE products SET size_letter = :l, sizeeu = NULL WHERE id = :id"),
                               {"l": letter, "id": pid})
                else:
                    db.execute(text("UPDATE products SET size_letter = :l WHERE id = :id"),
                               {"l": letter, "id": pid})
                db.commit()
                n_ok += 1
            except Exception as e:
                db.rollback()
                n_conflict += 1
                conflicts.append((pid, pnum, letter, f"ERROR: {type(e).__name__}: {str(e)[:120]}"))
        print(f"Committed {n_ok} updates ({n_conflict} conflicts — see below).")
        if conflicts:
            print(f"\n=== CONFLICTS (size_letter set, but sizeeu NOT cleared due to orphan twin): {len(conflicts)} ===")
            for pid, pnum, letter, info in conflicts[:30]:
                print(f"  id={pid:7}  pnum={pnum:12}  letter={letter:8}  conflict_with={info}")
            if len(conflicts) > 30:
                print(f"  ... and {len(conflicts) - 30} more")

        # Verify
        n_filled = db.execute(text("SELECT COUNT(*) FROM products WHERE size_letter IS NOT NULL")).scalar()
        n_remaining = db.execute(text("""
            SELECT COUNT(*) FROM products
            WHERE size_letter IS NULL
              AND sizeeu ~* '^(XS|S|M|L|XL|XXL|XXXL|XXXXL|XXXXXL|XXXXXXL|[2-6]XL)$'
        """)).scalar()
        print(f"\nFinal: products.size_letter filled = {n_filled}")
        print(f"Pure-letter sizeeu still un-migrated = {n_remaining}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

"""
Cleanup suffix duplicates (-2, -3) that were created by the old parser logic.

Strategy:
  For each product family (e.g. #Л313, #Л313-2, #Л313-3):
  1. If ALL records share the same brand (or have NULL brand) → Category A
     → Keep the base record, merge data from suffixed records, delete them.
  2. If brands genuinely differ → Category B (legitimate different products)
     → Keep all records as-is.

  "Merging data" means: update the base record with non-NULL fields from
  suffixed records (price, type, color, condition, etc.), preferring the
  record with the most complete data.

Safety:
  - Reassigns order_items from deleted products to the base record.
  - Dry-run mode by default (--apply to actually commit).
  - Full log of all actions.

Usage:
  python -m backend.scripts.cleanup_suffix_duplicates            # dry run
  python -m backend.scripts.cleanup_suffix_duplicates --apply    # commit changes
"""

import re
import sys
import logging
from collections import defaultdict

from sqlalchemy import text
from backend.models.database import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def _fields_match(a, b) -> bool:
    """Same logic as sheets_parser._fields_match."""
    def is_empty(v):
        return v is None or str(v).strip() == "" or v == 0
    if is_empty(a) or is_empty(b):
        return True
    return str(a).strip().lower() == str(b).strip().lower()


def _completeness_score(row: dict) -> int:
    """Higher score = more complete data. Used to pick the best record to keep."""
    score = 0
    for key in ('brandid', 'typeid', 'colorid', 'conditionid', 'sizeeu',
                'price', 'model', 'marking', 'description', 'genderid',
                'subtypeid', 'measurementscm'):
        val = row.get(key)
        if val is not None and str(val).strip() != '' and val != 0:
            score += 1
    if row.get('price') and float(row['price']) > 0:
        score += 2  # price is especially important
    return score


def run_cleanup(apply: bool = False):
    session = SessionLocal()
    try:
        # 1. Find all suffixed products
        suffixed = session.execute(text(
            "SELECT id, productnumber FROM products WHERE productnumber ~ '-\\d+$'"
        )).fetchall()

        if not suffixed:
            logger.info("No suffixed products found. Nothing to clean up.")
            return

        logger.info(f"Found {len(suffixed)} suffixed products")

        # Group by base number
        families = defaultdict(list)
        for row in suffixed:
            base = re.sub(r'-\d+$', '', row.productnumber)
            families[base].append(row.productnumber)

        merged_count = 0
        kept_count = 0
        deleted_ids = []

        for base_pnum, suffix_pnums in sorted(families.items()):
            # Fetch all family members (base + suffixed)
            all_pnums = [base_pnum] + suffix_pnums
            placeholders = ', '.join(f":p{i}" for i in range(len(all_pnums)))
            params = {f"p{i}": pn for i, pn in enumerate(all_pnums)}

            family = session.execute(text(f"""
                SELECT id, productnumber, brandid, typeid, colorid, conditionid,
                       sizeeu, price, model, marking, description, genderid,
                       subtypeid, measurementscm, quantity, statusid, deliveryid,
                       dateadded, oldprice, year, manufacturercountryid, ownercountryid
                FROM products
                WHERE productnumber IN ({placeholders})
                ORDER BY productnumber
            """), params).fetchall()

            if len(family) < 2:
                # Base record might not exist (was deleted), skip
                continue

            # Convert to dicts
            columns = ['id', 'productnumber', 'brandid', 'typeid', 'colorid',
                       'conditionid', 'sizeeu', 'price', 'model', 'marking',
                       'description', 'genderid', 'subtypeid', 'measurementscm',
                       'quantity', 'statusid', 'deliveryid', 'dateadded',
                       'oldprice', 'year', 'manufacturercountryid', 'ownercountryid']
            family_dicts = [dict(zip(columns, row)) for row in family]

            # Check if brands are compatible (Category A vs B)
            brands = [d['brandid'] for d in family_dicts if d['brandid'] is not None]
            unique_brands = set(brands)

            if len(unique_brands) > 1:
                # Category B: genuinely different brands → keep all
                logger.info(
                    f"  KEEP {base_pnum} family ({len(family_dicts)} records) "
                    f"— different brands: {unique_brands}"
                )
                kept_count += len(suffix_pnums)
                continue

            # Category A: same brand → merge into best record
            # Pick the record with the most complete data as "keeper"
            family_dicts.sort(key=lambda d: _completeness_score(d), reverse=True)
            keeper = family_dicts[0]
            to_delete = family_dicts[1:]

            # Merge: fill NULL fields in keeper from other records
            for donor in to_delete:
                for field in columns:
                    if field in ('id', 'productnumber', 'quantity'):
                        continue
                    keeper_val = keeper[field]
                    donor_val = donor[field]
                    if (keeper_val is None or str(keeper_val).strip() == '' or keeper_val == 0):
                        if donor_val is not None and str(donor_val).strip() != '' and donor_val != 0:
                            keeper[field] = donor_val

            keeper_id = keeper['id']
            keeper_pnum = keeper['productnumber']
            delete_ids = [d['id'] for d in to_delete]

            logger.info(
                f"  MERGE {base_pnum} family: keep id={keeper_id} ({keeper_pnum}), "
                f"delete ids={delete_ids}"
            )

            if apply:
                # Update keeper with merged data (except productnumber)
                update_fields = []
                update_params = {"keeper_id": keeper_id}
                for field in columns:
                    if field in ('id', 'productnumber'):
                        continue
                    val = keeper[field]
                    update_fields.append(f"{field} = :{field}")
                    update_params[field] = val

                session.execute(text(
                    f"UPDATE products SET {', '.join(update_fields)} "
                    f"WHERE id = :keeper_id"
                ), update_params)

                # Reassign order_items from deleted products to keeper
                for del_id in delete_ids:
                    session.execute(text(
                        "UPDATE order_items SET product_id = :keeper_id "
                        "WHERE product_id = :old_id"
                    ), {"keeper_id": keeper_id, "old_id": del_id})

                # DELETE duplicates FIRST (to free up productnumber + unique constraint)
                for del_id in delete_ids:
                    session.execute(text(
                        "DELETE FROM products WHERE id = :id"
                    ), {"id": del_id})

                # THEN rename keeper to base productnumber (if it had a suffix)
                if keeper_pnum != base_pnum:
                    session.execute(text(
                        "UPDATE products SET productnumber = :pnum WHERE id = :pid"
                    ), {"pnum": base_pnum, "pid": keeper_id})

                deleted_ids.extend(delete_ids)

            merged_count += 1

        if apply:
            session.commit()
            logger.info(f"\nDONE (APPLIED): {merged_count} families merged, "
                        f"{len(deleted_ids)} records deleted, "
                        f"{kept_count} records kept (different brands)")
        else:
            logger.info(f"\nDRY RUN: {merged_count} families would be merged, "
                        f"{kept_count} records would be kept (different brands)")
            logger.info("Run with --apply to commit changes.")

    except Exception as e:
        session.rollback()
        logger.error(f"Error: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    if apply_flag:
        logger.info("=== RUNNING IN APPLY MODE — changes WILL be committed ===")
    else:
        logger.info("=== DRY RUN — no changes will be made ===")
    run_cleanup(apply=apply_flag)

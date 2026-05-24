-- Migration: add measurementscm_min/max numeric columns alongside the existing
--            measurementscm TEXT column (kept for display backward-compat).
-- Date: 2026-05-24
--
-- Why:
--   `measurementscm` is the most-filtered measurement on shoes (insole cm,
--   correlates with size). Stored as TEXT ("26", "23.5", "39-40"), it forces
--   string-parsing in every range query — slow and brittle for "39-40,5",
--   en-dash variants, etc.
--   Adding paired numeric min/max lets us write fast indexed range filters
--   (`WHERE cm_min <= X AND cm_max >= X`) consistent with the other new
--   clothing/shoe measurements added in 2026_05_23_001…004.
--
-- Convention (same as other *_min/*_max pairs):
--   single value → (v, v).  range → (lo, hi).  Unparseable / empty → (NULL, NULL).
--
-- Backfill rules (regex-based):
--   1. "26" / "26.5" / "26,5"               → cm_min = cm_max = v
--   2. "39-40" / "39–40" / "39—40" / "39/40" (with comma decimals)
--                                           → cm_min = lo, cm_max = hi
--   3. Anything else ("60×120", "25.5 см",   → leave NULL (~20 legacy rows;
--      "108 х 3.2", …)                         user can fix manually)
--
-- Old TEXT column is NOT dropped — UI still reads it. Parser writes BOTH.

BEGIN;

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS measurementscm_min FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurementscm_max FLOAT DEFAULT NULL;

-- ── Backfill: range pattern first (more specific) ───────────────────────────
-- Matches: "39-40" / "39–40" / "39—40" / "39/40"  with optional decimals
-- (comma or dot) on either side, allowing surrounding whitespace.
UPDATE products
   SET measurementscm_min = REPLACE(
         SUBSTRING(measurementscm FROM '^\s*([0-9]+(?:[.,][0-9]+)?)\s*[-–—/]'),
         ',', '.'
       )::FLOAT,
       measurementscm_max = REPLACE(
         SUBSTRING(measurementscm FROM '[-–—/]\s*([0-9]+(?:[.,][0-9]+)?)\s*$'),
         ',', '.'
       )::FLOAT
 WHERE measurementscm IS NOT NULL
   AND measurementscm <> ''
   AND measurementscm ~ '^\s*[0-9]+(?:[.,][0-9]+)?\s*[-–—/]\s*[0-9]+(?:[.,][0-9]+)?\s*$';

-- ── Backfill: single-value pattern ──────────────────────────────────────────
UPDATE products
   SET measurementscm_min = REPLACE(TRIM(measurementscm), ',', '.')::FLOAT,
       measurementscm_max = REPLACE(TRIM(measurementscm), ',', '.')::FLOAT
 WHERE measurementscm IS NOT NULL
   AND measurementscm <> ''
   AND measurementscm_min IS NULL    -- not yet filled by range pass
   AND measurementscm ~ '^\s*[0-9]+(?:[.,][0-9]+)?\s*$';

CREATE INDEX IF NOT EXISTS idx_measurementscm_range
  ON products(measurementscm_min, measurementscm_max)
  WHERE measurementscm_min IS NOT NULL OR measurementscm_max IS NOT NULL;

COMMIT;

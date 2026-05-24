-- Migration: Extend clothing/shoe measurements + introduce materials taxonomy
-- Date: 2026-05-23
-- Purpose:
--   1) Convert single-value `measurements_length` → `_min/_max` (consistent with pog/pob/pot).
--   2) Add new min/max measurement columns: sleeve (рукав), height (висота взуття),
--      sole_thickness (товщина підошви/платформи).
--   3) Backfill existing single-value rows where `_max IS NULL` ←  copy `_min`
--      (parser previously stored single value as min only — broke BETWEEN-style filters).
--   4) Create `materials` taxonomy table with self-referencing parent_id (е.g. "шкіра" → "гладка шкіра")
--      and `category` (leather/textile/synthetic/rubber/membrane/other).
--   5) Create `product_materials` junction (many-to-many w/ position: upper/middle/insole/sole/membrane).
--
-- Idempotent: all ALTERs use IF [NOT] EXISTS; CREATE uses IF NOT EXISTS.
-- Safe to re-run.

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) Length: single → min/max
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS measurements_length_min FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_length_max FLOAT DEFAULT NULL;

-- Backfill from legacy single-value `measurements_length` (if column still exists)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='products' AND column_name='measurements_length'
  ) THEN
    UPDATE products
       SET measurements_length_min = COALESCE(measurements_length_min, measurements_length),
           measurements_length_max = COALESCE(measurements_length_max, measurements_length)
     WHERE measurements_length IS NOT NULL;

    ALTER TABLE products DROP COLUMN measurements_length;
  END IF;
END$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2) New min/max measurement columns
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS measurements_sleeve_min          FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_sleeve_max          FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_height_min          FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_height_max          FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_sole_thickness_min  FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_sole_thickness_max  FLOAT DEFAULT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3) Backfill single-value semantics: where _max IS NULL but _min IS NOT NULL → _max := _min
--    (so range filters `WHERE _min <= X AND _max >= X` start matching exact values).
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE products SET measurements_pog_max = measurements_pog_min
 WHERE measurements_pog_min IS NOT NULL AND measurements_pog_max IS NULL;

UPDATE products SET measurements_pob_max = measurements_pob_min
 WHERE measurements_pob_min IS NOT NULL AND measurements_pob_max IS NULL;

UPDATE products SET measurements_pot_max = measurements_pot_min
 WHERE measurements_pot_min IS NOT NULL AND measurements_pot_max IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4) Indexes for new range columns
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_measurements_length
  ON products(measurements_length_min, measurements_length_max)
  WHERE measurements_length_min IS NOT NULL OR measurements_length_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_sleeve
  ON products(measurements_sleeve_min, measurements_sleeve_max)
  WHERE measurements_sleeve_min IS NOT NULL OR measurements_sleeve_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_height
  ON products(measurements_height_min, measurements_height_max)
  WHERE measurements_height_min IS NOT NULL OR measurements_height_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_sole_thickness
  ON products(measurements_sole_thickness_min, measurements_sole_thickness_max)
  WHERE measurements_sole_thickness_min IS NOT NULL OR measurements_sole_thickness_max IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5) Materials taxonomy
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS materials (
    id           SERIAL PRIMARY KEY,
    materialname TEXT    NOT NULL UNIQUE,           -- canonical lowercase: 'шкіра', 'гладка шкіра', 'gore-tex'
    parent_id    INTEGER REFERENCES materials(id) ON DELETE SET NULL,
    category     TEXT    NOT NULL DEFAULT 'other',  -- leather|textile|synthetic|rubber|membrane|other
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_materials_parent   ON materials(parent_id);
CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category);

-- Junction: product × material × position
CREATE TABLE IF NOT EXISTS product_materials (
    product_id  INTEGER NOT NULL REFERENCES products(id)  ON DELETE CASCADE,
    position    TEXT    NOT NULL,                  -- upper|middle|insole|sole|membrane
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    ord         SMALLINT DEFAULT 0,                -- ordering within (product, position)
    PRIMARY KEY (product_id, position, material_id),
    CONSTRAINT chk_product_materials_position
      CHECK (position IN ('upper','middle','insole','sole','membrane'))
);

CREATE INDEX IF NOT EXISTS idx_product_materials_product  ON product_materials(product_id);
CREATE INDEX IF NOT EXISTS idx_product_materials_material ON product_materials(material_id);
CREATE INDEX IF NOT EXISTS idx_product_materials_position ON product_materials(position);

-- Unmapped material names from parser — queue for manual review (won't auto-create dictionary entries).
CREATE TABLE IF NOT EXISTS unmapped_materials (
    id           SERIAL PRIMARY KEY,
    raw_value    TEXT    NOT NULL,
    position     TEXT,
    product_id   INTEGER REFERENCES products(id) ON DELETE SET NULL,
    sheet_source TEXT,
    seen_count   INTEGER DEFAULT 1,
    first_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved     BOOLEAN DEFAULT FALSE,
    UNIQUE (raw_value, position)
);

CREATE INDEX IF NOT EXISTS idx_unmapped_materials_unresolved
  ON unmapped_materials(resolved) WHERE resolved = FALSE;

COMMIT;

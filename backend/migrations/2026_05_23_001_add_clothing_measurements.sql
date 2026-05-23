-- Migration: Add clothing measurement columns to products table
-- Date: 2026-05-23
-- Purpose: Support atomic storage of clothing measurements (length, chest, hips, waist)
--
-- New columns store measurements in centimeters with min/max for ranges:
-- measurements_length: single value (cm)
-- measurements_pog_min/max: chest circumference (периметр грудей)
-- measurements_pob_min/max: hip circumference (периметр бьодер)
-- measurements_pot_min/max: waist circumference (периметр талії)
--
-- Example usage:
--   measurements_length = 116
--   measurements_pog_min = 44, measurements_pog_max = 48  (range: 44-48)

ALTER TABLE products
ADD COLUMN IF NOT EXISTS measurements_length FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS measurements_pog_min FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS measurements_pog_max FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS measurements_pob_min FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS measurements_pob_max FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS measurements_pot_min FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS measurements_pot_max FLOAT DEFAULT NULL;

-- Optional: Index for range queries on clothing measurements
CREATE INDEX IF NOT EXISTS idx_measurements_pog ON products(measurements_pog_min, measurements_pog_max)
WHERE measurements_pog_min IS NOT NULL OR measurements_pog_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_pob ON products(measurements_pob_min, measurements_pob_max)
WHERE measurements_pob_min IS NOT NULL OR measurements_pob_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_pot ON products(measurements_pot_min, measurements_pot_max)
WHERE measurements_pot_min IS NOT NULL OR measurements_pot_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_length ON products(measurements_length)
WHERE measurements_length IS NOT NULL;

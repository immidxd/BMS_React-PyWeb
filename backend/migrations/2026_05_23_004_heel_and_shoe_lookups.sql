-- Migration: Heel measurement + 4 shoe-specific lookup tables
-- Date: 2026-05-23
-- Adds:
--   • measurements_heel_min/max (висота каблука/підбора)
--   • sole_types       — конструкція підошви (прошита, клеєна, goodyear welt, vibram, ...)
--   • toe_shapes       — форма носка (гострий, круглий, мигдалевидний, wingtip, ...)
--   • fastening_types  — тип застібки (шнурки, липучка, замок, монки, без застібки, ...)
--   • linings          — тип підкладки (натуральна шкіра, текстиль, хутро, ...)
--
-- Each lookup is single-FK on products (одне значення на товар), на відміну від materials
-- (де товар може мати кілька матеріалів на одній позиції).
--
-- Idempotent (IF NOT EXISTS everywhere).

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) Heel height (висота каблука/підбора)
--    Окремо від sole_thickness, бо це різні концепції:
--      sole_thickness — рівна частина (платформа) під стопою
--      heel           — задня припідняета частина над платформою
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS measurements_heel_min FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS measurements_heel_max FLOAT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_measurements_heel
  ON products(measurements_heel_min, measurements_heel_max)
  WHERE measurements_heel_min IS NOT NULL OR measurements_heel_max IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2) Lookup tables (узгоджено з patterns types/brands/colors)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sole_types (
    id            SERIAL PRIMARY KEY,
    soletypename  TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS toe_shapes (
    id            SERIAL PRIMARY KEY,
    toeshapename  TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fastening_types (
    id                 SERIAL PRIMARY KEY,
    fasteningtypename  TEXT NOT NULL UNIQUE,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS linings (
    id           SERIAL PRIMARY KEY,
    liningname   TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3) Foreign keys on products (nullable — більшість legacy товарів без значень)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS soletypeid       INTEGER REFERENCES sole_types(id)       ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS toeshapeid       INTEGER REFERENCES toe_shapes(id)       ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS fasteningtypeid  INTEGER REFERENCES fastening_types(id)  ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS liningid         INTEGER REFERENCES linings(id)          ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_products_soletypeid      ON products(soletypeid)      WHERE soletypeid      IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_toeshapeid      ON products(toeshapeid)      WHERE toeshapeid      IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_fasteningtypeid ON products(fasteningtypeid) WHERE fasteningtypeid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_liningid        ON products(liningid)        WHERE liningid        IS NOT NULL;

COMMIT;

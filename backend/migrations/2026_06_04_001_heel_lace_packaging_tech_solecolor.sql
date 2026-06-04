-- Migration: 5 new shoe-specific attributes
-- Date: 2026-06-04
-- Adds (mirrors existing single-FK shoe lookup pattern from 2026_05_23_004):
--   • heel_types       — тип каблука (шпилька, танкетка, платформа, ...)   ← окремо від measurements_heel (висота)
--   • lace_types       — тип шнурівки (плоскі, круглі, вощені, ...)        ← окремо від fastening_types «шнурки»
--   • packaging_types  — пакування (коробка, пакет, без пакування, ...)
--   • technologies     — технології (gore-tex, vibram, boost, ...)         ← v1: single value; multi-value cells → unmapped queue
--   • sole_colorid     — колір підошви: REUSE існуючої таблиці colors (без нової таблиці)
--
-- Кожен — single-FK на products (одне значення на товар), як sole_types/toe_shapes/...
-- Idempotent (IF NOT EXISTS / ON DELETE SET NULL).

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) Lookup tables (узгоджено з patterns sole_types/toe_shapes/fastening_types/linings)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS heel_types (
    id            SERIAL PRIMARY KEY,
    heeltypename  TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lace_types (
    id            SERIAL PRIMARY KEY,
    lacetypename  TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS packaging_types (
    id            SERIAL PRIMARY KEY,
    packagingname TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS technologies (
    id              SERIAL PRIMARY KEY,
    technologyname  TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2) Foreign keys on products (nullable — більшість legacy товарів без значень)
--    sole_colorid → colors (reuse спільного словника кольорів)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS heeltypeid    INTEGER REFERENCES heel_types(id)       ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS lacetypeid    INTEGER REFERENCES lace_types(id)       ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS packagingid   INTEGER REFERENCES packaging_types(id)  ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS technologyid  INTEGER REFERENCES technologies(id)     ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS sole_colorid  INTEGER REFERENCES colors(id)           ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_products_heeltypeid   ON products(heeltypeid)   WHERE heeltypeid   IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_lacetypeid   ON products(lacetypeid)   WHERE lacetypeid   IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_packagingid  ON products(packagingid)  WHERE packagingid  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_technologyid ON products(technologyid) WHERE technologyid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_sole_colorid ON products(sole_colorid) WHERE sole_colorid IS NOT NULL;

COMMIT;

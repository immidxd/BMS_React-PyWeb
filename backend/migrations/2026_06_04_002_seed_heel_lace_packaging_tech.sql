-- Seed starter values for the new shoe lookups.
-- Lowercase canonical names; UI may capitalize for display.
-- Idempotent (ON CONFLICT DO NOTHING). Extend via UI / re-parse as new values appear.
-- (sole_colorid reuses the existing `colors` table — no seed here.)

BEGIN;

-- ── Heel types (тип каблука) ────────────────────────────────────────────────
INSERT INTO heel_types (heeltypename) VALUES
  ('шпилька'),
  ('танкетка'),
  ('платформа'),
  ('широкий'),            -- стовпчик/блоковий
  ('столбик'),
  ('конусний'),
  ('рюмочка'),
  ('кітен-хіл'),          -- kitten heel (низький тонкий)
  ('скошений'),
  ('плоский'),
  ('без каблука')
ON CONFLICT (heeltypename) DO NOTHING;

-- ── Lace types (тип шнурівки) ───────────────────────────────────────────────
INSERT INTO lace_types (lacetypename) VALUES
  ('плоскі'),
  ('круглі'),
  ('вощені'),
  ('еластичні'),
  ('шкіряні'),
  ('текстильні'),
  ('паракорд'),
  ('стрічка'),
  ('без шнурівки')
ON CONFLICT (lacetypename) DO NOTHING;

-- ── Packaging (пакування) ───────────────────────────────────────────────────
INSERT INTO packaging_types (packagingname) VALUES
  ('фірмова коробка'),
  ('коробка'),
  ('фірмовий пакет'),
  ('пакет'),
  ('дастбег'),           -- тканинний мішечок (dust bag)
  ('без пакування')
ON CONFLICT (packagingname) DO NOTHING;

-- ── Technologies (технології) ───────────────────────────────────────────────
INSERT INTO technologies (technologyname) VALUES
  ('gore-tex'),
  ('vibram'),
  ('boost'),
  ('air'),
  ('zoom air'),
  ('react'),
  ('gel'),
  ('ortholite'),
  ('primaloft'),
  ('thinsulate'),
  ('contagrip'),
  ('dri-fit'),
  ('boa'),
  ('flyknit')
ON CONFLICT (technologyname) DO NOTHING;

COMMIT;

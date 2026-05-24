-- Seed minimal canonical materials taxonomy.
-- Idempotent (ON CONFLICT DO NOTHING). Safe to re-run.
-- All names lowercase. UI may capitalize for display.
--
-- This is a STARTING SET — extend via UI or direct INSERTs as new materials appear.
-- Parser does NOT auto-create; unknowns go into `unmapped_materials` for manual review.

BEGIN;

-- ── Parents (категорії-парасольки) ──────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category) VALUES
  ('шкіра',     NULL, 'leather'),
  ('текстиль',  NULL, 'textile'),
  ('синтетика', NULL, 'synthetic'),
  ('гума',      NULL, 'rubber'),
  ('gore-tex',  NULL, 'membrane'),
  ('sympatex',  NULL, 'membrane')
ON CONFLICT (materialname) DO NOTHING;

-- ── Шкіра — підвиди ─────────────────────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category)
SELECT name, (SELECT id FROM materials WHERE materialname='шкіра'), 'leather'
  FROM (VALUES
    ('гладка шкіра'),
    ('зерниста шкіра'),
    ('замша'),
    ('нубук'),
    ('лак'),
    ('наппа'),
    ('крек')
  ) AS t(name)
ON CONFLICT (materialname) DO NOTHING;

-- ── Текстиль — підвиди ──────────────────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category)
SELECT name, (SELECT id FROM materials WHERE materialname='текстиль'), 'textile'
  FROM (VALUES
    ('бавовна'),
    ('поліестер'),
    ('льон'),
    ('вовна'),
    ('фліс'),
    ('кашемір'),
    ('денім'),
    ('канвас')
  ) AS t(name)
ON CONFLICT (materialname) DO NOTHING;

-- ── Синтетика / штучні матеріали ────────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category)
SELECT name, (SELECT id FROM materials WHERE materialname='синтетика'), 'synthetic'
  FROM (VALUES
    ('еко-шкіра'),
    ('пу-шкіра'),
    ('пвх'),
    ('нейлон'),
    ('акрил')
  ) AS t(name)
ON CONFLICT (materialname) DO NOTHING;

-- ── Гумо/підошовні матеріали ─────────────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category)
SELECT name, (SELECT id FROM materials WHERE materialname='гума'), 'rubber'
  FROM (VALUES
    ('тпу'),               -- термопластична поліуретан
    ('тр'),                -- термопластична гума
    ('ева'),               -- EVA
    ('поліуретан'),
    ('каучук')
  ) AS t(name)
ON CONFLICT (materialname) DO NOTHING;

-- ── Gore-Tex — підвиди ──────────────────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category)
SELECT name, (SELECT id FROM materials WHERE materialname='gore-tex'), 'membrane'
  FROM (VALUES
    ('gore-tex pro'),
    ('gore-tex active'),
    ('gore-tex infinium')
  ) AS t(name)
ON CONFLICT (materialname) DO NOTHING;

-- ── Інші мембрани ───────────────────────────────────────────────────────────
INSERT INTO materials (materialname, parent_id, category) VALUES
  ('event',   NULL, 'membrane'),
  ('outdry',  NULL, 'membrane'),
  ('hydrosil',NULL, 'membrane')
ON CONFLICT (materialname) DO NOTHING;

COMMIT;

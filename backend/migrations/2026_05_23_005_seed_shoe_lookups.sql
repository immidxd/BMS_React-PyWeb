-- Seed starter values for shoe lookups.
-- Lowercase canonical names; UI may capitalize for display.
-- Idempotent (ON CONFLICT DO NOTHING). Extend via UI as new values appear.

BEGIN;

-- ── Sole types (конструкція підошви) ────────────────────────────────────────
INSERT INTO sole_types (soletypename) VALUES
  ('прошита'),           -- general stitched
  ('клеєна'),            -- glued
  ('литва'),             -- molded
  ('goodyear welt'),     -- класичний рантовий шов
  ('blake stitch'),      -- однострочний прошив через устілку
  ('vibram'),            -- бренд-конструкція
  ('lug sole'),          -- глибокий протектор
  ('track sole'),        -- біговий тип
  ('дабл-сол'),          -- подвійна підошва
  ('платформа'),
  ('танкетка'),
  ('шпилька'),
  ('гладка')             -- мінімальний/без протектора
ON CONFLICT (soletypename) DO NOTHING;

-- ── Toe shapes (форма носка) ────────────────────────────────────────────────
INSERT INTO toe_shapes (toeshapename) VALUES
  ('круглий'),
  ('гострий'),
  ('квадратний'),
  ('мигдалевидний'),     -- almond
  ('chisel'),            -- зрізаний прямокутник
  ('cap-toe'),           -- зі вставкою-носком
  ('wingtip'),           -- крильцеподібний
  ('plain-toe'),         -- без декору
  ('apron-toe'),         -- з фартухом
  ('peep-toe'),          -- відкритий
  ('open-toe'),          -- повністю відкритий
  ('moc-toe')            -- мокасинний шов
ON CONFLICT (toeshapename) DO NOTHING;

-- ── Fastening types (тип застібки) ──────────────────────────────────────────
INSERT INTO fastening_types (fasteningtypename) VALUES
  ('шнурки'),
  ('липучка'),
  ('замок'),             -- блискавка
  ('бічний замок'),
  ('ремінець'),
  ('монки'),             -- monk strap (одна/дві пряжки)
  ('пряжка'),
  ('еластик'),           -- безшнуркові на гумці
  ('кнопки'),
  ('банти'),
  ('без застібки')       -- slip-on, мокасини
ON CONFLICT (fasteningtypename) DO NOTHING;

-- ── Linings (підкладка) ─────────────────────────────────────────────────────
INSERT INTO linings (liningname) VALUES
  ('натуральна шкіра'),
  ('штучна шкіра'),
  ('текстиль'),
  ('хутро'),             -- натуральне/штучне разом — деталь у описі
  ('овчина'),
  ('фліс'),
  ('мембрана'),          -- gore-tex linings та аналоги
  ('меш'),               -- сітка (кросівки)
  ('без підкладки')
ON CONFLICT (liningname) DO NOTHING;

COMMIT;

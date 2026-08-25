-- Скільки Stories публікувати за один слот.
--
-- 1 за замовчуванням: пакет — свідомий вибір людини. Стеля 10 узята з бойового
-- випадку 2026-08-18, коли 33 сторіс у Facebook дали 66 завдань (Сторінок ДВІ)
-- і вперлися в ліміт застосунку Meta «(#4) Application request limit reached»
-- на ~22 завданнях за ~3 хвилини.
ALTER TABLE IF EXISTS story_automation_configs
    ADD COLUMN IF NOT EXISTS items_per_run SMALLINT NOT NULL DEFAULT 1;

DO $$
BEGIN
    ALTER TABLE story_automation_configs
        ADD CONSTRAINT story_items_per_run_range CHECK (items_per_run BETWEEN 1 AND 10);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

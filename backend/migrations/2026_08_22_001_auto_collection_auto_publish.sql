-- «Без затвердження» для щотижневих Top-9 підбірок.
--
-- FALSE за замовчуванням і для наявних рядків: увімкнення автопублікації —
-- свідома дія людини, а не наслідок оновлення програми.
ALTER TABLE IF EXISTS auto_collection_configs
    ADD COLUMN IF NOT EXISTS auto_publish BOOLEAN NOT NULL DEFAULT FALSE;

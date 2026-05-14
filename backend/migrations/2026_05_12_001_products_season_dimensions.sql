-- Додаємо нові поля до products:
--   season      — сезон (multi-value, comma-separated, напр. "Зима, Осінь")
--   dimensions  — габарити (рядок типу "40x20x5")
-- Поля nullable, тож існуючі товари не потребують backfill.

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS season VARCHAR(100),
    ADD COLUMN IF NOT EXISTS dimensions VARCHAR(50);

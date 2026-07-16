-- Price Engine використовує чинні категорійні тарифи Shafa у коді. Поля нижче
-- зберігають лише стратегію єдиної безпечної ціни та one-click режим.
ALTER TABLE shafa_config
    ADD COLUMN IF NOT EXISTS price_strategy VARCHAR(32) NOT NULL DEFAULT 'unified_safe',
    ADD COLUMN IF NOT EXISTS auto_publish BOOLEAN NOT NULL DEFAULT TRUE;

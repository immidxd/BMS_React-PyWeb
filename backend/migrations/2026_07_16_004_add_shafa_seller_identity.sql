-- Публічна верифікація Shafa не має токенів: BMS читає лише публічний GraphQL
-- (product(id) -> name/price/statusTitle/owner). Щоб звіряти, що знайдене
-- оголошення належить САМЕ цьому продавцю, зберігаємо його Shafa-username.
-- Значення НЕ хардкодимо: reader сам вивчає його з owner.username першого
-- підтвердженого лістингу (див. shafa_reader.learn_seller_from_listing).
ALTER TABLE shafa_config
    ADD COLUMN IF NOT EXISTS seller_username VARCHAR(120),
    ADD COLUMN IF NOT EXISTS store_synced_at TIMESTAMPTZ;

-- Останній публічний знімок оголошення (щоб не смикати Shafa щоцикл і мати
-- «чесний» стан наявності саме з боку Shafa, а не лише з дзеркала Prom).
ALTER TABLE shafa_publications
    ADD COLUMN IF NOT EXISTS shafa_presence VARCHAR(24),
    ADD COLUMN IF NOT EXISTS shafa_checked_at TIMESTAMPTZ;

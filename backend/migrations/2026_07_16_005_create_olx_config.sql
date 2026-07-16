-- Налаштування публікації та ціноутворення OLX (single-row, id=1).
-- OLX бере не % з продажу, а плату за ПУБЛІКАЦІЮ (пакет) + опц. комісію OLX
-- Доставки. Тут зберігаємо бізнес-параметри Price Engine та дефолти оголошення.
CREATE TABLE IF NOT EXISTS olx_config (
    id                  SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    ad_spend            NUMERIC(10,2) NOT NULL DEFAULT 0,        -- сер. витрати на рекламу/шт
    advertiser_type     VARCHAR(12)  NOT NULL DEFAULT 'business' -- business | private
        CHECK (advertiser_type IN ('business', 'private')),
    use_delivery        BOOLEAN NOT NULL DEFAULT TRUE,           -- враховувати комісію OLX Доставки
    branch_payment      BOOLEAN NOT NULL DEFAULT FALSE,          -- враховувати «оплату у відділенні»
    default_city_id     INTEGER,                                 -- локація оголошення за замовч.
    default_district_id INTEGER,
    default_lat         VARCHAR(24),
    default_lon         VARCHAR(24),
    contact_name        VARCHAR(120),
    contact_phone       VARCHAR(40),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO olx_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Локальний стан «намір/створення на OLX» до появи в sync-дзеркалі olx_adverts.
-- created — коли BMS відправила POST і отримала olx_id; needs_package — коли
-- OLX прийняв, але оголошення неактивне через відсутній пакет публікацій.
ALTER TABLE olx_adverts
    ADD COLUMN IF NOT EXISTS created_by_bms BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS needs_package  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_error     TEXT;

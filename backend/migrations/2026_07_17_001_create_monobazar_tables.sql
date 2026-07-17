-- monoБазар: READ-ONLY верифікація/моніторинг через ПУБЛІЧНИЙ REST-шлюз
-- (resale-public-api-gateway.monobazar.com.ua), знайдений реверс-інжинірингом
-- публічної вітрини продавця (JS-бандли Next.js, без авторизації). Створення
-- оголошень ЗАБЛОКОВАНЕ: цей шлюз не має write-ендпоінтів — «Нове оголошення»
-- живе лише в мобільному застосунку (приватне API, не досліджувалось).

CREATE TABLE IF NOT EXISTS monobazar_config (
    id                SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    seller_username   VARCHAR(120),           -- напр. 'ivanm1210' (з публічної вітрини)
    store_synced_at   TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO monobazar_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Дзеркало активних оголошень продавця monoБазар (аналог olx_adverts/shafa_publications).
CREATE TABLE IF NOT EXISTS monobazar_listings (
    id                serial PRIMARY KEY,
    monobazar_id      VARCHAR(64) NOT NULL,          -- UUID оголошення на monoБазар
    product_id        INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_number_raw VARCHAR(50),
    title             VARCHAR(300),
    price             NUMERIC(12,2),
    photo_url         VARCHAR(500),
    status             VARCHAR(24),                   -- active | ...
    view_count        INTEGER DEFAULT 0,
    match_score       INTEGER,                        -- впевненість автолінку (діагностика)
    match_confidence  VARCHAR(12),                     -- confident | ambiguous | none
    last_synced_at    TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT monobazar_listings_id_key UNIQUE (monobazar_id)
);
CREATE INDEX IF NOT EXISTS idx_monobazar_listings_product ON monobazar_listings(product_id);
CREATE INDEX IF NOT EXISTS idx_monobazar_listings_number  ON monobazar_listings(product_number_raw);

COMMENT ON TABLE monobazar_listings IS
    'Активні оголошення продавця monoБазар (публічний READ API). Лінк до products за автоматичним текстовим збігом (бренд/тип/колір/розмір/ціна) — без офіційного артикула в назві, тому match_confidence діагностує впевненість.';

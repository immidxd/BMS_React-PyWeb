-- Локальний оркестратор офіційного мосту Prom.ua -> Shafa.ua.
-- Shafa не надає публічного seller API, тому ці таблиці НЕ імітують віддалене
-- оголошення: вони зберігають намір, стан мосту та ручне підтвердження продавця.

CREATE TABLE IF NOT EXISTS shafa_config (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    bridge_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    bridge_confirmed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO shafa_config (id, bridge_enabled)
VALUES (1, FALSE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS shafa_publications (
    id BIGSERIAL PRIMARY KEY,
    productnumber VARCHAR(80) NOT NULL UNIQUE,
    anchor_product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    source VARCHAR(24) NOT NULL DEFAULT 'prom_bridge'
        CHECK (source IN ('prom_bridge', 'manual')),
    status VARCHAR(24) NOT NULL DEFAULT 'waiting_prom'
        CHECK (status IN ('waiting_prom', 'bridge_ready', 'confirmed',
                          'manual_existing', 'blocked', 'removed')),
    shafa_listing_id VARCHAR(80),
    shafa_url TEXT,
    last_error TEXT,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shafa_publications_status
    ON shafa_publications(status);
CREATE INDEX IF NOT EXISTS idx_shafa_publications_anchor
    ON shafa_publications(anchor_product_id);


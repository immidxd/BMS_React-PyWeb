-- Viber Channels Post API: локальний журнал чернеток/черги та результатів.
-- Секрети Viber тут НЕ зберігаються: вони живуть лише у Cloudflare Worker.

CREATE TABLE IF NOT EXISTS viber_publications (
    id BIGSERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_number VARCHAR(120) NOT NULL,
    channel_title VARCHAR(255),
    dispatcher_job_id VARCHAR(120),
    message_token VARCHAR(120),
    idempotency_key VARCHAR(160) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    caption TEXT NOT NULL,
    collage_key TEXT,
    collage_url TEXT,
    thumbnail_key TEXT,
    thumbnail_url TEXT,
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_viber_publications_product
    ON viber_publications(product_id, status);
CREATE INDEX IF NOT EXISTS idx_viber_publications_number
    ON viber_publications(product_number, status);
CREATE INDEX IF NOT EXISTS idx_viber_publications_job
    ON viber_publications(dispatcher_job_id);
CREATE INDEX IF NOT EXISTS idx_viber_publications_scheduled
    ON viber_publications(scheduled_at)
    WHERE status IN ('queued', 'scheduled', 'publishing');

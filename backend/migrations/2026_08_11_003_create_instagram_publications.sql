-- Instagram Platform: локальний журнал майбутніх чернеток/черги/результатів.
-- Перший етап BMS працює лише як preview/dry-run і не пише в цю таблицю.
-- Meta App Secret та access token тут не зберігаються: для живого етапу вони
-- мають жити виключно у Cloudflare Secrets.

CREATE TABLE IF NOT EXISTS instagram_publications (
    id BIGSERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_number VARCHAR(120) NOT NULL,
    instagram_account_id VARCHAR(120),
    instagram_media_id VARCHAR(120),
    container_id VARCHAR(120),
    dispatcher_job_id VARCHAR(120),
    idempotency_key VARCHAR(180) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    media_type VARCHAR(24) NOT NULL DEFAULT 'carousel',
    caption TEXT NOT NULL,
    media_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_instagram_publications_product
    ON instagram_publications(product_id, status);
CREATE INDEX IF NOT EXISTS idx_instagram_publications_number
    ON instagram_publications(product_number, status);
CREATE INDEX IF NOT EXISTS idx_instagram_publications_job
    ON instagram_publications(dispatcher_job_id);
CREATE INDEX IF NOT EXISTS idx_instagram_publications_scheduled
    ON instagram_publications(scheduled_at)
    WHERE status IN ('queued', 'scheduled', 'processing', 'retrying');

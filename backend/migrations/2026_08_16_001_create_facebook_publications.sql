-- Facebook Page: локальний журнал чернеток/черги/результатів.
-- Дзеркало instagram_publications, але зберігає page_id і post_id Сторінки.
-- App Secret і Page access token тут не зберігаються: вони живуть виключно
-- у Cloudflare Secrets воркера bms-facebook-dispatcher.

CREATE TABLE IF NOT EXISTS facebook_publications (
    id BIGSERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_number VARCHAR(120) NOT NULL,
    facebook_page_id VARCHAR(120),
    facebook_post_id VARCHAR(160),
    dispatcher_job_id VARCHAR(120),
    idempotency_key VARCHAR(180) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    media_type VARCHAR(24) NOT NULL DEFAULT 'feed',
    caption TEXT NOT NULL,
    media_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    cleanup_confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_facebook_publications_product
    ON facebook_publications(product_id, status);
CREATE INDEX IF NOT EXISTS idx_facebook_publications_number
    ON facebook_publications(product_number, status);
CREATE INDEX IF NOT EXISTS idx_facebook_publications_job
    ON facebook_publications(dispatcher_job_id);
CREATE INDEX IF NOT EXISTS idx_facebook_publications_scheduled
    ON facebook_publications(scheduled_at)
    WHERE status IN ('queued', 'scheduled', 'processing', 'retrying');
CREATE INDEX IF NOT EXISTS idx_facebook_publications_manual_cleanup
    ON facebook_publications(product_number, cleanup_confirmed_at)
    WHERE status = 'published' AND media_type = 'feed';

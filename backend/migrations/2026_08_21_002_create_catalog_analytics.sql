-- Clean event-level analytics collected by the public catalog and mirrored from Neon.
-- These tables are additive and contain no raw Telegram IDs, IP addresses or user agents.

CREATE TABLE IF NOT EXISTS catalog_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(32) NOT NULL,
    productnumber VARCHAR(80),
    visitor_key CHAR(64) NOT NULL,
    session_id UUID NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    CHECK (event_type IN (
        'catalog_open', 'product_view', 'favorite_add',
        'favorite_remove', 'contact_click'
    ))
);

CREATE INDEX IF NOT EXISTS ix_catalog_events_received
    ON catalog_events(received_at DESC);
CREATE INDEX IF NOT EXISTS ix_catalog_events_product_time
    ON catalog_events(productnumber, received_at DESC)
    WHERE productnumber IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_catalog_events_visitor_time
    ON catalog_events(visitor_key, received_at DESC);

CREATE TABLE IF NOT EXISTS catalog_analytics_product_snapshot (
    productnumber VARCHAR(80) PRIMARY KEY,
    active_favorites INTEGER NOT NULL DEFAULT 0 CHECK (active_favorites >= 0),
    -- Preserved old GET-counter for audit only. It is not used by clean rankings.
    legacy_views INTEGER NOT NULL DEFAULT 0 CHECK (legacy_views >= 0),
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog_analytics_sync_state (
    source VARCHAR(40) PRIMARY KEY,
    last_synced_at TIMESTAMPTZ,
    last_received_at TIMESTAMPTZ,
    rows_synced BIGINT NOT NULL DEFAULT 0,
    last_error TEXT
);

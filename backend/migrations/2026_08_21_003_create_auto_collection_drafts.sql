-- Безпечна автоматизація щотижневих Top-9 підбірок.
--
-- Ці таблиці свідомо НЕ є чергою публікацій. Автоматичний цикл зберігає лише
-- знімок кандидатів на ручну перевірку: без JPEG/R2, без dispatcher job і без
-- виклику Viber/Meta. Увімкнення автопублікації тут структурно неможливе.

CREATE TABLE IF NOT EXISTS auto_collection_configs (
    platform VARCHAR(24) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    weekday SMALLINT NOT NULL DEFAULT 6 CHECK (weekday BETWEEN 0 AND 6),
    local_time TIME NOT NULL DEFAULT TIME '10:00',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Kyiv',
    period_days SMALLINT NOT NULL DEFAULT 30 CHECK (period_days IN (0, 7, 30, 90)),
    cooldown_days SMALLINT NOT NULL DEFAULT 14 CHECK (cooldown_days BETWEEN 14 AND 90),
    item_count SMALLINT NOT NULL DEFAULT 9 CHECK (item_count BETWEEN 2 AND 9),
    enabled_at TIMESTAMPTZ,
    last_generated_at TIMESTAMPTZ,
    last_error TEXT,
    last_error_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (platform IN ('viber', 'facebook'))
);

INSERT INTO auto_collection_configs(platform)
VALUES ('viber'), ('facebook')
ON CONFLICT (platform) DO NOTHING;

CREATE TABLE IF NOT EXISTS auto_collection_drafts (
    id BIGSERIAL PRIMARY KEY,
    platform VARCHAR(24) NOT NULL REFERENCES auto_collection_configs(platform),
    source VARCHAR(24) NOT NULL DEFAULT 'scheduled',
    status VARCHAR(32) NOT NULL DEFAULT 'awaiting_review',
    scheduled_for TIMESTAMPTZ NOT NULL,
    selection_key VARCHAR(64) NOT NULL,
    product_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    product_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
    selected_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    reserves_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    audit_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, scheduled_for),
    CHECK (source IN ('scheduled', 'manual')),
    CHECK (status IN ('awaiting_review', 'approved', 'rejected', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_auto_collection_drafts_review
    ON auto_collection_drafts(status, scheduled_for DESC);
CREATE INDEX IF NOT EXISTS idx_auto_collection_drafts_platform
    ON auto_collection_drafts(platform, scheduled_for DESC);


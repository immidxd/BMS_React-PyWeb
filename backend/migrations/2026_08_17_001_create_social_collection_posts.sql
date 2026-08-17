-- Підбірки (колажі-сітки з кількох товарів) для Viber і Facebook.
--
-- Свідомо ОКРЕМА таблиця, а не рядки у viber_publications / facebook_publications:
-- підбірка — рекламний банер каналу, а не публікація конкретного товару. Якби
-- вона писалася в товарні таблиці, кожен товар із сітки отримав би чіп
-- «опубліковано», статистика й фільтри публікацій почали б рахувати покази
-- колажу як окремі пости, а повторний одиничний пост цього товару впирався б
-- у гард «товар уже опублікований». Тут же облік підбірок повністю власний і
-- на статус опублікованості товарів не впливає.

CREATE TABLE IF NOT EXISTS social_collection_posts (
    id BIGSERIAL PRIMARY KEY,
    platform VARCHAR(24) NOT NULL,
    account_id VARCHAR(120),
    account_label VARCHAR(255),
    dispatcher_job_id VARCHAR(120),
    external_post_id VARCHAR(160),
    message_token VARCHAR(120),
    idempotency_key VARCHAR(180) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    caption TEXT NOT NULL DEFAULT '',
    layout VARCHAR(16) NOT NULL DEFAULT 'grid9',
    item_count SMALLINT NOT NULL DEFAULT 0,
    image_key TEXT,
    image_url TEXT,
    thumbnail_key TEXT,
    thumbnail_url TEXT,
    -- Довідково: які саме товари потрапили в сітку. Це НЕ ознака публікації
    -- товару, а слід для людини — щоб через місяць знати, що показували.
    product_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    product_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_social_collection_posts_platform
    ON social_collection_posts(platform, status);
CREATE INDEX IF NOT EXISTS idx_social_collection_posts_job
    ON social_collection_posts(dispatcher_job_id);
CREATE INDEX IF NOT EXISTS idx_social_collection_posts_pending
    ON social_collection_posts(scheduled_at)
    WHERE status IN ('queued', 'scheduled', 'processing', 'retrying');

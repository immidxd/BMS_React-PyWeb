-- Відправлення постів майстерні в мережі.
--
-- Окремий облік, а не рядки в товарних таблицях публікацій — з тієї ж
-- причини, що й уся майстерня: анонс не є публікацією товару. Тут же
-- зберігається все, чого нема в `studio_posts`: у яку мережу, на який акаунт,
-- під яким ключем ідемпотентності й з яким job у хмарному диспетчері.
--
-- Один рядок = одна відправка в один пункт призначення. Facebook із двома
-- Сторінками дає ДВА рядки на той самий пост, і це навмисно: у Meta це два
-- різні дописи з різними лімітами й різними статусами.

CREATE TABLE IF NOT EXISTS studio_publications (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES studio_posts(id) ON DELETE CASCADE,
    platform VARCHAR(24) NOT NULL,
    -- Формат саме цієї відправки: одна майстерня може піти в Stories
    -- Instagram і квадратом у Viber.
    canvas_format VARCHAR(24) NOT NULL,
    account_id VARCHAR(120),
    account_label VARCHAR(255),
    -- Ключ містить відбиток самого кадру, тому повторне натискання
    -- «Опублікувати» на НЕзміненому пості впирається в кеш диспетчера, а
    -- виправлений макет чесно вважається новою публікацією.
    idempotency_key VARCHAR(180) NOT NULL UNIQUE,
    dispatcher_job_id VARCHAR(120),
    external_post_id VARCHAR(160),
    post_url TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    caption TEXT NOT NULL DEFAULT '',
    image_key TEXT,
    image_url TEXT,
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('queued', 'scheduled', 'processing', 'retrying',
                      'published', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_studio_publications_post
    ON studio_publications(post_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_studio_publications_pending
    ON studio_publications(platform, status)
    WHERE status IN ('queued', 'scheduled', 'processing', 'retrying');

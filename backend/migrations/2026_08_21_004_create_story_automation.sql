-- Регулярні Stories для Instagram і Facebook.
--
-- Побудовано за тією ж схемою, що й щотижневі Top-9: розклад створює ЧЕРНЕТКУ,
-- а не публікацію. Різниця з підбірками одна — тут добір задається фільтрами
-- товару («жіночі босоніжки», «усе HOKA»), а не рейтингом популярності.
--
-- Автопублікація вимкнена за замовчуванням (`auto_publish = FALSE`): спершу
-- людина дивиться чергу, і лише свідомим перемиканням віддає її автоматиці.

CREATE TABLE IF NOT EXISTS story_automation_configs (
    platform VARCHAR(24) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    -- Періодичність задається інтервалом, а не днем тижня: добова стрічка
    -- Stories живе іншим ритмом, ніж тижнева підбірка. 24 год = раз на добу;
    -- 8 год = тричі на добу. Стеля 168 год — це рівно тиждень.
    interval_hours SMALLINT NOT NULL DEFAULT 24 CHECK (interval_hours BETWEEN 4 AND 168),
    -- Якір за стінним годинником: перший слот — це `local_time` у день
    -- увімкнення або наступного дня, далі кожні `interval_hours`.
    local_time TIME NOT NULL DEFAULT TIME '11:00',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Kyiv',
    -- Скільки днів товар не повертається у Stories. Довший за підбірковий: пул
    -- фільтра зазвичай ширший, і повтор через тиждень виглядав би бідно.
    cooldown_days SMALLINT NOT NULL DEFAULT 30 CHECK (cooldown_days BETWEEN 7 AND 180),
    -- Критерії добору мовою фільтрів «Товарів»: brandids, typeids, genderids,
    -- seasons, min_price/max_price, min_sizeeu/max_sizeeu тощо.
    filters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- FALSE — чернетка чекає людину. TRUE — черга йде в диспетчер сама.
    auto_publish BOOLEAN NOT NULL DEFAULT FALSE,
    enabled_at TIMESTAMPTZ,
    last_generated_at TIMESTAMPTZ,
    last_error TEXT,
    last_error_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (platform IN ('instagram', 'facebook'))
);

INSERT INTO story_automation_configs(platform)
VALUES ('instagram'), ('facebook')
ON CONFLICT (platform) DO NOTHING;

CREATE TABLE IF NOT EXISTS story_automation_drafts (
    id BIGSERIAL PRIMARY KEY,
    platform VARCHAR(24) NOT NULL REFERENCES story_automation_configs(platform),
    source VARCHAR(24) NOT NULL DEFAULT 'scheduled',
    status VARCHAR(32) NOT NULL DEFAULT 'awaiting_review',
    scheduled_for TIMESTAMPTZ NOT NULL,
    product_id INTEGER NOT NULL,
    productnumber TEXT NOT NULL,
    -- Текст на кадрі. Зберігається, щоб людина бачила при перевірці саме те,
    -- що поїде, і могла виправити до відправлення.
    story_text TEXT,
    -- Запасні товари того ж добору: якщо основний продадуть до відправлення,
    -- черга не залишиться порожньою.
    reserves_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    audit_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Один слот — одна Story на майданчик. Той самий захист від дублю, що й у
    -- підбірках: два двигуни (BMS і хмара) не можуть створити дві чернетки.
    UNIQUE (platform, scheduled_for),
    CHECK (source IN ('scheduled', 'manual')),
    CHECK (status IN ('awaiting_review', 'approved', 'rejected', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_story_automation_drafts_review
    ON story_automation_drafts(status, scheduled_for DESC);
CREATE INDEX IF NOT EXISTS idx_story_automation_drafts_product
    ON story_automation_drafts(productnumber, scheduled_for DESC);

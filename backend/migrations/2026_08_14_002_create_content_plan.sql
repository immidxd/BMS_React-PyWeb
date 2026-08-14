-- Контент-план: слоти публікацій, імпортовані з Obsidian TaskNotes.
--
-- Розподіл володіння полями:
--   Obsidian володіє планом      → source_id, title, channel, post_format, scheduled_at, plan_status
--   BMS володіє виконанням       → slot_state, product_numbers, suggested_numbers, publication_ref, post_url
--
-- Слот імпортується у власну таблицю, а не читається наживо: HTTP API TaskNotes
-- живе лише поки відкритий Obsidian, а план має бути доступний і без нього.

CREATE TABLE IF NOT EXISTS content_plan_slots (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'tasknotes',
    -- Шлях нотатки у vault — саме ним TaskNotes API адресує задачу (PUT /api/tasks/:id).
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    post_format TEXT,
    rubric TEXT,
    product_count INTEGER NOT NULL DEFAULT 1,
    scheduled_at TIMESTAMPTZ,
    -- Статус із TaskNotes: planned / done / none
    plan_status TEXT NOT NULL DEFAULT 'planned',
    -- Стан виконання в BMS: new / suggested / confirmed / published / skipped
    slot_state TEXT NOT NULL DEFAULT 'new',
    -- Номери — для читання людиною й для запису назад у нотатку Obsidian.
    -- Публікація ж адресується САМЕ id: один productnumber може належати
    -- різним товарам, тому номер не є ключем публікації.
    product_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
    product_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggested_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggested_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    publication_ref TEXT,
    post_url TEXT,
    published_at TIMESTAMPTZ,
    source_modified_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT content_plan_slots_source_unique UNIQUE (source, source_id),
    CONSTRAINT content_plan_slots_channel_check
        CHECK (channel IN ('telegram', 'instagram', 'viber')),
    CONSTRAINT content_plan_slots_state_check
        CHECK (slot_state IN ('new', 'suggested', 'confirmed', 'published', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_content_plan_slots_scheduled
    ON content_plan_slots(scheduled_at);

CREATE INDEX IF NOT EXISTS idx_content_plan_slots_channel_state
    ON content_plan_slots(channel, slot_state);

-- Активні слоти, які ще чекають на публікацію — основна вибірка вкладки.
CREATE INDEX IF NOT EXISTS idx_content_plan_slots_pending
    ON content_plan_slots(scheduled_at)
    WHERE slot_state IN ('new', 'suggested', 'confirmed');

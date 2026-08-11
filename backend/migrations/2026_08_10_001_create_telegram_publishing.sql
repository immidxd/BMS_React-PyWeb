-- Створення постів у Telegram (раніше вкладка «Публікації» вміла лише читати
-- і знімати з публікації). Три таблиці під один флоу:
--
--   1. telegram_thread_mapping (ВЖЕ ІСНУЄ, була порожня) — правила автопідбору
--      гілок форуму. Розширюємо: сезон/бренд/ознака «дитяче» + прапорець, що
--      гілку взагалі можна пропонувати. Без цього мапа вміла лише тип+стать,
--      а живі гілки — це ще й «ЛІТО | ЖІНОЧІ» (сезон) і «HOKA» (бренд).
--
--   2. telegram_post_templates — памʼять маркетингового тексту. Емодзі,
--      «• короткий опис» і рядки «▪️ переваги» BMS не може вигадати з полів
--      товару чесно (description/extranote — ВНУТРІШНІ нотатки, їх ніколи не
--      публікуємо). Тому те, що людина написала руками для «бренд+модель»,
--      зберігаємо і підставляємо наступному товару тієї ж моделі.
--
--   3. telegram_scheduled_posts — форварди в канал BrandStore, заплановані
--      силами Telegram на 08:00. У telegram_posts їх класти НЕ можна:
--      у запланованих повідомлень окремий простір message_id, який після
--      реальної відправки міняється, і UNIQUE (chat_id, message_id) зламався б.
--      Тому чекаємо, поки штатний скан підбере вже опублікований пост.

-- ── 1. Розширення мапи гілок ────────────────────────────────────────────────
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS season      VARCHAR(50);
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS brand_id    INTEGER REFERENCES brands(id) ON DELETE SET NULL;
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS kids_only   BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS auto_suggest BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS type_names  TEXT;   -- CSV назв типів: гілка «КРОСІВКИ» ловить Кросівки/Кеди/Сліпони
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS sort_order  INTEGER NOT NULL DEFAULT 100;
ALTER TABLE telegram_thread_mapping ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT now();

-- ── 2. Памʼять тексту поста за брендом+моделлю ──────────────────────────────
CREATE TABLE IF NOT EXISTS telegram_post_templates (
    id          serial PRIMARY KEY,
    brand_key   VARCHAR(120) NOT NULL,   -- lower(trim(brandname)), '' коли бренду немає
    model_key   VARCHAR(300) NOT NULL,   -- lower(trim(model))
    emoji       VARCHAR(16),             -- 👟 / 👜 / 🐊 — творчий, не завжди за типом
    tagline     VARCHAR(300),            -- хвіст заголовка після «• »
    features    TEXT,                    -- JSON-масив рядків «▪️» (без самого маркера)
    search_q    VARCHAR(300),            -- те, що йде в google.com/search?q=
    use_count   INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT telegram_post_templates_key UNIQUE (brand_key, model_key)
);

-- ── 3. Заплановані форварди в канал ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS telegram_scheduled_posts (
    id               serial PRIMARY KEY,
    product_id       INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_number   VARCHAR(50),
    chat_id          BIGINT NOT NULL,
    chat_title       VARCHAR(200),
    scheduled_at     TIMESTAMPTZ NOT NULL,
    source_chat_id   BIGINT,             -- звідки форвардимо (форум)
    source_message_id BIGINT,            -- головне повідомлення альбому-джерела
    state            VARCHAR(20) NOT NULL DEFAULT 'scheduled',  -- scheduled | sent | cancelled
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tg_sched_product ON telegram_scheduled_posts (product_id);
CREATE INDEX IF NOT EXISTS idx_tg_sched_state   ON telegram_scheduled_posts (state, scheduled_at);

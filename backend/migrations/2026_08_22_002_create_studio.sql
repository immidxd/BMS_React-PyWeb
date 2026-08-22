-- Майстерня публікацій: власні (нетоварні) пости, галерея фонів і фірмові шрифти.
--
-- Свідомо ОКРЕМИЙ контур, а не рядки в товарних таблицях публікацій — з тієї ж
-- причини, що й `social_collection_posts` (див. 2026_08_17_001): анонс, вітання
-- чи оголошення не є публікацією конкретного товару. Якби воно писалося в
-- instagram_publications / facebook_publications, кожен такий пост псував би
-- статистику «опубліковано товарів» і впирався б у товарні гарди.
--
-- Растр (готовий PNG/JPEG) — майстер публікації, а не сам макет: мережі
-- приймають файл за публічним URL. Макет (`spec_json`) зберігаємо поруч, щоб
-- пост можна було відкрити й перезібрати, а не малювати з нуля.

-- ── Підбірки ────────────────────────────────────────────────────────────────
-- Одна таблиця на два різновиди: «медіа» фасує галерею, «пост» фасує самі пости.
-- Роздільник — `kind`, бо поведінка ідентична (назва + порядок), а дві таблиці
-- означали б два однакові CRUD.
CREATE TABLE IF NOT EXISTS studio_collections (
    id BIGSERIAL PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,
    name VARCHAR(120) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind IN ('media', 'post')),
    UNIQUE (kind, name)
);

-- ── Галерея ─────────────────────────────────────────────────────────────────
-- Майстер лежить у R2 (`r2_key`), програма роздає його через власний
-- ендпоінт-проксі. Проксі, а не прямий CDN-URL, свідомо: шрифти й фото треба
-- вшивати в SVG при рендері, а крос-доменний файл або «отруює» canvas, або
-- вимагає CORS-налаштувань на бакеті. Своя адреса = того самого походження.
CREATE TABLE IF NOT EXISTS studio_assets (
    id BIGSERIAL PRIMARY KEY,
    -- Дедуплікація: те саме фото, залите вдруге, не створює другий об'єкт у R2.
    sha256 CHAR(64) NOT NULL UNIQUE,
    r2_key TEXT NOT NULL UNIQUE,
    url TEXT,
    thumb_key TEXT,
    thumb_url TEXT,
    filename TEXT NOT NULL,
    title TEXT,
    mime VARCHAR(64) NOT NULL DEFAULT 'image/webp',
    width INTEGER,
    height INTEGER,
    bytes INTEGER NOT NULL DEFAULT 0,
    has_alpha BOOLEAN NOT NULL DEFAULT FALSE,
    collection_id BIGINT REFERENCES studio_collections(id) ON DELETE SET NULL,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_studio_assets_collection
    ON studio_assets(collection_id, sort_order, id DESC);

-- ── Фірмові шрифти ──────────────────────────────────────────────────────────
-- Накреслення — окремий рядок (Bold і Regular однієї родини — два файли), бо
-- саме так їх віддає браузерний `@font-face` і саме так вони вшиваються в SVG.
-- Синтетичного «жирного» не робимо: він виглядає дешево, а фірмовий шрифт має
-- показувати справжнє накреслення.
CREATE TABLE IF NOT EXISTS studio_fonts (
    id BIGSERIAL PRIMARY KEY,
    family VARCHAR(120) NOT NULL,
    weight SMALLINT NOT NULL DEFAULT 400,
    style VARCHAR(16) NOT NULL DEFAULT 'normal',
    label VARCHAR(160),
    sha256 CHAR(64) NOT NULL UNIQUE,
    r2_key TEXT NOT NULL UNIQUE,
    url TEXT,
    format VARCHAR(8) NOT NULL,
    filename TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    -- Чи має шрифт кирилицю. Перевіряється при заливці: макет українською в
    -- шрифті без кирилиці мовчки перетворюється на порожні прямокутники.
    has_cyrillic BOOLEAN NOT NULL DEFAULT FALSE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (style IN ('normal', 'italic')),
    CHECK (weight BETWEEN 100 AND 900),
    CHECK (format IN ('ttf', 'otf', 'woff', 'woff2')),
    UNIQUE (family, weight, style)
);

-- ── Пости майстерні ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS studio_posts (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL DEFAULT 'Без назви',
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    -- Формат, у якому пост намальовано. Кожна мережа може взяти свій — тоді
    -- макет перераховується під її полотно, а базовий лишається джерелом.
    base_format VARCHAR(24) NOT NULL DEFAULT 'story',
    -- Документ макета: полотно, шари (фон, текст), стилі. Версіонований полем
    -- `spec_json->>'version'`, щоб старі пости відкривались після змін схеми.
    spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Куди публікувати: [{platform, format, enabled, settings:{...}}].
    targets_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    caption TEXT NOT NULL DEFAULT '',
    preview_key TEXT,
    preview_url TEXT,
    -- Готові растри: {формат: {key, url, bytes, width, height, rendered_at}}.
    renders_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    collection_id BIGINT REFERENCES studio_collections(id) ON DELETE SET NULL,
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('draft', 'ready', 'scheduled', 'published', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_studio_posts_status
    ON studio_posts(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_studio_posts_collection
    ON studio_posts(collection_id, updated_at DESC);

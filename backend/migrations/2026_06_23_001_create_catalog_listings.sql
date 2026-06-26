-- Публікація товару в публічний інтернет-каталог (Telegram Mini App ~/Desktop/BMS_catalog).
-- Окрема additive-таблиця: схему products НЕ чіпаємо. Каталог — read-only вітрина,
-- керування публікацією — звідси (back-office BMS). Ключ = productnumber (картка
-- каталогу = ростовка: один номер може мати кілька рядків-розмірів у products,
-- тож publish діє на всю картку одразу). FK немає навмисно: products.productnumber
-- НЕ унікальний (354 ростовки), тож тут plain-text PK, decoupled від products.

create table if not exists public.catalog_listings (
    productnumber text primary key,                 -- = products.productnumber (verbatim, з префіксом #)
    is_published  boolean     not null default false,  -- показувати в публічному каталозі
    is_featured   boolean     not null default false,  -- «Рекомендований» → угору/у hero-блок
    published_at  timestamptz,                       -- коли вперше опубліковано
    updated_at    timestamptz not null default now()
);

-- Частковий індекс: каталог фільтрує саме за опублікованими
create index if not exists idx_catalog_listings_published
    on public.catalog_listings(is_published) where is_published;
create index if not exists idx_catalog_listings_featured
    on public.catalog_listings(is_featured) where is_featured;

comment on table public.catalog_listings is
    'Публікація товару в публічний Telegram-каталог (BMS_catalog). Ключ=productnumber (картка/ростовка). is_published → видимий публіці; is_featured → «Рекомендований». Керується з картки товару BMS; каталог лише читає. Старт: усе приховано, власник вмикає вручну.';

-- Prom.ua інтеграція (Фаза 2). Токен статичний (Bearer, з кабінету) — на відміну
-- від OLX OAuth. Дзеркала товарів/замовлень тягне polling через my.prom.ua/api/v1.
-- Лінк BMS↔Prom = за SKU (= productnumber; external_id на Prom порожній).
-- Усе ідемпотентно (IF NOT EXISTS) — раннер проганяє на кожному старті.

-- Конфіг: єдиний рядок (id=1) з API-токеном і датою його закінчення.
-- token_expires_at заповнює власник (API дату не віддає) → UI попереджає завчасно.
create table if not exists public.prom_config (
    id               smallint primary key default 1,
    api_token        text,
    token_expires_at timestamptz,
    updated_at       timestamptz default now(),
    constraint prom_config_singleton check (id = 1)
);
comment on table public.prom_config is
    'Єдиний рядок (id=1): статичний Bearer-токен Prom + дата закінчення (для попередження в UI).';

-- Дзеркало товарів Prom (як olx_adverts). status=on_display + presence=available
-- → товар вважається опублікованим і в наявності на Prom. sku лінкує до products.
create table if not exists public.prom_products (
    id                 serial primary key,
    prom_id            bigint not null,             -- id товару в Prom
    product_id         integer references products(id) on delete set null,
    sku                varchar(80),                 -- = productnumber (Ф4163…)
    name               varchar(400),
    presence           varchar(20),                 -- available | not_available | order | waiting
    status             varchar(30),                 -- on_display | draft | not_on_display | deleted…
    price              numeric(12,2),
    url                varchar(500),
    last_synced_at     timestamptz default now(),
    constraint prom_products_prom_id_key unique (prom_id)
);
create index if not exists idx_prom_products_product on public.prom_products(product_id);
create index if not exists idx_prom_products_sku     on public.prom_products(sku);
create index if not exists idx_prom_products_status  on public.prom_products(status);
comment on table public.prom_products is
    'Дзеркало товарів Prom (read-sync). Лінк до products за sku=productnumber. Маркер «на Prom» + sku→prom_id мапа для пушу наявності.';

-- Дзеркало замовлень Prom (окреме від core orders — рішення власника: НЕ вливаємо
-- в Google-журнал, лише показуємо для огляду). products — JSONB рядків замовлення.
create table if not exists public.prom_orders (
    id                 serial primary key,
    prom_id            bigint not null,             -- id замовлення в Prom
    status             varchar(30),                 -- pending|received|delivered|canceled|draft|paid
    source             varchar(40),                 -- portal|company_site|mobile_app|bigl…
    date_created       timestamptz,
    client_name        varchar(200),
    phone              varchar(60),
    price_text         varchar(60),                 -- як віддав Prom ("1 890 грн")
    price_num          numeric(12,2),               -- розпарсене число (для сум/сортування)
    products           jsonb,                       -- [{sku, name, quantity, price, product_id}]
    linked_count       integer default 0,           -- скільки рядків злінковано до BMS
    client_notes       text,
    last_synced_at     timestamptz default now(),
    constraint prom_orders_prom_id_key unique (prom_id)
);
create index if not exists idx_prom_orders_status on public.prom_orders(status);
create index if not exists idx_prom_orders_date   on public.prom_orders(date_created desc);
comment on table public.prom_orders is
    'Дзеркало замовлень Prom (read-sync, окреме від core orders). Показ для огляду; товари лінкуються за sku. У Google-журнал НЕ вливається.';

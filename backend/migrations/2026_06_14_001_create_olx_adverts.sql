-- OLX-інтеграція (read-only v1): дзеркало telegram_posts для оголошень OLX.
-- Синхронізація тягне власні оголошення через офіційний OLX API (OAuth2),
-- витягує #Ф-номер з опису/заголовка (той самий регекс, що й Telegram),
-- лінкує до products. Маркер у рядку товару = є active-оголошення на цей номер.

create table if not exists public.olx_adverts (
    id                 serial primary key,
    olx_id             bigint not null,            -- id оголошення в OLX (унікальний на акаунт)
    product_id         integer references products(id) on delete set null,
    product_number_raw varchar(50),                -- витягнутий номер (без Ф, як у telegram_posts)
    title              varchar(300),
    description        text,
    status             varchar(40),                -- active | limited | disabled | removed_by_user | ...
    url                varchar(500),
    external_id        varchar(120),               -- власний ref, якщо проставлятимемо при створенні
    category_id        integer,
    price              numeric(12,2),
    currency           varchar(8),
    sizes_in_post      text,                       -- JSON-масив розмірів (на майбутнє, як у TG)
    posted_at          timestamp,                  -- created_at з OLX
    valid_to           timestamp,                  -- коли оголошення згасне
    last_synced_at     timestamptz default now(),
    constraint olx_adverts_olx_id_key unique (olx_id)
);

create index if not exists idx_olx_adverts_product on public.olx_adverts(product_id);
create index if not exists idx_olx_adverts_number  on public.olx_adverts(product_number_raw);
create index if not exists idx_olx_adverts_status  on public.olx_adverts(status);

comment on table public.olx_adverts is
    'Оголошення OLX продавця (read-only sync через OLX API). Дзеркало telegram_posts: olx_id+product_number_raw, лінк до products через relink за номером. status=active → товар вважається опублікованим на OLX.';

-- Сховище OAuth2-токенів OLX (single-row). access_token короткоживучий,
-- refresh_token довгоживучий — оновлюємо access перед протуханням.
create table if not exists public.olx_oauth (
    id             smallint primary key default 1,
    access_token   text,
    refresh_token  text,
    token_type     varchar(20),
    scope          varchar(120),
    expires_at     timestamptz,
    updated_at     timestamptz default now(),
    constraint olx_oauth_singleton check (id = 1)
);

comment on table public.olx_oauth is
    'Єдиний рядок (id=1) з OAuth2-токенами OLX. Заповнюється після одноразової авторизації продавця; sync оновлює access_token через refresh_token.';

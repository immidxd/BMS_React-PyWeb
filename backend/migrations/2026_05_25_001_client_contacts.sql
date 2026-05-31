-- client_contacts: many-to-one канали звʼязку для клієнта.
-- Розвʼязує колізії, де реально у людини 2+ FB / 2+ phone і парсер на цьому
-- щораз створював нового клієнта замість резолву в існуючого master.
--
-- clients.* primary-колонки (phone_number, facebook, ...) лишаються як
-- denormalized view для існуючих SELECT/експортів і синхронізуються з
-- рядком, де is_primary=TRUE.

create table if not exists public.client_contacts (
    id            serial primary key,
    client_id     integer not null references clients(id) on delete cascade,
    kind          text    not null,
    value         text    not null,
    normalized    text,
    is_primary    boolean not null default false,
    source        text,           -- 'backfill' | 'sheet' | 'manual' | 'merge'
    first_seen_at timestamptz not null default now(),
    last_seen_at  timestamptz not null default now(),
    constraint client_contacts_kind_chk check (
        kind in ('phone','facebook','telegram','instagram','email',
                 'viber','olx','tiktok','messenger')
    )
);

-- Глобальна унікальність по нормалізованому значенню — це і є identity-якір
-- для парсера: "цей FB вже належить комусь — кому саме?"
create unique index if not exists ux_client_contacts_kind_normalized
    on public.client_contacts(kind, normalized)
    where normalized is not null;

-- Одне primary на (client_id, kind) — щоб primary-синк був детермінований.
create unique index if not exists ux_client_contacts_primary
    on public.client_contacts(client_id, kind)
    where is_primary = true;

create index if not exists idx_client_contacts_client
    on public.client_contacts(client_id);

comment on table public.client_contacts is
    'Усі канали звʼязку клієнта (N на 1). Identity-резолв парсера ходить сюди першим.';

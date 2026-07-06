-- Черга «перевести в чернетку» для експортованих на Prom товарів. Prom створює
-- товар АСИНХРОННО (1-3 хв, через чергу імпортів), тож одноразовий фоновий крок
-- ненадійний. Замість нього: експорт кладе sku сюди, а Prom-синк-цикл щоразу
-- намагається знайти товар і перевести в draft, доки не вдасться (idempotent).
create table if not exists public.prom_draft_queue (
    sku          varchar(80) primary key,
    requested_at timestamptz default now(),
    attempts     integer default 0
);
comment on table public.prom_draft_queue is
    'SKU, які експортовано на Prom як чернетку й треба перевести у status=draft, коли товар з''явиться (асинхронне створення).';

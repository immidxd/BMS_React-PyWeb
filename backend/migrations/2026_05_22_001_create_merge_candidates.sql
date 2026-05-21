-- Candidate-merge UX: workspace parser більше не auto-merge,
-- а пропонує кандидатів які користувач явно приймає/відхиляє.

create table if not exists public.merge_candidates (
    id              serial primary key,
    new_product_id  integer not null references products(id) on delete cascade,
    suggested_id    integer not null references products(id) on delete cascade,
    score           smallint not null,         -- 0-5 з _workspace_merge_score (strict)
    reason          text,                      -- human-readable: "brand+size+color match"
    status          varchar(16) not null default 'pending',  -- pending | accepted | declined
    created_at      timestamptz default now(),
    decided_at      timestamptz,
    constraint merge_candidates_status_chk check (status in ('pending', 'accepted', 'declined')),
    constraint merge_candidates_unique_pair unique (new_product_id, suggested_id)
);

-- Pending кандидати по new_product_id — для бейджа в UI / API запиту
create index if not exists idx_mc_new_pending
    on public.merge_candidates(new_product_id)
    where status = 'pending';

-- Пошук «чи відхиляв юзер цю пару раніше» при наступному парсингу
create index if not exists idx_mc_pair_status
    on public.merge_candidates(new_product_id, suggested_id, status);

comment on table public.merge_candidates is
    'Кандидати на об''єднання workspace-товарів з існуючими. Парсер створює status=pending; користувач робить accept/decline у UI.';

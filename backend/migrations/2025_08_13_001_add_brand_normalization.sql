-- 001: Add normalized_name and unique index; create blocklist table

-- Add normalized_name column if missing
alter table if exists public.brands
  add column if not exists normalized_name text;

-- Backfill normalized_name from brandname using a simple SQL normalization placeholder
-- Note: final normalization occurs in application code; here we lowercase and trim as a baseline
update public.brands
set normalized_name = nullif(regexp_replace(lower(coalesce(brandname, '')), '\s+', ' ', 'g'), '')
where normalized_name is null;

-- Unique index on normalized_name (nullable safe via partial index)
create unique index if not exists uq_brands_normalized_name
  on public.brands (normalized_name)
  where normalized_name is not null;

-- Blocklist table
create table if not exists public.brand_blocklist (
  normalized_name text primary key,
  reason text,
  created_at timestamptz default now()
);



-- Create durable parsing jobs table for runtime observability

create table if not exists public.parsing_jobs (
  id bigserial primary key,
  mode text not null,
  status text not null default 'queued', -- queued|running|succeeded|failed|canceled|stalled
  started_at timestamptz,
  updated_at timestamptz,
  ended_at timestamptz,
  total_items bigint,
  processed_items bigint default 0,
  percent integer default 0,
  items_per_sec numeric(12,4),
  eta_seconds bigint,
  current_step text,
  last_heartbeat_at timestamptz,
  error_summary text,
  logs_head text, -- rolling short log buffer
  cancel_requested boolean default false
);

create index if not exists idx_parsing_jobs_status on public.parsing_jobs(status);
create index if not exists idx_parsing_jobs_last_hb on public.parsing_jobs(last_heartbeat_at);



-- Cockpit Yannis — phase 1 schema.
-- One table holds a JSON snapshot per day. Future migrations will normalize
-- this into per-source tables (withings_weight, huawei_sleep, yazio_intake,
-- lab_panels) and views that aggregate into cockpit_snapshot. For now, the
-- agent / cron writes the full payload directly.

create table if not exists public.cockpit_snapshot (
  id              bigserial primary key,
  snapshot_date   date        not null,
  payload         jsonb       not null,
  created_at      timestamptz not null default now()
);

create unique index if not exists cockpit_snapshot_date_uniq
  on public.cockpit_snapshot (snapshot_date);

create index if not exists cockpit_snapshot_created_at_idx
  on public.cockpit_snapshot (created_at desc);

-- RLS: locked down. Service role bypasses RLS, which is what the Next.js API
-- route uses. No anon access; the cockpit is single-user behind the user's
-- own deployment.
alter table public.cockpit_snapshot enable row level security;

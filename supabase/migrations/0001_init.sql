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

-- RLS posture: single-user private cockpit, deployment behind Tailscale per
-- PRODUCT.md. Snapshot is the user's own dashboard data — not sensitive in
-- the traditional sense. RLS is enabled, with a permissive read policy so
-- the publishable (anon) key works from server-side rendering. Writes go
-- through Supabase's authenticated admin context (dashboard or CLI).
alter table public.cockpit_snapshot enable row level security;

drop policy if exists "cockpit_snapshot read" on public.cockpit_snapshot;
create policy "cockpit_snapshot read"
  on public.cockpit_snapshot
  for select
  to anon, authenticated
  using (true);

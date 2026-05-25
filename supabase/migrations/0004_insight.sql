-- Insights: detected signals/patterns/anomalies emitted by detectors in
-- ingest/insights. Families covered initially:
--   1: budgets & cibles (saturated fat, alcohol, protein deficit)
--   4: projections labo (Mensink-Katan LDL)
--   5: Wegovy (effet anorexigene, adherence)
-- Each detector emits candidates; the orchestrator UPSERTs by hash_dedup and
-- marks stale insights inactive at end of run.

create extension if not exists "pgcrypto";

create table if not exists public.insight (
  id              uuid primary key default gen_random_uuid(),
  detected_at     timestamptz not null default now(),
  detector_key    text not null,
  family          smallint not null,
  severity        text not null check (severity in ('info','watch','alert')),
  score           numeric not null,
  title           text not null,
  body            text not null,
  metric_keys     text[] not null default '{}',
  data            jsonb not null default '{}'::jsonb,
  link_href       text,
  active          boolean not null default true,
  superseded_by   uuid references public.insight(id),
  hash_dedup      text not null unique
);

create index if not exists insight_active_score_idx
  on public.insight (active, score desc);
create index if not exists insight_detected_at_idx
  on public.insight (detected_at desc);
create index if not exists insight_detector_idx
  on public.insight (detector_key);

alter table public.insight enable row level security;

drop policy if exists "insight read" on public.insight;
create policy "insight read"
  on public.insight
  for select
  to anon, authenticated
  using (true);

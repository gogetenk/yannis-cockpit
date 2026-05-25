-- yazio_correction: traceable log of every plausibility correction applied
-- to Yazio nutrient values by the sanitization layer in ingest/yazio/sanitize.py
-- (and later by the LLM sanity pass in ingest/yazio/llm_sanity.py).
--
-- Each row documents ONE decision: nutrient X on day D had raw value R and
-- was either dropped (sanitized_value NULL) or coerced to S, with a
-- machine-readable rule_key + a human-readable reason. `reverted_at` lets
-- the user disagree later without losing the audit trail.

create extension if not exists "pgcrypto";

create table if not exists public.yazio_correction (
  id                uuid primary key default gen_random_uuid(),
  date              date not null,
  nutrient_id       text not null,
  raw_value         numeric not null,
  sanitized_value   numeric,             -- NULL = dropped entirely
  source            text not null check (source in ('rule','llm')),
  rule_key          text,                -- e.g. 'alcohol_kcal_coherence'
  llm_model         text,
  llm_confidence    numeric,
  reason            text not null,
  applied_at        timestamptz not null default now(),
  reverted_at       timestamptz,
  unique (date, nutrient_id, rule_key, applied_at)
);

create index if not exists yazio_correction_date_idx
  on public.yazio_correction (date desc);
create index if not exists yazio_correction_active_idx
  on public.yazio_correction (applied_at desc) where reverted_at is null;
create index if not exists yazio_correction_dedup_idx
  on public.yazio_correction (date, nutrient_id, rule_key) where reverted_at is null;

alter table public.yazio_correction enable row level security;

drop policy if exists "yazio_correction read" on public.yazio_correction;
create policy "yazio_correction read"
  on public.yazio_correction
  for select
  to anon, authenticated
  using (true);

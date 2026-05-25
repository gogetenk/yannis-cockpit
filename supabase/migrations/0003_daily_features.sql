-- daily_features: one row per day aggregating Yazio + Withings + Huawei +
-- Wegovy (+ labs later) into a flat feature vector. Populated by
-- ingest/features/build_daily_features.py. Drives the insight detectors and
-- any downstream ML / dashboard logic. Includes rolling intra-individual
-- z-scores (28d, 84d) for a curated subset of metrics; z-scores exclude the
-- current day from their reference window to avoid leakage.

create table if not exists public.daily_features (
  date date primary key,

  -- intake (Yazio)
  kcal numeric,
  protein_g numeric,
  carb_g numeric,
  fat_g numeric,
  fat_sat_g numeric,
  fiber_g numeric,
  sodium_mg numeric,
  alcohol_g numeric,
  sugar_g numeric,

  -- macro % of total energy
  pct_e_sat numeric,
  pct_e_pufa numeric,
  pct_e_mufa numeric,

  -- weight / composition (Withings priority, fallback Yazio)
  weight_kg numeric,
  body_fat_pct numeric,
  muscle_kg numeric,

  -- activity (Withings + Health Connect)
  steps integer,
  active_kcal numeric,
  total_kcal numeric,
  active_min integer,

  -- cardio (Withings BP + Huawei HR)
  sbp numeric,
  dbp numeric,
  hr_rest_min numeric,
  hr_avg numeric,

  -- sleep (Huawei)
  sleep_total_min integer,
  sleep_deep_min integer,
  sleep_rem_min integer,

  -- Wegovy
  wegovy_dose_mg numeric,
  wegovy_days_since_injection integer,

  -- rolling intra-individual z-scores, 28d window
  z28_kcal numeric,
  z28_weight_kg numeric,
  z28_sbp numeric,
  z28_sleep_total_min numeric,
  z28_alcohol_g numeric,
  z28_fat_sat_g numeric,
  z28_sodium_mg numeric,
  z28_hr_rest_min numeric,

  -- rolling intra-individual z-scores, 84d window
  z84_kcal numeric,
  z84_weight_kg numeric,
  z84_sbp numeric,
  z84_sleep_total_min numeric,
  z84_alcohol_g numeric,
  z84_fat_sat_g numeric,
  z84_sodium_mg numeric,
  z84_hr_rest_min numeric,

  computed_at timestamptz not null default now()
);

create index if not exists daily_features_date_desc_idx
  on public.daily_features (date desc);

alter table public.daily_features enable row level security;

drop policy if exists "daily_features read" on public.daily_features;
create policy "daily_features read"
  on public.daily_features
  for select
  to anon, authenticated
  using (true);

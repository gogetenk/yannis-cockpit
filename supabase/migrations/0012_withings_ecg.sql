-- Withings Body Scan / ScanWatch ECG recordings, one row per signal.
-- Populated by ingest/withings/ingest_ecg.py: pulls v2/heart list+get, parses
-- the raw single-lead waveform, detects R-peaks and derives HRV time-domain
-- metrics (RMSSD/SDNN/pNN50) plus rhythm/interval data when Withings exposes
-- it. This is the waveform layer that the standard measure ingest (afib result
-- code type 130) cannot give us.
create table if not exists withings_ecg (
  signal_id    bigint primary key,
  ts           timestamptz not null,
  afib_result  int,            -- Withings: 0 none/unknown, 1 negative, 2 positive
  device_model int,
  withings_hr  numeric,        -- avg HR Withings reports for the strip (ground truth)
  sampling_hz  int,
  duration_s   numeric,
  -- Derived from our R-peak detection on the raw signal:
  n_beats      int,
  mean_hr      numeric,
  mean_rr_ms   numeric,
  sdnn_ms      numeric,
  rmssd_ms     numeric,
  pnn50_pct    numeric,
  -- Interval/rhythm data from Withings heart/get extra_data, when present:
  qrs_ms       numeric,
  pr_ms        numeric,
  qt_ms        numeric,
  qtc_ms       numeric,
  quality      text,           -- 'ok' | 'noisy' | 'insufficient'
  raw_signal   jsonb,          -- the integer sample array, for re-analysis
  extra        jsonb,          -- raw heart/get extra_data + list entry
  ingested_at  timestamptz not null default now()
);

create index if not exists withings_ecg_ts_idx on withings_ecg (ts desc);

-- Read-only access for the cockpit (service role bypasses RLS on write).
alter table withings_ecg enable row level security;
do $$ begin
  if not exists (
    select 1 from pg_policies where tablename = 'withings_ecg' and policyname = 'withings_ecg_read'
  ) then
    create policy withings_ecg_read on withings_ecg for select using (true);
  end if;
end $$;

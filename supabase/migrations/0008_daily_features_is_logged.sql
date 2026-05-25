-- 0008_daily_features_is_logged.sql
-- Distinguish "user logged 0 kcal that day" from "no log at all" so the
-- detectors / baselines stop diluting averages with implicit zeros.
--
-- is_logged = TRUE only when we have positive evidence the user actually
-- recorded food intake for that date (yazio_day.kcal > 0). The build script
-- back-populates this on every run.

alter table public.daily_features
  add column if not exists is_logged boolean not null default false;

create index if not exists daily_features_is_logged_idx
  on public.daily_features (is_logged) where is_logged;

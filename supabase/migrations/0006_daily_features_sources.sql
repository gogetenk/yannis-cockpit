-- 0006_daily_features_sources.sql
-- Track the provenance of each micronutrient value in daily_features so the
-- UI / detectors can distinguish raw Yazio measurements from LLM estimates
-- (Haiku 4.5) injected when Yazio's photo-AI logger only returned macros+kcal.
--
-- Allowed values (text, no enum so future sources are append-only):
--   NULL           value itself is NULL (nothing to attribute)
--   'yazio'        value comes directly from Yazio API (yazio_micronutrient_daily)
--   'llm_estimate' value was estimated by Haiku from the day's meal context
--   'llm_review'   value was refined by Haiku via sanitize -> llm_sanity
--   'mixed'        partial Yazio + LLM top-up (reserved, not used yet)
--
-- No indexes: this is metadata, only read alongside the value column.

alter table public.daily_features
  add column if not exists fat_sat_g_source text,
  add column if not exists sodium_mg_source text,
  add column if not exists sugar_g_source   text,
  add column if not exists fiber_g_source   text,
  add column if not exists alcohol_g_source text;

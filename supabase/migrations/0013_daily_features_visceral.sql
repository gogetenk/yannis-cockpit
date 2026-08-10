-- Visceral fat index (Withings Body Scan, type_code 170, /20 scale).
-- The absolute value is a proprietary BIA index and is NOT reliable vs DEXA VAT
-- (608 g measured on 2026-04-21); it is tracked only for its TREND over time.
-- Nullable, populated by build_daily_features.load_withings_measurements once the
-- whole-body position filter accepts position 7 (Body Scan emits muscle/visceral
-- at position 7 since 2026-04).
alter table daily_features add column if not exists visceral_fat_score numeric;

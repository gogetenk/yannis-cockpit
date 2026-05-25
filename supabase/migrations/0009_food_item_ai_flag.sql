-- 0009_food_item_ai_flag.sql
-- Mark yazio_food_item_daily rows with their source kind + AI-estimate flag.
--
-- The Yazio /user/consumed-items API returns THREE distinct collections:
--   * products[]         -- resolved items with product_id (nutrient lookup)
--   * recipe_portions[]  -- recipes with recipe_id (nutrient lookup)
--   * simple_products[]  -- free-form entries from AI photo / manual logging,
--                           macros embedded in the item, no product_id and no
--                           micronutrient detail.
--
-- Until now, the ingest only persisted `products[]`, silently dropping AI-photo
-- entries (typical when the user logs lunch via the camera). That under-counts
-- saturated fat / sodium / sugar / fiber / alcohol on those days.
--
-- This migration adds:
--   * source_kind   -- 'product' | 'recipe' | 'simple' (default 'product' for
--                      back-compat with existing rows).
--   * is_ai_estimate -- TRUE for simple_products (AI photo / freestyle). The
--                       LLM enrichment pipeline uses this flag to decide which
--                       items deserve a per-item micros estimate, replacing
--                       the previous coarse per-slot heuristic.
--
-- A partial index supports the LLM enrichment workflow (scan only AI items).

alter table public.yazio_food_item_daily
    add column if not exists is_ai_estimate boolean not null default false;

alter table public.yazio_food_item_daily
    add column if not exists source_kind text not null default 'product'
        check (source_kind in ('product','recipe','simple'));

create index if not exists yazio_food_item_daily_ai_estimate_idx
    on public.yazio_food_item_daily (date desc)
    where is_ai_estimate = true;

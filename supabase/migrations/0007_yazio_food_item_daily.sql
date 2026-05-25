-- 0007_yazio_food_item_daily.sql
-- Per-item Yazio food log persistence (1 row per consumed item per day-slot).
--
-- The existing yazio_day / yazio_meal tables only carry aggregated totals,
-- which is not enough for the LLM enrichment layer: an estimator that only
-- sees "lunch: 720 kcal / 30 g protein" produces guesses, while the same
-- estimator fed "lunch: Sushi mix 250 g, Edamame 100 g, Soupe miso 150 g"
-- can ground sodium / SFA estimates on actual ingredients.
--
-- The (date, meal_slot, item_index) primary key keeps the table idempotent:
-- re-ingesting the same day overwrites in place rather than duplicating.
-- item_index reflects the order the user logged the items in (Yazio API
-- returns them in chronological intra-meal order).

create table if not exists public.yazio_food_item_daily (
    date            date not null,
    meal_slot       text not null check (meal_slot in ('breakfast','lunch','dinner','snack')),
    item_index      int  not null,
    item_name       text not null,
    amount_g        numeric,
    product_id      text,
    kcal_per_100g   numeric,
    protein_per_100g          numeric,
    carb_per_100g             numeric,
    fat_per_100g              numeric,
    fat_sat_per_100g          numeric,
    sodium_per_100g_mg        numeric,
    sugar_per_100g            numeric,
    fiber_per_100g            numeric,
    alcohol_per_100g          numeric,
    cholesterol_per_100g_mg   numeric,
    ingested_at     timestamptz not null default now(),
    primary key (date, meal_slot, item_index)
);

create index if not exists yazio_food_item_daily_date_desc_idx
    on public.yazio_food_item_daily (date desc);

alter table public.yazio_food_item_daily enable row level security;

drop policy if exists "yazio_food_item_daily read" on public.yazio_food_item_daily;
create policy "yazio_food_item_daily read"
    on public.yazio_food_item_daily
    for select
    to anon, authenticated
    using (true);

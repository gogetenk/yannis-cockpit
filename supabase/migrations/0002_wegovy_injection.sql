-- Wegovy injection log: per-week confirmation of actual dose taken.
-- Drives the cockpit's "days since last injection" + overdue alerting,
-- replacing the previous weekday-assumption fallback.

create table if not exists wegovy_injection (
  date date primary key,
  dose_mg numeric not null,
  logged_at timestamptz not null default now()
);

insert into wegovy_injection (date, dose_mg) values
  ('2026-04-14', 0.25),
  ('2026-04-21', 0.25),
  ('2026-04-28', 0.25),
  ('2026-05-05', 0.25),
  ('2026-05-12', 0.5),
  ('2026-05-19', 0.5)
on conflict do nothing;

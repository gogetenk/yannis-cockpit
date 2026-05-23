-- Seed: insert today's snapshot mirroring the static mockup.
-- Re-run safely thanks to the unique index on snapshot_date.

insert into public.cockpit_snapshot (snapshot_date, payload) values
('2026-05-22', $${
  "today": "2026-05-22",
  "hero": {
    "status": "conforme",
    "statusLabel": "Conforme",
    "current_kg": 83.9,
    "ideal_kg": 83.6,
    "delta_kg": 0.3,
    "tolerance_kg": 0.8,
    "start_date": "2026-04-14",
    "today_week": 5.4,
    "range": { "kg_min": 75, "kg_max": 87, "week_min": 0, "week_max": 52 },
    "real_points": [
      { "week": 0,   "kg": 86.3 },
      { "week": 1.0, "kg": 85.9 },
      { "week": 2.0, "kg": 85.5 },
      { "week": 3.0, "kg": 85.3 },
      { "week": 4.0, "kg": 85.0 },
      { "week": 4.7, "kg": 84.4 },
      { "week": 5.0, "kg": 84.3 },
      { "week": 5.4, "kg": 83.9 }
    ],
    "eta_75kg": "2027-02-18"
  },
  "wegovy": {
    "day_since_start": 38,
    "current_dose_mg": 0.5,
    "step_index": 2,
    "ladder": [
      { "dose_mg": 0.25, "status": "done" },
      { "dose_mg": 0.5,  "status": "current" },
      { "dose_mg": 1.0,  "status": "upcoming" },
      { "dose_mg": 1.7,  "status": "upcoming" },
      { "dose_mg": 2.4,  "status": "upcoming" }
    ],
    "next_dose_mg": 1.0,
    "next_in_weeks": 2
  },
  "signals": [],
  "bio_age": {
    "composite": 33, "chrono": 35, "delta_vs_chrono": -2,
    "subages": [], "trajectory_12m": []
  },
  "pillars": []
}$$::jsonb)
on conflict (snapshot_date) do update set payload = excluded.payload;

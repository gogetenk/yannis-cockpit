-- Manual body measurements (tape, not BIA): waist, shoulder, chest, hip,
-- biceps, thigh, wrist. One row per measurement event (typically one per
-- weigh-in session). Used to compute V-shape ratio + track structural
-- changes that BIA can't see (e.g. shoulder hypertrophy without weight gain).

CREATE TABLE IF NOT EXISTS body_measurement (
  date date PRIMARY KEY,
  waist_cm numeric,              -- at navel, love handles included
  shoulder_cm numeric,           -- widest point, sleeve-to-sleeve over deltoids
  chest_cm numeric,              -- around nipple line, arms relaxed
  hip_cm numeric,                -- widest point of glutes/hips
  biceps_cm numeric,             -- flexed, peak
  thigh_cm numeric,              -- midpoint, relaxed
  wrist_cm numeric,              -- frame-size indicator, rarely changes
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS body_measurement_date_idx ON body_measurement (date DESC);

ALTER TABLE body_measurement ENABLE ROW LEVEL SECURITY;
CREATE POLICY "body_measurement_read" ON body_measurement FOR SELECT USING (true);

-- Seed historical points the user provided in conversation 2026-05-28.
INSERT INTO body_measurement (date, waist_cm, shoulder_cm, chest_cm, hip_cm, biceps_cm, thigh_cm, wrist_cm, notes)
VALUES
  ('2025-05-01', 96.0,  127.0, 111.0, NULL,  37.0, NULL,  18.0, 'seed: mai 2025'),
  ('2025-11-01', 97.5,  NULL,  107.0, 103.0, 36.5, 58.3, NULL,  'seed: nov 2025'),
  ('2026-04-11', 100.0, 125.0, 110.0, NULL,  36.0, NULL,  18.0, 'seed: avril 2026 (Wegovy J1)'),
  ('2026-05-28', 96.0,  NULL,  NULL,  NULL,  NULL, NULL,  NULL, 'seed: today (waist at navel)')
ON CONFLICT (date) DO NOTHING;

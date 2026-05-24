// Shape of the single "cockpit snapshot" payload returned by GET /api/cockpit.
// Mirrors the on-screen mockup. Future ingestion pipelines will derive these
// values from raw Withings / Huawei / Yazio / labs sources.

export type StatusBand = "conforme" | "derive_mineure" | "derive_notable" | "derive_marquee";

export interface WeightTrajectoryPoint {
  week: number; // weeks since Wegovy J1
  kg: number;
}

export interface WeightHero {
  status: StatusBand;
  statusLabel: string;
  current_kg: number;
  ideal_kg: number;
  delta_kg: number;
  tolerance_kg: number;
  start_date: string; // ISO date of W0
  today_week: number;
  range: { kg_min: number; kg_max: number; week_min: number; week_max: number };
  real_points: WeightTrajectoryPoint[];
  eta_75kg: string; // ISO date
}

export interface WegovyTitration {
  day_since_start: number;
  current_dose_mg: number;
  step_index: number; // 1-based, current step
  ladder: { dose_mg: number; status: "done" | "current" | "upcoming" }[];
  next_dose_mg: number;
  next_in_weeks: number;
  days_since_last_injection?: number;
  days_to_next_injection?: number;
  last_injection_label?: string;
}

export type SignalKind = "ok" | "watch";

export interface Signal {
  id: string;
  title: string;
  sub: string;
  value: string;
  unit: string;
  status: SignalKind;
  status_label: string;
  spark: SignalSpark;
}

export type SignalSpark =
  | { kind: "line"; points: [number, number][]; color: "sage" | "ambre"; end_dot?: boolean }
  | { kind: "bars"; values: number[]; dim_indices?: number[]; color: "sage" | "ambre" };

export interface SubAge {
  key: "cardio" | "blood" | "composition" | "skeleton";
  label: string;
  value: number;
  off?: boolean;
}

export interface BioAge {
  composite: number;
  chrono: number;
  delta_vs_chrono: number;
  subages: SubAge[];
  trajectory_12m: { month: string; value: number }[]; // 7 points
}

export interface PillarTile {
  key: "composition" | "activity" | "cardio" | "recovery";
  label: string;
  meta: string;
  figure: string;
  unit: string;
  chart: PillarChart;
}

export type PillarChart =
  | { kind: "area"; points: [number, number][]; target_label: string; target_y: number }
  | { kind: "bars"; values: number[]; target_label?: string; target_y?: number; dim_indices?: number[]; ambre_indices?: number[]; target_band?: { y: number; h: number } };

export type PillarKey = "composition" | "activity" | "cardio" | "recovery";

// Extended set of detail pages. Wegovy is not a "pillar" per se (it shows
// as a banner on the home), but users may want to drill into the full
// titration plan + STEP-1 methodology. Same for biology (lab panel report).
export type DetailKey = PillarKey | "wegovy" | "biology";

export interface BiologySummary {
  last_panel_date: string;
  last_panel_label: string;
  lab_name: string;
  phenoage: number;
  phenoage_delta: number;
  lifetime_cv_risk_pct: number;
  lifetime_cv_risk_label: string;
  lifetime_cv_risk_driver: string | null;
  prevent_30y_pct: number;
  prevent_30y_band: "low" | "borderline" | "intermediate" | "high";
  prevent_30y_band_label: string;
  sbp_avg: number | null;
  dbp_avg: number | null;
  days_since_last: number;
  days_until_next: number;
  next_recommended_date: string;
  n_markers: number;
}

export interface BiologyMarker {
  code: string;
  label: string;
  value: string;
  value_num: number | null;
  baseline_num: number | null;
  unit: string;
  ref_low: number | null;
  ref_high: number | null;
  flag: "H" | "L" | null;
  delta_str: string | null;
  delta_pct: number | null;
}

export interface BiologySection {
  key: string;
  label: string;
  markers: BiologyMarker[];
}

export interface DetailTrajectory {
  // Long-form trajectory (12–36 months). x: months from start, y: metric value.
  x_label: string;           // e.g. "12 mois"
  y_unit: string;            // e.g. "% MG"
  y_min: number;
  y_max: number;
  points: { date: string; value: number }[];
  ideal?: { date: string; value: number }[];
  target?: { value: number; label: string };
  tolerance?: number;        // ± band
}

export interface MeasurementRow {
  date: string;              // ISO
  value: number | string;
  unit: string;
  delta?: string;            // e.g. "−0,2"
  off?: boolean;
}

export interface SubTrajectory {
  // Smaller, secondary line chart inside a detail page (e.g. HR repos, HRV).
  key: string;
  label: string;
  unit: string;
  current: string;
  trend_label: string;       // e.g. "−4 / 90 j"
  points: { date: string; value: number }[];
  target?: { value: number; label: string };
  ambre?: boolean;
}

export interface MethodSection {
  heading: string;
  body: string;              // markdown-light: plain paragraphs, splitter on \n\n
}

export interface PillarDetail {
  key: PillarKey;
  title: string;             // e.g. "Composition corporelle"
  meta: string;              // e.g. "Withings · dernière mesure 21 avr 2026"
  hero: {
    figure: string;
    unit: string;
    delta_label?: string;    // e.g. "−4,2 pts vs janvier"
    status_label: string;    // Conforme / Dérive mineure / ...
    status_off?: boolean;
  };
  trajectory: DetailTrajectory;
  table: MeasurementRow[];
  subs?: SubTrajectory[];    // optional 1–3 sub-line charts (e.g. Cardio)
  method: MethodSection[];
  cross_link?: { label: string; href: string };
}

export interface CockpitSnapshot {
  today: string; // ISO date
  hero: WeightHero;
  wegovy: WegovyTitration;
  signals: Signal[];
  action_today?: string | null;
  ai_brief?: string | null;
  bio_age: BioAge;
  biology?: BiologySummary;
  pillars: PillarTile[];
  pillar_detail?: Partial<Record<DetailKey, PillarDetail & { sections?: BiologySection[] }>>;
}

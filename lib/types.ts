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

export interface CockpitSnapshot {
  today: string; // ISO date
  hero: WeightHero;
  wegovy: WegovyTitration;
  signals: Signal[];
  bio_age: BioAge;
  pillars: PillarTile[];
}

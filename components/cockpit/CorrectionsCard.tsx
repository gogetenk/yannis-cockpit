import { getSupabase } from "@/lib/supabase";
import type { Correction } from "@/lib/types";

// Server component: top-5 most recent ACTIVE Yazio sanitization corrections.
// Discreet card at the bottom of the home page. Renders nothing when empty —
// most days have no corrections, and a permanent placeholder would be noise.
// No revert UI here yet; the table already supports it for a later iteration.

const NUTRIENT_LABELS: Record<string, { label: string; unit: string }> = {
  "nutrient.alcohol":       { label: "Alcool",   unit: "g" },
  "nutrient.sodium":        { label: "Sodium",   unit: "mg" },
  "nutrient.fat_saturated": { label: "Saturés",  unit: "g" },
  "nutrient.sugar":         { label: "Sucres",   unit: "g" },
  "nutrient.fiber":         { label: "Fibres",   unit: "g" },
};

const FR_MONTH = [
  "janv", "févr", "mars", "avr", "mai", "juin",
  "juil", "août", "sept", "oct", "nov", "déc",
];

function shortDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getUTCDate()} ${FR_MONTH[d.getUTCMonth()]}`;
}

function fmtNum(n: number, unit: string): string {
  // mg → integer, g with one decimal when < 100.
  if (unit === "mg" || Math.abs(n) >= 100) {
    return Math.round(n).toLocaleString("fr-FR");
  }
  return n.toLocaleString("fr-FR", { maximumFractionDigits: 1 });
}

async function fetchRecent(): Promise<Correction[]> {
  const supabase = getSupabase();
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("yazio_correction")
    .select(
      "id, date, nutrient_id, raw_value, sanitized_value, source, rule_key, llm_model, llm_confidence, reason, applied_at, reverted_at",
    )
    .is("reverted_at", null)
    .order("date", { ascending: false })
    .order("applied_at", { ascending: false })
    .limit(5);
  if (error) return [];
  return (data ?? []) as Correction[];
}

function CorrectionRow({ c }: { c: Correction }) {
  const meta = NUTRIENT_LABELS[c.nutrient_id] ?? {
    label: c.nutrient_id, unit: "",
  };
  const rawStr = `${fmtNum(c.raw_value, meta.unit)}${meta.unit ? "\u202f" + meta.unit : ""}`;
  const sanStr = c.sanitized_value === null
    ? "ignoré"
    : `${fmtNum(c.sanitized_value, meta.unit)}${meta.unit ? "\u202f" + meta.unit : ""}`;
  return (
    <li className="correction">
      <div className="correction-head">
        <span className="correction-date">{shortDate(c.date)}</span>
        <span className="correction-sep">·</span>
        <span className="correction-nutrient">{meta.label}</span>
        <span className="correction-sep">·</span>
        <span className="correction-values">
          <span className="correction-raw">{rawStr}</span>
          <span className="correction-arrow" aria-hidden="true">→</span>
          <span className={c.sanitized_value === null ? "correction-dropped" : "correction-san"}>
            {sanStr}
          </span>
        </span>
      </div>
      <div className="correction-reason">{c.reason}</div>
    </li>
  );
}

export async function CorrectionsCard() {
  const corrections = await fetchRecent();
  if (!corrections.length) return null;
  return (
    <section className="corrections-card" aria-labelledby="corrections-heading">
      <header className="corrections-head">
        <h3 id="corrections-heading">Corrections Yazio</h3>
        <span className="corrections-count">{corrections.length}</span>
      </header>
      <ol className="corrections-list">
        {corrections.map((c) => (
          <CorrectionRow key={c.id} c={c} />
        ))}
      </ol>
    </section>
  );
}

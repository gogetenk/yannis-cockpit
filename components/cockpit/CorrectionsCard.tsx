import { getSupabase } from "@/lib/supabase";
import type { Correction, CorrectionKind, CorrectionsCoverage } from "@/lib/types";

// Server component: top-5 most recent ACTIVE Yazio sanitization corrections.
// Discreet card at the bottom of the home page. Renders nothing when empty —
// most days have no corrections, and a permanent placeholder would be noise.
//
// Visually distinguishes 3 provenances (computed from source + rule_key, the
// DB schema stays 'rule' | 'llm'):
//   rule         : déterministe (plafond physiologique)
//   llm_review   : LLM a corrigé/confirmé une valeur Yazio existante
//   llm_estimate : LLM a estimé une valeur depuis 0 (macros / photo plat)
//
// Also surfaces a discreet "couverture mesures" indicator on the 30-day window.

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

const REASON_MAX = 80;

const BADGE_META: Record<CorrectionKind, { label: string; title: string; className: string }> = {
  rule: {
    label: "règle",
    title: "Plafond physiologique appliqué",
    className: "correction-badge correction-badge--rule",
  },
  llm_review: {
    label: "LLM revu",
    title: "Valeur Yazio corrigée par LLM (Haiku 4.5)",
    className: "correction-badge correction-badge--review",
  },
  llm_estimate: {
    label: "LLM estimé",
    title: "Valeur estimée depuis macros (plat photo Yazio)",
    className: "correction-badge correction-badge--estimate",
  },
};

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

function truncateReason(reason: string): string {
  if (!reason) return "";
  if (reason.length <= REASON_MAX) return reason;
  return reason.slice(0, REASON_MAX - 3).trimEnd() + "...";
}

// DB schema keeps source ∈ {rule, llm}; provenance is encoded in rule_key:
//   rule_key starts with "llm_review_"   → LLM corrected a Yazio value
//   rule_key starts with "llm_estimate_" → LLM estimated from scratch
//   otherwise (source='llm')             → treat as review (legacy rows)
function classify(c: Pick<Correction, "source" | "rule_key" | "reason">): CorrectionKind {
  if (c.source === "rule") return "rule";
  const rk = (c.rule_key ?? "").toLowerCase();
  if (rk.startsWith("llm_estimate")) return "llm_estimate";
  if (rk.startsWith("llm_review")) return "llm_review";
  // Heuristic fallback on the reason text for legacy rows where the rule_key
  // does not yet carry the prefix convention.
  const r = (c.reason ?? "").toLowerCase();
  if (r.includes("estim") && (r.includes("macro") || r.includes("depuis"))) {
    return "llm_estimate";
  }
  return "llm_review";
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

async function fetchCoverage(): Promise<CorrectionsCoverage | null> {
  const supabase = getSupabase();
  if (!supabase) return null;
  // 30-day window — anchored on today (UTC), inclusive.
  const today = new Date();
  const since = new Date(today);
  since.setUTCDate(since.getUTCDate() - 30);
  const sinceIso = since.toISOString().slice(0, 10);
  const { data, error } = await supabase
    .from("yazio_correction")
    .select("source, rule_key, reason")
    .is("reverted_at", null)
    .gte("date", sinceIso);
  if (error || !data) return null;
  let measured = 0;
  let estimated = 0;
  for (const row of data as Pick<Correction, "source" | "rule_key" | "reason">[]) {
    const kind = classify(row);
    if (kind === "llm_estimate") estimated++;
    else measured++;
  }
  const total = measured + estimated;
  const coverage_pct = total === 0 ? 100 : Math.round((measured / total) * 100);
  return { measured, estimated, total, coverage_pct };
}

function CorrectionBadge({ kind }: { kind: CorrectionKind }) {
  const meta = BADGE_META[kind];
  return (
    <span className={meta.className} title={meta.title}>
      {meta.label}
    </span>
  );
}

function CorrectionRow({ c }: { c: Correction }) {
  const meta = NUTRIENT_LABELS[c.nutrient_id] ?? {
    label: c.nutrient_id, unit: "",
  };
  const rawStr = `${fmtNum(c.raw_value, meta.unit)}${meta.unit ? "\u202f" + meta.unit : ""}`;
  const sanStr = c.sanitized_value === null
    ? "ignoré"
    : `${fmtNum(c.sanitized_value, meta.unit)}${meta.unit ? "\u202f" + meta.unit : ""}`;
  const kind = classify(c);
  const shortReason = truncateReason(c.reason);
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
        <CorrectionBadge kind={kind} />
      </div>
      <div className="correction-reason" title={c.reason}>{shortReason}</div>
    </li>
  );
}

export async function CorrectionsCard() {
  const [corrections, coverage] = await Promise.all([fetchRecent(), fetchCoverage()]);
  if (!corrections.length) return null;
  return (
    <section className="corrections-card" aria-labelledby="corrections-heading">
      <header className="corrections-head">
        <h3 id="corrections-heading">Corrections Yazio</h3>
        <div className="corrections-meta">
          {coverage && coverage.total > 0 ? (
            <span
              className="corrections-coverage"
              title={`${coverage.measured} mesurée${coverage.measured > 1 ? "s" : ""} · ${coverage.estimated} estimée${coverage.estimated > 1 ? "s" : ""} sur 30 j`}
            >
              couverture mesures&nbsp;: {coverage.coverage_pct}&nbsp;%
            </span>
          ) : null}
          <span className="corrections-count">{corrections.length}</span>
        </div>
      </header>
      <ol className="corrections-list">
        {corrections.map((c) => (
          <CorrectionRow key={c.id} c={c} />
        ))}
      </ol>
    </section>
  );
}

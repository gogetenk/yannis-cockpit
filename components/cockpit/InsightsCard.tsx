import Link from "next/link";
import { getSupabase } from "@/lib/supabase";
import type { Insight } from "@/lib/types";

// Server component: reads top-3 active insights from Supabase and renders
// them as a compact list just below SignalsSection on the home.
// Renders nothing when there are no active insights (no empty placeholder).

async function fetchTopInsights(): Promise<Insight[]> {
  const supabase = getSupabase();
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("insight")
    .select(
      "id, detector_key, family, severity, score, title, body, metric_keys, data, link_href, detected_at",
    )
    .eq("active", true)
    .order("score", { ascending: false })
    .limit(3);
  if (error) return [];
  return (data ?? []) as Insight[];
}

function dotClass(sev: Insight["severity"]): string {
  return `insight-dot insight-dot--${sev}`;
}

function InsightRow({ ins }: { ins: Insight }) {
  const inner = (
    <>
      <span className={dotClass(ins.severity)} aria-label={ins.severity} />
      <div className="insight-body">
        <span className="insight-title">{ins.title}</span>
        <span className="insight-sub">{ins.body}</span>
      </div>
      {ins.link_href && <span className="insight-chevron" aria-hidden="true">›</span>}
    </>
  );
  if (ins.link_href) {
    return (
      <li className="insight insight--link">
        <Link href={ins.link_href} className="insight-link">
          {inner}
        </Link>
      </li>
    );
  }
  return <li className="insight">{inner}</li>;
}

export async function InsightsCard() {
  const insights = await fetchTopInsights();
  if (!insights.length) return null;
  return (
    <section className="insights-card" aria-labelledby="insights-heading">
      <header className="insights-head">
        <h3 id="insights-heading">Insights</h3>
      </header>
      <ol className="insights-list">
        {insights.map((ins) => (
          <InsightRow key={ins.id} ins={ins} />
        ))}
      </ol>
    </section>
  );
}

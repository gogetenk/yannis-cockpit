import { NextResponse } from "next/server";
import { getSupabase } from "@/lib/supabase";
import type { Correction } from "@/lib/types";

// GET /api/corrections — most recent ACTIVE (non-reverted) Yazio sanitization
// corrections, newest first. Cached 2 min — corrections only change when the
// ingest pipeline runs (hourly at most).

export const dynamic = "force-dynamic";

export async function GET() {
  const supabase = getSupabase();
  if (!supabase) {
    return NextResponse.json(
      { corrections: [], error: "supabase not configured" },
      { status: 200, headers: { "cache-control": "max-age=120" } },
    );
  }

  const { data, error } = await supabase
    .from("yazio_correction")
    .select(
      "id, date, nutrient_id, raw_value, sanitized_value, source, rule_key, llm_model, llm_confidence, reason, applied_at, reverted_at",
    )
    .is("reverted_at", null)
    .order("date", { ascending: false })
    .order("applied_at", { ascending: false })
    .limit(50);

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const corrections = (data ?? []) as Correction[];
  return NextResponse.json(
    { corrections },
    { headers: { "cache-control": "max-age=120" } },
  );
}

import { NextResponse } from "next/server";
import { getSupabase } from "@/lib/supabase";
import type { Insight } from "@/lib/types";

// GET /api/insights — top 5 active insights, ordered by score desc.
// Reads directly from the `insight` table populated by the detector pipeline.

export const dynamic = "force-dynamic";

export async function GET() {
  const supabase = getSupabase();
  if (!supabase) {
    return NextResponse.json(
      { insights: [], error: "supabase not configured" },
      { status: 200, headers: { "cache-control": "max-age=60" } },
    );
  }

  const { data, error } = await supabase
    .from("insight")
    .select(
      "id, detector_key, family, severity, score, title, body, metric_keys, data, link_href, detected_at",
    )
    .eq("active", true)
    .order("score", { ascending: false })
    .limit(5);

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const insights = (data ?? []) as Insight[];
  return NextResponse.json(
    { insights },
    { headers: { "cache-control": "max-age=60" } },
  );
}

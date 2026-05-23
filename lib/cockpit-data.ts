import "server-only";
import type { CockpitSnapshot } from "./types";
import { MOCK_SNAPSHOT } from "./mock-data";
import { getSupabase } from "./supabase";

// Single read path for the cockpit page + API route. When Supabase is wired,
// returns the latest snapshot row; otherwise returns the frozen mock so the
// app stays usable offline / before ingestion is built.

export async function readCockpitSnapshot(): Promise<{
  source: "supabase" | "mock";
  data: CockpitSnapshot;
}> {
  const client = getSupabase();
  if (!client) {
    return { source: "mock", data: MOCK_SNAPSHOT };
  }
  const { data, error } = await client
    .from("cockpit_snapshot")
    .select("payload, snapshot_date")
    .order("snapshot_date", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error || !data?.payload) {
    return { source: "mock", data: MOCK_SNAPSHOT };
  }
  return { source: "supabase", data: data.payload as CockpitSnapshot };
}

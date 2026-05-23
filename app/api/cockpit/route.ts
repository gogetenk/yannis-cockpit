import { NextResponse } from "next/server";
import { readCockpitSnapshot } from "@/lib/cockpit-data";

// GET /api/cockpit — single read endpoint for the dashboard.
// Returns { source: "supabase" | "mock", data: CockpitSnapshot }.
// No auth yet (single-user, deployment behind Tailscale per PRODUCT.md).

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const result = await readCockpitSnapshot();
    return NextResponse.json(result, {
      headers: { "cache-control": "no-store" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

import { NextResponse } from "next/server";

// POST /api/log-injection — proxies to the Supabase Edge Function `log-injection`,
// injecting the shared cockpit secret server-side so it never reaches the client.
// Body: { date: "YYYY-MM-DD", dose_mg: number }

export const dynamic = "force-dynamic";

const EDGE_URL =
  "https://rfigopnkrjxrdwsoggpt.supabase.co/functions/v1/log-injection";

export async function POST(req: Request) {
  const secret = process.env.COCKPIT_INGEST_SECRET;
  if (!secret) {
    return NextResponse.json(
      { error: "COCKPIT_INGEST_SECRET not configured" },
      { status: 500 },
    );
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const r = await fetch(EDGE_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-cockpit-secret": secret,
    },
    body: JSON.stringify(body),
  });
  const text = await r.text();
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    payload = { error: text };
  }
  return NextResponse.json(payload, { status: r.status });
}

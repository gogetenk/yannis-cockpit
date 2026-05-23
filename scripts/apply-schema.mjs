// Apply supabase/migrations/0001_init.sql and supabase/seed.sql to the
// database pointed at by SUPABASE_DB_URL. Idempotent.
import { readFileSync } from "fs";
import { Client } from "pg";

const url = process.env.SUPABASE_DB_URL;
if (!url) {
  console.error("SUPABASE_DB_URL not set");
  process.exit(1);
}

const files = ["supabase/migrations/0001_init.sql", "supabase/seed.sql"];

const client = new Client({ connectionString: url, ssl: { rejectUnauthorized: false } });

try {
  await client.connect();
  for (const f of files) {
    const sql = readFileSync(f, "utf8");
    console.log(`→ ${f}`);
    await client.query(sql);
  }
  const { rows } = await client.query("select snapshot_date from public.cockpit_snapshot order by snapshot_date desc limit 1");
  console.log("seeded snapshot:", rows[0]);
} finally {
  await client.end();
}

-- Persistent cache for LLM estimate calls (Haiku 4.5) so cron-driven
-- pipeline reruns on identical inputs hit the cache instead of re-paying.
-- Keys are sha256(kind|model|payload_json). Entries are immutable; bumping
-- the input changes the key. No TTL (cheap storage, mostly idempotent data).

CREATE TABLE IF NOT EXISTS llm_estimate_cache (
  cache_key text PRIMARY KEY,
  model text NOT NULL,
  kind text NOT NULL,
  output jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_estimate_cache_created_idx ON llm_estimate_cache (created_at DESC);
ALTER TABLE llm_estimate_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "llm_estimate_cache_read" ON llm_estimate_cache FOR SELECT USING (true);

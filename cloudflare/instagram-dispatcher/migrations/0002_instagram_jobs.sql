CREATE TABLE IF NOT EXISTS instagram_jobs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  instagram_account_id TEXT NOT NULL,
  product_id INTEGER NOT NULL,
  product_number TEXT NOT NULL,
  publish_type TEXT NOT NULL,
  caption TEXT NOT NULL DEFAULT '',
  media_json TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '{}',
  publish_at TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled',
  phase TEXT NOT NULL DEFAULT 'new',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  child_container_ids TEXT NOT NULL DEFAULT '[]',
  container_id TEXT,
  instagram_media_id TEXT,
  permalink TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_instagram_jobs_due
  ON instagram_jobs(status, next_attempt_at, publish_at);
CREATE INDEX IF NOT EXISTS idx_instagram_jobs_account_published
  ON instagram_jobs(instagram_account_id, status, published_at);

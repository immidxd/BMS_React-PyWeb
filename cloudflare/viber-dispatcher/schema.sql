CREATE TABLE IF NOT EXISTS viber_jobs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  product_id INTEGER NOT NULL,
  product_number TEXT NOT NULL,
  channel_title TEXT NOT NULL,
  caption TEXT NOT NULL,
  media_url TEXT NOT NULL,
  thumbnail_url TEXT NOT NULL,
  publish_at TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  message_token TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_viber_jobs_due
  ON viber_jobs(status, next_attempt_at, publish_at);

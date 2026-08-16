CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_expiry
  ON oauth_states(expires_at);

-- Зберігаємо ДВА токени: Page token публікує, а long-lived user token потрібен,
-- щоб перевидати Page token, коли термін першого добігає кінця.
CREATE TABLE IF NOT EXISTS facebook_accounts (
  id TEXT PRIMARY KEY,
  page_id TEXT NOT NULL UNIQUE,
  page_name TEXT,
  page_token_ciphertext TEXT NOT NULL,
  page_token_iv TEXT NOT NULL,
  user_token_ciphertext TEXT NOT NULL,
  user_token_iv TEXT NOT NULL,
  user_token_expires_at TEXT,
  scopes TEXT,
  connected_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facebook_webhook_events (
  id TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL,
  received_at TEXT NOT NULL,
  processed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_facebook_webhook_events_received
  ON facebook_webhook_events(received_at);

CREATE TABLE IF NOT EXISTS facebook_jobs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  facebook_page_id TEXT NOT NULL,
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
  child_media_ids TEXT NOT NULL DEFAULT '[]',
  video_id TEXT,
  facebook_post_id TEXT,
  permalink TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_facebook_jobs_due
  ON facebook_jobs(status, next_attempt_at, publish_at);
CREATE INDEX IF NOT EXISTS idx_facebook_jobs_page_published
  ON facebook_jobs(facebook_page_id, status, published_at);

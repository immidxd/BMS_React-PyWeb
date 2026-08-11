CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_expiry
  ON oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS instagram_accounts (
  id TEXT PRIMARY KEY,
  ig_user_id TEXT NOT NULL UNIQUE,
  page_id TEXT NOT NULL,
  username TEXT,
  page_name TEXT,
  token_ciphertext TEXT NOT NULL,
  token_iv TEXT NOT NULL,
  token_expires_at TEXT,
  scopes TEXT,
  connected_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instagram_webhook_events (
  id TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL,
  received_at TEXT NOT NULL,
  processed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_instagram_webhook_events_received
  ON instagram_webhook_events(received_at);

-- Жива черга з'явиться окремою міграцією лише після тестового акаунта й
-- явного дозволу на перший live-тест. Цей етап фізично не має publish endpoint.

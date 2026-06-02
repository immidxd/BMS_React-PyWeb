-- Layer C: whole-file change gate for incremental parsing.
--
-- Stores the Google Drive lastUpdateTime per (spreadsheet, mode) after a
-- successful parse. On the next run the parser fetches the current
-- lastUpdateTime (already loaded at open_by_key — no extra API call) and, if it
-- matches the recorded value AND parser_version is unchanged, skips the entire
-- run: no cell changed → re-parsing would produce identical results.
--
-- Keyed by mode so a 'full' request is gated against the last *full* parse, not
-- a partial 'quick' parse (products quantity is only complete after a full run).
--
-- Rollback: DROP TABLE spreadsheet_sync_state;
CREATE TABLE IF NOT EXISTS spreadsheet_sync_state (
    spreadsheet_id   VARCHAR(80)  NOT NULL,
    mode             VARCHAR(40)  NOT NULL,
    last_update_time VARCHAR(40)  NOT NULL,
    parser_version   INTEGER      NOT NULL DEFAULT 1,
    checked_at       TIMESTAMP    NOT NULL DEFAULT now(),
    PRIMARY KEY (spreadsheet_id, mode)
);

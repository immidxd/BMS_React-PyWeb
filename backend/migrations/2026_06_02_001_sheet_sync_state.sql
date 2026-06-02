-- Layer B: per-sheet change detection for incremental parsing.
--
-- Stores a content hash + parser_version per worksheet so the parser can skip
-- sheets whose content has not changed since the last parse (and whose logic
-- version still matches). Keyed by stable Google sheet_gid (rename-safe), not
-- by title. Currently consumed by the orders parser only (products keep full
-- reprocessing to preserve cross-sheet quantity aggregation).
--
-- Rollback: DROP TABLE sheet_sync_state;
CREATE TABLE IF NOT EXISTS sheet_sync_state (
    id             SERIAL PRIMARY KEY,
    spreadsheet_id VARCHAR(80)  NOT NULL,
    sheet_gid      BIGINT       NOT NULL,
    sheet_title    VARCHAR(255),
    content_hash   VARCHAR(64)  NOT NULL,
    parser_version INTEGER      NOT NULL DEFAULT 1,
    parsed_at      TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (spreadsheet_id, sheet_gid)
);

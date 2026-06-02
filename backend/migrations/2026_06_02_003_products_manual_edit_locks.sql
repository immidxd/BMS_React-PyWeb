-- Phase 2a: in-app editing locks for products (mirrors clients.manually_edited_*).
--
-- manually_edited_fields: CSV of field names the user edited in the app. The
-- parser restores these fields to the user's value after a reparse (snapshot-
-- restore), so in-app edits survive (sheet does NOT overwrite them) until the
-- user explicitly clears the lock ("revert to sheet").
-- manually_edited_at: timestamp of the last in-app edit (NULL = never edited →
-- no lock active, parser owns all fields as before).
--
-- Rollback: ALTER TABLE products DROP COLUMN manually_edited_fields,
--           DROP COLUMN manually_edited_at;
ALTER TABLE products ADD COLUMN IF NOT EXISTS manually_edited_fields TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS manually_edited_at TIMESTAMP;

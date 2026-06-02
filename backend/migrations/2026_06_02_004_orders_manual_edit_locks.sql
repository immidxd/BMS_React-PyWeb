-- Order editing Phase A: in-app locks (mirrors products/clients.manually_edited_*).
-- Edited order fields survive reparse (parser snapshot-restore). No sheet write-back yet.
-- Rollback: ALTER TABLE orders DROP COLUMN manually_edited_fields, DROP COLUMN manually_edited_at;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS manually_edited_fields TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS manually_edited_at TIMESTAMP;

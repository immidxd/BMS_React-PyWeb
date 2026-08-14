-- Ручне підтвердження прибирання проданих постів із майданчиків, де немає
-- безпечного API-видалення. Сам запис лишається в історії публікацій.

ALTER TABLE viber_publications
    ADD COLUMN IF NOT EXISTS cleanup_confirmed_at TIMESTAMPTZ;

ALTER TABLE instagram_publications
    ADD COLUMN IF NOT EXISTS cleanup_confirmed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_viber_publications_manual_cleanup
    ON viber_publications(product_number, cleanup_confirmed_at)
    WHERE status = 'published';

CREATE INDEX IF NOT EXISTS idx_instagram_publications_manual_cleanup
    ON instagram_publications(product_number, cleanup_confirmed_at)
    WHERE status = 'published' AND media_type = 'feed';

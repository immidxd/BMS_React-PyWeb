-- Старий UX дозволяв підтвердити Shafa без URL/ID і тим самим створював
-- фальшивий стан «опубліковано». Повертаємо такі записи до чесного очікування.
UPDATE shafa_publications
SET status = CASE WHEN source = 'manual' THEN 'removed' ELSE 'bridge_ready' END,
    confirmed_at = NULL,
    last_error = 'Локальне підтвердження без URL Shafa скинуто: потрібне фактичне посилання',
    updated_at = now()
WHERE status IN ('confirmed', 'manual_existing')
  AND NULLIF(BTRIM(shafa_url), '') IS NULL
  AND NULLIF(BTRIM(shafa_listing_id), '') IS NULL;

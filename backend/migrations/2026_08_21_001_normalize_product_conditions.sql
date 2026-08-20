-- Normalize the confirmed typo without deleting any lookup/audit rows.
-- Idempotent: safe to run repeatedly and independent of concrete IDs.
DO $$
DECLARE
    canonical_id INTEGER;
BEGIN
    SELECT id INTO canonical_id
    FROM conditions
    -- Exact Cyrillic comparison: this DB's C collation does not reliably
    -- lowercase Ukrainian text.
    WHERE btrim(conditionname) = 'Пошкоджений'
    ORDER BY id
    LIMIT 1;

    IF canonical_id IS NULL THEN
        INSERT INTO conditions (conditionname)
        VALUES ('Пошкоджений')
        RETURNING id INTO canonical_id;
    END IF;

    UPDATE products
    SET conditionid = canonical_id
    WHERE conditionid IN (
        SELECT id FROM conditions
        WHERE btrim(conditionname) = 'Пошкодженний'
    );

    UPDATE products
    SET current_conditionid = canonical_id
    WHERE current_conditionid IN (
        SELECT id FROM conditions
        WHERE btrim(conditionname) = 'Пошкодженний'
    );
END $$;

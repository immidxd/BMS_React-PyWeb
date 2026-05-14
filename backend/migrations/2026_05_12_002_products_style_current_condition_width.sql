-- Нові поля для products:
--   current_conditionid — поточний стан (FK на conditions, той самий словник, що й statusid/conditionid)
--   styleid             — стиль (FK на нову таблицю styles)
--   width               — ширина ніжки (рядок: "Вузька"/"Стандартна"/"Широка" або B/D/EE)
-- Окрема таблиця styles (аналог subtypes/conditions).

CREATE TABLE IF NOT EXISTS styles (
    id SERIAL PRIMARY KEY,
    stylename VARCHAR(100) UNIQUE NOT NULL
);

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS current_conditionid INTEGER REFERENCES conditions(id),
    ADD COLUMN IF NOT EXISTS styleid             INTEGER REFERENCES styles(id),
    ADD COLUMN IF NOT EXISTS width               VARCHAR(20);

CREATE INDEX IF NOT EXISTS ix_products_styleid ON products(styleid);
CREATE INDEX IF NOT EXISTS ix_products_current_conditionid ON products(current_conditionid);

-- Migration: Add letter size column to products
-- Date: 2026-05-24
-- Purpose: Окрема канонічна літерна шкала для одягу/сумок (XS/S/M/L/XL/XXL/XXXL),
--          паралельно до числового `sizeeu`. Дозволяє фільтрувати товари,
--          в яких розмір позначений тільки буквою (або одночасно буквою і числом).
--
-- Convention:
--   size_letter ∈ {XS, S, M, L, XL, XXL, XXXL, XXXXL} (canonical, без пробілів, NULL = немає)
--   Числові розміри лишаються у `sizeeu` (без змін).
--
-- Sheet column: "Буквений розмір" — нова колонка одразу після "Розмір".
-- Old rows with letters в `sizeeu` будуть очищені окремим cleanup-скриптом
-- (НЕ в цій міграції — щоб не зачепити нічого без явного DRY-run).

ALTER TABLE products
ADD COLUMN IF NOT EXISTS size_letter TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_products_size_letter
    ON products(size_letter)
    WHERE size_letter IS NOT NULL;

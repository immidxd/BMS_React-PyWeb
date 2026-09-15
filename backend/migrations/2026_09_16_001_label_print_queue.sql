-- Стікери з QR для складу: черга друку, історія друку, позначка на товарі.
--
-- НАВІЩО. Товар отримує стандартизований стікер (QR `bms:p:<id>:<номер>` +
-- номер/розмір/бренд), який далі сканують телефоном на складі. Принтер —
-- термо 100×100 мм, стікери йдуть по 4/6/9 на аркуш, тож друкувати треба
-- ПАКЕТОМ: «Додати товар» кладе стікер у чергу, а не витрачає цілий аркуш на
-- один стікер. Історія друку — щоб передрукувати пакет і бачити, коли саме
-- товар отримав стікер.
--
-- product_id без FK: товар може зникнути (злиття фантомів, перейменування
-- номера) — черга чиститься при рендері, історія лишається як є.
CREATE TABLE IF NOT EXISTS label_print_queue (
    id          BIGSERIAL PRIMARY KEY,
    product_id  INTEGER NOT NULL,
    copies      INTEGER NOT NULL DEFAULT 1,   -- скільки стікерів (ростовка: quantity)
    source      VARCHAR(24),                  -- add_product / selection / delivery / card
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    printed_at  TIMESTAMPTZ                   -- NULL = ще чекає в черзі
);
-- Один товар у черзі — один раз: повторне «у чергу» оновлює copies, а не дублює.
CREATE UNIQUE INDEX IF NOT EXISTS uq_label_print_queue_pending
    ON label_print_queue (product_id) WHERE printed_at IS NULL;

CREATE TABLE IF NOT EXISTS label_print_jobs (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    layout      VARCHAR(8) NOT NULL,          -- 2x2 / 2x3 / 3x3 (колонки×рядки)
    pages       INTEGER NOT NULL,
    stickers    INTEGER NOT NULL,
    mode        VARCHAR(12) NOT NULL,         -- print / save / download
    printer     VARCHAR(128),
    file_path   TEXT,
    items       JSONB NOT NULL                -- [{product_id, productnumber, copies}]
);

-- Коли товар востаннє отримав стікер (фільтр «без стікера» у таблиці товарів).
ALTER TABLE products ADD COLUMN IF NOT EXISTS label_printed_at TIMESTAMPTZ;

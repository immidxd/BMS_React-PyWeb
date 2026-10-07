-- Папки товарів: робочі набори для оперативної роботи в програмі («На фотосесію»,
-- «Для Олі», «Повернення»…), щоб не шукати ті самі товари фільтрами щоразу.
--
-- Суто локально: таблиць нема в списку синку каталогу (sync_to_cloud.TABLES),
-- тож у хмару вони не їдуть і Neon не будять.
--
-- Товар може бути в КІЛЬКОХ папках (як мітки) — звідси окрема таблиця зв'язку.
-- product_id — справжній FK із CASCADE: видалили товар → зник і з папок.
-- Злиття двійників (product_refs.repoint_product_refs) знаходить цей FK у
-- pg_constraint і переносить членство на товар, що лишився; дубль (той уже в
-- папці) ловиться первинним ключем і прибирається.
CREATE TABLE IF NOT EXISTS product_folders (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(120) NOT NULL UNIQUE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product_folder_items (
    folder_id   INTEGER NOT NULL REFERENCES product_folders(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (folder_id, product_id)
);
CREATE INDEX IF NOT EXISTS ix_product_folder_items_product
    ON product_folder_items (product_id);

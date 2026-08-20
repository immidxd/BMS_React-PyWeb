-- Черга записів у журнал (Google Sheets).
--
-- Навіщо: правка з картки писалась в аркуш фоновим потоком «вистрелив і забув».
-- Виняток усередині потоку (падіння токена OAuth, SSL, обрив мережі) лише
-- логувався — правка лишалась у БД під локом, аркуш мовчки відставав назавжди,
-- і ніде не було видно, що він відстав. У логах за три робочі дні — 40 таких
-- провалів на 218 успішних записів.
--
-- Тепер кожне поле спершу лягає сюди, і лише потім воркер несе його в аркуш:
-- падіння = не втрата, а attempts+1 і наступна спроба з відступом. Черга
-- переживає перезапуск застосунку (на старті воркер добирає pending).
CREATE TABLE IF NOT EXISTS journal_writeback_queue (
    id BIGSERIAL PRIMARY KEY,
    product_id INTEGER,
    productnumber VARCHAR(120) NOT NULL,
    sheet_title VARCHAR(255),
    field VARCHAR(64) NOT NULL,
    -- Значення вже РЕЗОЛВНУТЕ до того, що має лежати в клітинці (FK→назва),
    -- бо воркер працює поза сесією БД і дорезолвити не зможе.
    value TEXT,
    -- pending → очікує; processing → конкретна версія вже летить у Google;
    -- done → лягло в аркуш або було безпечно заміщене новішою правкою;
    -- skipped → писати нікуди
    -- (нема колонки/вкладки, per-item поле на багаторядковій ростовці);
    -- failed → вичерпані спроби, лишається видимим для ручного повтору.
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts SMALLINT NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TIMESTAMP NOT NULL DEFAULT now(),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    done_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jwq_ready ON journal_writeback_queue (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jwq_product ON journal_writeback_queue (product_id);

-- Одна НЕЗАХОПЛЕНА задача на (товар, поле): до мережевого виклику нова правка
-- замінює значення. Після переходу старої версії в processing нова створює
-- окремий pending-рядок, тому завершення старого HTTP-запиту її не погасить.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jwq_open_unique
    ON journal_writeback_queue (product_id, field)
    WHERE status IN ('pending', 'failed');

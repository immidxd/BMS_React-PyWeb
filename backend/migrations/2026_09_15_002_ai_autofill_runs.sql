-- Сирі відповіді моделі на кожен запуск автозаповнення.
--
-- НАВІЩО. «Чому не розпізнало ціну зі стікера?» — на це питання досі не було
-- відповіді без ПОВТОРНОГО виклику моделі, який коштує добову квоту (і в
-- момент питання її вже нема). Пропозиції зберігають лише те, що пройшло
-- поріг; усе відкинуте (стікер із чужим номером, значення нижче порогу,
-- порожні поля) зникало разом із відповіддю. Тепер відповідь лягає сюди
-- цілком — читати `prediction`, коли модель «нічого не знайшла».
CREATE TABLE IF NOT EXISTS ai_autofill_runs (
    id          BIGSERIAL PRIMARY KEY,
    product_id  INTEGER,                 -- без FK: товар може зникнути, історія — ні
    purpose     VARCHAR(24) NOT NULL,     -- autofill / backfill / …:paid
    model       VARCHAR(64),
    photos      TEXT,                     -- імена знімків через кому
    ok          BOOLEAN NOT NULL DEFAULT TRUE,
    prediction  JSONB,                    -- сира відповідь моделі (без _usage)
    outcome     JSONB,                    -- proposed / below_threshold / sticker / …
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_autofill_runs_product ON ai_autofill_runs (product_id, created_at DESC);

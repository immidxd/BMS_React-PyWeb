-- Черга постів у канал BrandStore (services/telegram_channel_queue.py).
--
-- Досі `telegram_scheduled_posts` була лише памʼяткою: BMS віддавав форвард
-- Telegram із `schedule=` і більше про нього не дізнавався. Перевірка
-- 16.10.2026 показала, що 20 із 95 таких форвардів Telegram мовчки НЕ
-- відправив, хоча оригінал у форумі живий (у хвилі 10.09 — 16 із 18), а ліміт
-- 100 запланованих повідомлень на канал (кожне фото альбому окремо) не дає
-- поставити більше ~18 товарів наперед.
--
-- Тепер таблиця — справжня черга зі станами:
--   planned    — план живе лише в BMS (у розклад Telegram ще не поставлено);
--   queued     — стоїть у розкладі Telegram, tg_scheduled_ids відомі;
--   cancelling — треба прибрати з розкладу Telegram (продано / замінено / скасовано);
--   sent       — пост ПЕРЕВІРЕНО в каналі (channel_message_id);
--   failed     — вичерпано спроби або доставка неможлива — потрібна людина;
--   cancelled  — знято (продано, скасовано вручну).
-- Старий стан 'scheduled' лишається лише в історичних рядках до 16.10.2026.

ALTER TABLE telegram_scheduled_posts
    ADD COLUMN IF NOT EXISTS source_message_ids    JSONB,
    ADD COLUMN IF NOT EXISTS photo_count           INTEGER,
    ADD COLUMN IF NOT EXISTS silent                BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS tg_scheduled_ids      JSONB,
    ADD COLUMN IF NOT EXISTS channel_message_id    BIGINT,
    ADD COLUMN IF NOT EXISTS attempts              INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error            TEXT,
    ADD COLUMN IF NOT EXISTS needs_refresh         BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS sent_at               TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sent_via              VARCHAR(20),
    ADD COLUMN IF NOT EXISTS original_scheduled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at            TIMESTAMPTZ NOT NULL DEFAULT now();

-- Історичні рядки старого механізму (без tg_scheduled_ids): розкласти за
-- фактом — чи є товар у каналі за даними сканування. Нічого не надсилає.
-- Ідемпотентно: після першого прогону рядків у стані 'scheduled' не лишається.
UPDATE telegram_scheduled_posts s
SET state = CASE WHEN EXISTS (
            SELECT 1 FROM telegram_posts tp
            WHERE tp.chat_id = s.chat_id AND tp.product_id = s.product_id
        ) THEN 'sent' ELSE 'failed' END,
    last_error = CASE WHEN EXISTS (
            SELECT 1 FROM telegram_posts tp
            WHERE tp.chat_id = s.chat_id AND tp.product_id = s.product_id
        ) THEN NULL
        ELSE 'Telegram не відправив заплановане пересилання (старий механізм, до 16.10.2026)' END,
    original_scheduled_at = COALESCE(s.original_scheduled_at, s.scheduled_at),
    updated_at = now()
WHERE s.state = 'scheduled'
  AND s.tg_scheduled_ids IS NULL
  AND s.scheduled_at < now() - interval '1 hour';

CREATE INDEX IF NOT EXISTS idx_tg_sched_active
    ON telegram_scheduled_posts (scheduled_at)
    WHERE state IN ('planned', 'queued', 'cancelling');

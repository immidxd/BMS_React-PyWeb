-- Витрати на рекламу Meta: списання з картки, курс НБУ і стан вивантаження.
--
-- Чому ОКРЕМА таблиця, а не запис одразу в `advertising_expenses`
-- ─────────────────────────────────────────────────────────────
-- `advertising_expenses` — ДЗЕРКАЛО аркуша: парсер читає підпис «Витрати на
-- рекламу», а якщо підпису немає — рядок звідти ВИДАЛЯЄ. Тобто все, що
-- записати туди напряму, наступний парс зітре. Тому джерелом правди про саму
-- рекламу лишається таблиця нижче, а в аркуш ми лише ДОПИСУЄМО те, чого там
-- ще немає; звідти воно приїде в `advertising_expenses` наявним механізмом.
--
-- Чому джерело — ВИПИСКА БАНКУ, а не Meta
-- ───────────────────────────────────────
-- Власник просив «суми на моменти списання коштів». Виписка містить рівно ту
-- гривню, яку зняли; Meta ж знає лише долари й витрати на покази (кабінет
-- працює за порогом $87, а не щодня). Спроба перерахувати долари через курс
-- НБУ з фіксованою надбавкою виміряна й ВІДКИНУТА як основний шлях:
--     01.08 курс банку 44.8309 / НБУ 44.6916 → 0.3117%
--     15.08            44.8310 /     44.6988 → 0.2958%
--     20.08            44.8314 /     44.7006 → 0.2926%
--     30.08            44.8008 /     44.5445 → 0.5754%
-- Надбавка плаває вдвічі, тож будь-яке фіксоване число завищує або занижує.
-- Розрахунок за НБУ лишається ТІЛЬКИ для періодів, куди виписка не дістає, і
-- зберігається поруч із банківською сумою — щоб було видно, чого варте
-- наближення.
--
-- ⚠️ Meta списує з КІЛЬКОХ карток. Перевірено 01.09.2026: 20.08 гроші пішли з
-- білої ···2438, решта — з чорної ···6650. Сканувати треба всі рахунки, інакше
-- списання зникає безслідно.

-- ── Курс НБУ, кешований назавжди ────────────────────────────────────────────
-- Курс на минулу дату не міняється ніколи, тож перезапитувати його — марна
-- мережа й ризик: якщо НБУ недоступний, порахований раніше рядок має лишитись
-- відтворюваним.
CREATE TABLE IF NOT EXISTS nbu_rates (
    rate_date   DATE          NOT NULL,
    currency    VARCHAR(3)    NOT NULL,
    rate        NUMERIC(16,6) NOT NULL,
    fetched_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (rate_date, currency)
);

-- ── Налаштування ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meta_ads_config (
    id            SMALLINT PRIMARY KEY DEFAULT 1,
    account_id    VARCHAR(64),           -- рекламний кабінет Meta (act_…), довідково
    enabled       BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Використовуються ЛИШЕ на запасному шляху (немає рядка у виписці).
    -- Нуль у ПДВ не помилка: сума списання вже містить податки.
    vat_pct       NUMERIC(6,3) NOT NULL DEFAULT 0.000,
    bank_fee_pct  NUMERIC(7,4) NOT NULL DEFAULT 0.0000,
    backfill_from DATE,                  -- порожньо = шукати початок автоматично
    last_synced_at TIMESTAMPTZ,
    last_error     TEXT,
    last_error_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT meta_ads_config_singleton CHECK (id = 1)
);

INSERT INTO meta_ads_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── Поступ вивантаження виписки ─────────────────────────────────────────────
-- Personal API віддає 31 день за запит і приймає 1 запит на 60 секунд. Історія
-- в кілька років — це десятки хвилин очікування НА КОЖЕН рахунок, тож прогін
-- мусить переживати переривання: стан пишеться після КОЖНОГО вікна, і
-- наступний запуск продовжує з того ж місця, а не починає спочатку.
CREATE TABLE IF NOT EXISTS mono_sync_state (
    account_id     VARCHAR(64) PRIMARY KEY,
    masked_pan     VARCHAR(32),
    -- Найдавніше вікно, яке вже прочитано. Далі йдемо від нього назад.
    oldest_fetched DATE,
    newest_fetched DATE,
    -- Скільки вікон поспіль виявились БЕЗ ЖОДНОЇ операції. Кілька таких підряд
    -- означають, що картки тоді ще не існувало — далі копати немає сенсу.
    empty_streak   SMALLINT    NOT NULL DEFAULT 0,
    -- Історію цього рахунку пройдено до кінця; повторно не чіпаємо.
    exhausted      BOOLEAN     NOT NULL DEFAULT FALSE,
    windows_done   INTEGER     NOT NULL DEFAULT 0,
    charges_found  INTEGER     NOT NULL DEFAULT 0,
    last_run_at    TIMESTAMPTZ,
    last_error     TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Списання ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meta_ad_charges (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(16)   NOT NULL DEFAULT 'monobank',
    bank_account_id VARCHAR(64),
    -- Ключ ідемпотентності: id операції в банку. Повторний прогін того самого
    -- вікна нічого не дублює.
    transaction_id  VARCHAR(128)  NOT NULL,
    -- Номер квитанції банку — той самий, що надрукований у PDF
    -- («Квитанція № 9X42-18TP-5593-954H»). Дає звірити рядок із чеком.
    receipt_id      VARCHAR(64),
    charge_date     DATE          NOT NULL,
    charged_at      TIMESTAMPTZ,
    description     TEXT,
    mcc             INTEGER,
    -- ТОЧНА гривня з виписки. Саме вона потрапляє в аркуш.
    amount_uah      NUMERIC(16,2) NOT NULL,
    -- Скільки й у якій валюті зняла Meta (для звірки з її історією платежів).
    operation_amount   NUMERIC(16,4),
    operation_currency VARCHAR(3),
    -- Довідково: скільки вийшло б за курсом НБУ з надбавкою. Зберігається
    -- поруч НАВМИСНО — інакше неможливо побачити, чого варте наближення там,
    -- де виписки немає.
    nbu_rate        NUMERIC(16,6),
    nbu_amount_uah  NUMERIC(16,2),
    -- До якого ефіру віднесено. NULL = ефіру ще немає (списання пізніше за
    -- останній аркуш) — такий рядок НЕ втрачається, а чекає нового аркуша.
    air_date        DATE,
    sheet_gid       BIGINT,
    -- Аркуш із ручним значенням лишається 'skipped_manual' назавжди:
    -- чуже число не чіпаємо.
    write_status    VARCHAR(24)   NOT NULL DEFAULT 'pending',
    write_note      TEXT,
    written_at      TIMESTAMPTZ,
    raw_json        JSONB         NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT meta_ad_charges_unique UNIQUE (source, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_meta_ad_charges_air ON meta_ad_charges (air_date);
CREATE INDEX IF NOT EXISTS idx_meta_ad_charges_date ON meta_ad_charges (charge_date);
CREATE INDEX IF NOT EXISTS idx_meta_ad_charges_pending
    ON meta_ad_charges (write_status) WHERE write_status = 'pending';

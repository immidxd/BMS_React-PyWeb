-- Витрати на рекламу Meta: сирий журнал, курс НБУ і налаштування переліку.
--
-- Чому ОКРЕМА таблиця, а не запис одразу в `advertising_expenses`
-- ─────────────────────────────────────────────────────────────
-- `advertising_expenses` — ДЗЕРКАЛО аркуша: парсер читає підпис «Витрати на
-- рекламу», а якщо підпису немає — рядок звідти ВИДАЛЯЄ. Тобто все, що
-- записати туди напряму, наступний парс зітре. Тому джерелом правди про саму
-- рекламу лишається `meta_ad_spend`, а в аркуш ми лише ДОПИСУЄМО те, чого там
-- ще немає; звідти воно приїде в `advertising_expenses` наявним механізмом.
-- Так ланцюг лишається однонаправленим (аркуш → база), а не роздвоюється.

-- ── Курс НБУ, кешований назавжди ────────────────────────────────────────────
-- Курс на минулу дату не міняється ніколи, тож перезапитувати його — марна
-- мережа й ризик: якщо НБУ недоступний, порахований раніше рядок має лишитись
-- відтворюваним. Зберігаємо саме те, що віддав НБУ, без округлень.
CREATE TABLE IF NOT EXISTS nbu_rates (
    rate_date   DATE         NOT NULL,
    currency    VARCHAR(3)   NOT NULL,
    rate        NUMERIC(16,6) NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (rate_date, currency)
);

-- ── Налаштування: кабінет і надбавка над курсом НБУ ─────────────────────────
-- Meta віддає витрати у валюті кабінету ($). Скільки гривень зняв банк —
-- відомо лише банку, тому рахуємо: сума × курс НБУ на дату × (1+ПДВ) × (1+комісія).
-- Обидва відсотки — окремі поля НАВМИСНО: ПДВ 20% Meta додає зверху за
-- українським законом, а комісія — це вже надбавка банку до курсу. Змішувати їх
-- в одне число означало б, що при зміні одного не зрозуміло, що саме правити.
CREATE TABLE IF NOT EXISTS meta_ads_config (
    id            SMALLINT PRIMARY KEY DEFAULT 1,
    account_id    VARCHAR(64),           -- act_XXXXXXXXX
    enabled       BOOLEAN     NOT NULL DEFAULT FALSE,
    vat_pct       NUMERIC(6,3) NOT NULL DEFAULT 20.000,
    bank_fee_pct  NUMERIC(6,3) NOT NULL DEFAULT 0.000,
    backfill_from DATE,                  -- порожньо = вся історія, яку віддасть Meta
    last_synced_at TIMESTAMPTZ,
    last_error     TEXT,
    last_error_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT meta_ads_config_singleton CHECK (id = 1)
);

INSERT INTO meta_ads_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── Сирий журнал витрат ─────────────────────────────────────────────────────
-- Один рядок = одна кампанія за один день. Кілька кампаній в один день — це
-- кілька рядків, які потім підсумовуються в ОДИН аркуш ефіру; саме тому
-- унікальність тут по (кабінет, кампанія, дата), а не по даті.
CREATE TABLE IF NOT EXISTS meta_ad_spend (
    id              BIGSERIAL PRIMARY KEY,
    account_id      VARCHAR(64)  NOT NULL,
    campaign_id     VARCHAR(64)  NOT NULL,
    campaign_name   TEXT,
    spend_date      DATE         NOT NULL,
    amount          NUMERIC(16,4) NOT NULL,   -- у валюті кабінету, як віддала Meta
    currency        VARCHAR(3)   NOT NULL,
    -- Знімок розрахунку: курс і відсотки зберігаються РАЗОМ із результатом, щоб
    -- через півроку можна було пояснити кожну цифру, навіть якщо налаштування
    -- відтоді змінили.
    nbu_rate        NUMERIC(16,6),
    vat_pct         NUMERIC(6,3),
    bank_fee_pct    NUMERIC(6,3),
    amount_uah      NUMERIC(16,2),
    -- До якого ефіру віднесено. NULL = ефіру ще немає (реклама куплена після
    -- останнього аркуша) — такий рядок НЕ втрачається, а чекає на новий аркуш.
    air_date        DATE,
    sheet_gid       BIGINT,
    -- Чи вже дописано в аркуш. Аркуш із ручним значенням лишається
    -- 'skipped_manual' назавжди: чуже число не чіпаємо.
    write_status    VARCHAR(24)  NOT NULL DEFAULT 'pending',
    write_note      TEXT,
    written_at      TIMESTAMPTZ,
    raw_json        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT meta_ad_spend_unique_row UNIQUE (account_id, campaign_id, spend_date)
);

CREATE INDEX IF NOT EXISTS idx_meta_ad_spend_air ON meta_ad_spend (air_date);
CREATE INDEX IF NOT EXISTS idx_meta_ad_spend_pending
    ON meta_ad_spend (write_status) WHERE write_status = 'pending';

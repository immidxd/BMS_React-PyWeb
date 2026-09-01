-- Витрати на рекламу Meta: списання з картки, курс НБУ і налаштування.
--
-- Чому ОКРЕМА таблиця, а не запис одразу в `advertising_expenses`
-- ─────────────────────────────────────────────────────────────
-- `advertising_expenses` — ДЗЕРКАЛО аркуша: парсер читає підпис «Витрати на
-- рекламу», а якщо підпису немає — рядок звідти ВИДАЛЯЄ. Тобто все, що
-- записати туди напряму, наступний парс зітре. Тому джерелом правди про саму
-- рекламу лишається таблиця нижче, а в аркуш ми лише ДОПИСУЄМО те, чого там
-- ще немає; звідти воно приїде в `advertising_expenses` наявним механізмом.
--
-- Чому СПИСАННЯ, а не щоденні витрати кампаній
-- ────────────────────────────────────────────
-- Кабінет «Бренд Людмила» (act_660787891581345) працює за ПОРОГОМ: Meta
-- знімає гроші, коли баланс сягає $87, а не щодня. У налаштуваннях прямо
-- написано «Поточний баланс $4,47 + усі застосовні комісії» — тобто витрати на
-- покази й сума списання це РІЗНІ числа. Власник просив саме «суми на моменти
-- списання коштів», тож джерело грошей тут — транзакція: у ній податки вже
-- враховані, бо стільки Meta реально зняла з картки.
--
-- ⚠️ Кожне списання цього кабінету йде ПАРОЮ: спроба на одну картку зі статусом
-- «Помилка» і успішна на другу. Рахувати можна ЛИШЕ оплачені — інакше витрати
-- подвоюються. Саме тому статус тут окреме поле, а не відкинутий шум.

-- ── Курс НБУ, кешований назавжди ────────────────────────────────────────────
-- Курс на минулу дату не міняється ніколи, тож перезапитувати його — марна
-- мережа й ризик: якщо НБУ недоступний, порахований раніше рядок має лишитись
-- відтворюваним. Зберігаємо саме те, що віддав НБУ, без округлень.
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
    account_id    VARCHAR(64),           -- act_XXXXXXXXX
    enabled       BOOLEAN      NOT NULL DEFAULT FALSE,
    -- ⚠️ НУЛЬ, і це не недогляд. Ми беремо суму, яку Meta вже зняла з картки,
    -- тож податки в ній сидять. Додати сюди 20% означало б завищити витрати на
    -- п'яту частину. Поле лишається як аварійний важіль на випадок, якщо для
    -- якогось періоду Meta почне виставляти ПДВ окремим рядком.
    vat_pct       NUMERIC(6,3) NOT NULL DEFAULT 0.000,
    -- Надбавка банку до курсу НБУ. Скільки саме — знає лише виписка, тому
    -- значення задає власник; нуль означає «рахувати чистим курсом НБУ».
    bank_fee_pct  NUMERIC(6,3) NOT NULL DEFAULT 0.000,
    backfill_from DATE,                  -- порожньо = вся історія, яку віддасть Meta
    last_synced_at TIMESTAMPTZ,
    last_error     TEXT,
    last_error_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT meta_ads_config_singleton CHECK (id = 1)
);

INSERT INTO meta_ads_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── Списання з картки ───────────────────────────────────────────────────────
-- Один рядок = одна транзакція Meta. Кілька списань між двома ефірами
-- підсумовуються в ОДИН аркуш — саме тому унікальність по транзакції, а не по даті.
CREATE TABLE IF NOT EXISTS meta_ad_charges (
    id              BIGSERIAL PRIMARY KEY,
    account_id      VARCHAR(64)   NOT NULL,
    transaction_id  VARCHAR(128)  NOT NULL,
    charge_date     DATE          NOT NULL,
    amount          NUMERIC(16,4) NOT NULL,   -- у валюті кабінету, як віддала Meta
    currency        VARCHAR(3)    NOT NULL,
    -- 'paid' / 'failed' / інше. У гроші йде ЛИШЕ 'paid'.
    status          VARCHAR(32)   NOT NULL,
    payment_method  VARCHAR(64),
    vat_invoice_id  VARCHAR(64),
    -- Знімок розрахунку: курс і відсотки зберігаються РАЗОМ із результатом, щоб
    -- через півроку можна було пояснити кожну цифру, навіть якщо налаштування
    -- відтоді змінили.
    nbu_rate        NUMERIC(16,6),
    vat_pct         NUMERIC(6,3),
    bank_fee_pct    NUMERIC(6,3),
    amount_uah      NUMERIC(16,2),
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
    CONSTRAINT meta_ad_charges_unique UNIQUE (account_id, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_meta_ad_charges_air ON meta_ad_charges (air_date);
CREATE INDEX IF NOT EXISTS idx_meta_ad_charges_paid
    ON meta_ad_charges (charge_date) WHERE status = 'paid';

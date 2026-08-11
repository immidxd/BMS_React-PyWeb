-- Витрати на рекламу з підсумкового блоку датованих вкладок «Замовлення».
-- Одна вкладка = одна витрата; повторний парсинг оновлює той самий запис.
CREATE TABLE IF NOT EXISTS advertising_expenses (
    id                    SERIAL PRIMARY KEY,
    expense_date          DATE NOT NULL,
    amount                NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (amount >= 0),
    sales_channel         VARCHAR(50) NOT NULL DEFAULT 'Ефір',
    source_spreadsheet_id VARCHAR(128) NOT NULL,
    source_sheet_gid      BIGINT NOT NULL,
    source_sheet_title    VARCHAR(255),
    source_label_cell     VARCHAR(20),
    source_value_cell     VARCHAR(20),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_advertising_expense_sheet
        UNIQUE (source_spreadsheet_id, source_sheet_gid)
);

CREATE INDEX IF NOT EXISTS idx_advertising_expenses_date
    ON advertising_expenses (expense_date);

CREATE UNIQUE INDEX IF NOT EXISTS uq_advertising_expense_sheet
    ON advertising_expenses (source_spreadsheet_id, source_sheet_gid);

CREATE INDEX IF NOT EXISTS idx_advertising_expenses_channel_date
    ON advertising_expenses (sales_channel, expense_date);

COMMENT ON TABLE advertising_expenses IS
    'Дзеркало комірок «Витрати на рекламу» з датованих вкладок Google Sheets «Замовлення»';

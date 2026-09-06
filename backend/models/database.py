import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Отримання параметрів підключення з .env
DB_NAME = os.getenv("DB_NAME", "bsstorage")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

# Формування URL для PostgreSQL
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Configure logging
logger = logging.getLogger(__name__)
logger.info(f"Using database connection: {DATABASE_URL}")

# Create SQLAlchemy engine with connection pool limits
engine = create_engine(
    DATABASE_URL,
    # TCP-keepalive: «напіввідкриті» з'єднання (типово після сну Mac) вбиваються
    # за ~60с замість системних хвилин — pre_ping не висне на мертвому сокеті.
    connect_args=(
        {"keepalives": 1, "keepalives_idle": 30,
         "keepalives_interval": 10, "keepalives_count": 3,
         # ── Запобіжники проти «вічного зависання» ───────────────────────────
         # lock_timeout: НІКОЛИ не стоїмо в черзі за чужим блокуванням довше
         #   15с. Без нього один зовнішній клієнт із відкритою транзакцією
         #   (завислий cloud-sync) + будь-який DDL ставили в чергу ВЕСЬ UI —
         #   запити висіли хвилинами, аж до повної непрацездатності програми.
         # idle_in_transaction_session_timeout: наші власні «забуті» транзакції
         #   (фонові джоби, обірваний запит) самознімаються за 2 хв, а не
         #   тримають блокування нескінченно.
         # statement_timeout НЕ ставимо: важкий парсинг/бекфіли легально довгі.
         "options": (
             f"-c lock_timeout={os.getenv('DB_LOCK_TIMEOUT_MS', '15000')} "
             f"-c idle_in_transaction_session_timeout="
             f"{os.getenv('DB_IDLE_TX_TIMEOUT_MS', '120000')}"
         )}
        if DATABASE_URL.startswith("postgresql") else {"check_same_thread": False}
    ),
    echo=False,  # Вимкнено для зменшення навантаження
    # Синхронні обробники FastAPI виконуються в threadpool (типово 40 потоків) +
    # фонові джоби. Пул мусить це покривати, інакше запити чекають на pool_timeout
    # і UI знову «висне». 20+30=50 з'єднань при max_connections=200 — з запасом.
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,  # Таймаут очікування з'єднання
    pool_recycle=1800,  # Перестворювати з'єднання кожні 30 хв
    pool_pre_ping=True  # Перевіряти з'єднання перед використанням
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ⚠️ scoped_session тут БУТИ НЕ МАЄ (і немає — свідомо).
# Раніше get_db() віддавав `scoped_session(SessionLocal)`, прив'язану до
# ПОТОКУ. Але FastAPI виконує синхронну generator-залежність у пулі потоків
# AnyIO: вхід (`db_session()`) і вихід (`db.close()`) можуть потрапити в РІЗНІ
# воркери, а один воркер обслуговує послідовно різні запити. Через це та сама
# Session видавалась одночасно кільком запитам «у польоті» (виміряно: ~24%
# запитів на старті), і db.close() одного запиту накладався на запит іншого:
#   IllegalStateChangeError: Method 'close()' can't be called here;
#   method '_connection_for_bind()' is already in progress
# Симптом — перші секунди після старту частина запитів (напр. /api/products/filters)
# падала в 500, далі «саме собою» стабілізувалось.
# Правило: кожен запит і кожен фоновий джоб створює ВЛАСНУ Session через
# SessionLocal() і гарантовано її закриває.

# Create base class for models
Base = declarative_base()

def get_db():
    """Session на ОДИН запит. Новий об'єкт щоразу — жодного спільного стану
    між паралельними запитами (див. коментар про scoped_session вище)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initialize database with tables and initial data"""
    try:
        # Import all models to ensure they are registered with Base
        from models import models  # noqa

        # Create all tables
        Base.metadata.create_all(bind=engine)
        # Ensure new columns exist in existing DB without full migrations
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("alter table if exists parsing_jobs add column if not exists logs_head text"))
            # supplier_aliases — аналог brand_aliases: зберігає злиті/видалені назви постачальників
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS supplier_aliases (
                    id SERIAL PRIMARY KEY,
                    alias_name VARCHAR(200) UNIQUE NOT NULL,
                    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_supplier_aliases_supplier_id ON supplier_aliases(supplier_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_oi_order_id ON order_items(order_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_oi_product_id_price ON order_items(product_id, price) WHERE price > 0"))
            conn.execute(text("alter table if exists parsing_jobs add column if not exists cancel_requested boolean default false"))
            conn.execute(text("alter table if exists deliveries add column if not exists delivery_cost numeric(12,2) default 0"))
            conn.execute(text("alter table if exists deliveries add column if not exists purchase_cost numeric(12,2) default 0"))
            conn.execute(text("alter table if exists orders add column if not exists sales_channel varchar(50) default 'Ефір'"))
            conn.execute(text("alter table if exists orders add column if not exists source_fingerprint varchar(64)"))
            conn.execute(text("create index if not exists ix_orders_source_fingerprint on orders (source_fingerprint)"))
            conn.execute(text("DROP INDEX IF EXISTS uix_products_num_size"))
            # ⚠️ БУКВЕНИЙ РОЗМІР — ЧАСТИНА КЛЮЧА. Без нього два рядки журналу
            # під одним номером, XL і M, стикались тут: у одягу `sizeeu`
            # порожній з обох боків. Парсер ловив IntegrityError і замість
            # другого рядка піднімав `quantity` наявному, через що #Ф4384
            # показувався як «XL ×2», а M зникав. Старий індекс прибираємо, щоб
            # наявні бази зійшлись на новому.
            conn.execute(text("drop index if exists uix_products_num_size_color"))
            conn.execute(text("create unique index if not exists uix_products_num_size_color_letter on products (productnumber, COALESCE(sizeeu, ''), COALESCE(size_letter, ''), COALESCE(colorid, 0))"))
            # Розширюємо поля довідників — в Google Sheets значення можуть бути довшими за 50 символів
            conn.execute(text("alter table if exists colors alter column colorname type varchar(100)"))
            conn.execute(text("alter table if exists brands alter column brandname type varchar(150)"))
            conn.execute(text("alter table if exists types alter column typename type varchar(100)"))
            conn.execute(text("alter table if exists subtypes alter column subtypename type varchar(100)"))
            conn.execute(text("alter table if exists conditions alter column conditionname type varchar(100)"))
            # phone_number varchar(20) → 255 (Google Sheets іноді містить URL замість телефону).
            # ⚠️ Ідемпотентно: ALTER лише коли колонка ВУЖЧА за 255. Інакше на вже
            # розширеній БД (відновлений дамп) Postgres падає
            # «cannot alter type of a column used in a trigger definition»
            # (trg_clients_sync_to_contacts залежить від phone_number) і відкочує
            # весь блок init_db. Перевірка довжини оминає зайвий ALTER → конфлікту нема.
            conn.execute(text("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'clients' AND column_name = 'phone_number'
                          AND (character_maximum_length IS NULL OR character_maximum_length < 255)
                    ) THEN
                        ALTER TABLE clients ALTER COLUMN phone_number TYPE varchar(255);
                    END IF;
                END $$;
            """))
            # brand_concerns — групування брендів за консерном/компанією-власником
            conn.execute(text("""CREATE TABLE IF NOT EXISTS brand_concerns (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) UNIQUE NOT NULL,
                country VARCHAR(100),
                description TEXT
            )"""))
            conn.execute(text("ALTER TABLE IF EXISTS brands ADD COLUMN IF NOT EXISTS concern_id INTEGER REFERENCES brand_concerns(id) ON DELETE SET NULL"))
            # brand_aliases — зберігає назви зливених брендів для парсера
            conn.execute(text("""CREATE TABLE IF NOT EXISTS brand_aliases (
                id SERIAL PRIMARY KEY,
                alias_name VARCHAR(200) UNIQUE NOT NULL,
                brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE
            )"""))
            # supplier_groups — групування постачальників за компанією-власником
            conn.execute(text("""CREATE TABLE IF NOT EXISTS supplier_groups (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) UNIQUE NOT NULL,
                country VARCHAR(100),
                description TEXT
            )"""))
            conn.execute(text("ALTER TABLE IF EXISTS suppliers ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES supplier_groups(id) ON DELETE SET NULL"))
            # nickname — для клієнтів з нікнеймами замість реальних імен
            conn.execute(text("alter table if exists clients add column if not exists nickname varchar(255)"))
            # orders.client_id — дозволити NULL (анонімні покупки, продаж в магазині)
            conn.execute(text("alter table if exists orders alter column client_id drop not null"))
            # Знімаємо обмеження total_amount >= 0: повернення/знижки можуть мати від'ємну суму
            conn.execute(text("alter table if exists orders drop constraint if exists orders_total_amount_check"))
            # Таблиці для системи кольорових груп
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS color_groups (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50) UNIQUE NOT NULL,
                    hex_code VARCHAR(7),
                    display_order INT DEFAULT 0
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS color_group_members (
                    color_id INT REFERENCES colors(id) ON DELETE CASCADE,
                    group_id INT REFERENCES color_groups(id) ON DELETE CASCADE,
                    PRIMARY KEY (color_id, group_id)
                )
            """))
            # brand_blocklist — типи взуття, які помилково потрапили в бренди.
            # НЕ є SQLAlchemy-моделлю → create_all її не створює. На свіжій БД
            # таблиця існує лише у migrations/2025_08_13_001_add_brand_normalization.sql,
            # тож створюємо ідемпотентно ТУТ, до першого INSERT (інакше init_db падає
            # на порожній базі: UndefinedTable "brand_blocklist").
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS brand_blocklist (
                    normalized_name text PRIMARY KEY,
                    reason text,
                    created_at timestamptz DEFAULT now()
                )
            """))
            conn.execute(text("""
                INSERT INTO brand_blocklist (normalized_name, reason) VALUES
                    ('кросівки', 'тип взуття'), ('кросівкі', 'тип взуття'),
                    ('черевики', 'тип взуття'), ('туфлі', 'тип взуття'),
                    ('босоніжки', 'тип взуття'), ('кеди', 'тип взуття'),
                    ('чоботи', 'тип взуття'), ('сандалі', 'тип взуття'),
                    ('шльопанці', 'тип взуття'), ('крос боді', 'тип взуття'),
                    ('кросівки жіночі', 'тип взуття'), ('кросівки чоловічі', 'тип взуття')
                ON CONFLICT DO NOTHING
            """))
            # Очистити products від заблокованих брендів
            conn.execute(text("""
                UPDATE products SET brandid = NULL
                WHERE brandid IN (
                    SELECT b.id FROM brands b
                    JOIN brand_blocklist bl ON lower(b.brandname) = bl.normalized_name
                )
            """))
            conn.execute(text("""
                DELETE FROM brands
                WHERE lower(brandname) IN (SELECT normalized_name FROM brand_blocklist)
            """))
            # Таблиця telegram_posts — для синхронізації постів з Telegram
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS telegram_posts (
                    id SERIAL PRIMARY KEY,
                    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                    product_number_raw VARCHAR(50),
                    chat_id BIGINT NOT NULL,
                    chat_title VARCHAR(200),
                    chat_type VARCHAR(20),                -- 'channel' | 'forum' | 'archive'
                    thread_id INTEGER,                    -- topic_id для форумів
                    thread_title VARCHAR(200),
                    message_id BIGINT NOT NULL,
                    message_text TEXT,
                    message_date TIMESTAMP,
                    is_master BOOLEAN DEFAULT false,      -- чи це головна гілка
                    tg_status VARCHAR(50) DEFAULT 'published',  -- 'published' | 'archived' | 'deleted'
                    sizes_in_post TEXT,                   -- JSON array розмірів у пості
                    is_multi_size BOOLEAN DEFAULT false,  -- multi-size пост
                    detected_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(chat_id, message_id)
                )
            """))
            # Migrate existing tables (add new columns if missing)
            # «Загублені» товари (Воркспейс/Старі) — кандидати на пошук оригіналу (Фаза 3)
            conn.execute(text("ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS is_lost BOOLEAN DEFAULT false"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_products_is_lost ON products(is_lost) WHERE is_lost = true"))
            # Persistent merge-рішення зі СТАБІЛЬНИМ ключем (переживають ре-парс/зміну product.id)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS merge_decisions (
                    id SERIAL PRIMARY KEY,
                    lost_key TEXT NOT NULL,
                    original_key TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_merge_decisions ON merge_decisions(lost_key, original_key)"))
            conn.execute(text("ALTER TABLE IF EXISTS telegram_posts ADD COLUMN IF NOT EXISTS sizes_in_post TEXT"))
            conn.execute(text("ALTER TABLE IF EXISTS telegram_posts ADD COLUMN IF NOT EXISTS is_multi_size BOOLEAN DEFAULT false"))
            conn.execute(text("ALTER TABLE IF EXISTS telegram_posts ADD COLUMN IF NOT EXISTS grouped_id BIGINT"))
            conn.execute(text("ALTER TABLE IF EXISTS telegram_posts ADD COLUMN IF NOT EXISTS needs_manual_edit BOOLEAN DEFAULT false"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_posts_product ON telegram_posts(product_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_posts_number ON telegram_posts(product_number_raw)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_posts_chat ON telegram_posts(chat_id, thread_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_posts_multi ON telegram_posts(is_multi_size) WHERE is_multi_size = true"))
            # Per-size mapping для multi-size постів — кожен розмір -> свій product_id
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS telegram_post_sizes (
                    id SERIAL PRIMARY KEY,
                    telegram_post_id INTEGER NOT NULL REFERENCES telegram_posts(id) ON DELETE CASCADE,
                    size_eu VARCHAR(20) NOT NULL,
                    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                    is_sold BOOLEAN DEFAULT false,
                    UNIQUE(telegram_post_id, size_eu)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_post_sizes_post ON telegram_post_sizes(telegram_post_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_post_sizes_product ON telegram_post_sizes(product_id)"))
            # Таблиця thread_mapping — для визначення гілок форуму за типом товара
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS telegram_thread_mapping (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT,
                    thread_id INTEGER,
                    thread_title VARCHAR(200),
                    type_id INTEGER REFERENCES types(id) ON DELETE SET NULL,
                    subtype_id INTEGER REFERENCES subtypes(id) ON DELETE SET NULL,
                    gender_id INTEGER REFERENCES genders(id) ON DELETE SET NULL,
                    is_master BOOLEAN DEFAULT false,
                    UNIQUE(chat_id, thread_id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_mapping_type ON telegram_thread_mapping(type_id)"))

            # ── Адресна книга клієнта (готова під НП/УП API) ──────────────────
            # Окрема таблиця, не плутаємо з addresses (snapshot per-order).
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS client_addresses (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    label VARCHAR(64),
                    delivery_type VARCHAR(20) NOT NULL DEFAULT 'np_warehouse',
                    recipient_name VARCHAR(255),
                    recipient_phone VARCHAR(50),
                    -- Геогр. поля + refs для майбутнього API
                    city VARCHAR(255),
                    city_ref VARCHAR(64),
                    region VARCHAR(255),
                    warehouse_number VARCHAR(20),
                    warehouse_ref VARCHAR(64),
                    street VARCHAR(255),
                    building VARCHAR(64),
                    apartment VARCHAR(32),
                    postal_code VARCHAR(20),
                    -- Метадані
                    is_primary BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    source VARCHAR(20) DEFAULT 'manual',
                    source_order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
                    fingerprint VARCHAR(32),
                    usage_count INTEGER DEFAULT 0,
                    last_used_at TIMESTAMP,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_addr_client ON client_addresses(client_id)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_client_addr_primary ON client_addresses(client_id) WHERE is_primary = TRUE"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_addr_fp ON client_addresses(client_id, fingerprint)"))

            # ── Звʼязки між клієнтами ("разом замовляють", родичі, друзі) ────
            # client_relations:   per-pair metadata (1 рядок на напрямок A→B)
            # client_relation_orders: junction — стійка до повторного парсингу
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS client_relations (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    related_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    relation_type VARCHAR(20) NOT NULL DEFAULT 'together',
                    -- together | family | friend | spouse | other
                    label VARCHAR(100),
                    source VARCHAR(20) DEFAULT 'order_import',
                    -- order_import | manual
                    confirmed BOOLEAN DEFAULT FALSE,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    CONSTRAINT chk_client_relations_no_self CHECK (client_id <> related_id),
                    CONSTRAINT uq_client_relations_pair UNIQUE (client_id, related_id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_relations_client ON client_relations(client_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_relations_related ON client_relations(related_id)"))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS client_relation_orders (
                    relation_id INTEGER NOT NULL REFERENCES client_relations(id) ON DELETE CASCADE,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    noted_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (relation_id, order_id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_rel_orders_order ON client_relation_orders(order_id)"))

            # ── Identity & Aliases (Step 4) ──────────────────────────────────
            # 1) Залізна фіксація ручних редагувань
            conn.execute(text(
                "ALTER TABLE IF EXISTS clients ADD COLUMN IF NOT EXISTS manually_edited_at TIMESTAMP"
            ))
            conn.execute(text(
                "ALTER TABLE IF EXISTS clients ADD COLUMN IF NOT EXISTS manually_edited_fields TEXT"
            ))
            # Дівоче прізвище (для жіночих профілів) — альтернативний пошуковий ключ.
            conn.execute(text(
                "ALTER TABLE IF EXISTS clients ADD COLUMN IF NOT EXISTS maiden_name TEXT"
            ))
            # Карусель дублікатів: збереження «це різні люди» (щоб пари не пропонувалися знов).
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS client_non_duplicates (
                  id SERIAL PRIMARY KEY,
                  client_a_id INTEGER NOT NULL,
                  client_b_id INTEGER NOT NULL,
                  dismissed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  dismissed_by VARCHAR(100),
                  note TEXT,
                  CONSTRAINT client_non_dup_ordered CHECK (client_a_id < client_b_id),
                  CONSTRAINT uq_client_non_dup UNIQUE (client_a_id, client_b_id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cnd_a ON client_non_duplicates(client_a_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cnd_b ON client_non_duplicates(client_b_id)"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_clients_maiden_name "
                "ON clients(LOWER(maiden_name)) WHERE maiden_name IS NOT NULL"
            ))
            # 1.5) Identity normalization (Step 5):
            #     canonical форми сильних сигналів → 1 запис на людину навіть
            #     якщо FB / phone / IG записані по-різному.
            for col in ("phone_normalized", "facebook_normalized",
                        "instagram_normalized", "telegram_normalized"):
                conn.execute(text(
                    f"ALTER TABLE IF EXISTS clients ADD COLUMN IF NOT EXISTS {col} TEXT"
                ))
            # Partial UNIQUE indexes — race-condition guard для паралельного парсингу.
            for col in ("phone_normalized", "facebook_normalized",
                        "instagram_normalized", "telegram_normalized"):
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_{col} "
                    f"ON clients({col}) WHERE {col} IS NOT NULL"
                ))
            # 2) Історія всіх варіантів імен/нікнеймів цього клієнта.
            #    Парсер шукає кандидата ПО aliases — тому навіть якщо ти змінив
            #    "Льоша (Балу)" → "Льоша", оригінал залишиться як alias і
            #    майбутні рядки з Sheets все одно прив'яжуться до того ж клієнта.
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS client_aliases (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    first_name VARCHAR(255),
                    last_name VARCHAR(255),
                    nickname VARCHAR(255),
                    full_raw VARCHAR(500),
                    -- key для UNIQUE: lower(coalesce(first,'')) || '|' || lower(coalesce(last,'')) || '|' || lower(coalesce(nick,''))
                    norm_key VARCHAR(500) NOT NULL,
                    source VARCHAR(20) NOT NULL DEFAULT 'parser',
                    -- parser | manual_edit_history | merge | initial_backfill
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TIMESTAMP DEFAULT NOW(),
                    last_seen_at TIMESTAMP DEFAULT NOW(),
                    CONSTRAINT uq_client_aliases UNIQUE (client_id, norm_key)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_aliases_client ON client_aliases(client_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_aliases_norm ON client_aliases(norm_key)"))
            # UNIQUE (client_id, norm_key) — модель ClientAlias НЕ оголошує цей
            # constraint, тож на свіжій БД create_all робить таблицю БЕЗ нього, а
            # inline CREATE TABLE вище стає no-op (таблиця вже є). Без цього
            # ON CONFLICT (client_id, norm_key) (backfill нижче + upsert парсера)
            # падає. Унікальний індекс задовольняє ON CONFLICT-інференс; на
            # бойовій БД ім'я вже зайняте constraint-індексом → IF NOT EXISTS no-op.
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_client_aliases "
                "ON client_aliases(client_id, norm_key)"
            ))

            # 3) Прапорці клієнтів (підсвітка проблемних/невідповідних)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS client_flags (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    flag_type VARCHAR(40) NOT NULL,
                    -- possible_duplicate | ambiguous_name_at_parse | phone_mismatch_with_alias | merged_into
                    severity VARCHAR(10) NOT NULL DEFAULT 'warn',
                    -- info | warn | error
                    peer_client_ids INTEGER[],
                    details TEXT,
                    dismissed BOOLEAN DEFAULT FALSE,
                    dismissed_at TIMESTAMP,
                    dismissed_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_flags_client ON client_flags(client_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_client_flags_active ON client_flags(client_id) WHERE dismissed = FALSE"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_client_flags_active "
                "ON client_flags(client_id, flag_type) WHERE dismissed = FALSE"
            ))

        # ── Застосувати версіоновані SQL-міграції (backend/migrations/*.sql) ─
        # create_all + inline-DDL вище дають лише ~54 таблиці. Решта схеми
        # (client_contacts, sheet_sync_state, merge_candidates, olx_adverts,
        # olx_oauth, catalog_listings, unmapped_materials + сіди lookup-ів)
        # живе у версіонованих .sql-міграціях, які НІХТО не застосовував на
        # свіжій БД — тому автономний вузол піднімався з неповною схемою.
        # Проганяємо їх ТУТ у порядку імені (дата-префікс = порядок історії):
        #   • кожен файл — окрема транзакція (engine.begin),
        #   • exec_driver_sql — сирий SQL повз bind-парсер `:` (do $$…$$, ::cast),
        #   • помилка одного файлу логується й НЕ валить старт,
        #   • файли ідемпотентні (IF NOT EXISTS / ON CONFLICT) → на бойовій БД
        #     це майже no-op, повторні старти безпечні.
        try:
            import glob
            mig_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "migrations",
            )
            sql_files = sorted(glob.glob(os.path.join(mig_dir, "*.sql")))
            applied = 0
            for _path in sql_files:
                _name = os.path.basename(_path)
                try:
                    with open(_path, "r", encoding="utf-8") as _fh:
                        _raw_sql = _fh.read()
                    if not _raw_sql.strip():
                        continue
                    # Сирий DBAPI-cursor БЕЗ params: psycopg2 робить '%'-інтерполяцію
                    # лише коли передано другий аргумент. Міграції містять літеральні
                    # '%I'/'%s' (plpgsql format()), тож params НЕ передаємо — інакше
                    # psycopg2 сприйме їх як плейсхолдери й впаде.
                    _raw = engine.raw_connection()
                    try:
                        _cur = _raw.cursor()
                        _cur.execute(_raw_sql)
                        _raw.commit()
                        _cur.close()
                    except Exception:
                        _raw.rollback()
                        raise
                    finally:
                        _raw.close()
                    applied += 1
                except Exception as _me:  # noqa: BLE001
                    logger.warning("SQL-міграція %s пропущена: %s", _name, _me)
            logger.info("SQL-міграції застосовано: %d/%d", applied, len(sql_files))
        except Exception as _e:  # noqa: BLE001
            logger.warning("SQL migrations runner skipped: %s", _e)

        # ── Однократний backfill звʼязків з історії "разом з ..." ──────────
        # Idempotent: ON CONFLICT DO NOTHING/DO UPDATE; пропускає вже наявні.
        # Виконується тільки якщо junction-таблиця порожня (швидкий no-op
        # при наступних стартах).
        try:
            with engine.connect() as conn:
                empty = conn.execute(text(
                    "SELECT NOT EXISTS (SELECT 1 FROM client_relation_orders LIMIT 1)"
                )).scalar()
                if empty:
                    logger.info("Backfilling client_relations from historical orders…")
                    from scripts.sheets_parser import _link_together_partners as _link_together
                    rows = conn.execute(text("""
                        SELECT id, client_id, notes FROM orders
                         WHERE notes ILIKE '%%разом з%%' AND client_id IS NOT NULL
                    """)).fetchall()
                    # Use a session for the upsert helper
                    from sqlalchemy.orm import Session as _Session
                    with _Session(bind=engine) as _s:
                        n = 0
                        for r in rows:
                            try:
                                _link_together(_s, r.id, r.client_id, r.notes or "")
                                n += 1
                            except Exception as _be:  # noqa: BLE001
                                logger.warning("backfill skip order=%s: %s", r.id, _be)
                        _s.commit()
                    logger.info("Backfill done: scanned %s orders", n)
        except Exception as _e:  # noqa: BLE001
            logger.warning("client_relations backfill skipped: %s", _e)

        # ── Identity backfill (Step 4) ─────────────────────────────────────
        # 1) Створити перший alias для кожного клієнта (norm_key з його імен).
        # 2) Просканувати потенційні дублікати за нормалізованим іменем →
        #    додати warn-flags для ручного огляду.
        # Idempotent: ON CONFLICT DO NOTHING + skip коли в aliases вже є рядок.
        try:
            with engine.begin() as conn:
                # 1) Initial alias backfill
                conn.execute(text("""
                    INSERT INTO client_aliases
                        (client_id, first_name, last_name, nickname, full_raw,
                         norm_key, source, seen_count)
                    SELECT
                        c.id,
                        NULLIF(c.first_name,''),
                        NULLIF(c.last_name,''),
                        NULLIF(c.nickname,''),
                        TRIM(BOTH ' ' FROM
                            COALESCE(c.first_name,'') || ' ' ||
                            COALESCE(c.last_name,'')  ||
                            CASE WHEN COALESCE(c.nickname,'') <> ''
                                 THEN ' (' || c.nickname || ')' ELSE '' END
                        ) AS full_raw,
                        lower(COALESCE(c.first_name,'')) || '|' ||
                        lower(COALESCE(c.last_name,''))  || '|' ||
                        lower(COALESCE(c.nickname,''))   AS norm_key,
                        'initial_backfill', 1
                    FROM clients c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM client_aliases ca WHERE ca.client_id = c.id
                    )
                    ON CONFLICT (client_id, norm_key) DO NOTHING
                """))

                # 2) Duplicate scan — однакові name-комбінації між РІЗНИМИ клієнтами
                #    Ставимо possible_duplicate flags (тільки якщо ще не існує active).
                conn.execute(text("""
                    WITH groups AS (
                        SELECT
                            lower(COALESCE(first_name,'')) || '|' ||
                            lower(COALESCE(last_name,''))  || '|' ||
                            lower(COALESCE(nickname,''))   AS k,
                            array_agg(id ORDER BY id) AS ids
                        FROM clients
                        WHERE COALESCE(first_name,'') || COALESCE(last_name,'') || COALESCE(nickname,'') <> ''
                        GROUP BY 1
                        HAVING COUNT(*) > 1
                    )
                    INSERT INTO client_flags (client_id, flag_type, severity, peer_client_ids, details)
                    SELECT
                        cid, 'possible_duplicate', 'warn',
                        (SELECT array_agg(x) FROM unnest(g.ids) x WHERE x <> cid),
                        'Auto-detected by identical normalized name on startup backfill'
                    FROM groups g, unnest(g.ids) AS cid
                    ON CONFLICT DO NOTHING
                """))
        except Exception as _e:  # noqa: BLE001
            logger.warning("identity/aliases backfill skipped: %s", _e)

        # 3) Identity-normalized backfill (Step 5):
        #    For any client where *_normalized is NULL but raw value exists,
        #    compute the canonical form via Python normalizer. Idempotent.
        try:
            from utils.identity_normalizer import (
                normalize_phone, normalize_facebook,
                normalize_instagram, normalize_telegram,
            )
            with engine.begin() as conn:
                rows = conn.execute(text("""
                    SELECT id, phone_number, facebook, instagram, telegram
                    FROM clients
                    WHERE (phone_number    IS NOT NULL AND phone_normalized    IS NULL)
                       OR (facebook        IS NOT NULL AND facebook_normalized IS NULL)
                       OR (instagram       IS NOT NULL AND instagram_normalized IS NULL)
                       OR (telegram        IS NOT NULL AND telegram_normalized  IS NULL)
                """)).fetchall()
                for r in rows:
                    conn.execute(text("""
                        UPDATE clients
                        SET phone_normalized=:p, facebook_normalized=:f,
                            instagram_normalized=:i, telegram_normalized=:t
                        WHERE id=:id
                    """), {
                        'p': normalize_phone(r[1]), 'f': normalize_facebook(r[2]),
                        'i': normalize_instagram(r[3]), 't': normalize_telegram(r[4]),
                        'id': r[0],
                    })
                if rows:
                    logger.info("Backfilled normalized signals for %d clients", len(rows))
        except Exception as _e:  # noqa: BLE001
            logger.warning("normalized signals backfill skipped: %s", _e)

        # Populate initial reference data (only adds basic reference data, no test data)
        from .seed_data import populate_initial_data
        # Власна сесія з гарантованим close: `next(get_db())` лишав генератор
        # незакритим, тож Session (і з'єднання з пулу) висіли до GC.
        db = SessionLocal()
        try:
            populate_initial_data(db)
        finally:
            db.close()

        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise 
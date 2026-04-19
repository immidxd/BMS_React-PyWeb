import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
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
    connect_args={} if DATABASE_URL.startswith("postgresql") else {"check_same_thread": False},
    echo=False,  # Вимкнено для зменшення навантаження
    pool_size=5,  # Основний пул з'єднань
    max_overflow=10,  # Максимум додаткових з'єднань
    pool_timeout=30,  # Таймаут очікування з'єднання
    pool_recycle=1800,  # Перестворювати з'єднання кожні 30 хв
    pool_pre_ping=True  # Перевіряти з'єднання перед використанням
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create scoped session for thread safety
db_session = scoped_session(SessionLocal)

# Create base class for models
Base = declarative_base()
Base.query = db_session.query_property()

def get_db():
    """Get database session"""
    db = db_session()
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
            conn.execute(text("create unique index if not exists uix_products_num_size_color on products (productnumber, COALESCE(sizeeu, ''), COALESCE(colorid, 0))"))
            # Розширюємо поля довідників — в Google Sheets значення можуть бути довшими за 50 символів
            conn.execute(text("alter table if exists colors alter column colorname type varchar(100)"))
            conn.execute(text("alter table if exists brands alter column brandname type varchar(150)"))
            conn.execute(text("alter table if exists types alter column typename type varchar(100)"))
            conn.execute(text("alter table if exists subtypes alter column subtypename type varchar(100)"))
            conn.execute(text("alter table if exists conditions alter column conditionname type varchar(100)"))
            # phone_number varchar(20) → 255 (Google Sheets іноді містить URL замість телефону)
            conn.execute(text("alter table if exists clients alter column phone_number type varchar(255)"))
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
            # brand_blocklist — типи взуття, які помилково потрапили в бренди
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

        # Populate initial reference data (only adds basic reference data, no test data)
        from .seed_data import populate_initial_data
        db = next(get_db())
        populate_initial_data(db)
        
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise 
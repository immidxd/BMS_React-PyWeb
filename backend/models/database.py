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
            conn.execute(text("alter table if exists parsing_jobs add column if not exists cancel_requested boolean default false"))
            conn.execute(text("alter table if exists deliveries add column if not exists delivery_cost numeric(12,2) default 0"))
            conn.execute(text("alter table if exists orders add column if not exists sales_channel varchar(50) default 'Ефір'"))
            conn.execute(text("alter table if exists orders add column if not exists source_fingerprint varchar(64)"))
            conn.execute(text("create index if not exists ix_orders_source_fingerprint on orders (source_fingerprint)"))
            conn.execute(text("create unique index if not exists uix_products_num_size on products (productnumber, COALESCE(sizeeu, ''))"))
            # Розширюємо поля довідників — в Google Sheets значення можуть бути довшими за 50 символів
            conn.execute(text("alter table if exists colors alter column colorname type varchar(100)"))
            conn.execute(text("alter table if exists brands alter column brandname type varchar(150)"))
            conn.execute(text("alter table if exists types alter column typename type varchar(100)"))
            conn.execute(text("alter table if exists subtypes alter column subtypename type varchar(100)"))
            conn.execute(text("alter table if exists conditions alter column conditionname type varchar(100)"))
            # phone_number varchar(20) → 255 (Google Sheets іноді містить URL замість телефону)
            conn.execute(text("alter table if exists clients alter column phone_number type varchar(255)"))
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

        
        # Populate initial reference data (only adds basic reference data, no test data)
        from .seed_data import populate_initial_data
        db = next(get_db())
        populate_initial_data(db)
        
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise 
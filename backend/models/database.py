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

# Create SQLAlchemy engine
engine = create_engine(
    DATABASE_URL, 
    connect_args={} if DATABASE_URL.startswith("postgresql") else {"check_same_thread": False},
    echo=True  # Set to True for SQL query logging
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
        from backend.models import models  # noqa

        # Create all tables
        Base.metadata.create_all(bind=engine)
        
        # Populate initial reference data (only adds basic reference data, no test data)
        from .seed_data import populate_initial_data
        db = next(get_db())
        populate_initial_data(db)
        
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise 
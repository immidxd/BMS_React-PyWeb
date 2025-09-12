#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
БЕЗПЕЧНИЙ ПАРСЕР
Мінімальне використання БД з'єднань для критичних ситуацій
"""

import os
import sys
import time
import logging
import psycopg2
from contextlib import contextmanager
from dotenv import load_dotenv

# Додаємо шлях до backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Параметри підключення
DB_NAME = os.getenv("DB_NAME", "bsstorage")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

@contextmanager
def get_single_connection():
    """Контекстний менеджер для одиночного з'єднання."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=10
        )
        yield conn
    except Exception as e:
        logger.error(f"Помилка з'єднання: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def test_connection():
    """Тестує з'єднання з БД."""
    try:
        with get_single_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            logger.info("✅ З'єднання з БД працює")
            return True
    except Exception as e:
        logger.error(f"❌ З'єднання з БД не працює: {e}")
        return False

def safe_products_import():
    """Безпечний імпорт товарів з мінімальним використанням з'єднань."""
    logger.info("📦 БЕЗПЕЧНИЙ ІМПОРТ ТОВАРІВ")
    
    if not test_connection():
        return False
    
    try:
        # Імпортуємо тільки необхідні модулі
        from googlesheets_pars import get_google_sheet_client
        
        # Отримуємо Google Sheets клієнт
        client = get_google_sheet_client()
        if not client:
            logger.error("Не вдалося підключитися до Google Sheets")
            return False
        
        # Відкриваємо документ
        doc_name = os.getenv("GOOGLE_SHEETS_DOCUMENT_NAME", "Журнал")
        doc = client.open(doc_name)
        sheets = doc.worksheets()
        
        logger.info(f"Знайдено {len(sheets)} аркушів")
        
        # Обробляємо по одному аркушу з закриттям з'єднання
        processed = 0
        for i, sheet in enumerate(sheets[:5], 1):  # Обмежуємо до 5 аркушів для тесту
            if sheet.title in ['Suppliers', 'Publications', 'New']:
                continue
                
            logger.info(f"Обробка аркуша {i}: {sheet.title}")
            
            try:
                with get_single_connection() as conn:
                    cursor = conn.cursor()
                    
                    # Отримуємо дані аркуша
                    data = sheet.get_all_values()
                    if len(data) < 2:
                        continue
                    
                    # Простий імпорт (без складної логіки)
                    headers = data[0]
                    rows = data[1:10]  # Тільки перші 10 рядків для тесту
                    
                    for row in rows:
                        if len(row) > 0 and row[0]:  # Перевіряємо номер товару
                            # Простий INSERT без складної логіки
                            try:
                                cursor.execute("""
                                    INSERT INTO products (productnumber, description, price, statusid)
                                    VALUES (%s, %s, %s, %s)
                                    ON CONFLICT (productnumber) DO NOTHING
                                """, (
                                    row[0][:50],  # Номер товару
                                    row[5][:200] if len(row) > 5 else '',  # Опис
                                    1000.0,  # Дефолтна ціна
                                    2  # Статус "непродано"
                                ))
                            except Exception as e:
                                logger.warning(f"Пропуск рядка: {e}")
                    
                    conn.commit()
                    cursor.close()
                    processed += 1
                    
                    logger.info(f"✅ Аркуш {sheet.title} оброблено")
                    
            except Exception as e:
                logger.error(f"❌ Помилка обробки аркуша {sheet.title}: {e}")
                continue
            
            # Пауза між аркушами
            time.sleep(1)
        
        logger.info(f"✅ Безпечний імпорт завершено: {processed} аркушів")
        return True
        
    except Exception as e:
        logger.error(f"❌ Помилка безпечного імпорту: {e}")
        return False

def main():
    """Головна функція безпечного парсера."""
    logger.info("🛡️ БЕЗПЕЧНИЙ РЕЖИМ ПАРСИНГУ")
    logger.info("=" * 50)
    
    # Тест з'єднання
    if not test_connection():
        logger.error("❌ БД недоступна. Перезапустіть PostgreSQL.")
        return
    
    # Безпечний імпорт
    safe_products_import()

if __name__ == "__main__":
    main()

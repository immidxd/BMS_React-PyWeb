#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
МЕНЕДЖЕР ПРОДАЖУ РОСТОВКИ
Правильна обробка продажу товарів з ростовки
"""

import logging
import sys
import os
from decimal import Decimal
from datetime import datetime
import psycopg2
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = os.getenv("DB_NAME", "bsstorage")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

def connect_to_db():
    """Підключення до БД."""
    try:
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD
        )
    except Exception as e:
        logger.error(f"Помилка підключення: {e}")
        return None

def sell_from_rostovka(product_number: str, quantity_to_sell: int = 1, specific_size: str = None):
    """
    Продає товар з ростовки з правильним зменшенням кількості.
    
    Args:
        product_number: Номер товару (наприклад, "Ф986")
        quantity_to_sell: Кількість для продажу
        specific_size: Конкретний розмір (опціонально)
    """
    logger.info(f"💰 ПРОДАЖ З РОСТОВКИ: {product_number}")
    logger.info(f"   Кількість: {quantity_to_sell}")
    logger.info(f"   Розмір: {specific_size or 'будь-який'}")
    
    conn = connect_to_db()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо товар
            cursor.execute("""
                SELECT id, productnumber, quantity, price, sizeeu, description, statusid
                FROM products 
                WHERE productnumber = %s AND statusid = 2
            """, (product_number,))
            
            product = cursor.fetchone()
            
            if not product:
                logger.error(f"❌ Товар {product_number} не знайдено або вже проданий")
                return False
            
            product_id, pnum, current_qty, price, size, description, status = product
            
            logger.info(f"📦 Знайдено товар: {pnum}")
            logger.info(f"   Поточна кількість: {current_qty}")
            logger.info(f"   Розмір: {size}")
            logger.info(f"   Ціна: {price}")
            
            if current_qty < quantity_to_sell:
                logger.error(f"❌ Недостатньо товару! В наявності: {current_qty}, потрібно: {quantity_to_sell}")
                return False
            
            # Розраховуємо нову кількість
            new_quantity = current_qty - quantity_to_sell
            
            if new_quantity == 0:
                # Весь товар продано - змінюємо статус
                cursor.execute("""
                    UPDATE products 
                    SET statusid = 1, quantity = 0, updated_at = now()
                    WHERE id = %s
                """, (product_id,))
                
                logger.info(f"✅ Весь товар {pnum} продано (статус змінено на 'Продано')")
                
            elif new_quantity > 0:
                # Частина залишилась - зменшуємо кількість
                cursor.execute("""
                    UPDATE products 
                    SET quantity = %s, updated_at = now()
                    WHERE id = %s
                """, (new_quantity, product_id))
                
                # Створюємо запис про продану частину для історії
                cursor.execute("""
                    INSERT INTO products (
                        productnumber, price, oldprice, quantity, sizeeu, measurementscm,
                        description, brandid, typeid, subtypeid, genderid, colorid,
                        statusid, created_at, updated_at, dateadded
                    )
                    SELECT 
                        productnumber || '(sold)', price, oldprice, %s, sizeeu, measurementscm,
                        description || '; продано ' || now()::date, brandid, typeid, subtypeid, 
                        genderid, colorid, 1, now(), now(), dateadded
                    FROM products 
                    WHERE id = %s
                    RETURNING id
                """, (quantity_to_sell, product_id))
                
                sold_id = cursor.fetchone()[0]
                
                logger.info(f"✅ Продано {quantity_to_sell} з {current_qty}")
                logger.info(f"   Залишилось: {new_quantity} (ID: {product_id})")
                logger.info(f"   Продано: {quantity_to_sell} (ID: {sold_id})")
            
            conn.commit()
            return True
    
    except Exception as e:
        logger.error(f"❌ Помилка продажу: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def demonstrate_rostovka_sales():
    """Демонструє логіку продажу з ростовки."""
    logger.info("🎭 ДЕМОНСТРАЦІЯ ПРОДАЖУ З РОСТОВКИ")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Показуємо поточний стан Ф986
            cursor.execute("""
                SELECT productnumber, quantity, sizeeu, s.statusname
                FROM products p
                LEFT JOIN statuses s ON p.statusid = s.id
                WHERE productnumber LIKE 'Ф986%'
                ORDER BY productnumber
            """)
            
            before = cursor.fetchall()
            
            logger.info("📊 ДО ПРОДАЖУ:")
            for row in before:
                logger.info(f"  {row[0]}: кількість={row[1]}, розмір={row[2]}, статус={row[3]}")
            
            # Повертаємо Ф986-2 до статусу "Непродано" для демонстрації
            cursor.execute("UPDATE products SET statusid = 2 WHERE productnumber = 'Ф986-2'")
            conn.commit()
            
            logger.info("\n🛒 СЦЕНАРІЙ ПРОДАЖУ:")
            logger.info("Клієнт купує 1 шт Ф986 розміру 36")
            
            # Тестуємо продаж
            success = sell_from_rostovka("Ф986", 1, "36")
            
            if success:
                # Показуємо результат
                cursor.execute("""
                    SELECT productnumber, quantity, sizeeu, s.statusname
                    FROM products p
                    LEFT JOIN statuses s ON p.statusid = s.id
                    WHERE productnumber LIKE 'Ф986%'
                    ORDER BY productnumber
                """)
                
                after = cursor.fetchall()
                
                logger.info("\n📊 ПІСЛЯ ПРОДАЖУ:")
                for row in after:
                    logger.info(f"  {row[0]}: кількість={row[1]}, розмір={row[2]}, статус={row[3]}")
    
    except Exception as e:
        logger.error(f"❌ Помилка демонстрації: {e}")
    finally:
        conn.close()

def test_quantity_decrease():
    """Тестує зменшення кількості при продажу."""
    logger.info("\n🧪 ТЕСТ ЗМЕНШЕННЯ КІЛЬКОСТІ")
    logger.info("=" * 50)
    
    # Тестові сценарії
    scenarios = [
        {"product": "Ф986", "sell": 1, "description": "Продаж 1 з 2 (залишиться 1)"},
        {"product": "Ф986-2", "sell": 1, "description": "Продаж 1 з 1 (статус → Продано)"},
    ]
    
    for scenario in scenarios:
        logger.info(f"\n📋 Сценарій: {scenario['description']}")
        
        conn = connect_to_db()
        if not conn:
            continue
        
        try:
            with conn.cursor() as cursor:
                # Показуємо стан до продажу
                cursor.execute("""
                    SELECT quantity, s.statusname
                    FROM products p
                    LEFT JOIN statuses s ON p.statusid = s.id
                    WHERE productnumber = %s
                """, (scenario['product'],))
                
                before = cursor.fetchone()
                if before:
                    logger.info(f"  До: кількість={before[0]}, статус={before[1]}")
                    
                    # Виконуємо продаж
                    sell_from_rostovka(scenario['product'], scenario['sell'])
                    
                    # Показуємо стан після
                    cursor.execute("""
                        SELECT quantity, s.statusname
                        FROM products p
                        LEFT JOIN statuses s ON p.statusid = s.id
                        WHERE productnumber = %s
                    """, (scenario['product'],))
                    
                    after = cursor.fetchone()
                    if after:
                        logger.info(f"  Після: кількість={after[0]}, статус={after[1]}")
        
        except Exception as e:
            logger.error(f"❌ Помилка сценарію: {e}")
        finally:
            conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demonstrate_rostovka_sales()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_quantity_decrease()
    else:
        logger.info("🎯 ДОСТУПНІ КОМАНДИ:")
        logger.info("--demo : Демонстрація продажу з ростовки")
        logger.info("--test : Тест зменшення кількості")
        
        # За замовчуванням показуємо демо
        demonstrate_rostovka_sales()

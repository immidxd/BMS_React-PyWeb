#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ТЕСТУВАННЯ ЛОГІКИ РОСТОВКИ
Перевіряє роботу нової системи обробки ростовки товарів
"""

import sys
import os
import logging
from decimal import Decimal

# Додаємо шлях до backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.googlesheets_pars import connect_to_db
from scripts.advanced_rostovka_manager import RostovkaManager, batch_consolidate_all_rostovka

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_rostovka_detection():
    """Тестує виявлення ростовки для товару Ф1300."""
    logger.info("🧪 ТЕСТУВАННЯ ВИЯВЛЕННЯ РОСТОВКИ Ф1300")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        logger.error("❌ Не вдалося підключитися до БД")
        return
    
    try:
        with conn.cursor() as cursor:
            rostovka_manager = RostovkaManager(cursor, conn)
            
            # Тестуємо пошук ростовки для Ф1300
            rostovka_items = rostovka_manager.find_rostovka_group("Ф1300")
            
            logger.info(f"📊 РЕЗУЛЬТАТИ ДЛЯ Ф1300:")
            logger.info(f"Знайдено товарів в ростовці: {len(rostovka_items)}")
            
            if rostovka_items:
                logger.info("\n📋 ДЕТАЛІ РОСТОВКИ:")
                total_quantity = 0
                prices = []
                
                for i, item in enumerate(rostovka_items, 1):
                    logger.info(f"{i}. ID: {item['id']}")
                    logger.info(f"   Номер: {item['productnumber']}")
                    logger.info(f"   Розмір: {item['sizeeu']} EU / {item['measurementscm']} см")
                    logger.info(f"   Ціна: {item['price']} (стара: {item['oldprice']})")
                    logger.info(f"   Кількість: {item['quantity']}")
                    logger.info(f"   Бренд: {item['brandname']}")
                    logger.info(f"   Модель: {item['model']}")
                    logger.info("")
                    
                    total_quantity += item.get('quantity', 1)
                    if item.get('price'):
                        prices.append(float(item['price']))
                
                logger.info(f"📊 ПІДСУМКИ:")
                logger.info(f"Загальна кількість: {total_quantity}")
                logger.info(f"Ціни: {prices}")
                logger.info(f"Унікальні розміри: {len(set(item['sizeeu'] for item in rostovka_items if item['sizeeu']))}")
                
                # Тестуємо логіку цін
                new_price, old_price = rostovka_manager.get_latest_price_info(rostovka_items)
                logger.info(f"💰 РЕКОМЕНДОВАНА ЦІНА: {new_price} (попередня: {old_price})")
                
            else:
                logger.warning("❌ Ростовку Ф1300 не знайдено")
                
                # Пошук товарів з схожими номерами
                cursor.execute("""
                    SELECT productnumber, id, price, sizeeu 
                    FROM products 
                    WHERE productnumber LIKE 'Ф1300%' 
                    ORDER BY productnumber
                """)
                
                similar = cursor.fetchall()
                if similar:
                    logger.info(f"Знайдено {len(similar)} товарів з номерами схожими на Ф1300:")
                    for prod in similar:
                        logger.info(f"  {prod[0]} (ID: {prod[1]}, ціна: {prod[2]}, розмір: {prod[3]})")
    
    except Exception as e:
        logger.error(f"❌ Помилка тестування: {e}")
    finally:
        conn.close()

def test_price_synchronization():
    """Тестує синхронізацію цін в ростовці."""
    logger.info("\n💰 ТЕСТУВАННЯ СИНХРОНІЗАЦІЇ ЦІН")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо товари з різними цінами в одній ростовці
            cursor.execute("""
                WITH base_numbers AS (
                    SELECT REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '') as base_number,
                           COUNT(*) as count,
                           COUNT(DISTINCT price) as price_variants
                    FROM products 
                    WHERE price > 0
                    GROUP BY REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '')
                    HAVING COUNT(*) > 1 AND COUNT(DISTINCT price) > 1
                    LIMIT 5
                )
                SELECT p.productnumber, p.price, p.oldprice, p.sizeeu, bn.base_number
                FROM products p
                JOIN base_numbers bn ON REGEXP_REPLACE(p.productnumber, '\\(\\d+\\)$', '') = bn.base_number
                ORDER BY bn.base_number, p.created_at
            """)
            
            results = cursor.fetchall()
            
            if results:
                logger.info("📋 ЗНАЙДЕНІ РОСТОВКИ З РІЗНИМИ ЦІНАМИ:")
                current_base = None
                
                for prod in results:
                    productnumber, price, oldprice, sizeeu, base_number = prod
                    
                    if current_base != base_number:
                        logger.info(f"\n🔸 Ростовка: {base_number}")
                        current_base = base_number
                    
                    logger.info(f"  {productnumber}: {price} грн (стара: {oldprice}), розмір: {sizeeu}")
            else:
                logger.info("✅ Ростовок з різними цінами не знайдено")
    
    except Exception as e:
        logger.error(f"❌ Помилка тестування цін: {e}")
    finally:
        conn.close()

def run_rostovka_consolidation():
    """Запускає консолідацію ростовок."""
    logger.info("\n🔄 ЗАПУСК КОНСОЛІДАЦІЇ РОСТОВОК")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        # Показуємо статистику до консолідації
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_products,
                    COUNT(DISTINCT REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '')) as unique_base_numbers
                FROM products
            """)
            
            before_stats = cursor.fetchone()
            logger.info(f"📊 ДО КОНСОЛІДАЦІЇ:")
            logger.info(f"  Всього товарів: {before_stats[0]}")
            logger.info(f"  Унікальних базових номерів: {before_stats[1]}")
            logger.info(f"  Потенційних дублікатів: {before_stats[0] - before_stats[1]}")
        
        # Запускаємо консолідацію
        batch_consolidate_all_rostovka(conn)
        
        # Показуємо статистику після консолідації
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_products,
                    COUNT(DISTINCT REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '')) as unique_base_numbers,
                    SUM(quantity) as total_quantity
                FROM products
            """)
            
            after_stats = cursor.fetchone()
            logger.info(f"\n📊 ПІСЛЯ КОНСОЛІДАЦІЇ:")
            logger.info(f"  Всього товарів: {after_stats[0]}")
            logger.info(f"  Унікальних базових номерів: {after_stats[1]}")
            logger.info(f"  Загальна кількість: {after_stats[2]}")
            logger.info(f"  Видалено дублікатів: {before_stats[0] - after_stats[0]}")
    
    except Exception as e:
        logger.error(f"❌ Помилка консолідації: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    """Головна функція тестування."""
    logger.info("🧪 ТЕСТУВАННЯ РОЗШИРЕНОЇ ЛОГІКИ РОСТОВКИ")
    logger.info("=" * 60)
    
    # Тест 1: Виявлення ростовки
    test_rostovka_detection()
    
    # Тест 2: Синхронізація цін
    test_price_synchronization()
    
    # Тест 3: Запуск консолідації (опціонально)
    if len(sys.argv) > 1 and sys.argv[1] == "--run-consolidation":
        run_rostovka_consolidation()
    else:
        logger.info("\n💡 Для запуску консолідації використайте:")
        logger.info("python test_rostovka_logic.py --run-consolidation")

if __name__ == "__main__":
    main()

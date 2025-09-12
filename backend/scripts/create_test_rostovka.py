#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СТВОРЕННЯ ТЕСТОВОЇ РОСТОВКИ
Створює тестові дані для демонстрації роботи логіки ростовки
"""

import sys
import os
import logging
from decimal import Decimal

# Додаємо шлях до backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.googlesheets_pars import connect_to_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_test_rostovka():
    """Створює тестову ростовку для демонстрації."""
    logger.info("🧪 СТВОРЕННЯ ТЕСТОВОЇ РОСТОВКИ")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        logger.error("❌ Не вдалося підключитися до БД")
        return
    
    try:
        with conn.cursor() as cursor:
            # Створюємо тестову ростовку Т1000 в різних розмірах
            test_products = [
                {
                    'productnumber': 'Т1000',
                    'price': Decimal('1500.00'),
                    'oldprice': None,
                    'sizeeu': '38',
                    'measurementscm': '24',
                    'description': 'Тестові кросівки, розмір 38',
                    'brandid': 2,  # Adidas
                    'typeid': 26,  # Кросівки
                    'genderid': 1,  # Унісекс
                    'statusid': 2,  # Непродано
                    'quantity': 1
                },
                {
                    'productnumber': 'Т1000(1)',
                    'price': Decimal('1500.00'),
                    'oldprice': None,
                    'sizeeu': '39',
                    'measurementscm': '24.5',
                    'description': 'Тестові кросівки, розмір 39',
                    'brandid': 2,  # Adidas
                    'typeid': 26,  # Кросівки
                    'genderid': 1,  # Унісекс
                    'statusid': 2,  # Непродано
                    'quantity': 1
                },
                {
                    'productnumber': 'Т1000(2)',
                    'price': Decimal('1400.00'),  # Інша ціна
                    'oldprice': Decimal('1500.00'),  # Стара ціна
                    'sizeeu': '40',
                    'measurementscm': '25',
                    'description': 'Тестові кросівки, розмір 40, ціна знижена',
                    'brandid': 2,  # Adidas
                    'typeid': 26,  # Кросівки
                    'genderid': 1,  # Унісекс
                    'statusid': 2,  # Непродано
                    'quantity': 1
                },
                {
                    'productnumber': 'Т1000(3)',
                    'price': Decimal('1600.00'),  # Ще інша ціна
                    'oldprice': None,
                    'sizeeu': '41',
                    'measurementscm': '25.5',
                    'description': 'Тестові кросівки, розмір 41, підвищена ціна',
                    'brandid': 2,  # Adidas
                    'typeid': 26,  # Кросівки
                    'genderid': 1,  # Унісекс
                    'statusid': 2,  # Непродано
                    'quantity': 2  # Подвійна кількість
                }
            ]
            
            # Видаляємо існуючі тестові товари
            cursor.execute("DELETE FROM products WHERE productnumber LIKE 'Т1000%'")
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"🗑️ Видалено {deleted} старих тестових товарів")
            
            # Створюємо тестові товари
            for product in test_products:
                columns = ', '.join(product.keys())
                placeholders = ', '.join(['%s'] * len(product))
                values = tuple(product.values())
                
                insert_query = f"""
                    INSERT INTO products ({columns}, created_at, updated_at)
                    VALUES ({placeholders}, now(), now())
                    RETURNING id
                """
                
                cursor.execute(insert_query, values)
                product_id = cursor.fetchone()[0]
                
                logger.info(f"✅ Створено {product['productnumber']} (ID: {product_id})")
            
            conn.commit()
            
            # Показуємо створену ростовку
            cursor.execute("""
                SELECT productnumber, price, oldprice, quantity, sizeeu, measurementscm
                FROM products 
                WHERE productnumber LIKE 'Т1000%'
                ORDER BY productnumber
            """)
            
            results = cursor.fetchall()
            
            logger.info(f"\n📊 СТВОРЕНА ТЕСТОВА РОСТОВКА:")
            logger.info("Номер       | Ціна    | Стара   | К-сть | Розмір | СМ")
            logger.info("-" * 55)
            
            for row in results:
                logger.info(f"{row[0]:<11} | {row[1]:<7} | {row[2] or 'None':<7} | {row[3]:<5} | {row[4]:<6} | {row[5]}")
            
            logger.info("\n🎯 ТЕПЕР МОЖНА ТЕСТУВАТИ ЛОГІКУ РОСТОВКИ!")
            logger.info("Запустіть: python test_rostovka_logic.py")
    
    except Exception as e:
        logger.error(f"❌ Помилка створення тестової ростовки: {e}")
        conn.rollback()
    finally:
        conn.close()

def test_advanced_rostovka():
    """Тестує покращену логіку ростовки на тестових даних."""
    logger.info("\n🔬 ТЕСТУВАННЯ ПОКРАЩЕНОЇ ЛОГІКИ РОСТОВКИ")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        from advanced_rostovka_manager import RostovkaManager
        
        with conn.cursor() as cursor:
            rostovka_manager = RostovkaManager(cursor, conn)
            
            # Тестуємо на створеній ростовці Т1000
            rostovka_items = rostovka_manager.find_rostovka_group("Т1000")
            
            logger.info(f"📊 ЗНАЙДЕНО РОСТОВКУ Т1000: {len(rostovka_items)} товарів")
            
            if len(rostovka_items) > 1:
                # Тестуємо консолідацію
                consolidated = rostovka_manager.consolidate_rostovka_sizes(rostovka_items)
                
                logger.info(f"🔄 КОНСОЛІДАЦІЯ:")
                logger.info(f"  Загальна кількість: {consolidated['quantity']}")
                logger.info(f"  Рекомендована ціна: {consolidated['price']}")
                logger.info(f"  Попередня ціна: {consolidated['oldprice']}")
                logger.info(f"  Доступні розміри: {consolidated['available_sizes']}")
                
                # Застосовуємо консолідацію
                logger.info("\n🚀 ЗАСТОСУВАННЯ КОНСОЛІДАЦІЇ...")
                main_id = rostovka_manager.process_rostovka("Т1000", {
                    'price': 1500.00,
                    'sizeeu': '42',
                    'measurementscm': '26',
                    '_b_name': 'Adidas',
                    '_t_name': 'Кросівки'
                })
                
                if main_id:
                    logger.info(f"✅ Ростовка консолідована в товар ID: {main_id}")
                    
                    # Перевіряємо результат
                    cursor.execute("""
                        SELECT productnumber, price, oldprice, quantity, description
                        FROM products 
                        WHERE productnumber LIKE 'Т1000%'
                        ORDER BY productnumber
                    """)
                    
                    final_results = cursor.fetchall()
                    
                    logger.info(f"\n📊 РЕЗУЛЬТАТ КОНСОЛІДАЦІЇ:")
                    for row in final_results:
                        logger.info(f"  {row[0]}: ціна={row[1]}, стара={row[2]}, к-сть={row[3]}")
                        if row[4]:
                            logger.info(f"    Опис: {row[4][:100]}...")
            else:
                logger.warning("❌ Ростовку Т1000 не знайдено або вона має тільки 1 товар")
    
    except Exception as e:
        logger.error(f"❌ Помилка тестування: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    """Головна функція."""
    logger.info("🧪 ТЕСТУВАННЯ РОЗШИРЕНОЇ ЛОГІКИ РОСТОВКИ")
    logger.info("=" * 60)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--create-test":
        create_test_rostovka()
    else:
        # Спочатку створюємо тестові дані
        create_test_rostovka()
        
        # Потім тестуємо логіку
        test_advanced_rostovka()

if __name__ == "__main__":
    main()

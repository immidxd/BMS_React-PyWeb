#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ВІДНОВЛЕННЯ РОСТОВКИ З ЗАМОВЛЕНЬ
Створює повну ростовку товарів на основі інформації з замовлень
"""

import sys
import os
import logging
import psycopg2
import re
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

def extract_sizes_from_orders(product_base: str):
    """Витягує всі розміри товару з замовлень."""
    conn = connect_to_db()
    if not conn:
        return []
    
    try:
        with conn.cursor() as cursor:
            # Шукаємо всі згадки товару в замовленнях
            cursor.execute("""
                SELECT DISTINCT
                    oi.notes,
                    oi.price,
                    o.order_date,
                    COUNT(*) as frequency
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.id
                JOIN products p ON oi.product_id = p.id
                WHERE p.productnumber LIKE %s
                  AND (oi.notes LIKE %s OR p.productnumber LIKE %s)
                GROUP BY oi.notes, oi.price, o.order_date
                ORDER BY COUNT(*) DESC, o.order_date DESC
            """, (f"{product_base}%", f"%{product_base}%", f"{product_base}%"))
            
            orders_data = cursor.fetchall()
            
            # Витягуємо розміри з уточнень
            sizes_info = {}
            
            for notes, price, date, freq in orders_data:
                # Шукаємо розміри в форматі (36), (37), тощо
                size_matches = re.findall(rf'{product_base}[^(]*\((\d+(?:\.\d+)?)\)', notes or '')
                
                for size in size_matches:
                    if size not in sizes_info:
                        sizes_info[size] = {
                            'price': price,
                            'frequency': 0,
                            'last_date': date
                        }
                    sizes_info[size]['frequency'] += freq
                    if date > sizes_info[size]['last_date']:
                        sizes_info[size]['last_date'] = date
                        sizes_info[size]['price'] = price
            
            return sizes_info
    
    except Exception as e:
        logger.error(f"Помилка витягування розмірів: {e}")
        return {}
    finally:
        conn.close()

def rebuild_complete_rostovka(product_base: str):
    """Відновлює повну ростовку товару."""
    logger.info(f"🔄 ВІДНОВЛЕННЯ ПОВНОЇ РОСТОВКИ: {product_base}")
    logger.info("=" * 50)
    
    # Витягуємо розміри з замовлень
    sizes_info = extract_sizes_from_orders(product_base)
    
    if not sizes_info:
        logger.warning(f"❌ Розміри для {product_base} не знайдені в замовленнях")
        return
    
    logger.info(f"📏 Знайдено розміри в замовленнях: {sorted(sizes_info.keys())}")
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо існуючі товари цієї ростовки
            cursor.execute("""
                SELECT id, productnumber, sizeeu, quantity, price
                FROM products 
                WHERE productnumber LIKE %s
                ORDER BY productnumber
            """, (f"{product_base}%",))
            
            existing_products = cursor.fetchall()
            existing_sizes = {str(p[2]): p for p in existing_products if p[2]}
            
            logger.info(f"📦 Існуючі товари: {len(existing_products)}")
            for prod in existing_products:
                logger.info(f"  {prod[1]}: розмір={prod[2]}, к-сть={prod[3]}")
            
            # Знаходимо базовий товар для копіювання характеристик
            base_product = existing_products[0] if existing_products else None
            
            if base_product:
                cursor.execute("""
                    SELECT brandid, typeid, subtypeid, genderid, colorid, description
                    FROM products WHERE id = %s
                """, (base_product[0],))
                
                base_chars = cursor.fetchone()
                brandid, typeid, subtypeid, genderid, colorid, description = base_chars
            else:
                # Дефолтні характеристики
                brandid, typeid, subtypeid, genderid, colorid = 86, 31, None, 1, 2489  # Karl Lagerfeld, Кеди
                description = f"Товар {product_base} відновлений з замовлень"
            
            # Створюємо відсутні розміри
            created_count = 0
            
            for size, info in sizes_info.items():
                if size not in existing_sizes:
                    # Створюємо новий товар для цього розміру
                    new_number = f"{product_base}({len(existing_products) + created_count + 1})"
                    
                    cursor.execute("""
                        INSERT INTO products (
                            productnumber, price, sizeeu, measurementscm,
                            description, brandid, typeid, subtypeid, 
                            genderid, colorid, statusid, quantity,
                            created_at, updated_at, dateadded
                        ) VALUES (
                            %s, %s, %s, NULL,
                            %s, %s, %s, %s,
                            %s, %s, 2, 1,
                            now(), now(), %s
                        ) RETURNING id
                    """, (
                        new_number,
                        info['price'] or 1700,
                        size,
                        f"{description}; розмір {size}; відновлено з замовлень",
                        brandid, typeid, subtypeid,
                        genderid, colorid,
                        info['last_date']
                    ))
                    
                    new_id = cursor.fetchone()[0]
                    created_count += 1
                    
                    logger.info(f"✅ Створено {new_number} розмір {size} (ID: {new_id})")
                    logger.info(f"   Ціна: {info['price']}, використано {info['frequency']} разів")
            
            conn.commit()
            
            logger.info(f"\n🎉 ВІДНОВЛЕННЯ ЗАВЕРШЕНО:")
            logger.info(f"  Створено нових товарів: {created_count}")
            
            # Показуємо повну ростовку
            cursor.execute("""
                SELECT productnumber, quantity, sizeeu, price, s.statusname
                FROM products p
                LEFT JOIN statuses s ON p.statusid = s.id
                WHERE productnumber LIKE %s
                ORDER BY productnumber
            """, (f"{product_base}%",))
            
            final_rostovka = cursor.fetchall()
            
            logger.info(f"\n📊 ПОВНА РОСТОВКА {product_base} ({len(final_rostovka)} товарів):")
            for item in final_rostovka:
                logger.info(f"  {item[0]}: к-сть={item[1]}, розмір={item[2]}, ціна={item[3]}, статус={item[4]}")
    
    except Exception as e:
        logger.error(f"❌ Помилка відновлення: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    """Головна функція."""
    logger.info("🎯 ВІДНОВЛЕННЯ РОСТОВКИ З ЗАМОВЛЕНЬ")
    logger.info("=" * 60)
    
    if len(sys.argv) > 1:
        product_base = sys.argv[1]
        rebuild_complete_rostovka(product_base)
    else:
        logger.info("💡 Використання:")
        logger.info("python rebuild_rostovka_from_orders.py Ф986")
        
        # За замовчуванням відновлюємо Ф986
        rebuild_complete_rostovka("Ф986")

if __name__ == "__main__":
    main()

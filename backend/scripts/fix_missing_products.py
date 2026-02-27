#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ВИПРАВЛЕННЯ ВІДСУТНІХ ТОВАРІВ
Створює повні записи товарів на основі інформації з замовлень
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

def find_products_from_orders():
    """Знаходить товари що були створені з замовлень, але не з каталогу."""
    conn = connect_to_db()
    if not conn:
        return []
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо товари що використовуються в замовленнях, але мають мінімальну інформацію
            cursor.execute("""
                SELECT DISTINCT
                    p.id,
                    p.productnumber,
                    p.price,
                    p.quantity,
                    p.sizeeu,
                    p.description,
                    p.brandid,
                    p.typeid,
                    COUNT(oi.id) as orders_count,
                    -- Витягуємо розміри з уточнень замовлень
                    STRING_AGG(DISTINCT 
                        REGEXP_REPLACE(oi.notes, '.*\\((\\d+(?:\\.\\d+)?)\\).*', '\\1', 'g'), 
                        ', ' 
                        ORDER BY REGEXP_REPLACE(oi.notes, '.*\\((\\d+(?:\\.\\d+)?)\\).*', '\\1', 'g')
                    ) as sizes_from_orders
                FROM products p
                LEFT JOIN order_items oi ON p.id = oi.product_id
                WHERE 
                    -- Товари з мінімальною інформацією (створені парсером замовлень)
                    (p.description IS NULL OR p.description = '' OR LENGTH(p.description) < 10)
                    AND p.brandid IS NULL
                    AND p.typeid IS NULL
                    AND oi.id IS NOT NULL  -- Використовуються в замовленнях
                GROUP BY p.id, p.productnumber, p.price, p.quantity, p.sizeeu, p.description, p.brandid, p.typeid
                HAVING COUNT(oi.id) > 0
                ORDER BY COUNT(oi.id) DESC
                LIMIT 20
            """)
            
            results = cursor.fetchall()
            return results
    
    except Exception as e:
        logger.error(f"Помилка пошуку: {e}")
        return []
    finally:
        conn.close()

def analyze_f986_orders():
    """Аналізує замовлення з товарами Ф986."""
    logger.info("🔍 АНАЛІЗ ЗАМОВЛЕНЬ З Ф986")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо всі замовлення з Ф986
            cursor.execute("""
                SELECT 
                    o.id as order_id,
                    o.order_date,
                    c.first_name || ' ' || c.last_name as client_name,
                    oi.notes,
                    oi.price as item_price,
                    oi.quantity as item_quantity,
                    p.productnumber,
                    p.sizeeu as product_size
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.id
                JOIN clients c ON o.client_id = c.id
                JOIN products p ON oi.product_id = p.id
                WHERE p.productnumber LIKE 'Ф986%'
                ORDER BY o.order_date DESC
                LIMIT 20
            """)
            
            orders = cursor.fetchall()
            
            logger.info(f"📊 Знайдено {len(orders)} замовлень з Ф986:")
            
            sizes_from_orders = set()
            
            for order in orders:
                order_id, date, client, notes, price, qty, product_num, product_size = order
                
                # Витягуємо розмір з уточнень
                size_match = re.search(r'\((\d+(?:\.\d+)?)\)', notes or '')
                order_size = size_match.group(1) if size_match else product_size
                
                if order_size:
                    sizes_from_orders.add(str(order_size))
                
                logger.info(f"  📋 Замовлення {order_id}: {client}")
                logger.info(f"     Товар: {product_num} (розмір: {order_size})")
                logger.info(f"     Уточнення: {notes}")
                logger.info(f"     Дата: {date}")
                logger.info("")
            
            logger.info(f"📏 РОЗМІРИ З ЗАМОВЛЕНЬ: {sorted(sizes_from_orders)}")
            logger.info(f"📦 РОЗМІРИ В БД: 36, 37 (з консолідації)")
            
            missing_sizes = set(sizes_from_orders) - {'36', '37'}
            if missing_sizes:
                logger.warning(f"⚠️ ВІДСУТНІ РОЗМІРИ: {sorted(missing_sizes)}")
                logger.warning("Ці розміри були в замовленнях, але не потрапили в каталог товарів!")
    
    except Exception as e:
        logger.error(f"❌ Помилка аналізу: {e}")
    finally:
        conn.close()

def create_missing_rostovka_items():
    """Створює відсутні товари ростовки на основі замовлень."""
    logger.info("\n🛠️ СТВОРЕННЯ ВІДСУТНІХ ТОВАРІВ РОСТОВКИ")
    logger.info("=" * 50)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Аналізуємо які розміри Ф986 є в замовленнях
            cursor.execute("""
                SELECT DISTINCT
                    REGEXP_REPLACE(oi.notes, '.*Ф986[^(]*\\((\\d+(?:\\.\\d+)?)\\).*', '\\1', 'g') as size_from_notes,
                    oi.price,
                    COUNT(*) as frequency
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                WHERE p.productnumber LIKE 'Ф986%'
                  AND oi.notes ~ 'Ф986.*\\(\\d+(?:\\.\\d+)?\\)'
                GROUP BY 
                    REGEXP_REPLACE(oi.notes, '.*Ф986[^(]*\\((\\d+(?:\\.\\d+)?)\\).*', '\\1', 'g'),
                    oi.price
                ORDER BY frequency DESC
            """)
            
            order_sizes = cursor.fetchall()
            
            logger.info("📋 РОЗМІРИ Ф986 З ЗАМОВЛЕНЬ:")
            for size_info in order_sizes:
                size, price, freq = size_info
                logger.info(f"  Розмір {size}: ціна {price}, використано {freq} разів")
            
            # Знаходимо базовий товар Ф986 для копіювання характеристик
            cursor.execute("""
                SELECT brandid, typeid, subtypeid, genderid, colorid, description, price
                FROM products 
                WHERE productnumber = 'Ф986'
            """)
            
            base_product = cursor.fetchone()
            
            if not base_product:
                logger.error("❌ Базовий товар Ф986 не знайдено")
                return
            
            brandid, typeid, subtypeid, genderid, colorid, description, base_price = base_product
            
            logger.info(f"📦 Базовий товар Ф986: бренд={brandid}, тип={typeid}, ціна={base_price}")
            
            # Створюємо відсутні розміри
            existing_sizes = {'36', '37'}  # Які вже є в БД
            created_count = 0
            
            for size_info in order_sizes:
                size, price, freq = size_info
                
                if size not in existing_sizes and size.replace('.', '').isdigit():
                    logger.info(f"🆕 Створюю Ф986 розмір {size}")
                    
                    # Створюємо новий товар ростовки
                    cursor.execute("""
                        INSERT INTO products (
                            productnumber, price, oldprice, quantity,
                            sizeeu, measurementscm, description,
                            brandid, typeid, subtypeid, genderid, colorid,
                            statusid, created_at, updated_at, dateadded
                        ) VALUES (
                            %s, %s, NULL, 1,
                            %s, NULL, %s,
                            %s, %s, %s, %s, %s,
                            2, now(), now(), '2025-01-23'
                        ) RETURNING id
                    """, (
                        f"Ф986({len(existing_sizes) + created_count + 1})",  # Унікальний номер
                        price or base_price,
                        size,
                        f"{description}; розмір {size}",
                        brandid, typeid, subtypeid, genderid, colorid
                    ))
                    
                    new_id = cursor.fetchone()[0]
                    created_count += 1
                    existing_sizes.add(size)
                    
                    logger.info(f"  ✅ Створено Ф986({len(existing_sizes)}) розмір {size} (ID: {new_id})")
            
            conn.commit()
            
            logger.info(f"\n🎉 СТВОРЕНО {created_count} нових товарів ростовки")
            
            # Показуємо повну ростовку
            cursor.execute("""
                SELECT productnumber, quantity, sizeeu, s.statusname
                FROM products p
                LEFT JOIN statuses s ON p.statusid = s.id
                WHERE productnumber LIKE 'Ф986%'
                ORDER BY productnumber
            """)
            
            final_rostovka = cursor.fetchall()
            
            logger.info(f"📊 ПОВНА РОСТОВКА Ф986 ({len(final_rostovka)} товарів):")
            for item in final_rostovka:
                logger.info(f"  {item[0]}: к-сть={item[1]}, розмір={item[2]}, статус={item[3]}")
    
    except Exception as e:
        logger.error(f"❌ Помилка створення: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    """Головна функція."""
    logger.info("🎯 ВИПРАВЛЕННЯ СИСТЕМНОЇ ПРОБЛЕМИ РОСТОВКИ")
    logger.info("=" * 60)
    
    # Крок 1: Аналіз замовлень
    analyze_f986_orders()
    
    # Крок 2: Знаходимо товари з мінімальною інформацією
    missing_products = find_products_from_orders()
    
    logger.info(f"\n📊 ТОВАРИ З МІНІМАЛЬНОЮ ІНФОРМАЦІЄЮ:")
    logger.info(f"Знайдено {len(missing_products)} товарів створених з замовлень")
    
    for prod in missing_products[:10]:
        product_id, pnum, price, qty, size, desc, brand, type_id, orders_count, sizes = prod
        logger.info(f"  {pnum}: {orders_count} замовлень, розміри з замовлень: {sizes}")
    
    # Крок 3: Створюємо відсутні товари ростовки
    if len(sys.argv) > 1 and sys.argv[1] == "--create-missing":
        create_missing_rostovka_items()
    else:
        logger.info("\n💡 Для створення відсутніх товарів запустіть:")
        logger.info("python fix_missing_products.py --create-missing")

if __name__ == "__main__":
    main()

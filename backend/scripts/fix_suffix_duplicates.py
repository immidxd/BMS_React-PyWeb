#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ВИПРАВЛЕННЯ СУФІКСНИХ ДУБЛІКАТІВ
Простий скрипт для очищення автоматичних дублікатів (1), (2)
"""

import sys
import os
import logging
import psycopg2
from dotenv import load_dotenv

# Додаємо шлях до backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Параметри БД
DB_NAME = os.getenv("DB_NAME", "bsstorage")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

def connect_to_db():
    """Підключення до БД."""
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
    except Exception as e:
        logger.error(f"Помилка підключення: {e}")
        return None

def find_auto_duplicates():
    """Знаходить автоматичні дублікати (1), (2)."""
    conn = connect_to_db()
    if not conn:
        return []
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо товари з автоматичними суфіксами
            cursor.execute("""
                SELECT 
                    p1.productnumber as base_number,
                    p1.id as base_id,
                    p1.price as base_price,
                    p1.quantity as base_quantity,
                    p1.sizeeu as base_size,
                    p2.productnumber as dup_number,
                    p2.id as dup_id,
                    p2.price as dup_price,
                    p2.quantity as dup_quantity,
                    p2.sizeeu as dup_size,
                    b1.brandname as brand,
                    t1.typename as type
                FROM products p1
                JOIN products p2 ON REGEXP_REPLACE(p2.productnumber, '\\(\\d+\\)$', '') = p1.productnumber
                LEFT JOIN brands b1 ON p1.brandid = b1.id
                LEFT JOIN types t1 ON p1.typeid = t1.id
                WHERE p2.productnumber ~ '\\(\\d+\\)$'
                  AND p1.productnumber !~ '\\(\\d+\\)$'
                  AND p1.brandid = p2.brandid
                  AND p1.typeid = p2.typeid
                ORDER BY p1.productnumber
            """)
            
            results = cursor.fetchall()
            return results
    
    except Exception as e:
        logger.error(f"Помилка пошуку: {e}")
        return []
    finally:
        conn.close()

def consolidate_specific_duplicate(base_number: str):
    """Консолідує конкретний дублікат."""
    conn = connect_to_db()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо всі варіанти цього номера
            cursor.execute("""
                SELECT id, productnumber, price, oldprice, quantity, sizeeu, measurementscm
                FROM products 
                WHERE productnumber = %s OR productnumber LIKE %s
                ORDER BY 
                    CASE WHEN productnumber = %s THEN 0 ELSE 1 END,
                    created_at ASC
            """, (base_number, f"{base_number}(%)", base_number))
            
            items = cursor.fetchall()
            
            if len(items) < 2:
                logger.info(f"❌ {base_number}: немає дублікатів для консолідації")
                return False
            
            logger.info(f"📊 {base_number}: знайдено {len(items)} товарів")
            
            # Головний товар (без суфіксів або найстаріший)
            main_item = items[0]
            duplicates = items[1:]
            
            # Розраховуємо консолідовані дані
            total_quantity = sum(item[4] or 1 for item in items)
            sizes = [str(item[5]) for item in items if item[5]]
            unique_sizes = sorted(set(sizes))
            
            logger.info(f"  Головний: {main_item[1]} (ID: {main_item[0]})")
            logger.info(f"  Дублікати: {[item[1] for item in duplicates]}")
            logger.info(f"  Загальна кількість: {total_quantity}")
            logger.info(f"  Розміри: {', '.join(unique_sizes)}")
            
            # Оновлюємо головний товар
            cursor.execute("""
                UPDATE products 
                SET 
                    quantity = %s,
                    description = CASE 
                        WHEN description LIKE '%розміри:%' 
                        THEN REGEXP_REPLACE(description, '; розміри:.*$', '; розміри: ' || %s)
                        ELSE COALESCE(description, '') || '; розміри: ' || %s
                    END,
                    updated_at = now()
                WHERE id = %s
            """, (
                total_quantity,
                ', '.join(unique_sizes),
                ', '.join(unique_sizes),
                main_item[0]
            ))
            
            # Переносимо зв'язки з замовленнями
            duplicate_ids = [item[0] for item in duplicates]
            
            if duplicate_ids:
                cursor.execute("""
                    UPDATE order_items 
                    SET product_id = %s 
                    WHERE product_id = ANY(%s)
                """, (main_item[0], duplicate_ids))
                
                cursor.execute("""
                    UPDATE order_items 
                    SET product_id = %s 
                    WHERE product_id = ANY(%s)
                """, (main_item[0], duplicate_ids))
                
                # Видаляємо дублікати
                cursor.execute("DELETE FROM products WHERE id = ANY(%s)", (duplicate_ids,))
                
                deleted = cursor.rowcount
                logger.info(f"✅ Видалено {deleted} дублікатів")
            
            conn.commit()
            return True
    
    except Exception as e:
        logger.error(f"❌ Помилка консолідації {base_number}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def main():
    """Головна функція."""
    logger.info("🔧 ВИПРАВЛЕННЯ СУФІКСНИХ ДУБЛІКАТІВ")
    logger.info("=" * 50)
    
    # Демонстрація логіки
    logger.info("📋 ЛОГІКА ОБРОБКИ СУФІКСІВ:")
    logger.info("• Ф986, Ф986(1) → КОНСОЛІДУВАТИ (ростовка)")
    logger.info("• Ф986-2, Ф986-3 → ЗАЛИШИТИ (різні товари)")
    logger.info("")
    
    # Знаходимо автоматичні дублікати
    duplicates = find_auto_duplicates()
    
    if not duplicates:
        logger.info("✅ Автоматичних дублікатів не знайдено")
        return
    
    logger.info(f"📊 Знайдено {len(duplicates)} автоматичних дублікатів:")
    
    # Показуємо знайдені дублікати
    for dup in duplicates[:10]:  # Показуємо перші 10
        base_num, base_id, base_price, base_qty, base_size = dup[0], dup[1], dup[2], dup[3], dup[4]
        dup_num, dup_id, dup_price, dup_qty, dup_size = dup[5], dup[6], dup[7], dup[8], dup[9]
        brand, type_name = dup[10], dup[11]
        
        logger.info(f"  {base_num} (ID:{base_id}, розмір:{base_size}) + {dup_num} (ID:{dup_id}, розмір:{dup_size})")
        logger.info(f"    {brand} {type_name}, ціни: {base_price} + {dup_price}")
    
    if len(duplicates) > 10:
        logger.info(f"  ... та ще {len(duplicates) - 10} дублікатів")
    
    # Консолідуємо кожен дублікат
    logger.info(f"\n🚀 ПОЧАТОК КОНСОЛІДАЦІЇ:")
    
    consolidated = 0
    processed_bases = set()
    
    for dup in duplicates:
        base_number = dup[0]
        
        # Пропускаємо якщо вже обробили цей базовий номер
        if base_number in processed_bases:
            continue
        
        processed_bases.add(base_number)
        
        if consolidate_specific_duplicate(base_number):
            consolidated += 1
    
    logger.info(f"🎉 КОНСОЛІДАЦІЯ ЗАВЕРШЕНА: {consolidated} ростовок оброблено")

if __name__ == "__main__":
    main()

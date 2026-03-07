#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ПАКЕТНЕ ВИПРАВЛЕННЯ ДУБЛІКАТІВ
Автоматично виправляє всі автоматичні дублікати (1), (2)
"""

import sys
import os
import logging
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

def fix_all_auto_duplicates():
    """Виправляє всі автоматичні дублікати."""
    logger.info("🔧 ПАКЕТНЕ ВИПРАВЛЕННЯ АВТОМАТИЧНИХ ДУБЛІКАТІВ")
    logger.info("=" * 60)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Список дублікатів для виправлення
            duplicates_to_fix = [
                '4402', 'В205', 'В65', 'Г227', 'Е1', 
                'Р101', 'Р102', 'Р103', 'Р104', 'Р105', 'Ф612'
            ]
            
            fixed_count = 0
            
            for base_number in duplicates_to_fix:
                logger.info(f"🔄 Виправлення {base_number}...")
                
                try:
                    # Отримуємо дані про дублікати
                    cursor.execute("""
                        SELECT id, productnumber, quantity, sizeeu, price, oldprice
                        FROM products 
                        WHERE productnumber = %s OR productnumber LIKE %s
                        ORDER BY 
                            CASE WHEN productnumber = %s THEN 0 ELSE 1 END,
                            created_at ASC
                    """, (base_number, f"{base_number}(%)", base_number))
                    
                    items = cursor.fetchall()
                    
                    if len(items) < 2:
                        logger.info(f"  ⚠️ {base_number}: немає дублікатів")
                        continue
                    
                    main_item = items[0]  # Головний товар
                    duplicates = items[1:]  # Дублікати
                    
                    main_id = main_item[0]
                    total_quantity = sum(item[2] or 1 for item in items)
                    
                    # Збираємо розміри
                    sizes = [str(item[3]) for item in items if item[3]]
                    unique_sizes = sorted(set(sizes))
                    sizes_text = ', '.join(unique_sizes) if unique_sizes else None
                    
                    logger.info(f"  📊 {len(items)} товарів → 1 товар")
                    logger.info(f"  📏 Розміри: {sizes_text}")
                    logger.info(f"  📦 Кількість: {total_quantity}")
                    
                    # Оновлюємо головний товар
                    if sizes_text:
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
                        """, (total_quantity, sizes_text, sizes_text, main_id))
                    else:
                        cursor.execute("""
                            UPDATE products 
                            SET quantity = %s, updated_at = now()
                            WHERE id = %s
                        """, (total_quantity, main_id))
                    
                    # Переносимо зв'язки та видаляємо дублікати
                    for dup_item in duplicates:
                        dup_id = dup_item[0]
                        
                        # Переносимо зв'язки
                        cursor.execute("UPDATE order_items SET product_id = %s WHERE product_id = %s", (main_id, dup_id))
                        cursor.execute("UPDATE order_items SET product_id = %s WHERE product_id = %s", (main_id, dup_id))
                        
                        # Видаляємо дублікат
                        cursor.execute("DELETE FROM products WHERE id = %s", (dup_id,))
                    
                    conn.commit()
                    fixed_count += 1
                    logger.info(f"  ✅ {base_number} виправлено")
                    
                except Exception as e:
                    logger.error(f"  ❌ Помилка {base_number}: {e}")
                    conn.rollback()
                    continue
            
            logger.info(f"\n🎉 ВИПРАВЛЕННЯ ЗАВЕРШЕНО: {fixed_count} ростовок консолідовано")
            
            # Показуємо підсумкову статистику
            cursor.execute("""
                SELECT COUNT(*) as remaining_duplicates
                FROM products 
                WHERE productnumber ~ '\\(\\d+\\)$'
            """)
            
            remaining = cursor.fetchone()[0]
            logger.info(f"📊 Залишилось автоматичних дублікатів: {remaining}")
    
    except Exception as e:
        logger.error(f"❌ Критична помилка: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    fix_all_auto_duplicates()

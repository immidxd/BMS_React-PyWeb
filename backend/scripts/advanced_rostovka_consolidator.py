#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
РОЗШИРЕНИЙ КОНСОЛІДАТОР РОСТОВКИ
Злиття товарів з однаковими розмірами в ростовці
"""

import sys
import os
import logging
import psycopg2
from decimal import Decimal
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

def merge_measurements(measurements):
    """Об'єднує заміри розумно."""
    valid_measurements = [m for m in measurements if m and str(m).strip()]
    
    if not valid_measurements:
        return None
    
    if len(valid_measurements) == 1:
        return str(valid_measurements[0])
    
    # Конвертуємо в float для порівняння
    try:
        float_measurements = [float(str(m).replace(',', '.')) for m in valid_measurements]
        
        # Якщо всі однакові (з точністю до 0.1)
        if max(float_measurements) - min(float_measurements) <= 0.1:
            return str(max(float_measurements))  # Беремо більший
        
        # Якщо різні - робимо діапазон
        min_val = min(float_measurements)
        max_val = max(float_measurements)
        return f"{min_val}-{max_val}"
        
    except:
        # Якщо не можемо конвертувати - просто об'єднуємо
        return ', '.join(set(str(m) for m in valid_measurements))

def consolidate_same_size_items(base_number: str):
    """Консолідує товари з однаковими розмірами в ростовці."""
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            logger.info(f"🔍 Аналіз ростовки {base_number}")
            
            # Знаходимо всю ростовку
            cursor.execute("""
                SELECT 
                    id, productnumber, price, oldprice, quantity,
                    sizeeu, measurementscm, statusid, genderid, colorid,
                    description, model, marking, created_at
                FROM products 
                WHERE productnumber = %s OR productnumber LIKE %s
                ORDER BY created_at ASC
            """, (base_number, f"{base_number}(%"))
            
            items = cursor.fetchall()
            
            if len(items) < 2:
                logger.info(f"  ❌ {base_number}: немає ростовки")
                return
            
            logger.info(f"  📊 Знайдено {len(items)} товарів в ростовці")
            
            # Групуємо за розміром та статусом
            size_groups = {}
            
            for item in items:
                id_, pnum, price, oldprice, qty, sizeeu, measurementscm, statusid, genderid, colorid, desc, model, marking, created_at = item
                
                # Ключ групування: розмір + статус (продані окремо)
                size_key = f"{sizeeu}_{statusid}"
                
                if size_key not in size_groups:
                    size_groups[size_key] = []
                
                size_groups[size_key].append({
                    'id': id_, 'productnumber': pnum, 'price': price, 'oldprice': oldprice,
                    'quantity': qty, 'sizeeu': sizeeu, 'measurementscm': measurementscm,
                    'statusid': statusid, 'genderid': genderid, 'colorid': colorid,
                    'description': desc, 'model': model, 'marking': marking,
                    'created_at': created_at
                })
            
            logger.info(f"  📏 Групи за розміром+статус: {len(size_groups)}")
            
            # Обробляємо кожну групу
            consolidated_groups = 0
            
            for size_key, group_items in size_groups.items():
                if len(group_items) < 2:
                    continue  # Нічого консолідувати
                
                size, status = size_key.split('_')
                logger.info(f"    🔄 Група розмір {size}, статус {status}: {len(group_items)} товарів")
                
                # Вибираємо головний товар (найстаріший)
                main_item = min(group_items, key=lambda x: x['created_at'])
                duplicates = [item for item in group_items if item['id'] != main_item['id']]
                
                # Розраховуємо консолідовані дані
                total_quantity = sum(item['quantity'] or 1 for item in group_items)
                
                # Об'єднуємо заміри
                measurements = [item['measurementscm'] for item in group_items if item['measurementscm']]
                merged_measurement = merge_measurements(measurements)
                
                # Об'єднуємо маркування
                markings = [item['marking'] for item in group_items if item['marking']]
                merged_marking = ', '.join(set(markings)) if markings else None
                
                logger.info(f"      📦 Кількість: {total_quantity}")
                logger.info(f"      📏 Заміри: {merged_measurement}")
                logger.info(f"      🏷️ Маркування: {merged_marking}")
                
                # Оновлюємо головний товар
                cursor.execute("""
                    UPDATE products 
                    SET 
                        quantity = %s,
                        measurementscm = %s,
                        marking = %s,
                        updated_at = now()
                    WHERE id = %s
                """, (
                    total_quantity,
                    merged_measurement,
                    merged_marking,
                    main_item['id']
                ))
                
                # Переносимо зв'язки та видаляємо дублікати
                for dup in duplicates:
                    # Переносимо зв'язки
                    cursor.execute("UPDATE order_items SET product_id = %s WHERE product_id = %s", (main_item['id'], dup['id']))
                    cursor.execute("UPDATE order_items SET product_id = %s WHERE product_id = %s", (main_item['id'], dup['id']))
                    
                    # Видаляємо дублікат
                    cursor.execute("DELETE FROM products WHERE id = %s", (dup['id'],))
                    
                    logger.info(f"      🗑️ Видалено {dup['productnumber']} (ID: {dup['id']})")
                
                consolidated_groups += 1
            
            conn.commit()
            
            if consolidated_groups > 0:
                logger.info(f"  ✅ {base_number}: консолідовано {consolidated_groups} груп")
                
                # Показуємо результат
                cursor.execute("""
                    SELECT productnumber, price, quantity, sizeeu, measurementscm, marking
                    FROM products 
                    WHERE productnumber LIKE %s
                    ORDER BY productnumber
                """, (f"{base_number}%",))
                
                results = cursor.fetchall()
                logger.info(f"    📊 Результат: {len(results)} товарів")
                for row in results:
                    logger.info(f"      {row[0]}: к-сть={row[2]}, розмір={row[3]}, заміри={row[4]}")
            else:
                logger.info(f"  ℹ️ {base_number}: консолідація не потрібна")
    
    except Exception as e:
        logger.error(f"❌ Помилка консолідації {base_number}: {e}")
        conn.rollback()
    finally:
        conn.close()

def find_and_fix_same_size_duplicates():
    """Знаходить та виправляє дублікати з однаковими розмірами."""
    logger.info("🔍 ПОШУК ДУБЛІКАТІВ З ОДНАКОВИМИ РОЗМІРАМИ")
    logger.info("=" * 60)
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        with conn.cursor() as cursor:
            # Знаходимо ростовки з дублікатами розмірів
            cursor.execute("""
                WITH rostovka_analysis AS (
                    SELECT 
                        REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '') as base_number,
                        sizeeu,
                        statusid,
                        COUNT(*) as count
                    FROM products 
                    WHERE productnumber ~ '^[^(]+\\(\\d+\\)$|^[^(]+$'
                    GROUP BY 
                        REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', ''),
                        sizeeu,
                        statusid
                    HAVING COUNT(*) > 1
                )
                SELECT base_number, sizeeu, statusid, count
                FROM rostovka_analysis
                WHERE base_number != ''
                ORDER BY base_number, sizeeu
            """)
            
            duplicates = cursor.fetchall()
            
            logger.info(f"📊 Знайдено {len(duplicates)} груп з дублікатами розмірів:")
            
            processed_bases = set()
            
            for dup in duplicates:
                base_number, size, status, count = dup
                logger.info(f"  {base_number}: розмір {size}, статус {status} - {count} товарів")
                
                # Додаємо до списку для обробки
                processed_bases.add(base_number)
            
            # Обробляємо кожну ростовку
            logger.info(f"\n🚀 ОБРОБКА {len(processed_bases)} РОСТОВОК:")
            
            for base_number in sorted(processed_bases):
                consolidate_same_size_items(base_number)
    
    except Exception as e:
        logger.error(f"❌ Помилка пошуку: {e}")
    finally:
        conn.close()

def main():
    """Головна функція."""
    logger.info("🎯 РОЗШИРЕНА КОНСОЛІДАЦІЯ РОСТОВКИ")
    logger.info("=" * 60)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--fix-f1300":
        # Виправляємо конкретно Ф1300
        consolidate_same_size_items("Ф1300")
    else:
        # Знаходимо та виправляємо всі проблеми
        find_and_fix_same_size_duplicates()

if __name__ == "__main__":
    main()

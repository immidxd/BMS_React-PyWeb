#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
РОЗШИРЕНИЙ МЕНЕДЖЕР РОСТОВКИ
Розумна обробка ростовки товарів з синхронізацією цін
"""

import logging
import re
from typing import List, Dict, Optional, Tuple
from decimal import Decimal
from datetime import datetime

logger = logging.getLogger(__name__)

class RostovkaManager:
    """Клас для розумної обробки ростовки товарів."""
    
    def __init__(self, cursor, connection):
        self.cursor = cursor
        self.conn = connection
    
    def find_rostovka_group(self, base_number: str) -> List[Dict]:
        """Знаходить всю ростовку для базового номера товару."""
        # Видаляємо суфікси типу (1), (2) для пошуку базового номера
        clean_base = re.sub(r'\(\d+\)$', '', base_number).strip()
        
        # Шукаємо всі товари з цим базовим номером
        self.cursor.execute("""
            SELECT 
                p.id,
                p.productnumber,
                p.price,
                p.oldprice,
                p.quantity,
                p.sizeeu,
                p.measurementscm,
                p.created_at,
                p.updated_at,
                p.dateadded,
                b.brandname,
                t.typename,
                st.subtypename,
                p.model,
                p.marking,
                p.description,
                p.genderid,
                p.colorid,
                p.statusid
            FROM products p
            LEFT JOIN brands b ON p.brandid = b.id
            LEFT JOIN types t ON p.typeid = t.id
            LEFT JOIN subtypes st ON p.subtypeid = st.id
            WHERE 
                (p.productnumber = %s OR 
                 p.productnumber LIKE %s OR
                 REGEXP_REPLACE(p.productnumber, '\\(\\d+\\)$', '') = %s)
            ORDER BY p.created_at ASC
        """, (base_number, f"{clean_base}(%)", clean_base))
        
        rows = self.cursor.fetchall()
        
        # Конвертуємо в список словників
        rostovka_items = []
        for row in rows:
            item = {
                'id': row[0],
                'productnumber': row[1],
                'price': row[2],
                'oldprice': row[3],
                'quantity': row[4],
                'sizeeu': row[5],
                'measurementscm': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'dateadded': row[9],
                'brandname': row[10],
                'typename': row[11],
                'subtypename': row[12],
                'model': row[13],
                'marking': row[14],
                'description': row[15],
                'genderid': row[16],
                'colorid': row[17],
                'statusid': row[18]
            }
            rostovka_items.append(item)
        
        return rostovka_items
    
    def is_same_product(self, item1: Dict, item2: Dict) -> bool:
        """Перевіряє чи це той самий товар (для ростовки)."""
        # Критерії для визначення того самого товару
        criteria = [
            ('brandname', 'brandname'),
            ('typename', 'typename'),
            ('subtypename', 'subtypename'),
            ('model', 'model'),
            ('marking', 'marking'),
            ('genderid', 'genderid'),
            ('colorid', 'colorid')
        ]
        
        matches = 0
        for field1, field2 in criteria:
            val1 = str(item1.get(field1, '')).strip().lower()
            val2 = str(item2.get(field2, '')).strip().lower()
            
            # Пропускаємо порожні значення
            if not val1 and not val2:
                continue
            
            if val1 == val2:
                matches += 1
        
        # Вважаємо товари однаковими якщо збігається 4+ критерії
        return matches >= 4
    
    def get_latest_price_info(self, rostovka_items: List[Dict]) -> Tuple[Decimal, Optional[Decimal]]:
        """Визначає найостаннішу ціну для ростовки."""
        price_history = []
        
        for item in rostovka_items:
            current_price = item.get('price', 0)
            old_price = item.get('oldprice')
            updated_at = item.get('updated_at') or item.get('created_at')
            
            if current_price and current_price > 0:
                price_history.append({
                    'price': Decimal(str(current_price)),
                    'old_price': Decimal(str(old_price)) if old_price else None,
                    'updated_at': updated_at,
                    'item_id': item['id']
                })
        
        if not price_history:
            return Decimal('0'), None
        
        # Сортуємо за датою оновлення (найновіші спочатку)
        price_history.sort(key=lambda x: x['updated_at'], reverse=True)
        
        # Знаходимо записи з обома цінами (нова та стара)
        items_with_price_change = [p for p in price_history if p['old_price'] is not None]
        
        if items_with_price_change:
            # Якщо є записи зі зміною ціни, беремо найновіший
            latest_change = items_with_price_change[0]
            new_price = latest_change['price']
            old_price = latest_change['old_price']
            
            logger.info(f"Знайдено зміну ціни: {old_price} → {new_price}")
            return new_price, old_price
        
        # Якщо немає записів зі зміною ціни, беремо найменшу з усіх цін
        all_prices = [p['price'] for p in price_history]
        min_price = min(all_prices)
        
        # Шукаємо попередню ціну (другу найменшу або None)
        unique_prices = sorted(set(all_prices))
        old_price = unique_prices[1] if len(unique_prices) > 1 else None
        
        logger.info(f"Використовуємо найменшу ціну: {min_price}, попередня: {old_price}")
        return min_price, old_price
    
    def consolidate_rostovka_sizes(self, rostovka_items: List[Dict]) -> Dict:
        """Консолідує розміри ростовки в один запис."""
        if not rostovka_items:
            return {}
        
        # Групуємо по розмірах
        size_groups = {}
        for item in rostovka_items:
            size_key = f"{item.get('sizeeu', '')}_{item.get('measurementscm', '')}"
            if size_key not in size_groups:
                size_groups[size_key] = []
            size_groups[size_key].append(item)
        
        # Вибираємо головний товар (найстаріший за датою створення)
        main_item = min(rostovka_items, key=lambda x: x.get('created_at', datetime.now()))
        
        # Підраховуємо загальну кількість
        total_quantity = sum(item.get('quantity', 1) for item in rostovka_items)
        
        # Збираємо всі унікальні розміри
        unique_sizes = set()
        for item in rostovka_items:
            if item.get('sizeeu'):
                unique_sizes.add(item['sizeeu'])
        
        # Отримуємо найостаннішу ціну
        new_price, old_price = self.get_latest_price_info(rostovka_items)
        
        # Створюємо консолідований запис
        consolidated = main_item.copy()
        consolidated.update({
            'quantity': total_quantity,
            'price': new_price,
            'oldprice': old_price,
            'available_sizes': ', '.join(sorted(unique_sizes)) if unique_sizes else None,
            'updated_at': datetime.now()
        })
        
        return consolidated
    
    def process_rostovka(self, product_number: str, new_product_data: Dict) -> Optional[int]:
        """Обробляє товар як частину ростовки."""
        logger.info(f"🔄 Обробка ростовки для {product_number}")
        
        # Знаходимо всю ростовку
        rostovka_items = self.find_rostovka_group(product_number)
        
        if not rostovka_items:
            logger.info(f"Ростовка для {product_number} не знайдена, створюємо новий товар")
            return None
        
        logger.info(f"Знайдено {len(rostovka_items)} товарів в ростовці {product_number}")
        
        # Перевіряємо чи новий товар належить до цієї ростовки
        is_part_of_rostovka = False
        for existing_item in rostovka_items:
            if self.is_same_product(existing_item, new_product_data):
                is_part_of_rostovka = True
                break
        
        if not is_part_of_rostovka:
            logger.info(f"Новий товар {product_number} не належить до існуючої ростовки")
            return None
        
        # Додаємо новий товар до ростовки
        rostovka_items.append({
            'id': None,  # Новий товар
            'productnumber': product_number,
            'price': new_product_data.get('price', 0),
            'oldprice': new_product_data.get('oldprice'),
            'quantity': new_product_data.get('quantity', 1),
            'sizeeu': new_product_data.get('sizeeu'),
            'measurementscm': new_product_data.get('measurementscm'),
            'created_at': datetime.now(),
            'updated_at': datetime.now(),
            'dateadded': new_product_data.get('dateadded'),
            'brandname': new_product_data.get('_b_name'),
            'typename': new_product_data.get('_t_name'),
            'subtypename': new_product_data.get('_st_name'),
            'model': new_product_data.get('model'),
            'marking': new_product_data.get('marking'),
            'description': new_product_data.get('description'),
            'genderid': new_product_data.get('genderid'),
            'colorid': new_product_data.get('colorid'),
            'statusid': new_product_data.get('statusid', 2)
        })
        
        # Консолідуємо ростовку
        consolidated = self.consolidate_rostovka_sizes(rostovka_items)
        
        # Знаходимо головний товар для оновлення
        main_item = min([item for item in rostovka_items if item['id']], 
                       key=lambda x: x.get('created_at', datetime.now()))
        main_id = main_item['id']
        
        # Оновлюємо головний товар
        self.cursor.execute("""
            UPDATE products 
            SET 
                quantity = %s,
                price = %s,
                oldprice = %s,
                description = CASE 
                    WHEN %s IS NOT NULL AND %s != '' 
                    THEN COALESCE(description, '') || CASE 
                        WHEN description IS NULL OR description = '' THEN %s
                        ELSE '; розміри: ' || %s
                    END
                    ELSE description
                END,
                updated_at = now()
            WHERE id = %s
        """, (
            consolidated['quantity'],
            consolidated['price'],
            consolidated['oldprice'],
            consolidated['available_sizes'],
            consolidated['available_sizes'],
            consolidated['available_sizes'],
            consolidated['available_sizes'],
            main_id
        ))
        
        # Видаляємо дублікати (залишаємо тільки головний)
        duplicate_ids = [item['id'] for item in rostovka_items 
                        if item['id'] and item['id'] != main_id]
        
        if duplicate_ids:
            logger.info(f"Видаляємо {len(duplicate_ids)} дублікатів ростовки")
            
            # Спочатку переносимо зв'язки з замовленнями (order_details та order_items)
            self.cursor.execute("""
                UPDATE order_details 
                SET product_id = %s 
                WHERE product_id = ANY(%s)
            """, (main_id, duplicate_ids))
            
            self.cursor.execute("""
                UPDATE order_items 
                SET product_id = %s 
                WHERE product_id = ANY(%s)
            """, (main_id, duplicate_ids))
            
            # Тепер видаляємо дублікати
            self.cursor.execute("""
                DELETE FROM products 
                WHERE id = ANY(%s)
            """, (duplicate_ids,))
            
            deleted_count = self.cursor.rowcount
            logger.info(f"Видалено {deleted_count} дублікатів ростовки")
        
        self.conn.commit()
        
        logger.info(f"✅ Ростовка {product_number} оновлена: "
                   f"кількість={consolidated['quantity']}, "
                   f"ціна={consolidated['price']}")
        
        return main_id
    
    def sync_rostovka_prices(self, base_number: str):
        """Синхронізує ціни в рамках ростовки."""
        rostovka_items = self.find_rostovka_group(base_number)
        
        if len(rostovka_items) < 2:
            return  # Немає що синхронізувати
        
        # Отримуємо найостаннішу ціну
        new_price, old_price = self.get_latest_price_info(rostovka_items)
        
        # Оновлюємо ціни у всіх товарах ростовки
        rostovka_ids = [item['id'] for item in rostovka_items]
        
        self.cursor.execute("""
            UPDATE products 
            SET 
                price = %s,
                oldprice = %s,
                updated_at = now()
            WHERE id = ANY(%s)
        """, (new_price, old_price, rostovka_ids))
        
        self.conn.commit()
        
        logger.info(f"🔄 Синхронізовано ціни ростовки {base_number}: "
                   f"{old_price} → {new_price} для {len(rostovka_ids)} товарів")


def improved_insert_or_update_product(cursor, p_data, conn):
    """Покращена функція вставки/оновлення товару з розумною ростовкою."""
    pnum = p_data['productnumber']
    logger.info(f"📦 Обробка товару: {pnum}")
    
    # Створюємо менеджер ростовки
    rostovka_manager = RostovkaManager(cursor, conn)
    
    # Спочатку перевіряємо чи це частина існуючої ростовки
    rostovka_id = rostovka_manager.process_rostovka(pnum, p_data)
    if rostovka_id:
        logger.info(f"✅ Товар {pnum} оброблено як частину ростовки (ID: {rostovka_id})")
        return rostovka_id
    
    # Якщо не ростовка, використовуємо стандартну логіку
    cursor.execute("SELECT id FROM products WHERE productnumber=%s", (pnum,))
    existing = cursor.fetchone()
    
    if existing:
        # Товар існує, оновлюємо його
        existing_id = existing[0]
        
        # Перевіряємо чи це точно той самий товар
        cursor.execute("""
            SELECT brandname, typename, subtypename, model, marking
            FROM products p
            LEFT JOIN brands b ON p.brandid = b.id
            LEFT JOIN types t ON p.typeid = t.id  
            LEFT JOIN subtypes st ON p.subtypeid = st.id
            WHERE p.id = %s
        """, (existing_id,))
        
        existing_data = cursor.fetchone()
        if existing_data:
            # Порівнюємо характеристики
            same_brand = str(existing_data[0] or '').lower() == str(p_data.get('_b_name', '')).lower()
            same_type = str(existing_data[1] or '').lower() == str(p_data.get('_t_name', '')).lower()
            same_model = str(existing_data[3] or '').lower() == str(p_data.get('model', '')).lower()
            
            if same_brand and same_type and same_model:
                # Це той самий товар, оновлюємо його
                update_fields = []
                update_values = []
                
                for field, value in p_data.items():
                    if field not in ['productnumber', '_b_name', '_t_name', '_st_name'] and value is not None:
                        update_fields.append(f"{field} = %s")
                        update_values.append(value)
                
                if update_fields:
                    update_query = f"""
                        UPDATE products 
                        SET {', '.join(update_fields)}, updated_at = now()
                        WHERE id = %s
                    """
                    cursor.execute(update_query, update_values + [existing_id])
                    conn.commit()
                    
                    logger.info(f"🔄 Оновлено існуючий товар {pnum} (ID: {existing_id})")
                    return existing_id
            else:
                # Різний товар з тим самим номером, створюємо з суфіксом
                base = re.sub(r'\(\d+\)$', '', pnum).strip()
                suffix = 1
                
                while True:
                    new_number = f"{base}({suffix})"
                    cursor.execute("SELECT id FROM products WHERE productnumber = %s", (new_number,))
                    if not cursor.fetchone():
                        p_data['productnumber'] = new_number
                        break
                    suffix += 1
                
                logger.info(f"📝 Створено новий номер для різного товару: {pnum} → {new_number}")
    
    # Створюємо новий товар
    final_pnum = p_data['productnumber']
    
    # Підготовка даних для вставки
    clean_data = {k: v for k, v in p_data.items() if not k.startswith('_')}
    
    columns = ', '.join(clean_data.keys())
    placeholders = ', '.join(['%s'] * len(clean_data))
    values = tuple(clean_data.values())
    
    insert_query = f"INSERT INTO products ({columns}) VALUES ({placeholders}) RETURNING id"
    cursor.execute(insert_query, values)
    
    new_id = cursor.fetchone()[0]
    conn.commit()
    
    logger.info(f"✅ Створено новий товар {final_pnum} (ID: {new_id})")
    
    # Після створення перевіряємо чи потрібно синхронізувати ціни в ростовці
    base_number = re.sub(r'\(\d+\)$', '', final_pnum).strip()
    rostovka_manager.sync_rostovka_prices(base_number)
    
    return new_id


def batch_consolidate_all_rostovka(conn):
    """Пакетна консолідація всіх ростовок в БД."""
    logger.info("🔄 ПАКЕТНА КОНСОЛІДАЦІЯ ВСІХ РОСТОВОК")
    
    with conn.cursor() as cursor:
        # Знаходимо всі базові номери товарів
        cursor.execute("""
            SELECT DISTINCT REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '') as base_number
            FROM products
            WHERE productnumber ~ '^[^(]+$|^[^(]+\\(\\d+\\)$'
            ORDER BY base_number
        """)
        
        base_numbers = [row[0] for row in cursor.fetchall()]
        logger.info(f"Знайдено {len(base_numbers)} базових номерів для перевірки")
        
        rostovka_manager = RostovkaManager(cursor, conn)
        consolidated_count = 0
        
        for i, base_number in enumerate(base_numbers, 1):
            if i % 100 == 0:
                logger.info(f"Прогрес консолідації: {i}/{len(base_numbers)} ({i/len(base_numbers)*100:.1f}%)")
            
            try:
                rostovka_items = rostovka_manager.find_rostovka_group(base_number)
                
                # Якщо знайдено більше 1 товару з цим базовим номером
                if len(rostovka_items) > 1:
                    # Перевіряємо чи це справді ростовка
                    first_item = rostovka_items[0]
                    is_rostovka = all(
                        rostovka_manager.is_same_product(first_item, item) 
                        for item in rostovka_items[1:]
                    )
                    
                    if is_rostovka:
                        # Консолідуємо ростовку
                        consolidated = rostovka_manager.consolidate_rostovka_sizes(rostovka_items)
                        
                        # Оновлюємо головний товар
                        main_id = min(item['id'] for item in rostovka_items)
                        
                        cursor.execute("""
                            UPDATE products 
                            SET 
                                quantity = %s,
                                price = %s,
                                oldprice = %s,
                                description = CASE 
                                    WHEN %s IS NOT NULL 
                                    THEN COALESCE(description, '') || '; розміри: ' || %s
                                    ELSE description
                                END,
                                updated_at = now()
                            WHERE id = %s
                        """, (
                            consolidated['quantity'],
                            consolidated['price'],
                            consolidated['oldprice'],
                            consolidated['available_sizes'],
                            consolidated['available_sizes'],
                            main_id
                        ))
                        
                        # Видаляємо дублікати
                        duplicate_ids = [item['id'] for item in rostovka_items if item['id'] != main_id]
                        if duplicate_ids:
                            # Переносимо зв'язки з замовленнями (обидві таблиці)
                            cursor.execute("""
                                UPDATE order_details 
                                SET product_id = %s 
                                WHERE product_id = ANY(%s)
                            """, (main_id, duplicate_ids))
                            
                            cursor.execute("""
                                UPDATE order_items 
                                SET product_id = %s 
                                WHERE product_id = ANY(%s)
                            """, (main_id, duplicate_ids))
                            
                            # Видаляємо дублікати
                            cursor.execute("DELETE FROM products WHERE id = ANY(%s)", (duplicate_ids,))
                        
                        consolidated_count += 1
                        logger.info(f"✅ Консолідовано ростовку {base_number}: {len(rostovka_items)} → 1 товар")
                
            except Exception as e:
                logger.error(f"❌ Помилка консолідації {base_number}: {e}")
                conn.rollback()
                continue
        
        conn.commit()
        logger.info(f"🎉 Консолідація завершена: оброблено {consolidated_count} ростовок")


if __name__ == "__main__":
    # Тестування
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from googlesheets_pars import connect_to_db
    
    conn = connect_to_db()
    if conn:
        try:
            batch_consolidate_all_rostovka(conn)
        finally:
            conn.close()

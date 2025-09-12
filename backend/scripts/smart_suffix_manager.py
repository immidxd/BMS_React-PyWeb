#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
РОЗУМНИЙ МЕНЕДЖЕР СУФІКСІВ
Розрізняє автоматичні суфікси (1), (2) та ручні суфікси -2, -3
"""

import logging
import re
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

class SuffixType:
    """Типи суфіксів номерів товарів."""
    AUTO_DUPLICATE = "auto"    # (1), (2), (3) - автоматичні дублікати
    MANUAL_VARIANT = "manual"  # -2, -3, -4 - ручні варіанти
    CLEAN = "clean"           # без суфіксів

class SmartSuffixManager:
    """Розумний менеджер для роботи з суфіксами номерів товарів."""
    
    def __init__(self, cursor, connection):
        self.cursor = cursor
        self.conn = connection
    
    def analyze_suffix(self, product_number: str) -> Tuple[str, str, str]:
        """Аналізує суфікс номера товару.
        
        Returns:
            (base_number, suffix, suffix_type)
        """
        # Автоматичні суфікси: (1), (2), (3)
        auto_match = re.search(r'^(.+)\((\d+)\)$', product_number)
        if auto_match:
            base = auto_match.group(1).strip()
            suffix = auto_match.group(2)
            return base, f"({suffix})", SuffixType.AUTO_DUPLICATE
        
        # Ручні суфікси: -2, -3, -4
        manual_match = re.search(r'^(.+)-(\d+)$', product_number)
        if manual_match:
            base = manual_match.group(1).strip()
            suffix = manual_match.group(2)
            return base, f"-{suffix}", SuffixType.MANUAL_VARIANT
        
        # Без суфіксів
        return product_number.strip(), "", SuffixType.CLEAN
    
    def find_product_family(self, product_number: str) -> Dict[str, List[Dict]]:
        """Знаходить всю родину товарів (ростовки та варіанти).
        
        Returns:
            {
                'rostovka': [список автоматичних дублікатів],
                'variants': [список ручних варіантів],
                'base': [базовий товар без суфіксів]
            }
        """
        base_number, _, _ = self.analyze_suffix(product_number)
        
        # Шукаємо всі товари з цим базовим номером
        self.cursor.execute("""
            SELECT 
                p.id, p.productnumber, p.price, p.oldprice, p.quantity,
                p.sizeeu, p.measurementscm, p.description,
                p.created_at, p.updated_at,
                b.brandname, t.typename, st.subtypename, 
                p.model, p.marking, p.genderid, p.colorid, p.statusid
            FROM products p
            LEFT JOIN brands b ON p.brandid = b.id
            LEFT JOIN types t ON p.typeid = t.id
            LEFT JOIN subtypes st ON p.subtypeid = st.id
            WHERE 
                p.productnumber = %s OR
                p.productnumber LIKE %s OR
                p.productnumber LIKE %s
            ORDER BY p.created_at ASC
        """, (
            base_number,                    # Точний базовий номер
            f"{base_number}(%)",           # Автоматичні суфікси (1), (2)
            f"{base_number}-%"             # Ручні суфікси -2, -3
        ))
        
        rows = self.cursor.fetchall()
        
        # Групуємо за типом суфіксів
        family = {
            'rostovka': [],      # Автоматичні дублікати - ростовка
            'variants': [],      # Ручні варіанти - різні товари
            'base': []          # Базовий товар
        }
        
        for row in rows:
            item = {
                'id': row[0], 'productnumber': row[1], 'price': row[2], 'oldprice': row[3],
                'quantity': row[4], 'sizeeu': row[5], 'measurementscm': row[6],
                'description': row[7], 'created_at': row[8], 'updated_at': row[9],
                'brandname': row[10], 'typename': row[11], 'subtypename': row[12],
                'model': row[13], 'marking': row[14], 'genderid': row[15],
                'colorid': row[16], 'statusid': row[17]
            }
            
            _, _, suffix_type = self.analyze_suffix(row[1])
            
            if suffix_type == SuffixType.AUTO_DUPLICATE:
                family['rostovka'].append(item)
            elif suffix_type == SuffixType.MANUAL_VARIANT:
                family['variants'].append(item)
            else:
                family['base'].append(item)
        
        return family
    
    def is_same_product(self, item1: Dict, item2: Dict) -> bool:
        """Перевіряє чи це той самий товар (для ростовки)."""
        # Критерії для ростовки
        same_brand = str(item1.get('brandname', '')).lower() == str(item2.get('brandname', '')).lower()
        same_type = str(item1.get('typename', '')).lower() == str(item2.get('typename', '')).lower()
        same_model = str(item1.get('model', '')).lower() == str(item2.get('model', '')).lower()
        same_marking = str(item1.get('marking', '')).lower() == str(item2.get('marking', '')).lower()
        same_color = item1.get('colorid') == item2.get('colorid')
        
        # Для ростовки достатньо 3+ збігів з ключових характеристик
        matches = sum([same_brand, same_type, same_model, same_marking, same_color])
        return matches >= 3
    
    def consolidate_rostovka(self, base_number: str) -> bool:
        """Консолідує ростовку (автоматичні дублікати) в один запис."""
        family = self.find_product_family(base_number)
        
        # Об'єднуємо базовий товар з ростовкою
        all_rostovka = family['base'] + family['rostovka']
        
        if len(all_rostovka) < 2:
            return False  # Немає що консолідувати
        
        logger.info(f"🔄 Консолідація ростовки {base_number}: {len(all_rostovka)} товарів")
        
        # Перевіряємо чи це справді ростовка
        first_item = all_rostovka[0]
        is_rostovka = all(self.is_same_product(first_item, item) for item in all_rostovka[1:])
        
        if not is_rostovka:
            logger.info(f"❌ {base_number}: товари не є ростовкою (різні характеристики)")
            return False
        
        # Вибираємо головний товар (базовий або найстаріший)
        main_item = None
        if family['base']:
            main_item = family['base'][0]  # Базовий товар без суфіксів
        else:
            main_item = min(all_rostovka, key=lambda x: x['created_at'])  # Найстаріший
        
        main_id = main_item['id']
        
        # Розраховуємо консолідовані дані
        total_quantity = sum(item['quantity'] or 1 for item in all_rostovka)
        
        # Логіка цін згідно з вимогами
        prices_with_old = [item for item in all_rostovka if item['oldprice']]
        
        if prices_with_old:
            # Є ціни зі зміною - беремо найостаннішу
            latest_price_change = max(prices_with_old, key=lambda x: x['updated_at'])
            new_price = latest_price_change['price']
            old_price = latest_price_change['oldprice']
            logger.info(f"💰 Використовуємо останню зміну ціни: {old_price} → {new_price}")
        else:
            # Немає змін цін - беремо найменшу як основну
            all_prices = [item['price'] for item in all_rostovka if item['price'] and item['price'] > 0]
            if all_prices:
                new_price = min(all_prices)
                # Попередня ціна - друга найменша або None
                unique_prices = sorted(set(all_prices))
                old_price = unique_prices[1] if len(unique_prices) > 1 else None
                logger.info(f"💰 Використовуємо найменшу ціну: {new_price}, попередня: {old_price}")
            else:
                new_price, old_price = 0, None
        
        # Збираємо всі унікальні розміри
        unique_sizes = set()
        for item in all_rostovka:
            if item['sizeeu']:
                unique_sizes.add(str(item['sizeeu']))
        
        sizes_info = ', '.join(sorted(unique_sizes)) if unique_sizes else None
        
        # Оновлюємо головний товар
        self.cursor.execute("""
            UPDATE products 
            SET 
                productnumber = %s,  -- Прибираємо суфікси
                quantity = %s,
                price = %s,
                oldprice = %s,
                description = CASE 
                    WHEN %s IS NOT NULL 
                    THEN COALESCE(description, '') || 
                         CASE WHEN description LIKE '%розміри:%' 
                              THEN REGEXP_REPLACE(description, '; розміри:.*$', '; розміри: ' || %s)
                              ELSE '; розміри: ' || %s
                         END
                    ELSE description
                END,
                updated_at = now()
            WHERE id = %s
        """, (
            base_number,  # Прибираємо суфікси з головного товару
            total_quantity,
            new_price,
            old_price,
            sizes_info,
            sizes_info,
            sizes_info,
            main_id
        ))
        
        # Видаляємо дублікати (зберігаючи зв'язки)
        duplicate_ids = [item['id'] for item in all_rostovka if item['id'] != main_id]
        
        if duplicate_ids:
            logger.info(f"🗑️ Видаляємо {len(duplicate_ids)} автоматичних дублікатів")
            
            # Переносимо зв'язки з замовленнями
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
            
            # Видаляємо дублікати
            self.cursor.execute("DELETE FROM products WHERE id = ANY(%s)", (duplicate_ids,))
            
            deleted_count = self.cursor.rowcount
            logger.info(f"✅ Видалено {deleted_count} автоматичних дублікатів")
        
        self.conn.commit()
        
        logger.info(f"✅ Ростовка {base_number} консолідована: "
                   f"кількість={total_quantity}, ціна={new_price}, розміри={sizes_info}")
        
        return True
    
    def clean_auto_duplicates_only(self):
        """Очищає тільки автоматичні дублікати (1), (2), залишаючи ручні -2, -3."""
        logger.info("🧹 ОЧИЩЕННЯ АВТОМАТИЧНИХ ДУБЛІКАТІВ")
        logger.info("=" * 50)
        
        # Знаходимо всі товари з автоматичними суфіксами
        self.cursor.execute("""
            SELECT DISTINCT REGEXP_REPLACE(productnumber, '\\(\\d+\\)$', '') as base_number
            FROM products
            WHERE productnumber ~ '\\(\\d+\\)$'
            ORDER BY base_number
        """)
        
        base_numbers = [row[0] for row in self.cursor.fetchall()]
        logger.info(f"Знайдено {len(base_numbers)} базових номерів з автоматичними суфіксами")
        
        consolidated_count = 0
        
        for base_number in base_numbers:
            try:
                if self.consolidate_rostovka(base_number):
                    consolidated_count += 1
                    
                    if consolidated_count % 10 == 0:
                        logger.info(f"Прогрес: {consolidated_count} ростовок консолідовано")
                        
            except Exception as e:
                logger.error(f"❌ Помилка консолідації {base_number}: {e}")
                self.conn.rollback()
                continue
        
        logger.info(f"🎉 Очищення завершено: {consolidated_count} ростовок консолідовано")
        
        return consolidated_count

def demonstrate_suffix_logic():
    """Демонструє роботу з різними типами суфіксів."""
    logger.info("🎭 ДЕМОНСТРАЦІЯ ЛОГІКИ СУФІКСІВ")
    logger.info("=" * 50)
    
    test_numbers = [
        "Ф986",      # Базовий
        "Ф986(1)",   # Автоматичний дублікат - ростовка
        "Ф986(2)",   # Автоматичний дублікат - ростовка  
        "Ф986-2",    # Ручний варіант - інший товар
        "Ф986-3",    # Ручний варіант - інший товар
        "А123",      # Інший базовий
        "А123(1)",   # Його ростовка
        "Б456-2"     # Ручний варіант
    ]
    
    manager = SmartSuffixManager(None, None)
    
    logger.info("📋 АНАЛІЗ НОМЕРІВ:")
    logger.info("Номер        | Базовий | Суфікс | Тип")
    logger.info("-" * 45)
    
    for number in test_numbers:
        base, suffix, suffix_type = manager.analyze_suffix(number)
        type_name = {
            SuffixType.AUTO_DUPLICATE: "Ростовка",
            SuffixType.MANUAL_VARIANT: "Варіант", 
            SuffixType.CLEAN: "Базовий"
        }[suffix_type]
        
        logger.info(f"{number:<12} | {base:<7} | {suffix:<6} | {type_name}")
    
    logger.info("\n🎯 ЛОГІКА ОБРОБКИ:")
    logger.info("• Ф986, Ф986(1), Ф986(2) → КОНСОЛІДУВАТИ в один запис")
    logger.info("• Ф986-2, Ф986-3 → ЗАЛИШИТИ як окремі товари")
    logger.info("• А123, А123(1) → КОНСОЛІДУВАТИ в один запис")

def fix_f986_example():
    """Виправляє конкретний приклад Ф986."""
    logger.info("🔧 ВИПРАВЛЕННЯ ПРИКЛАДУ Ф986")
    logger.info("=" * 50)
    
    from googlesheets_pars import connect_to_db
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        manager = SmartSuffixManager(conn.cursor(), conn)
        
        # Показуємо поточний стан
        conn.cursor().execute("""
            SELECT productnumber, price, oldprice, quantity, sizeeu, description
            FROM products 
            WHERE productnumber LIKE 'Ф986%'
            ORDER BY productnumber
        """)
        
        before = conn.cursor().fetchall()
        
        logger.info("📊 ДО ВИПРАВЛЕННЯ:")
        for row in before:
            logger.info(f"  {row[0]}: ціна={row[1]}, к-сть={row[3]}, розмір={row[4]}")
        
        # Консолідуємо ростовку
        success = manager.consolidate_rostovka("Ф986")
        
        if success:
            # Показуємо результат
            conn.cursor().execute("""
                SELECT productnumber, price, oldprice, quantity, sizeeu, description
                FROM products 
                WHERE productnumber LIKE 'Ф986%'
                ORDER BY productnumber
            """)
            
            after = conn.cursor().fetchall()
            
            logger.info("\n📊 ПІСЛЯ ВИПРАВЛЕННЯ:")
            for row in after:
                logger.info(f"  {row[0]}: ціна={row[1]}, стара={row[2]}, к-сть={row[3]}, розмір={row[4]}")
                if 'розміри:' in str(row[5]):
                    sizes_part = str(row[5]).split('розміри:')[-1].strip()
                    logger.info(f"    Доступні розміри: {sizes_part}")
        
    except Exception as e:
        logger.error(f"❌ Помилка виправлення: {e}")
        conn.rollback()
    finally:
        conn.close()

def clean_all_auto_duplicates():
    """Очищає всі автоматичні дублікати в БД."""
    logger.info("🧹 ГЛОБАЛЬНЕ ОЧИЩЕННЯ АВТОМАТИЧНИХ ДУБЛІКАТІВ")
    logger.info("=" * 60)
    
    from googlesheets_pars import connect_to_db
    
    conn = connect_to_db()
    if not conn:
        return
    
    try:
        # Показуємо статистику до очищення
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_products,
                    COUNT(*) FILTER (WHERE productnumber ~ '\\(\\d+\\)$') as auto_duplicates,
                    COUNT(*) FILTER (WHERE productnumber ~ '-\\d+$') as manual_variants
                FROM products
            """)
            
            stats = cursor.fetchone()
            logger.info(f"📊 СТАТИСТИКА ДО ОЧИЩЕННЯ:")
            logger.info(f"  Всього товарів: {stats[0]}")
            logger.info(f"  Автоматичних дублікатів (1), (2): {stats[1]}")
            logger.info(f"  Ручних варіантів -2, -3: {stats[2]}")
        
        # Запускаємо очищення
        manager = SmartSuffixManager(conn.cursor(), conn)
        consolidated = manager.clean_auto_duplicates_only()
        
        # Показуємо статистику після очищення
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_products,
                    COUNT(*) FILTER (WHERE productnumber ~ '\\(\\d+\\)$') as auto_duplicates,
                    COUNT(*) FILTER (WHERE productnumber ~ '-\\d+$') as manual_variants,
                    SUM(quantity) as total_quantity
                FROM products
            """)
            
            after_stats = cursor.fetchone()
            logger.info(f"\n📊 СТАТИСТИКА ПІСЛЯ ОЧИЩЕННЯ:")
            logger.info(f"  Всього товарів: {after_stats[0]}")
            logger.info(f"  Автоматичних дублікатів: {after_stats[1]}")
            logger.info(f"  Ручних варіантів: {after_stats[2]}")
            logger.info(f"  Загальна кількість: {after_stats[3]}")
            logger.info(f"  Видалено дублікатів: {stats[0] - after_stats[0]}")
            logger.info(f"  Консолідовано ростовок: {consolidated}")
    
    except Exception as e:
        logger.error(f"❌ Помилка очищення: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--demo":
            demonstrate_suffix_logic()
        elif sys.argv[1] == "--fix-f986":
            fix_f986_example()
        elif sys.argv[1] == "--clean-all":
            clean_all_auto_duplicates()
    else:
        logger.info("🎯 ДОСТУПНІ КОМАНДИ:")
        logger.info("--demo      : Демонстрація логіки суфіксів")
        logger.info("--fix-f986  : Виправлення прикладу Ф986")
        logger.info("--clean-all : Очищення всіх автоматичних дублікатів")
        
        # За замовчуванням показуємо демо
        demonstrate_suffix_logic()

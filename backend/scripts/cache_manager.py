#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
УТИЛІТА УПРАВЛІННЯ КЕШЕМ ПАРСЕРА
Дозволяє очищати, аналізувати та оптимізувати кеш
"""

import os
import pickle
import shutil
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any

# Шляхи до кешу
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
SHEETS_CACHE_FILE = os.path.join(CACHE_DIR, "sheets_cache.pkl")
ORDERS_HASH_FILE = os.path.join(CACHE_DIR, "orders_hashes.pkl")
PRODUCTS_CACHE_FILE = os.path.join(CACHE_DIR, "products_cache.pkl")

class CacheUtility:
    """Утиліта для управління кешем."""
    
    def __init__(self):
        self.cache_files = {
            'sheets': SHEETS_CACHE_FILE,
            'orders': ORDERS_HASH_FILE,
            'products': PRODUCTS_CACHE_FILE
        }
    
    def load_cache(self, file_path: str) -> Any:
        """Завантажує кеш з файлу."""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            print(f"Помилка завантаження кешу {file_path}: {e}")
        return None
    
    def get_file_size(self, file_path: str) -> str:
        """Повертає розмір файлу у зручному форматі."""
        if not os.path.exists(file_path):
            return "0 B"
        
        size = os.path.getsize(file_path)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    
    def analyze_cache(self):
        """Аналізує стан кешу."""
        print("📊 АНАЛІЗ КЕШУ ПАРСЕРА")
        print("=" * 50)
        
        if not os.path.exists(CACHE_DIR):
            print("❌ Директорія кешу не існує")
            return
        
        total_size = 0
        
        for cache_name, file_path in self.cache_files.items():
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                total_size += size
                
                # Завантажуємо та аналізуємо вміст
                cache_data = self.load_cache(file_path)
                
                print(f"\n🗂️  {cache_name.upper()} CACHE:")
                print(f"   Файл: {os.path.basename(file_path)}")
                print(f"   Розмір: {self.get_file_size(file_path)}")
                print(f"   Модифіковано: {datetime.fromtimestamp(os.path.getmtime(file_path))}")
                
                if cache_data is not None:
                    if isinstance(cache_data, dict):
                        print(f"   Записів: {len(cache_data)}")
                        if cache_name == 'sheets':
                            # Аналіз кешу аркушів
                            recent_sheets = 0
                            old_sheets = 0
                            cutoff = datetime.now() - timedelta(days=30)
                            
                            for sheet_name in cache_data.keys():
                                try:
                                    # Спробуємо витягти дату з назви аркуша
                                    date_parts = sheet_name.split('.')
                                    if len(date_parts) == 3:
                                        day, month, year = map(int, date_parts)
                                        sheet_date = datetime(year, month, day)
                                        if sheet_date >= cutoff:
                                            recent_sheets += 1
                                        else:
                                            old_sheets += 1
                                except:
                                    pass
                            
                            print(f"   Нові аркуші (< 30 днів): {recent_sheets}")
                            print(f"   Старі аркуші (> 30 днів): {old_sheets}")
                    
                    elif isinstance(cache_data, set):
                        print(f"   Записів: {len(cache_data)}")
                    
                    else:
                        print(f"   Тип даних: {type(cache_data)}")
            else:
                print(f"\n❌ {cache_name.upper()} CACHE: файл не існує")
        
        print(f"\n📦 ЗАГАЛЬНИЙ РОЗМІР КЕШУ: {self.get_file_size_bytes(total_size)}")
        
        # Рекомендації
        print("\n💡 РЕКОМЕНДАЦІЇ:")
        if total_size > 100 * 1024 * 1024:  # > 100MB
            print("   - Кеш досить великий, розгляньте очищення старих записів")
        
        sheets_cache = self.load_cache(SHEETS_CACHE_FILE)
        if sheets_cache and len(sheets_cache) > 200:
            print("   - Багато закешованих аркушів, можна очистити старі")
        
        orders_cache = self.load_cache(ORDERS_HASH_FILE)
        if orders_cache and len(orders_cache) > 50000:
            print("   - Багато хешів замовлень, можна очистити старі")
    
    def get_file_size_bytes(self, size_bytes: int) -> str:
        """Конвертує байти у зручний формат."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"
    
    def clear_cache(self, cache_type: str = 'all'):
        """Очищає кеш."""
        print(f"🧹 ОЧИЩЕННЯ КЕШУ: {cache_type.upper()}")
        
        if cache_type == 'all':
            if os.path.exists(CACHE_DIR):
                shutil.rmtree(CACHE_DIR)
                print("✅ Весь кеш очищено")
            os.makedirs(CACHE_DIR, exist_ok=True)
        
        elif cache_type in self.cache_files:
            file_path = self.cache_files[cache_type]
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"✅ Кеш {cache_type} очищено")
            else:
                print(f"❌ Файл кешу {cache_type} не існує")
        
        else:
            print(f"❌ Невідомий тип кешу: {cache_type}")
            print(f"Доступні типи: {', '.join(self.cache_files.keys())}, all")
    
    def optimize_cache(self):
        """Оптимізує кеш, видаляючи старі записи."""
        print("⚡ ОПТИМІЗАЦІЯ КЕШУ")
        print("=" * 30)
        
        # Оптимізація кешу аркушів - видаляємо аркуші старше 60 днів
        sheets_cache = self.load_cache(SHEETS_CACHE_FILE)
        if sheets_cache:
            cutoff = datetime.now() - timedelta(days=60)
            original_count = len(sheets_cache)
            optimized_cache = {}
            
            for sheet_name, hash_value in sheets_cache.items():
                try:
                    # Спробуємо витягти дату з назви аркуша
                    date_parts = sheet_name.split('.')
                    if len(date_parts) == 3:
                        day, month, year = map(int, date_parts)
                        sheet_date = datetime(year, month, day)
                        if sheet_date >= cutoff:
                            optimized_cache[sheet_name] = hash_value
                    else:
                        # Зберігаємо аркуші без дати
                        optimized_cache[sheet_name] = hash_value
                except:
                    # Зберігаємо аркуші з некоректними датами
                    optimized_cache[sheet_name] = hash_value
            
            if len(optimized_cache) < original_count:
                with open(SHEETS_CACHE_FILE, 'wb') as f:
                    pickle.dump(optimized_cache, f)
                removed = original_count - len(optimized_cache)
                print(f"📄 Кеш аркушів: видалено {removed} старих записів")
            else:
                print("📄 Кеш аркушів: оптимізація не потрібна")
        
        # Оптимізація кешу замовлень - обмежуємо до 30000 записів
        orders_cache = self.load_cache(ORDERS_HASH_FILE)
        if orders_cache and len(orders_cache) > 30000:
            # Конвертуємо set в list, беремо останні 30000
            orders_list = list(orders_cache)
            optimized_orders = set(orders_list[-30000:])
            
            with open(ORDERS_HASH_FILE, 'wb') as f:
                pickle.dump(optimized_orders, f)
            
            removed = len(orders_cache) - len(optimized_orders)
            print(f"🛒 Кеш замовлень: видалено {removed} старих записів")
        else:
            print("🛒 Кеш замовлень: оптимізація не потрібна")
        
        print("✅ Оптимізація завершена")
    
    def backup_cache(self, backup_dir: str):
        """Створює резервну копію кешу."""
        print(f"💾 СТВОРЕННЯ РЕЗЕРВНОЇ КОПІЇ: {backup_dir}")
        
        if not os.path.exists(CACHE_DIR):
            print("❌ Кеш не існує")
            return
        
        os.makedirs(backup_dir, exist_ok=True)
        
        for cache_name, file_path in self.cache_files.items():
            if os.path.exists(file_path):
                backup_file = os.path.join(backup_dir, os.path.basename(file_path))
                shutil.copy2(file_path, backup_file)
                print(f"✅ {cache_name} -> {backup_file}")
        
        print("✅ Резервна копія створена")
    
    def restore_cache(self, backup_dir: str):
        """Відновлює кеш з резервної копії."""
        print(f"📥 ВІДНОВЛЕННЯ З РЕЗЕРВНОЇ КОПІЇ: {backup_dir}")
        
        if not os.path.exists(backup_dir):
            print("❌ Директорія резервної копії не існує")
            return
        
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        for cache_name, file_path in self.cache_files.items():
            backup_file = os.path.join(backup_dir, os.path.basename(file_path))
            if os.path.exists(backup_file):
                shutil.copy2(backup_file, file_path)
                print(f"✅ {backup_file} -> {cache_name}")
            else:
                print(f"⚠️  Файл {cache_name} не знайдено в резервній копії")
        
        print("✅ Відновлення завершено")

def main():
    """Основна функція."""
    parser = argparse.ArgumentParser(description='Утиліта управління кешем парсера')
    parser.add_argument('action', choices=['analyze', 'clear', 'optimize', 'backup', 'restore'], 
                       help='Дія для виконання')
    parser.add_argument('--type', choices=['sheets', 'orders', 'products', 'all'], default='all',
                       help='Тип кешу для очищення')
    parser.add_argument('--backup-dir', help='Директорія для резервної копії')
    
    args = parser.parse_args()
    
    cache_util = CacheUtility()
    
    if args.action == 'analyze':
        cache_util.analyze_cache()
    
    elif args.action == 'clear':
        cache_util.clear_cache(args.type)
    
    elif args.action == 'optimize':
        cache_util.optimize_cache()
    
    elif args.action == 'backup':
        if not args.backup_dir:
            print("❌ Потрібно вказати --backup-dir")
            return
        cache_util.backup_cache(args.backup_dir)
    
    elif args.action == 'restore':
        if not args.backup_dir:
            print("❌ Потрібно вказати --backup-dir")
            return
        cache_util.restore_cache(args.backup_dir)

if __name__ == "__main__":
    main() 
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ТЕСТУВАННЯ ПАРАЛЕЛЬНОГО ПАРСИНГУ
Скрипт для тестування та порівняння продуктивності
"""

import asyncio
import time
import sys
import os
import logging
from typing import List, Dict

# Додаємо шлях до backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.unified_parser import UnifiedParser, ParsingMode
from scripts.parallel_parser import ParallelParser, benchmark_parallel_vs_sequential

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_parallel_modes():
    """Тестує всі паралельні режими парсингу."""
    logger.info("=" * 60)
    logger.info("ТЕСТУВАННЯ ПАРАЛЕЛЬНИХ РЕЖИМІВ ПАРСИНГУ")
    logger.info("=" * 60)
    
    # Створюємо парсер з увімкненою паралельною обробкою
    parser = UnifiedParser(use_parallel=True)
    
    # Список режимів для тестування
    test_modes = [
        (ParsingMode.PARALLEL_PRODUCTS, "Паралельний парсинг товарів"),
        (ParsingMode.PARALLEL_ORDERS, "Паралельний парсинг замовлень"),
        (ParsingMode.PARALLEL_FULL, "Паралельний повний парсинг"),
    ]
    
    results = {}
    
    for mode, description in test_modes:
        logger.info(f"\nТестування: {description}")
        logger.info("-" * 40)
        
        start_time = time.time()
        
        try:
            # Запускаємо парсинг
            await parser.parse(mode)
            
            elapsed = time.time() - start_time
            status = "✅ Успішно"
            
            results[mode.value] = {
                "status": status,
                "time": elapsed,
                "errors": []
            }
            
            logger.info(f"{status} - Час виконання: {elapsed:.2f} сек")
            
        except Exception as e:
            elapsed = time.time() - start_time
            status = "❌ Помилка"
            
            results[mode.value] = {
                "status": status,
                "time": elapsed,
                "errors": [str(e)]
            }
            
            logger.error(f"{status} - {e}")
    
    # Виводимо підсумки
    logger.info("\n" + "=" * 60)
    logger.info("ПІДСУМКИ ТЕСТУВАННЯ")
    logger.info("=" * 60)
    
    for mode_name, result in results.items():
        logger.info(f"\n{mode_name}:")
        logger.info(f"  Статус: {result['status']}")
        logger.info(f"  Час: {result['time']:.2f} сек")
        if result['errors']:
            logger.info(f"  Помилки: {', '.join(result['errors'])}")


async def compare_performance():
    """Порівнює продуктивність паралельної та послідовної обробки."""
    logger.info("=" * 60)
    logger.info("ПОРІВНЯННЯ ПРОДУКТИВНОСТІ")
    logger.info("=" * 60)
    
    # Тест 1: Послідовна обробка
    logger.info("\n📊 Послідовна обробка...")
    sequential_parser = UnifiedParser(use_parallel=False)
    
    start_time = time.time()
    try:
        await sequential_parser.parse(ParsingMode.PRODUCTS_ONLY)
        sequential_time = time.time() - start_time
        logger.info(f"✅ Послідовна обробка: {sequential_time:.2f} сек")
    except Exception as e:
        sequential_time = None
        logger.error(f"❌ Помилка послідовної обробки: {e}")
    
    # Тест 2: Паралельна обробка
    logger.info("\n⚡ Паралельна обробка...")
    parallel_parser = UnifiedParser(use_parallel=True)
    
    start_time = time.time()
    try:
        await parallel_parser.parse(ParsingMode.PARALLEL_PRODUCTS)
        parallel_time = time.time() - start_time
        logger.info(f"✅ Паралельна обробка: {parallel_time:.2f} сек")
    except Exception as e:
        parallel_time = None
        logger.error(f"❌ Помилка паралельної обробки: {e}")
    
    # Порівняння результатів
    if sequential_time and parallel_time:
        speedup = sequential_time / parallel_time
        improvement = ((sequential_time - parallel_time) / sequential_time) * 100
        
        logger.info("\n" + "=" * 60)
        logger.info("РЕЗУЛЬТАТИ ПОРІВНЯННЯ:")
        logger.info(f"  Послідовна: {sequential_time:.2f} сек")
        logger.info(f"  Паралельна: {parallel_time:.2f} сек")
        logger.info(f"  Прискорення: {speedup:.2f}x")
        logger.info(f"  Покращення: {improvement:.1f}%")
        logger.info("=" * 60)


def test_parallel_parser_directly():
    """Прямий тест паралельного парсера."""
    logger.info("=" * 60)
    logger.info("ПРЯМИЙ ТЕСТ ПАРАЛЕЛЬНОГО ПАРСЕРА")
    logger.info("=" * 60)
    
    # Створюємо тестові дані
    test_sheets = [
        {"name": f"Sheet_{i}", "data": [["Product", "Price"]] + [[f"Product_{j}", j*100] for j in range(100)]}
        for i in range(10)
    ]
    
    test_products = [
        {"id": i, "name": f"Product_{i}", "price": i * 100}
        for i in range(1000)
    ]
    
    test_orders = [
        {"id": i, "client": f"Client_{i}", "total": i * 500}
        for i in range(500)
    ]
    
    # Створюємо парсер
    parser = ParallelParser(max_workers=4)
    
    # Тест 1: Обробка аркушів
    logger.info("\n📄 Тест обробки аркушів...")
    start_time = time.time()
    sheet_results = parser.process_sheet_batch(test_sheets)
    sheet_time = time.time() - start_time
    
    successful_sheets = sum(1 for r in sheet_results if r.success)
    logger.info(f"  Оброблено {successful_sheets}/{len(test_sheets)} аркушів за {sheet_time:.2f} сек")
    
    # Тест 2: Обробка товарів
    logger.info("\n📦 Тест обробки товарів...")
    start_time = time.time()
    product_results = parser.process_products_batch(test_products, batch_size=100)
    product_time = time.time() - start_time
    
    successful_products = sum(1 for r in product_results if r.success)
    logger.info(f"  Оброблено {successful_products} пакетів товарів за {product_time:.2f} сек")
    
    # Тест 3: Обробка замовлень
    logger.info("\n🛒 Тест обробки замовлень...")
    start_time = time.time()
    order_results = parser.process_orders_batch(test_orders, batch_size=50)
    order_time = time.time() - start_time
    
    successful_orders = sum(1 for r in order_results if r.success)
    logger.info(f"  Оброблено {successful_orders} пакетів замовлень за {order_time:.2f} сек")
    
    # Завершення
    parser.shutdown()
    
    logger.info("\n" + "=" * 60)
    logger.info("ПІДСУМКИ ПРЯМОГО ТЕСТУ:")
    logger.info(f"  Аркуші: {sheet_time:.2f} сек")
    logger.info(f"  Товари: {product_time:.2f} сек")
    logger.info(f"  Замовлення: {order_time:.2f} сек")
    logger.info(f"  Загальний час: {sheet_time + product_time + order_time:.2f} сек")
    logger.info("=" * 60)


async def main():
    """Головна функція тестування."""
    logger.info("\n🚀 ПОЧАТОК ТЕСТУВАННЯ ПАРАЛЕЛЬНОГО ПАРСИНГУ\n")
    
    # Тест 1: Прямий тест паралельного парсера
    logger.info("\n[1/3] Прямий тест паралельного парсера")
    test_parallel_parser_directly()
    
    # Тест 2: Бенчмарк паралельної vs послідовної обробки
    logger.info("\n[2/3] Бенчмарк продуктивності")
    benchmark_parallel_vs_sequential()
    
    # Тест 3: Тестування режимів парсингу
    logger.info("\n[3/3] Тестування паралельних режимів")
    await test_parallel_modes()
    
    # Тест 4: Порівняння продуктивності (опціонально)
    # await compare_performance()
    
    logger.info("\n✅ ТЕСТУВАННЯ ЗАВЕРШЕНО\n")


if __name__ == "__main__":
    # Запускаємо тести
    asyncio.run(main())

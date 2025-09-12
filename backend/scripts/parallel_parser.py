#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ПАРАЛЕЛЬНИЙ ПАРСЕР
Реалізація паралельної обробки для прискорення парсингу
"""

import asyncio
import logging
import time
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass
from datetime import datetime
import threading
import queue
import multiprocessing

# Додаємо шлях до backend для імпорту моделей
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPTS_DIR))

from models.database import SessionLocal
from models.models import ParsingLog

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('parallel_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфігурація паралельної обробки
PARALLEL_CONFIG = {
    "max_workers": min(3, multiprocessing.cpu_count()),  # Обмежено для економії БД з'єднань
    "batch_size": 25,  # Зменшений розмір пакету
    "queue_timeout": 30,  # Таймаут черги (сек)
    "db_pool_size": 3,  # Зменшений пул БД з'єднань
    "progress_update_interval": 2.0,  # Збільшений інтервал оновлення
}

@dataclass
class ParallelTask:
    """Задача для паралельної обробки."""
    id: str
    type: str  # 'sheet', 'product_batch', 'order_batch'
    data: Any
    priority: int = 0
    retry_count: int = 0
    max_retries: int = 3

@dataclass
class TaskResult:
    """Результат виконання задачі."""
    task_id: str
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    duration: float = 0.0
    items_processed: int = 0

class ProgressTracker:
    """Відстеження прогресу паралельних задач."""
    
    def __init__(self, callback: Optional[Callable] = None):
        self.callback = callback
        self.total_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.current_tasks = {}
        self.lock = threading.Lock()
        self.start_time = time.time()
        
    def register_task(self, task_id: str, description: str):
        """Реєструє нову задачу."""
        with self.lock:
            self.total_tasks += 1
            self.current_tasks[task_id] = {
                "description": description,
                "status": "pending",
                "start_time": None
            }
            self._update_callback()
    
    def start_task(self, task_id: str):
        """Позначає початок виконання задачі."""
        with self.lock:
            if task_id in self.current_tasks:
                self.current_tasks[task_id]["status"] = "running"
                self.current_tasks[task_id]["start_time"] = time.time()
                self._update_callback()
    
    def complete_task(self, task_id: str, success: bool = True):
        """Позначає завершення задачі."""
        with self.lock:
            if task_id in self.current_tasks:
                self.current_tasks[task_id]["status"] = "completed" if success else "failed"
                if success:
                    self.completed_tasks += 1
                else:
                    self.failed_tasks += 1
                self._update_callback()
    
    def get_progress(self) -> Dict:
        """Повертає поточний прогрес."""
        with self.lock:
            elapsed = time.time() - self.start_time
            progress_percent = (self.completed_tasks / self.total_tasks * 100) if self.total_tasks > 0 else 0
            
            # Розрахунок швидкості
            tasks_per_second = self.completed_tasks / elapsed if elapsed > 0 else 0
            
            # Оцінка часу до завершення
            remaining_tasks = self.total_tasks - self.completed_tasks - self.failed_tasks
            eta = remaining_tasks / tasks_per_second if tasks_per_second > 0 else 0
            
            return {
                "total": self.total_tasks,
                "completed": self.completed_tasks,
                "failed": self.failed_tasks,
                "progress_percent": progress_percent,
                "elapsed_time": elapsed,
                "eta": eta,
                "tasks_per_second": tasks_per_second,
                "running_tasks": sum(1 for t in self.current_tasks.values() if t["status"] == "running")
            }
    
    def _update_callback(self):
        """Викликає callback з оновленим статусом."""
        if self.callback:
            try:
                self.callback(self.get_progress())
            except Exception as e:
                logger.debug(f"Помилка виклику callback: {e}")

class ParallelParser:
    """Основний клас для паралельного парсингу."""
    
    def __init__(self, max_workers: Optional[int] = None, progress_callback: Optional[Callable] = None):
        self.max_workers = max_workers or PARALLEL_CONFIG["max_workers"]
        self.progress_tracker = ProgressTracker(progress_callback)
        self.task_queue = queue.PriorityQueue()
        self.result_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self.is_running = False
        self.db_lock = threading.Lock()
        
        logger.info(f"Ініціалізовано паралельний парсер з {self.max_workers} воркерами")
    
    def add_task(self, task: ParallelTask):
        """Додає задачу в чергу."""
        # Пріоритет: менше значення = вищий пріоритет
        priority_tuple = (-task.priority, time.time())  # Негативний для правильного сортування
        self.task_queue.put((priority_tuple, task))
        self.progress_tracker.register_task(task.id, f"{task.type}: {task.id}")
    
    def process_sheet_batch(self, sheets: List[Dict]) -> List[TaskResult]:
        """Паралельна обробка пакету аркушів."""
        logger.info(f"Початок паралельної обробки {len(sheets)} аркушів")
        
        # Створюємо задачі для кожного аркуша
        for sheet in sheets:
            task = ParallelTask(
                id=sheet.get('name', f'sheet_{time.time()}'),
                type='sheet',
                data=sheet,
                priority=1  # Нормальний пріоритет
            )
            self.add_task(task)
        
        # Запускаємо обробку
        results = self.run()
        
        logger.info(f"Завершено обробку {len(results)} аркушів")
        return results
    
    def process_products_batch(self, products: List[Dict], batch_size: Optional[int] = None) -> List[TaskResult]:
        """Паралельна обробка пакету товарів."""
        batch_size = batch_size or PARALLEL_CONFIG["batch_size"]
        logger.info(f"Початок паралельної обробки {len(products)} товарів (пакети по {batch_size})")
        
        # Розбиваємо на пакети
        batches = [products[i:i+batch_size] for i in range(0, len(products), batch_size)]
        
        # Створюємо задачі для кожного пакету
        for i, batch in enumerate(batches):
            task = ParallelTask(
                id=f'product_batch_{i}',
                type='product_batch',
                data=batch,
                priority=2  # Нижчий пріоритет ніж аркуші
            )
            self.add_task(task)
        
        # Запускаємо обробку
        results = self.run()
        
        logger.info(f"Завершено обробку {len(products)} товарів в {len(batches)} пакетах")
        return results
    
    def process_orders_batch(self, orders: List[Dict], batch_size: Optional[int] = None) -> List[TaskResult]:
        """Паралельна обробка пакету замовлень."""
        batch_size = batch_size or PARALLEL_CONFIG["batch_size"]
        logger.info(f"Початок паралельної обробки {len(orders)} замовлень (пакети по {batch_size})")
        
        # Розбиваємо на пакети
        batches = [orders[i:i+batch_size] for i in range(0, len(orders), batch_size)]
        
        # Створюємо задачі для кожного пакету
        for i, batch in enumerate(batches):
            task = ParallelTask(
                id=f'order_batch_{i}',
                type='order_batch',
                data=batch,
                priority=2
            )
            self.add_task(task)
        
        # Запускаємо обробку
        results = self.run()
        
        logger.info(f"Завершено обробку {len(orders)} замовлень в {len(batches)} пакетах")
        return results
    
    def _worker(self, task: ParallelTask) -> TaskResult:
        """Воркер для обробки задачі."""
        start_time = time.time()
        self.progress_tracker.start_task(task.id)
        
        try:
            # Вибираємо обробник залежно від типу задачі
            if task.type == 'sheet':
                result = self._process_sheet(task.data)
            elif task.type == 'product_batch':
                result = self._process_product_batch(task.data)
            elif task.type == 'order_batch':
                result = self._process_order_batch(task.data)
            else:
                raise ValueError(f"Невідомий тип задачі: {task.type}")
            
            # Успішний результат
            duration = time.time() - start_time
            self.progress_tracker.complete_task(task.id, success=True)
            
            return TaskResult(
                task_id=task.id,
                success=True,
                data=result,
                duration=duration,
                items_processed=result.get('items_processed', 0) if isinstance(result, dict) else 0
            )
            
        except Exception as e:
            # Помилка обробки
            logger.error(f"Помилка обробки задачі {task.id}: {e}", exc_info=True)
            duration = time.time() - start_time
            self.progress_tracker.complete_task(task.id, success=False)
            
            # Перевіряємо можливість повторної спроби
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                logger.info(f"Повторна спроба {task.retry_count}/{task.max_retries} для задачі {task.id}")
                self.add_task(task)  # Додаємо назад в чергу
            
            return TaskResult(
                task_id=task.id,
                success=False,
                error=str(e),
                duration=duration
            )
    
    def _process_sheet(self, sheet_data: Dict) -> Dict:
        """Обробка одного аркуша."""
        # Імпортуємо парсер тільки при потребі
        from googlesheets_pars import process_sheet_data
        
        logger.debug(f"Обробка аркуша: {sheet_data.get('name')}")
        
        # Створюємо окрему сесію БД для цього потоку
        with self.db_lock:
            db_session = SessionLocal()
        
        try:
            # Обробляємо аркуш
            result = process_sheet_data(sheet_data, db_session)
            db_session.commit()
            
            return {
                "sheet_name": sheet_data.get('name'),
                "items_processed": result.get('processed_count', 0),
                "errors": result.get('errors', [])
            }
            
        except Exception as e:
            db_session.rollback()
            raise
        finally:
            db_session.close()
    
    def _process_product_batch(self, products: List[Dict]) -> Dict:
        """Обробка пакету товарів."""
        logger.debug(f"Обробка пакету з {len(products)} товарів")
        
        # Створюємо окрему сесію БД
        with self.db_lock:
            db_session = SessionLocal()
        
        try:
            processed = 0
            errors = []
            
            for product in products:
                try:
                    # Тут має бути логіка обробки товару
                    # Поки що заглушка
                    processed += 1
                except Exception as e:
                    errors.append(str(e))
            
            db_session.commit()
            
            return {
                "items_processed": processed,
                "errors": errors
            }
            
        except Exception as e:
            db_session.rollback()
            raise
        finally:
            db_session.close()
    
    def _process_order_batch(self, orders: List[Dict]) -> Dict:
        """Обробка пакету замовлень."""
        logger.debug(f"Обробка пакету з {len(orders)} замовлень")
        
        # Створюємо окрему сесію БД
        with self.db_lock:
            db_session = SessionLocal()
        
        try:
            processed = 0
            errors = []
            
            for order in orders:
                try:
                    # Тут має бути логіка обробки замовлення
                    # Поки що заглушка
                    processed += 1
                except Exception as e:
                    errors.append(str(e))
            
            db_session.commit()
            
            return {
                "items_processed": processed,
                "errors": errors
            }
            
        except Exception as e:
            db_session.rollback()
            raise
        finally:
            db_session.close()
    
    def run(self) -> List[TaskResult]:
        """Запускає паралельну обробку всіх задач в черзі."""
        self.is_running = True
        results = []
        futures = []
        
        logger.info(f"Запуск паралельної обробки з {self.task_queue.qsize()} задачами")
        
        # Запускаємо задачі
        while not self.task_queue.empty():
            try:
                _, task = self.task_queue.get(timeout=1)
                future = self.executor.submit(self._worker, task)
                futures.append(future)
            except queue.Empty:
                break
        
        # Чекаємо на завершення
        for future in as_completed(futures):
            try:
                result = future.result(timeout=PARALLEL_CONFIG["queue_timeout"])
                results.append(result)
                self.result_queue.put(result)
            except Exception as e:
                logger.error(f"Помилка отримання результату: {e}")
        
        self.is_running = False
        
        # Підсумки
        progress = self.progress_tracker.get_progress()
        logger.info(f"""
        ==========================================
        ПАРАЛЕЛЬНА ОБРОБКА ЗАВЕРШЕНА
        ==========================================
        Всього задач: {progress['total']}
        Успішно: {progress['completed']}
        Помилок: {progress['failed']}
        Час виконання: {progress['elapsed_time']:.2f} сек
        Швидкість: {progress['tasks_per_second']:.2f} задач/сек
        ==========================================
        """)
        
        return results
    
    def shutdown(self):
        """Завершує роботу парсера."""
        logger.info("Завершення роботи паралельного парсера")
        self.executor.shutdown(wait=True)


# Асинхронна обгортка для інтеграції з unified_parser
class AsyncParallelParser:
    """Асинхронна обгортка для паралельного парсера."""
    
    def __init__(self, progress_callback: Optional[Callable] = None):
        self.parser = ParallelParser(progress_callback=progress_callback)
        self.loop = asyncio.get_event_loop()
    
    async def process_sheets_async(self, sheets: List[Dict]) -> List[TaskResult]:
        """Асинхронна обробка аркушів."""
        return await self.loop.run_in_executor(
            None,
            self.parser.process_sheet_batch,
            sheets
        )
    
    async def process_products_async(self, products: List[Dict]) -> List[TaskResult]:
        """Асинхронна обробка товарів."""
        return await self.loop.run_in_executor(
            None,
            self.parser.process_products_batch,
            products
        )
    
    async def process_orders_async(self, orders: List[Dict]) -> List[TaskResult]:
        """Асинхронна обробка замовлень."""
        return await self.loop.run_in_executor(
            None,
            self.parser.process_orders_batch,
            orders
        )


def benchmark_parallel_vs_sequential():
    """Тест продуктивності паралельної vs послідовної обробки."""
    import random
    
    # Генеруємо тестові дані
    test_products = [
        {"id": i, "name": f"Product_{i}", "price": random.randint(100, 1000)}
        for i in range(1000)
    ]
    
    logger.info("=" * 50)
    logger.info("BENCHMARK: Паралельна vs Послідовна обробка")
    logger.info(f"Тестові дані: {len(test_products)} товарів")
    logger.info("=" * 50)
    
    # Послідовна обробка
    start_time = time.time()
    for product in test_products:
        time.sleep(0.001)  # Симуляція обробки
    sequential_time = time.time() - start_time
    
    logger.info(f"Послідовна обробка: {sequential_time:.2f} сек")
    
    # Паралельна обробка
    parser = ParallelParser(max_workers=4)
    start_time = time.time()
    results = parser.process_products_batch(test_products, batch_size=100)
    parallel_time = time.time() - start_time
    parser.shutdown()
    
    logger.info(f"Паралельна обробка: {parallel_time:.2f} сек")
    logger.info(f"Прискорення: {sequential_time/parallel_time:.2f}x")
    

if __name__ == "__main__":
    # Запускаємо бенчмарк
    benchmark_parallel_vs_sequential()

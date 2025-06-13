#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОБ'ЄДНАНИЙ ПАРСЕР
Керує всіма видами парсингу через єдиний інтерфейс
"""

import asyncio
import logging
import sys
import os
from datetime import datetime
from typing import Dict, Optional, Callable
from enum import Enum
import json

# Додаємо шлях до backend для імпорту моделей
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('unified_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ParsingMode(Enum):
    """Режими парсингу."""
    FULL = "full"  # Повний парсинг всього
    INCREMENTAL = "incremental"  # Інкрементальний (тільки нові/змінені)
    PRODUCTS_ONLY = "products_only"  # Тільки товари
    ORDERS_ONLY = "orders_only"  # Тільки замовлення
    NEW_PRODUCTS = "new_products"  # Пошук новинок
    QUICK_UPDATE = "quick_update"  # Швидке оновлення (останні 3 дні)

class ParsingStatus:
    """Клас для відстеження статусу парсингу."""
    
    def __init__(self, callback: Optional[Callable] = None):
        self.callback = callback
        self.current_task = ""
        self.current_progress = 0
        self.total_progress = 0
        self.is_running = False
        self.start_time = None
        self.errors = []
        
    def update(self, task: str, current: int = 0, total: int = 0):
        """Оновлює статус парсингу."""
        self.current_task = task
        self.current_progress = current
        self.total_progress = total
        
        if self.callback:
            self.callback(self.get_status())
    
    def add_error(self, error: str):
        """Додає помилку."""
        self.errors.append(error)
        logger.error(error)
    
    def get_status(self) -> Dict:
        """Повертає поточний статус."""
        elapsed_time = None
        if self.start_time:
            elapsed_time = (datetime.now() - self.start_time).total_seconds()
        
        return {
            "task": self.current_task,
            "current": self.current_progress,
            "total": self.total_progress,
            "is_running": self.is_running,
            "elapsed_time": elapsed_time,
            "errors": self.errors
        }
    
    def start(self):
        """Початок парсингу."""
        self.is_running = True
        self.start_time = datetime.now()
        self.errors = []
        self.update("Початок парсингу...")
    
    def finish(self):
        """Завершення парсингу."""
        self.is_running = False
        elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        self.update(f"Парсинг завершено за {elapsed:.1f} сек")

class UnifiedParser:
    """Об'єднаний парсер для всіх типів даних."""
    
    def __init__(self, status_callback: Optional[Callable] = None):
        self.status = ParsingStatus(status_callback)
        self.is_cancelled = False
        
    async def parse(self, mode: ParsingMode, **kwargs):
        """Запускає парсинг у вибраному режимі."""
        self.is_cancelled = False
        self.status.start()
        
        try:
            if mode == ParsingMode.FULL:
                await self._parse_full()
            elif mode == ParsingMode.INCREMENTAL:
                await self._parse_incremental(kwargs.get('days', 7))
            elif mode == ParsingMode.PRODUCTS_ONLY:
                await self._parse_products_only()
            elif mode == ParsingMode.ORDERS_ONLY:
                await self._parse_orders_only()
            elif mode == ParsingMode.NEW_PRODUCTS:
                await self._parse_new_products()
            elif mode == ParsingMode.QUICK_UPDATE:
                await self._parse_quick_update()
            else:
                raise ValueError(f"Невідомий режим парсингу: {mode}")
                
        except asyncio.CancelledError:
            self.status.update("Парсинг скасовано")
            logger.info("Парсинг скасовано користувачем")
        except Exception as e:
            self.status.add_error(f"Критична помилка: {str(e)}")
            logger.error(f"Критична помилка парсингу: {e}", exc_info=True)
            raise
        finally:
            self.status.finish()
    
    def cancel(self):
        """Скасовує поточний парсинг."""
        self.is_cancelled = True
        self.status.update("Скасування парсингу...")
    
    async def _check_cancelled(self):
        """Перевіряє, чи не скасовано парсинг."""
        if self.is_cancelled:
            raise asyncio.CancelledError()
    
    async def _parse_full(self):
        """Повний парсинг всіх даних."""
        logger.info("🚀 ПОВНИЙ ПАРСИНГ")
        
        # Крок 1: Парсинг товарів
        self.status.update("Парсинг товарів...", 0, 2)
        await self._run_products_parser()
        await self._check_cancelled()
        
        # Крок 2: Парсинг замовлень
        self.status.update("Парсинг замовлень...", 1, 2)
        await self._run_orders_parser(force_reparse=True)
        await self._check_cancelled()
        
        self.status.update("Повний парсинг завершено", 2, 2)
    
    async def _parse_incremental(self, days: int):
        """Інкрементальний парсинг за останні N днів."""
        logger.info(f"🔄 ІНКРЕМЕНТАЛЬНИЙ ПАРСИНГ (останні {days} днів)")
        
        # Використовуємо інкрементальний парсер
        from incremental_parser import IncrementalParser
        
        self.status.update(f"Пошук змін за {days} днів...", 0, 1)
        
        parser = IncrementalParser()
        
        # Передаємо callback для оновлення статусу
        original_parse_sheet = parser.parser.parse_orders_sheet
        
        def wrapped_parse_sheet(worksheet, *args, **kwargs):
            self.status.update(f"Аркуш: {worksheet.title}", 
                             parser.parser.stats.get('processed_sheets', 0),
                             parser.parser.stats.get('total_sheets', 0))
            return original_parse_sheet(worksheet, *args, **kwargs)
        
        parser.parser.parse_orders_sheet = wrapped_parse_sheet
        
        await parser.parse_recent_sheets(days=days)
        
        self.status.update("Інкрементальний парсинг завершено", 1, 1)
    
    async def _parse_products_only(self):
        """Парсинг тільки товарів."""
        logger.info("📦 ПАРСИНГ ТОВАРІВ")
        
        self.status.update("Парсинг товарів...", 0, 1)
        await self._run_products_parser()
        self.status.update("Парсинг товарів завершено", 1, 1)
    
    async def _parse_orders_only(self):
        """Парсинг тільки замовлень."""
        logger.info("🛒 ПАРСИНГ ЗАМОВЛЕНЬ")
        
        self.status.update("Парсинг замовлень...", 0, 1)
        await self._run_orders_parser()
        self.status.update("Парсинг замовлень завершено", 1, 1)
    
    async def _parse_new_products(self):
        """Пошук нових товарів."""
        logger.info("🆕 ПОШУК НОВИНОК")
        
        self.status.update("Пошук нових товарів...", 0, 1)
        
        # Запускаємо парсер товарів з параметром для пошуку новинок
        process = await asyncio.create_subprocess_exec(
            sys.executable, 'googlesheets_pars.py', '--new-only',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Читаємо вивід для оновлення статусу
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line_text = line.decode('utf-8').strip()
            if 'Обробка аркуша' in line_text:
                self.status.update(line_text)
            
            await self._check_cancelled()
            if self.is_cancelled:
                process.terminate()
                await process.wait()
                return
        
        await process.wait()
        
        if process.returncode != 0:
            stderr = await process.stderr.read()
            self.status.add_error(f"Помилка пошуку новинок: {stderr.decode('utf-8')}")
        
        self.status.update("Пошук новинок завершено", 1, 1)
    
    async def _parse_quick_update(self):
        """Швидке оновлення за останні 3 дні."""
        logger.info("⚡ ШВИДКЕ ОНОВЛЕННЯ")
        
        # Інкрементальний парсинг за 3 дні
        await self._parse_incremental(3)
    
    async def _run_products_parser(self):
        """Запускає парсер товарів."""
        process = await asyncio.create_subprocess_exec(
            sys.executable, 'googlesheets_pars.py',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Читаємо вивід для оновлення статусу
        sheet_count = 0
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line_text = line.decode('utf-8').strip()
            if 'Обробка аркуша' in line_text:
                sheet_count += 1
                self.status.update(f"Парсинг товарів: аркуш {sheet_count}", sheet_count, 0)
            elif 'з' in line_text and 'аркушів' in line_text:
                # Витягуємо загальну кількість аркушів
                try:
                    parts = line_text.split()
                    for i, part in enumerate(parts):
                        if part == 'з' and i + 1 < len(parts):
                            total = int(parts[i + 1])
                            self.status.update(f"Парсинг товарів: аркуш {sheet_count}", sheet_count, total)
                except:
                    pass
            
            await self._check_cancelled()
            if self.is_cancelled:
                process.terminate()
                await process.wait()
                return
        
        await process.wait()
        
        if process.returncode != 0:
            stderr = await process.stderr.read()
            self.status.add_error(f"Помилка парсингу товарів: {stderr.decode('utf-8')}")
    
    async def _run_orders_parser(self, force_reparse: bool = False):
        """Запускає парсер замовлень."""
        cmd = [sys.executable, 'orders_comprehensive_parser.py']
        if force_reparse:
            cmd.append('--force-reparse')
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Читаємо вивід для оновлення статусу
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line_text = line.decode('utf-8').strip()
            if 'Обробляємо аркуш' in line_text:
                self.status.update(line_text)
            elif 'Парсинг аркуша:' in line_text:
                self.status.update(f"Замовлення: {line_text}")
            
            await self._check_cancelled()
            if self.is_cancelled:
                process.terminate()
                await process.wait()
                return
        
        await process.wait()
        
        if process.returncode != 0:
            stderr = await process.stderr.read()
            self.status.add_error(f"Помилка парсингу замовлень: {stderr.decode('utf-8')}")

# Функції для використання з FastAPI
def get_parsing_modes():
    """Повертає доступні режими парсингу."""
    return [
        {
            "id": ParsingMode.FULL.value,
            "name": "Повний парсинг",
            "description": "Повний парсинг всіх товарів та замовлень",
            "icon": "🔄",
            "estimated_time": "1-2 години"
        },
        {
            "id": ParsingMode.INCREMENTAL.value,
            "name": "Оновлення змін",
            "description": "Парсинг тільки нових та змінених даних",
            "icon": "📈",
            "estimated_time": "5-15 хвилин",
            "params": {
                "days": {
                    "type": "number",
                    "default": 7,
                    "min": 1,
                    "max": 30,
                    "description": "Кількість днів для перевірки"
                }
            }
        },
        {
            "id": ParsingMode.QUICK_UPDATE.value,
            "name": "Швидке оновлення",
            "description": "Оновлення за останні 3 дні",
            "icon": "⚡",
            "estimated_time": "2-5 хвилин"
        },
        {
            "id": ParsingMode.PRODUCTS_ONLY.value,
            "name": "Тільки товари",
            "description": "Парсинг тільки каталогу товарів",
            "icon": "📦",
            "estimated_time": "30-60 хвилин"
        },
        {
            "id": ParsingMode.ORDERS_ONLY.value,
            "name": "Тільки замовлення",
            "description": "Парсинг тільки замовлень клієнтів",
            "icon": "🛒",
            "estimated_time": "30-60 хвилин"
        },
        {
            "id": ParsingMode.NEW_PRODUCTS.value,
            "name": "Пошук новинок",
            "description": "Пошук нових товарів в каталозі",
            "icon": "🆕",
            "estimated_time": "15-30 хвилин"
        }
    ]

async def test_parser():
    """Тестова функція."""
    def status_callback(status):
        print(f"Статус: {status['task']} ({status['current']}/{status['total']})")
    
    parser = UnifiedParser(status_callback)
    
    # Тест швидкого оновлення
    await parser.parse(ParsingMode.QUICK_UPDATE)

if __name__ == "__main__":
    asyncio.run(test_parser()) 
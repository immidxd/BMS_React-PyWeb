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
from pathlib import Path
import time

# Додаємо шлях до backend для імпорту моделей
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPTS_DIR, "..", ".."))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Імпортуємо моделі для логування
from models.database import SessionLocal
from models.models import ParsingLog

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

# Глобальні ліміти часу (секунди) для захисту від зависань
TIME_LIMITS = {
    "full": 2 * 60 * 60,           # 2 години
    "incremental": 30 * 60,       # 30 хв
    "products_only": 90 * 60,     # 1.5 год
    "orders_only": 90 * 60,       # 1.5 год
    "new_products": 30 * 60,      # 30 хв
}

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
            # Підтримка асинхронного callback (FastAPI WebSocket broadcaster)
            try:
                import asyncio, inspect
                result = self.callback(self.get_status())
                if inspect.iscoroutine(result):
                    try:
                        asyncio.get_running_loop()
                        asyncio.create_task(result)
                    except RuntimeError:
                        # Немає активного loop: ігноруємо асинхронний виклик
                        pass
            except Exception:
                # Не блокуємо основний парсинг через помилки оновлення статусу
                logger.debug("Не вдалося відправити статус оновлення через callback", exc_info=True)
    
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
        self.parsing_log = None
        self.db_session = None
        self.current_process: Optional[asyncio.subprocess.Process] = None
        
    async def parse(self, mode: ParsingMode, **kwargs):
        """Запускає парсинг у вибраному режимі."""
        self.is_cancelled = False
        self.status.start()
        
        # Створюємо запис в БД
        self._create_parsing_log(mode)

        # Старт watchdog за лімітом часу
        start_ts = time.time()
        limit = TIME_LIMITS.get(mode.value, 2 * 60 * 60)
        
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
            
            # Перевірка ліміту часу
            if (time.time() - start_ts) > limit:
                self.is_cancelled = True
                self.status.update("Перервано через перевищення ліміту часу")
                raise asyncio.CancelledError()
                
        except asyncio.CancelledError:
            self.status.update("Парсинг скасовано")
            logger.info("Парсинг скасовано користувачем")
            self._update_parsing_log("cancelled")
        except Exception as e:
            self.status.add_error(f"Критична помилка: {str(e)}")
            logger.error(f"Критична помилка парсингу: {e}", exc_info=True)
            self._update_parsing_log("failed", str(e))
            raise
        finally:
            # Якщо було скасовано – не показуємо "завершено", а чітко фіксуємо скасування
            if self.is_cancelled:
                self.status.is_running = False
                self.status.update("Парсинг скасовано")
                # лог оновлено вище
            else:
                self.status.finish()
                self._update_parsing_log("completed")
            # Закриваємо сесію
            self._close_db_session()
            # TODO: Закрити сесію БД
            # self._close_db_session()
    
    def cancel(self):
        """Скасовує поточний парсинг."""
        self.is_cancelled = True
        self.status.update("Скасування парсингу...")
        # Якщо є запущений підпроцес - завершуємо його
        try:
            if self.current_process and self.current_process.returncode is None:
                self.current_process.terminate()
                # Якщо не завершився за 5сек - вбиваємо
                try:
                    loop = asyncio.get_event_loop()
                    async def _wait_and_kill():
                        try:
                            await asyncio.wait_for(self.current_process.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            self.current_process.kill()
                    loop.create_task(_wait_and_kill())
                except RuntimeError:
                    # немає активного loop – ігноруємо
                    pass
            # Негайно сигналізуємо UI, що виконання припиняється, аби кнопка й таблиця не блокувались
            self.status.finish()
        except Exception:
            logger.debug("Не вдалося завершити підпроцес при скасуванні", exc_info=True)
    
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
        """Інкрементальний парсинг за останні N днів (запуск у підпроцесі для неблокуючої роботи і коректного скасування)."""
        logger.info(f"🔄 ІНКРЕМЕНТАЛЬНИЙ ПАРСИНГ (останні {days} днів)")

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            os.path.join(SCRIPTS_DIR, 'incremental_parser.py'),
            '--days', str(days),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=SCRIPTS_DIR,
        )
        self.current_process = process

        # Читаємо вивід як прогрес
        start_ts = time.time()
        limit = TIME_LIMITS.get('incremental', 30 * 60)
        sheets_done = 0
        sheets_total = 0
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                await self._check_cancelled()
                # контроль ліміту часу
                if (time.time() - start_ts) > limit:
                    self.is_cancelled = True
                    self.status.update("Перервано: інкрементальний парсинг перевищив ліміт часу")
                    process.terminate()
                    await process.wait()
                    self.current_process = None
                    return
                continue
            if not line:
                break

            text = line.decode('utf-8', errors='ignore').strip()
            if text:
                # Документ відкрито
                if text.startswith('✅ Документ відкрито:'):
                    doc = text.split(':', 1)[1].strip()
                    self.status.update(f"Документ: {doc}")
                # Індикатор аркушів: "Перевіряємо аркуш i/N: Назва"
                elif text.startswith('Перевіряємо аркуш') or text.startswith('Обробляємо аркуш'):
                    import re
                    m = re.search(r"(Перевіряємо|Обробляємо) аркуш\s+(\d+)/(\d+):\s*(.+)$", text)
                    if m:
                        i = int(m.group(2)); tot = int(m.group(3)); title = m.group(4)
                        # Показуємо прогрес ДО початку обробки (i-1), щоб не скакало одразу на 100%
                        cur = max(i - 1, 0)
                        sheets_done = cur
                        sheets_total = tot
                        self.status.update(f"Аркуш: {title}", sheets_done, sheets_total)
                    else:
                        self.status.update(text)
                elif "✅ Аркуш" in text and "оброблено та збережено" in text:
                    # По завершенні конкретного аркуша підтягуємо i/N з тексту вище не завжди є, тому просто індикуємо крок
                    sheets_done = min(sheets_done + 1, max(sheets_total, sheets_done + 1))
                    self.status.update(text, sheets_done, sheets_total or sheets_done)
                elif "не змінився, пропускаємо" in text and "Аркуш '" in text:
                    sheets_done = min(sheets_done + 1, max(sheets_total, sheets_done + 1))
                    self.status.update(text, sheets_done, sheets_total or sheets_done)
                elif text.startswith('📊') or 'СТАТИСТИКА' in text:
                    self.status.update('Оновлення завершення...', sheets_done, sheets_total or sheets_done)

            await self._check_cancelled()
            if self.is_cancelled:
                process.terminate()
                await process.wait()
                self.current_process = None
                return

        await process.wait()
        self.current_process = None

        # Якщо користувач скасував – тихо завершуємо без помилки
        if self.is_cancelled:
            return

        if process.returncode != 0:
            stderr = await process.stderr.read()
            self.status.add_error(f"Помилка інкрементального парсингу: {stderr.decode('utf-8')}")
            self._update_parsing_log("failed", "Помилка інкрементального парсингу")
        else:
            # фінальний меседж БЕЗ примусових 1/1, зберігаємо останні лічильники
            self.status.update("Інкрементальний парсинг завершено", sheets_done, sheets_total or sheets_done)
            self._update_parsing_log("completed", "Інкрементальний парсинг завершено")
    
    async def _parse_products_only(self):
        """Парсинг тільки товарів."""
        logger.info("📦 ПАРСИНГ ТОВАРІВ")
        
        self.status.update("Парсинг товарів...", 0, 1)
        await self._run_products_parser()
        self.status.update("Парсинг товарів завершено", 1, 1)
        self._update_parsing_log("completed", "Парсинг товарів завершено")
    
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
            sys.executable,
            os.path.join(SCRIPTS_DIR, 'googlesheets_pars.py'),
            '--new-only',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=SCRIPTS_DIR,
        )
        self.current_process = process
        
        # Читаємо вивід для оновлення статусу
        start_ts = time.time()
        limit = TIME_LIMITS.get('new_products', 30 * 60)
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                await self._check_cancelled()
                if (time.time() - start_ts) > limit:
                    self.is_cancelled = True
                    self.status.update("Перервано: пошук новинок перевищив ліміт часу")
                    process.terminate()
                    await process.wait()
                    self.current_process = None
                    return
                continue
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
        self.current_process = None
        
        if self.is_cancelled:
            return

        if process.returncode != 0:
            stderr = await process.stderr.read()
            self.status.add_error(f"Помилка пошуку новинок: {stderr.decode('utf-8')}")
            self._update_parsing_log("failed", "Помилка пошуку новинок")
        
        self.status.update("Пошук новинок завершено", 1, 1)
        self._update_parsing_log("completed", "Пошук новинок завершено")
    
    async def _parse_quick_update(self):
        """Швидке оновлення за останні 3 дні."""
        logger.info("⚡ ШВИДКЕ ОНОВЛЕННЯ")
        
        # Інкрементальний парсинг за 3 дні
        await self._parse_incremental(3)
    
    async def _run_products_parser(self):
        """Запускає парсер товарів."""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            os.path.join(SCRIPTS_DIR, 'googlesheets_pars.py'),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=SCRIPTS_DIR,
        )
        self.current_process = process
        
        # Читаємо вивід для оновлення статусу
        sheet_count = 0
        start_ts = time.time()
        limit = TIME_LIMITS.get('products_only', 90 * 60)
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                await self._check_cancelled()
                if (time.time() - start_ts) > limit:
                    self.is_cancelled = True
                    self.status.update("Перервано: парсинг товарів перевищив ліміт часу")
                    process.terminate()
                    await process.wait()
                    self.current_process = None
                    return
                continue
            if not line:
                break
            
            line_text = line.decode('utf-8', errors='ignore').strip()
            did_update = False
            # 1) Назва документа
            if line_text.startswith('Документ:'):
                doc_name = line_text.split(':', 1)[1].strip()
                self.status.update(f"Документ: {doc_name}")
                did_update = True
            # 2) Заголовок: обробка конкретної поставки/довідника з позицією i/N
            elif line_text.startswith('Обробка поставки:') or line_text.startswith('Обробка довідника:'):
                import re
                m = re.search(r":\s*(.+?)\s*\((\d+)/(\d+)", line_text)
                if m:
                    title = m.group(1)
                    cur = int(m.group(2) or 0)
                    tot = int(m.group(3) or 0)
                    sheet_count = cur
                    self.status.update(f"Аркуш: {title}", cur, tot)
                else:
                    sheet_count += 1
                    self.status.update(f"Аркуш: ?", sheet_count, 0)
                did_update = True
            # 3) Прогрес усередині аркуша за рядками
            elif "прогрес обробки" in line_text and "Аркуш '" in line_text:
                import re
                m = re.search(r"Аркуш '([^']+)':\s*прогрес обробки\s*(\d+)%\s*\((\d+)/(\d+)\)", line_text)
                if m:
                    title = m.group(1)
                    row_cur = int(m.group(3))
                    row_tot = int(m.group(4))
                    self.status.update(f"Аркуш: {title} — рядки {row_cur}/{row_tot}", row_cur, row_tot)
                    did_update = True
            # 4) Старт обробки аркуша
            elif 'Початок обробки аркуша' in line_text:
                import re
                m = re.search(r":\s*(.+)$", line_text)
                title = m.group(1).strip() if m else "?"
                sheet_count += 1
                self.status.update(f"Аркуш: {title}", sheet_count, sheet_count)
                did_update = True

            # Якщо рядок не відповідає жодному з шаблонів прогресу — все одно прокинемо його в короткий лог job
            if not did_update and self.status.callback:
                try:
                    payload = self.status.get_status()
                    payload['task'] = line_text
                    await self.status.callback(payload)
                except Exception:
                    pass

            # Відправляємо callback після оновлення статусу, щоб лог рядка потрапив у logs_head
            if did_update and self.status.callback:
                try:
                    await self.status.callback(self.status.get_status())
                except Exception:
                    pass
            
            await self._check_cancelled()
            if self.is_cancelled:
                process.terminate()
                await process.wait()
                return
        
        await process.wait()
        self.current_process = None
        
        if self.is_cancelled:
            return

        if process.returncode != 0:
            stderr_text = ""
            try:
                if process.stderr is not None:
                    stderr_bytes = await process.stderr.read()
                    try:
                        stderr_text = stderr_bytes.decode('utf-8')
                    except Exception:
                        stderr_text = str(stderr_bytes)
            except Exception:
                stderr_text = ""
            err_msg = f"Помилка парсингу товарів: {stderr_text or 'див. stdout'}"
            self.status.add_error(err_msg)
            self._update_parsing_log("failed", err_msg)
    
    async def _run_orders_parser(self, force_reparse: bool = False):
        """Запускає парсер замовлень."""
        cmd = [
            sys.executable,
            os.path.join(SCRIPTS_DIR, 'orders_comprehensive_parser.py'),
        ]
        if force_reparse:
            cmd.append('--force-reparse')
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=SCRIPTS_DIR,
        )
        self.current_process = process
        
        # Читаємо вивід для оновлення статусу
        start_ts = time.time()
        limit = TIME_LIMITS.get('orders_only', 90 * 60)
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                await self._check_cancelled()
                if (time.time() - start_ts) > limit:
                    self.is_cancelled = True
                    self.status.update("Перервано: парсинг замовлень перевищив ліміт часу")
                    process.terminate()
                    await process.wait()
                    self.current_process = None
                    return
                continue
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
        self.current_process = None
        
        if self.is_cancelled:
            return

        if process.returncode != 0:
            stderr = await process.stderr.read()
            self.status.add_error(f"Помилка парсингу замовлень: {stderr.decode('utf-8')}")
            self._update_parsing_log("failed", "Помилка парсингу замовлень")

    # =============================
    # DB LOGGING HELPERS
    # =============================
    def _open_db(self):
        if self.db_session is None:
            try:
                self.db_session = SessionLocal()
            except Exception:
                logger.debug("Не вдалося відкрити сесію БД для логування", exc_info=True)

    def _create_parsing_log(self, mode: ParsingMode):
        try:
            self._open_db()
            if not self.db_session:
                return
            log = ParsingLog(
                source_id=1,
                status="in_progress",
                message=f"Старт парсингу: {mode.value}"
            )
            self.db_session.add(log)
            self.db_session.commit()
            self.db_session.refresh(log)
            self.parsing_log = log
        except Exception:
            logger.debug("Не вдалося створити parsing_log", exc_info=True)

    def _update_parsing_log(self, status: str, message: Optional[str] = None):
        try:
            if not self.parsing_log:
                return
            self._open_db()
            if not self.db_session:
                return
            log = self.db_session.query(ParsingLog).get(self.parsing_log.id)
            if not log:
                return
            log.status = status
            if status in ("completed", "failed", "cancelled"):
                log.end_time = datetime.now()
            if message:
                log.message = (log.message + "\n" if log.message else "") + message
            self.db_session.commit()
        except Exception:
            logger.debug("Не вдалося оновити parsing_log", exc_info=True)

    def _close_db_session(self):
        try:
            if self.db_session is not None:
                self.db_session.close()
        except Exception:
            pass

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
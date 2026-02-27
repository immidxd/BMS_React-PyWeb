#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ІНКРЕМЕНТАЛЬНИЙ ПАРСЕР ЗАМОВЛЕНЬ
Швидкий парсинг тільки нових або змінених аркушів
"""

import asyncio
import logging
from datetime import datetime, timedelta
from orders_comprehensive_parser import OrdersComprehensiveParser

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('incremental_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class IncrementalParser:
    """Клас для інкрементального парсингу."""
    
    def __init__(self):
        self.parser = OrdersComprehensiveParser(use_cache=True, force_reparse=False)
    
    async def parse_recent_sheets(self, days: int = 7):
        """Парсить тільки аркуші за останні N днів."""
        logger.info(f"🚀 ІНКРЕМЕНТАЛЬНИЙ ПАРСИНГ (останні {days} днів)")
        logger.info("=" * 50)
        
        # Ініціалізація
        from orders_comprehensive_parser import SessionLocal, ClientManager, ProductPriceManager, ProductCache, OrderDeduplicator
        self.parser.session = SessionLocal()
        self.parser.client_manager = ClientManager(self.parser.session)
        self.parser.price_manager = ProductPriceManager(self.parser.session)
        self.parser.product_cache = ProductCache(self.parser.session)
        self.parser.order_deduplicator = OrderDeduplicator(self.parser.session)
        
        try:
            # Підключення до Google Sheets
            client = self.parser.get_google_sheet_client()
            if not client:
                raise Exception("Не вдалося підключитися до Google Sheets")
            
            from orders_comprehensive_parser import ORDERS_DOCUMENT_NAME
            doc = client.open(ORDERS_DOCUMENT_NAME)
            self.parser.workbook = doc
            logger.info(f"✅ Документ відкрито: {doc.title}")
            
            # Отримуємо довідкові дані
            order_statuses, payment_statuses, delivery_methods, delivery_statuses, payment_methods = self.parser.get_or_create_reference_data()
            
            # Отримуємо всі аркуші
            worksheets = doc.worksheets()
            
            # Фільтруємо аркуші за датою
            cutoff_date = datetime.now() - timedelta(days=days)
            recent_sheets = []
            
            for ws in worksheets:
                if any(char.isdigit() for char in ws.title) and '.' in ws.title:
                    try:
                        # Парсимо дату з назви аркуша
                        date_parts = ws.title.split('.')
                        if len(date_parts) == 3:
                            day, month, year = map(int, date_parts)
                            sheet_date = datetime(year, month, day)
                            
                            if sheet_date >= cutoff_date:
                                recent_sheets.append((ws, sheet_date))
                    except:
                        continue
            
            # Сортуємо за датою (найновіші спочатку)
            recent_sheets.sort(key=lambda x: x[1], reverse=True)
            
            logger.info(f"Знайдено {len(recent_sheets)} аркушів за останні {days} днів")
            
            # Обробляємо тільки нові/змінені аркуші
            for i, (worksheet, sheet_date) in enumerate(recent_sheets, 1):
                logger.info(f"Перевіряємо аркуш {i}/{len(recent_sheets)}: {worksheet.title}")
                
                # Отримуємо дані аркуша
                try:
                    all_data = self.parser.parse_sheet(worksheet.title)
                    
                    # Перевіряємо, чи змінився аркуш
                    if not self.parser.cache_manager.is_sheet_changed(worksheet.title, all_data):
                        logger.info(f"Аркуш '{worksheet.title}' не змінився, пропускаємо")
                        self.parser.stats['skipped_sheets'] += 1
                        continue
                    
                    # Парсимо аркуш
                    self.parser.parse_orders_sheet(
                        worksheet,
                        order_statuses,
                        payment_statuses,
                        delivery_methods,
                        delivery_statuses,
                        payment_methods
                    )
                    
                    # Комітимо після кожного аркуша для інкрементального режиму
                    self.parser.session.commit()
                    logger.info(f"✅ Аркуш '{worksheet.title}' оброблено та збережено")
                    
                except Exception as e:
                    logger.error(f"Помилка обробки аркуша {worksheet.title}: {e}")
                    try:
                        self.parser.session.rollback()
                    except:
                        pass
                    continue
            
            # Застосовуємо оновлення товарів
            if self.parser.price_manager.price_updates or self.parser.price_manager.size_updates or self.parser.price_manager.measurement_updates:
                logger.info("Застосовуємо оновлення товарів...")
                self.parser.price_manager.apply_all_updates(self.parser.product_cache)
                self.parser.session.commit()
            
            # Зберігаємо кеші
            self.parser.cache_manager.save_all_caches()
            
            # Статистика
            logger.info("\n" + "=" * 50)
            logger.info("📊 СТАТИСТИКА ІНКРЕМЕНТАЛЬНОГО ПАРСИНГУ:")
            logger.info(f"  Всього рядків оброблено: {self.parser.stats['total_orders']}")
            logger.info(f"  Успішних замовлень: {self.parser.stats['successful_orders']}")
            logger.info(f"  Помилок: {self.parser.stats['errors']}")
            logger.info(f"  Аркушів оброблено: {self.parser.stats['processed_sheets']}")
            logger.info(f"  Аркушів пропущено: {self.parser.stats['skipped_sheets']}")
            logger.info(f"  Дублікатів пропущено: {self.parser.stats['skipped_duplicates']}")
            logger.info(f"  Клієнтів створено: {self.parser.client_manager.stats['created']}")
            logger.info(f"  Клієнтів оновлено: {self.parser.client_manager.stats['updated']}")
            
        except Exception as e:
            logger.error(f"Критична помилка інкрементального парсингу: {e}")
            try:
                self.parser.session.rollback()
            except:
                pass
            raise
        finally:
            if self.parser.session:
                self.parser.session.close()

async def main():
    """Основна функція."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Інкрементальний парсинг замовлень')
    parser.add_argument('--days', type=int, default=7, help='Кількість днів для парсингу (за замовчуванням: 7)')
    args = parser.parse_args()
    
    incremental_parser = IncrementalParser()
    await incremental_parser.parse_recent_sheets(days=args.days)

if __name__ == "__main__":
    asyncio.run(main()) 

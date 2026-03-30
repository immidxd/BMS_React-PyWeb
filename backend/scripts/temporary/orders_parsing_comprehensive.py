#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
КОМПЛЕКСНИЙ ПАРСИНГ ЗАМОВЛЕНЬ З GOOGLE SHEETS
Враховує всі виявлені паттерни:
- Дедуплікація клієнтів по телефону/Facebook
- Розпізнавання методів оплати
- Парсинг уточнень (розміри, заміри, коментарі)
- Синхронізація цін (остання ціна продажу → поточна ціна)
- Обробка множинних товарів у замовленні
"""

import os
import sys
import asyncio
import logging
import re
from datetime import datetime
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Tuple, Any
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

# Додаємо шляхи для імпортів
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from models.database import get_db_connection, get_session_factory
from models.models import (
    Client, Order, OrderDetail, Product, OrderStatus, 
    PaymentStatus, DeliveryMethod, ProductType, Brand, ClientAddress
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, text

# Завантажуємо змінні оточення
load_dotenv()

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('orders_parsing_comprehensive.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Google Sheets налаштування
GOOGLE_SHEETS_JSON_KEY = os.getenv("GOOGLE_SHEETS_JSON_KEY")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GOOGLE_SHEETS_CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, GOOGLE_SHEETS_JSON_KEY)
ORDERS_DOCUMENT_NAME = os.getenv("GOOGLE_SHEETS_DOCUMENT_NAME_ORDERS")

class PaymentMethodManager:
    """Клас для розпізнавання методів оплати."""
    
    @staticmethod
    def identify_payment_method(text: str) -> Optional[str]:
        """Розпізнає метод оплати з тексту."""
        if not text:
            return None
        
        text_lower = text.lower().strip()
        
        # Патерни для методів оплати
        payment_patterns = {
            'Карта': [
                r'\bтермінал\b', r'\bкарт[аиуою]\b', r'\bна карт[у]\b',
                r'\bкартк[аоую]\b', r'\bкартой\b', r'\bcard\b', r'\bкартою\b',
                r'\bприват\b', r'\bмоно\b', r'\bвізою\b'
            ],
            'Готівка': [
                r'\bготівк[аоию]\b', r'\bготівкою\b', r'\bналичн[ыеіі]\b',
                r'\bналичкой\b', r'\bcash\b', r'\bгрош[іиыі]\b', r'\bналом\b'
            ],
            'Переказ': [
                r'\bпереказ\b', r'\bперевод\b', r'\bна карт[у]\b',
                r'\bна рахунок\b', r'\bтрансфер\b', r'\bпереведе\b'
            ]
        }
        
        for method, patterns in payment_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return method
        
        return None

class ClarificationParser:
    """Клас для парсингу колонки 'Уточнення'."""
    
    @staticmethod
    def parse_clarification(text: str) -> Dict[str, Any]:
        """Парсить уточнення та повертає структуровані дані."""
        if not text:
            return {'type': None, 'data': None}
        
        result = {
            'type': None,
            'data': None,
            'original': text.strip()
        }
        
        # 1. Розміри з кодом товару: Ф2181 (38)
        size_with_code_pattern = r'([Фф][0-9]+)\s*\(([234][0-9](?:[.,][05])?)\)'
        matches = re.findall(size_with_code_pattern, text)
        if matches:
            result['type'] = 'size_with_code'
            result['data'] = [
                {
                    'product_code': match[0].upper(),
                    'size': match[1].replace(',', '.')
                }
                for match in matches
            ]
            return result
        
        # 2. Просто розміри
        size_patterns = [
            r'\b([234][0-9](?:[.,][05])?)\s*(?:розмір|размер|eu|eur)?\b',
            r'\bрозмір\s*([234][0-9](?:[.,][05])?)\b'
        ]
        
        for pattern in size_patterns:
            match = re.search(pattern, text.lower())
            if match:
                result['type'] = 'size'
                result['data'] = {'size': match.group(1).replace(',', '.')}
                return result
        
        # 3. Заміри
        measurement_patterns = [
            r'\b([0-9]{2,3}(?:[.,][0-9])?)\s*см\b',
            r'\bзамір\s*([0-9]{2,3}(?:[.,][0-9])?)\b',
            r'\bстелька\s*([0-9]{2,3}(?:[.,][0-9])?)\b'
        ]
        
        for pattern in measurement_patterns:
            match = re.search(pattern, text.lower())
            if match:
                result['type'] = 'measurement'
                result['data'] = {'measurement': match.group(1).replace(',', '.')}
                return result
        
        # 4. Метод оплати
        payment_method = PaymentMethodManager.identify_payment_method(text)
        if payment_method:
            result['type'] = 'payment'
            result['data'] = {'method': payment_method}
            return result
        
        # 5. Все інше - коментар
        result['type'] = 'comment'
        result['data'] = {'comment': text.strip()}
        return result

class ClientManager:
    """Клас для управління клієнтами та їх дедуплікацією."""
    
    def __init__(self, session):
        self.session = session
        self.client_cache = {}
        self.phone_cache = {}
        self.facebook_cache = {}
    
    def normalize_phone(self, phone: str) -> str:
        """Нормалізує номер телефону."""
        if not phone:
            return ""
        return re.sub(r'[^\d+]', '', phone.strip())
    
    def normalize_facebook(self, facebook_url: str) -> str:
        """Нормалізує Facebook URL."""
        if not facebook_url:
            return ""
        url = facebook_url.lower().strip()
        url = url.replace('https://', '').replace('http://', '').replace('www.', '')
        return url
    
    def find_or_create_client(self, client_data: Dict[str, str]) -> Client:
        """Знаходить існуючого клієнта або створює нового з дедуплікацією."""
        name = client_data.get('name', '').strip()
        phone = self.normalize_phone(client_data.get('phone', ''))
        facebook = self.normalize_facebook(client_data.get('facebook', ''))
        
        # Пошук за телефоном
        if phone and phone in self.phone_cache:
            existing_client = self.phone_cache[phone]
            self._update_client_info(existing_client, client_data)
            return existing_client
        
        # Пошук за Facebook
        if facebook and facebook in self.facebook_cache:
            existing_client = self.facebook_cache[facebook]
            self._update_client_info(existing_client, client_data)
            return existing_client
        
        # Пошук в базі даних
        existing_client = None
        if phone:
            existing_client = self.session.query(Client).filter(
                Client.phone == phone
            ).first()
        
        if not existing_client and facebook:
            existing_client = self.session.query(Client).filter(
                Client.facebook == facebook
            ).first()
        
        if existing_client:
            self._cache_client(existing_client)
            self._update_client_info(existing_client, client_data)
            return existing_client
        
        # Якщо немає жодних даних — анонімне замовлення
        if not name and not phone and not facebook:
            return None

        # Створюємо нового клієнта
        new_client = Client(
            name=name or None,
            phone=phone or None,
            facebook=facebook or None,
            viber=client_data.get('viber', '').strip() or None,
            telegram=client_data.get('telegram', '').strip() or None,
            instagram=client_data.get('instagram', '').strip() or None,
            email=client_data.get('email', '').strip() or None
        )
        
        self.session.add(new_client)
        self.session.flush()  # Отримуємо ID
        
        self._cache_client(new_client)
        return new_client
    
    def _cache_client(self, client: Client):
        """Кешує клієнта."""
        self.client_cache[client.id] = client
        if client.phone:
            self.phone_cache[client.phone] = client
        if client.facebook:
            normalized_fb = self.normalize_facebook(client.facebook)
            self.facebook_cache[normalized_fb] = client
    
    def _update_client_info(self, client: Client, new_data: Dict[str, str]):
        """Оновлює інформацію про клієнта, якщо нова інформація є корисною."""
        updated = False
        
        # Оновлюємо тільки порожні поля
        for field in ['name', 'phone', 'facebook', 'viber', 'telegram', 'instagram', 'email']:
            current_value = getattr(client, field)
            new_value = new_data.get(field, '').strip()
            
            if not current_value and new_value:
                setattr(client, field, new_value)
                updated = True
        
        if updated:
            logger.info(f"Оновлено дані клієнта: {client.name}")

class ProductPriceManager:
    """Клас для управління цінами товарів."""
    
    def __init__(self, session):
        self.session = session
        self.price_updates = defaultdict(list)
    
    def register_sale_price(self, product_code: str, sale_price: float, sale_date: datetime):
        """Реєструє ціну продажу товару."""
        self.price_updates[product_code].append({
            'price': sale_price,
            'date': sale_date
        })
    
    def apply_price_updates(self):
        """Застосовує оновлення цін: остання ціна продажу → поточна ціна."""
        logger.info(f"Застосовуємо оновлення цін для {len(self.price_updates)} товарів")
        
        for product_code, sales in self.price_updates.items():
            # Сортуємо за датою - остання ціна продажу
            latest_sale = max(sales, key=lambda x: x['date'])
            latest_price = latest_sale['price']
            
            # Знаходимо товар
            product = self.session.query(Product).filter(
                Product.productnumber == product_code
            ).first()
            
            if product:
                # Зберігаємо стару ціну в oldprice (якщо її там ще немає)
                if product.price and not product.oldprice:
                    product.oldprice = product.price
                
                # Встановлюємо нову ціну
                old_price = product.price
                product.price = latest_price
                
                logger.info(f"Товар {product_code}: ціна {old_price} → {latest_price}")
            else:
                logger.warning(f"Товар {product_code} не знайдено для оновлення ціни")

class OrdersParser:
    """Основний клас парсингу замовлень."""
    
    def __init__(self):
        self.session = None
        self.client_manager = None
        self.price_manager = None
        self.stats = {
            'total_orders': 0,
            'successful_orders': 0,
            'errors': 0,
            'clients_created': 0,
            'clients_updated': 0,
            'price_updates': 0
        }
    
    def get_google_sheet_client(self):
        """Повертає авторизований клієнт Google Sheets."""
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                GOOGLE_SHEETS_CREDENTIALS_FILE,
                ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            )
            return gspread.authorize(creds)
        except Exception as e:
            logger.error(f"Помилка підключення до Google Sheets: {e}")
            return None
    
    def parse_product_codes(self, products_text: str) -> List[str]:
        """Парсить коди товарів з тексту (розділені крапкою з комою)."""
        if not products_text:
            return []
        
        # Розділяємо по ; та очищуємо
        codes = [code.strip() for code in products_text.split(';') if code.strip()]
        return codes
    
    def get_or_create_reference_data(self):
        """Отримує або створює довідкові дані."""
        # Статуси замовлення
        order_statuses = {
            'Нове': self.session.query(OrderStatus).filter_by(name='Нове').first(),
            'В обробці': self.session.query(OrderStatus).filter_by(name='В обробці').first(),
            'Відправлено': self.session.query(OrderStatus).filter_by(name='Відправлено').first(),
            'Доставлено': self.session.query(OrderStatus).filter_by(name='Доставлено').first(),
            'Скасовано': self.session.query(OrderStatus).filter_by(name='Скасовано').first(),
        }
        
        # Створюємо відсутні статуси
        for status_name, status_obj in order_statuses.items():
            if not status_obj:
                status_obj = OrderStatus(name=status_name)
                self.session.add(status_obj)
                self.session.flush()
                order_statuses[status_name] = status_obj
        
        # Статуси оплати
        payment_statuses = {
            'Не оплачено': self.session.query(PaymentStatus).filter_by(name='Не оплачено').first(),
            'Оплачено': self.session.query(PaymentStatus).filter_by(name='Оплачено').first(),
            'Часткова оплата': self.session.query(PaymentStatus).filter_by(name='Часткова оплата').first(),
        }
        
        for status_name, status_obj in payment_statuses.items():
            if not status_obj:
                status_obj = PaymentStatus(name=status_name)
                self.session.add(status_obj)
                self.session.flush()
                payment_statuses[status_name] = status_obj
        
        # Методи доставки
        delivery_methods = {
            'Нова Пошта': self.session.query(DeliveryMethod).filter_by(name='Нова Пошта').first(),
            'Укрпошта': self.session.query(DeliveryMethod).filter_by(name='Укрпошта').first(),
            'Самовивіз': self.session.query(DeliveryMethod).filter_by(name='Самовивіз').first(),
        }
        
        for method_name, method_obj in delivery_methods.items():
            if not method_obj:
                method_obj = DeliveryMethod(name=method_name)
                self.session.add(method_obj)
                self.session.flush()
                delivery_methods[method_name] = method_obj
        
        return order_statuses, payment_statuses, delivery_methods
    
    def parse_order_row(self, row: List[str], headers: List[str], 
                       order_statuses: Dict, payment_statuses: Dict, 
                       delivery_methods: Dict, sheet_date: datetime) -> bool:
        """Парсить один рядок замовлення."""
        try:
            # Витягуємо дані з рядка
            order_data = {}
            for i, header in enumerate(headers):
                order_data[header] = row[i] if i < len(row) else ''
            
            # Перевіряємо наявність товарів
            products_text = order_data.get('Товар', '').strip()
            if not products_text:
                return False
            
            # Парсимо коди товарів
            product_codes = self.parse_product_codes(products_text)
            if not product_codes:
                return False
            
            # Обробляємо клієнта
            client_data = {
                'name': order_data.get('Клієнт', ''),
                'phone': order_data.get('Контактний номер', ''),
                'facebook': order_data.get('Facebook', ''),
                'viber': order_data.get('Viber', ''),
                'telegram': order_data.get('Telegram', ''),
                'instagram': order_data.get('Instagram', ''),
                'email': order_data.get('E-mail', '')
            }
            
            client = self.client_manager.find_or_create_client(client_data)
            
            # Парсимо ціну
            price_text = order_data.get('Сума', '').replace(',', '.').strip()
            try:
                price = float(price_text) if price_text else 0.0
            except ValueError:
                price = 0.0
            
            # Створюємо замовлення
            order = Order(
                client_id=client.id,
                order_date=sheet_date,
                total_amount=price,
                status_id=order_statuses['Нове'].id,
                payment_status_id=payment_statuses['Не оплачено'].id,
                delivery_method_id=delivery_methods['Нова Пошта'].id,
                notes=order_data.get('Коментарі', '').strip() or None
            )
            
            # Обробляємо уточнення
            clarification_text = order_data.get('Уточнення', '').strip()
            if clarification_text:
                clarification_data = ClarificationParser.parse_clarification(clarification_text)
                
                if clarification_data['type'] == 'payment':
                    # Оновлюємо метод оплати замовлення
                    payment_method = clarification_data['data']['method']
                    if payment_method == 'Готівка':
                        order.notes = (order.notes or '') + f"; Оплата: {payment_method}"
                    elif payment_method in ['Карта', 'Переказ']:
                        order.notes = (order.notes or '') + f"; Оплата: {payment_method}"
                
                elif clarification_data['type'] == 'comment':
                    # Додаємо до коментарів
                    comment = clarification_data['data']['comment']
                    if order.notes:
                        order.notes += f"; {comment}"
                    else:
                        order.notes = comment
            
            self.session.add(order)
            self.session.flush()  # Отримуємо ID замовлення
            
            # Створюємо деталі замовлення
            for product_code in product_codes:
                product = self.session.query(Product).filter(
                    Product.productnumber == product_code
                ).first()
                
                if product:
                    # Створюємо деталь замовлення
                    order_detail = OrderDetail(
                        order_id=order.id,
                        product_id=product.id,
                        quantity=1,  # Зазвичай по 1 штуці
                        unit_price=price / len(product_codes) if len(product_codes) > 0 else price
                    )
                    self.session.add(order_detail)
                    
                    # Реєструємо ціну продажу для оновлення
                    if price > 0:
                        unit_price = price / len(product_codes)
                        self.price_manager.register_sale_price(product_code, unit_price, sheet_date)
                    
                    # Обробляємо уточнення розмірів
                    if clarification_text:
                        clarification_data = ClarificationParser.parse_clarification(clarification_text)
                        
                        if clarification_data['type'] == 'size_with_code':
                            # Оновлюємо розмір конкретного товару
                            for size_data in clarification_data['data']:
                                if size_data['product_code'] == product_code:
                                    if not product.sizeeu:  # Оновлюємо тільки якщо немає розміру
                                        product.sizeeu = size_data['size']
                                        logger.info(f"Оновлено розмір товару {product_code}: {size_data['size']}")
                        
                        elif clarification_data['type'] == 'size' and len(product_codes) == 1:
                            # Якщо один товар в замовленні - оновлюємо його розмір
                            if not product.sizeeu:
                                product.sizeeu = clarification_data['data']['size']
                        
                        elif clarification_data['type'] == 'measurement':
                            # Оновлюємо заміри
                            if not product.measurementscm:
                                product.measurementscm = clarification_data['data']['measurement']
                                logger.info(f"Оновлено заміри товару {product_code}: {clarification_data['data']['measurement']}см")
                else:
                    logger.warning(f"Товар {product_code} не знайдено в базі даних")
            
            self.stats['successful_orders'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Помилка парсингу рядка замовлення: {e}")
            self.stats['errors'] += 1
            return False
    
    def parse_orders_sheet(self, worksheet, order_statuses, payment_statuses, delivery_methods):
        """Парсить один аркуш замовлень."""
        try:
            sheet_name = worksheet.title
            logger.info(f"Парсинг аркуша: {sheet_name}")
            
            # Витягуємо дату з назви аркуша
            try:
                date_parts = sheet_name.split('.')
                if len(date_parts) == 3:
                    day, month, year = map(int, date_parts)
                    sheet_date = datetime(year, month, day)
                else:
                    sheet_date = datetime.now()
            except:
                sheet_date = datetime.now()
            
            all_data = worksheet.get_all_values()
            if len(all_data) < 2:
                logger.warning(f"Аркуш {sheet_name} порожній або має тільки заголовки")
                return
            
            headers = all_data[0]
            data_rows = all_data[1:]
            
            logger.info(f"Обробляємо {len(data_rows)} рядків замовлень з аркуша {sheet_name}")
            
            for row_idx, row in enumerate(data_rows, 1):
                if not any(cell.strip() for cell in row):  # Пропускаємо порожні рядки
                    continue
                
                self.stats['total_orders'] += 1
                success = self.parse_order_row(row, headers, order_statuses, 
                                             payment_statuses, delivery_methods, sheet_date)
                
                if success and self.stats['total_orders'] % 100 == 0:
                    logger.info(f"Оброблено {self.stats['total_orders']} замовлень")
                    
        except Exception as e:
            logger.error(f"Помилка парсингу аркуша {worksheet.title}: {e}")
    
    async def parse_all_orders(self):
        """Основний метод парсингу всіх замовлень."""
        logger.info("🚀 ПОЧАТОК КОМПЛЕКСНОГО ПАРСИНГУ ЗАМОВЛЕНЬ")
        logger.info("=" * 60)
        
        # Ініціалізація
        session_factory = get_session_factory()
        self.session = session_factory()
        self.client_manager = ClientManager(self.session)
        self.price_manager = ProductPriceManager(self.session)
        
        try:
            # Підключення до Google Sheets
            client = self.get_google_sheet_client()
            if not client:
                raise Exception("Не вдалося підключитися до Google Sheets")
            
            doc = client.open(ORDERS_DOCUMENT_NAME)
            logger.info(f"✅ Документ відкрито: {doc.title}")
            
            # Отримуємо довідкові дані
            order_statuses, payment_statuses, delivery_methods = self.get_or_create_reference_data()
            
            # Отримуємо всі аркуші замовлень
            worksheets = doc.worksheets()
            order_sheets = [
                ws for ws in worksheets 
                if any(char.isdigit() for char in ws.title) and '.' in ws.title
            ]
            
            logger.info(f"Знайдено {len(order_sheets)} аркушів замовлень")
            
            # Парсимо аркуші (починаємо з останніх для актуальних цін)
            order_sheets.sort(key=lambda x: x.title, reverse=True)
            
            for i, worksheet in enumerate(order_sheets, 1):
                logger.info(f"Обробляємо аркуш {i}/{len(order_sheets)}: {worksheet.title}")
                self.parse_orders_sheet(worksheet, order_statuses, payment_statuses, delivery_methods)
                
                # Комітимо кожні 10 аркушів
                if i % 10 == 0:
                    self.session.commit()
                    logger.info(f"Збережено зміни після {i} аркушів")
            
            # Застосовуємо оновлення цін
            logger.info("Застосовуємо оновлення цін товарів...")
            self.price_manager.apply_price_updates()
            
            # Фінальний коміт
            self.session.commit()
            logger.info("Всі зміни збережено в базу даних")
            
            # Статистика
            logger.info("\n" + "=" * 60)
            logger.info("📊 СТАТИСТИКА ПАРСИНГУ:")
            logger.info(f"  Всього рядків оброблено: {self.stats['total_orders']}")
            logger.info(f"  Успішних замовлень: {self.stats['successful_orders']}")
            logger.info(f"  Помилок: {self.stats['errors']}")
            logger.info(f"  Успішність: {(self.stats['successful_orders']/max(self.stats['total_orders'], 1)*100):.1f}%")
            
        except Exception as e:
            logger.error(f"Критична помилка парсингу: {e}")
            self.session.rollback()
            raise
        finally:
            self.session.close()

async def main():
    """Основна функція."""
    parser = OrdersParser()
    await parser.parse_all_orders()

if __name__ == "__main__":
    asyncio.run(main()) 
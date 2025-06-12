#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
КОМПЛЕКСНИЙ ПАРСИНГ ЗАМОВЛЕНЬ З GOOGLE SHEETS
Реалізує всі виявлені паттерни:
- Дедуплікація клієнтів по телефону/Facebook
- Розпізнавання методів оплати з коментарів/уточнень
- Парсинг уточнень (розміри, заміри, коментарі)
- Синхронізація цін (остання ціна продажу → поточна ціна)
- Обробка множинних товарів у замовленні
- Автоматичне оновлення розмірів та замірів товарів
"""

import time
from datetime import datetime, timedelta
import logging
import argparse
import sys
import os
import re
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Tuple, Any
import asyncio
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv
from sqlalchemy import text

# Додаємо шлях для імпортів
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.database import SessionLocal
from models.models import (
    Client, Order, OrderItem, Product, OrderStatus, 
    PaymentStatus, DeliveryMethod, Address
)

# Додаємо поточну директорію до шляху
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.models.database import get_db
from backend.models.models import *

# Завантажуємо змінні оточення
load_dotenv()

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('orders_comprehensive_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Google Sheets налаштування
GOOGLE_SHEETS_JSON_KEY = os.getenv("GOOGLE_SHEETS_JSON_KEY")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GOOGLE_SHEETS_CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "secure_creds", GOOGLE_SHEETS_JSON_KEY)
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
                r'\bналичними\b', r'\bналичкой\b', r'\bcash\b', r'\bгрош[іиыі]\b', r'\bналом\b'
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
        
        # 1. Розміри з кодом товару: Ф2181 (38); Ф2080 (41);
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
        
        # 2. Заміри (перевіряємо спочатку, бо мають приоритет)
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
        
        # 3. Просто розміри
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
        self.stats = {'created': 0, 'updated': 0, 'found': 0}
    
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
            if self._update_client_info(existing_client, client_data):
                self.stats['updated'] += 1
            else:
                self.stats['found'] += 1
            return existing_client
        
        # Пошук за Facebook
        if facebook and facebook in self.facebook_cache:
            existing_client = self.facebook_cache[facebook]
            if self._update_client_info(existing_client, client_data):
                self.stats['updated'] += 1
            else:
                self.stats['found'] += 1
            return existing_client
        
        # Пошук в базі даних
        existing_client = None
        if phone:
            existing_client = self.session.query(Client).filter(
                Client.phone_number == phone
            ).first()
        
        if not existing_client and facebook:
            existing_client = self.session.query(Client).filter(
                Client.facebook == facebook
            ).first()
        
        if existing_client:
            self._cache_client(existing_client)
            if self._update_client_info(existing_client, client_data):
                self.stats['updated'] += 1
            else:
                self.stats['found'] += 1
            return existing_client
        
        # Створюємо нового клієнта (використовуємо реальну структуру БД)
        name_parts = name.split(' ', 1) if name else ['Невідомий', 'клієнт']
        first_name = name_parts[0] if name_parts else 'Невідомий'
        last_name = name_parts[1] if len(name_parts) > 1 else 'клієнт'
        
        new_client = Client(
            first_name=first_name,
            last_name=last_name,
            phone_number=phone or None,
            email=client_data.get('email', '').strip() or None,
            facebook=facebook or None,
            viber=client_data.get('viber', '').strip() or None,
            telegram=client_data.get('telegram', '').strip() or None,
            instagram=client_data.get('instagram', '').strip() or None
        )
        
        self.session.add(new_client)
        self.session.flush()  # Отримуємо ID
        
        self._cache_client(new_client)
        self.stats['created'] += 1
        return new_client
    
    def _cache_client(self, client: Client):
        """Кешує клієнта."""
        self.client_cache[client.id] = client
        if client.phone_number:
            self.phone_cache[client.phone_number] = client
        if client.facebook:
            normalized_fb = self.normalize_facebook(client.facebook)
            self.facebook_cache[normalized_fb] = client
    
    def _update_client_info(self, client: Client, new_data: Dict[str, str]) -> bool:
        """Оновлює інформацію про клієнта, якщо нова інформація є корисною."""
        updated = False
        
        # Оновлюємо тільки порожні поля
        if not client.email and new_data.get('email', '').strip():
            client.email = new_data.get('email', '').strip()
            updated = True
        
        if not client.facebook and new_data.get('facebook', '').strip():
            client.facebook = new_data.get('facebook', '').strip()
            updated = True
        
        if not client.viber and new_data.get('viber', '').strip():
            client.viber = new_data.get('viber', '').strip()
            updated = True
            
        if not client.telegram and new_data.get('telegram', '').strip():
            client.telegram = new_data.get('telegram', '').strip()
            updated = True
            
        if not client.instagram and new_data.get('instagram', '').strip():
            client.instagram = new_data.get('instagram', '').strip()
            updated = True
        
        return updated

class ProductPriceManager:
    """Клас для управління цінами товарів."""
    
    def __init__(self, session):
        self.session = session
        self.price_updates = defaultdict(list)
        self.size_updates = defaultdict(list)
        self.measurement_updates = defaultdict(list)
    
    def register_sale_price(self, product_code: str, sale_price: float, sale_date: datetime):
        """Реєструє ціну продажу товару."""
        self.price_updates[product_code].append({
            'price': sale_price,
            'date': sale_date
        })
    
    def register_size_update(self, product_code: str, size: str):
        """Реєструє оновлення розміру товару."""
        if size and size.strip():
            self.size_updates[product_code].append(size.strip())
    
    def register_measurement_update(self, product_code: str, measurement: str):
        """Реєструє оновлення заміру товару."""
        if measurement and measurement.strip():
            self.measurement_updates[product_code].append(measurement.strip())
    
    def apply_all_updates(self):
        """Застосовує всі зібрані оновлення товарів."""
        logger.info("Застосовуємо оновлення товарів...")
        
        # Оновлення цін
        self._apply_price_updates()
        
        # Оновлення розмірів
        self._apply_size_updates()
        
        # Оновлення замірів
        self._apply_measurement_updates()
    
    def _apply_price_updates(self):
        """Застосовує оновлення цін: остання ціна продажу → поточна ціна."""
        logger.info(f"Оновлення цін для {len(self.price_updates)} товарів")
        
        for product_code, sales in self.price_updates.items():
            # Сортуємо за датою - остання ціна продажу
            latest_sale = max(sales, key=lambda x: x['date'])
            latest_price = latest_sale['price']
            
            # Додаємо # до номера товару для пошуку в БД
            db_product_code = f"#{product_code}" if not product_code.startswith('#') else product_code
            
            # Знаходимо товар
            product = self.session.query(Product).filter(
                Product.productnumber == db_product_code
            ).first()
            
            if product:
                # Зберігаємо стару ціну в oldprice (якщо її там ще немає)
                if product.price and not product.oldprice:
                    product.oldprice = product.price
                
                # Встановлюємо нову ціну
                old_price = product.price
                product.price = latest_price
                
                logger.debug(f"Товар {product_code}: ціна {old_price} → {latest_price}")
            else:
                logger.warning(f"Товар {product_code} не знайдено для оновлення ціни")
    
    def _apply_size_updates(self):
        """Застосовує оновлення розмірів товарів."""
        logger.info(f"Оновлення розмірів для {len(self.size_updates)} товарів")
        
        for product_code, sizes in self.size_updates.items():
            # Беремо останній розмір
            latest_size = sizes[-1] if sizes else None
            
            if latest_size:
                # Додаємо # до номера товару для пошуку в БД
                db_product_code = f"#{product_code}" if not product_code.startswith('#') else product_code
                
                product = self.session.query(Product).filter(
                    Product.productnumber == db_product_code
                ).first()
                
                if product:
                    # Оновлюємо тільки якщо розмір був порожній
                    if not product.sizeeu:
                        product.sizeeu = latest_size
                        logger.debug(f"Товар {product_code}: встановлено розмір {latest_size}")
                else:
                    logger.warning(f"Товар {product_code} не знайдено для оновлення розміру")
    
    def _apply_measurement_updates(self):
        """Застосовує оновлення замірів товарів."""
        logger.info(f"Оновлення замірів для {len(self.measurement_updates)} товарів")
        
        for product_code, measurements in self.measurement_updates.items():
            # Беремо останній замір
            latest_measurement = measurements[-1] if measurements else None
            
            if latest_measurement:
                # Додаємо # до номера товару для пошуку в БД
                db_product_code = f"#{product_code}" if not product_code.startswith('#') else product_code
                
                product = self.session.query(Product).filter(
                    Product.productnumber == db_product_code
                ).first()
                
                if product:
                    # Оновлюємо тільки якщо замір був порожній
                    if not product.measurementscm:
                        product.measurementscm = latest_measurement
                        logger.debug(f"Товар {product_code}: встановлено замір {latest_measurement}см")
                else:
                    logger.warning(f"Товар {product_code} не знайдено для оновлення заміру")

class OrdersComprehensiveParser:
    """Основний клас комплексного парсингу замовлень."""
    
    def __init__(self):
        self.session = None
        self.client_manager = None
        self.price_manager = None
        self.stats = {
            'total_orders': 0,
            'successful_orders': 0,
            'errors': 0,
            'missing_products': 0,
            'orders_with_missing_products': 0,
            'clients_created': 0,
            'clients_updated': 0,
            'price_updates': 0,
            'size_updates': 0,
            'measurement_updates': 0
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
        logger.info("Ініціалізація довідкових даних...")
        
        # Статуси замовлення - використовуємо існуючі
        order_statuses = {}
        # Мапінг наших назв на існуючі в БД
        status_mapping = {
            'Нове': 'Підтверджено',          # ID 1
            'В обробці': 'В черзі',          # ID 8  
            'Відправлено': 'Підтверджено',   # ID 1
            'Доставлено': 'Підтверджено',    # ID 1
            'Скасовано': 'Відміна'           # ID 5
        }
        
        for our_name, db_name in status_mapping.items():
            result = self.session.execute(
                text("SELECT id FROM order_statuses WHERE status_name = :name"),
                {"name": db_name}
            ).first()
            order_statuses[our_name] = type('OrderStatus', (), {'id': result[0]})()
        
        # Статуси оплати - використовуємо існуючі
        payment_statuses = {}
        # Мапінг наших назв на існуючі в БД
        payment_mapping = {
            'Не оплачено': 'Не оплачено',    # ID 4
            'Оплачено': 'Оплачено',          # ID 1
            'Часткова оплата': 'Доплатити'   # ID 2
        }
        
        for our_name, db_name in payment_mapping.items():
            result = self.session.execute(
                text("SELECT id FROM payment_statuses WHERE status_name = :name"),
                {"name": db_name}
            ).first()
            payment_statuses[our_name] = type('PaymentStatus', (), {'id': result[0]})()
        
        # Методи доставки - використовуємо існуючі
        delivery_methods = {}
        # Мапінг наших назв на існуючі в БД
        delivery_mapping = {
            'Нова Пошта': 'нп',             # ID 1
            'Укрпошта': 'уп',               # ID 2
            'Самовивіз': 'самовивіз'        # ID 4
        }
        
        for our_name, db_name in delivery_mapping.items():
            result = self.session.execute(
                text("SELECT id FROM delivery_methods WHERE method_name = :name"),
                {"name": db_name}
            ).first()
            delivery_methods[our_name] = type('DeliveryMethod', (), {'id': result[0]})()
        
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
            products_text = order_data.get('Номера товарів', '').strip()
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
                # Обробляємо від'ємні суми (повернення) - встановлюємо 0 і додаємо нотатку
                if price < 0:
                    return_note = f"ПОВЕРНЕННЯ: {price} грн"
                    if comments:
                        comments += f"; {return_note}"
                    else:
                        comments = return_note
                    price = 0.0  # Встановлюємо 0 щоб не порушувати constraint
            except ValueError:
                price = 0.0
            
            # Обробляємо уточнення
            clarification_text = order_data.get('Уточнення', '').strip()
            clarification_data = None
            if clarification_text:
                clarification_data = ClarificationParser.parse_clarification(clarification_text)
            
            # Визначаємо статуси
            order_status_text = order_data.get('Статус відповіді', '').strip()
            payment_status_text = order_data.get('Статус оплати', '').strip()
            delivery_method_text = order_data.get('Доставка', '').strip()
            
            # Маппинг статусів на ID в БД
            order_status_id = self.map_order_status(order_status_text, order_statuses)
            payment_status_id = self.map_payment_status(payment_status_text, payment_statuses)
            delivery_method_id = self.map_delivery_method(delivery_method_text, delivery_methods)
            
            # Дата відстрочки
            deferred_text = order_data.get('Відкладено до', '').strip()
            deferred_date = None
            if deferred_text:
                try:
                    deferred_date = datetime.strptime(deferred_text, '%d.%m.%Y').date()
                except:
                    pass
            
            # Коментарі з різних полів
            comments_parts = []
            for field in ['Коментарі', 'Уточнення']:
                comment = order_data.get(field, '').strip()
                if comment:
                    comments_parts.append(comment)
            comments = '; '.join(comments_parts) if comments_parts else None

            # Створюємо замовлення
            order = Order(
                client_id=client.id,
                order_date=sheet_date,
                order_status_id=order_status_id,
                total_amount=float(order_data.get('Сума', 0) or 0),
                payment_status_id=payment_status_id,
                delivery_method_id=delivery_method_id,
                tracking_number=order_data.get('Номер накладної', '').strip() or None,
                notes=comments,
                priority=int(order_data.get('Пріорітетність', 0) or 0),
                deferred_until=deferred_date
            )
            
            # Перевіряємо методи оплати в коментарях
            comments_text = order_data.get('Коментарі', '').strip()
            payment_method_from_comments = None
            if comments_text:
                payment_method_from_comments = PaymentMethodManager.identify_payment_method(comments_text)
            
            # Додаємо інформацію з уточнень до замовлення
            if clarification_data:
                if clarification_data['type'] == 'payment':
                    # Оновлюємо інформацію про оплату в notes
                    payment_method = clarification_data['data']['method']
                    payment_note = f"Оплата: {payment_method}"
                    if order.notes:
                        order.notes += f"; {payment_note}"
                    else:
                        order.notes = payment_note
                
                elif clarification_data['type'] == 'comment':
                    # Додаємо коментар з уточнень
                    comment = clarification_data['data']['comment']
                    if order.notes:
                        order.notes += f"; {comment}"
                    else:
                        order.notes = comment
            
            # Додаємо метод оплати з коментарів (якщо не було в уточненнях)
            if payment_method_from_comments and not (clarification_data and clarification_data['type'] == 'payment'):
                payment_note = f"Оплата: {payment_method_from_comments}"
                if order.notes:
                    order.notes += f"; {payment_note}"
                else:
                    order.notes = payment_note
            
            self.session.add(order)
            self.session.flush()  # Отримуємо ID замовлення
            
            # Перевіряємо, чи всі товари існують в базі даних
            found_products = []
            missing_products = []
            
            for product_code in product_codes:
                # Додаємо # до номера товару для пошуку в БД
                db_product_code = f"#{product_code}" if not product_code.startswith('#') else product_code
                
                product = self.session.query(Product).filter(
                    Product.productnumber == db_product_code
                ).first()
                
                if product:
                    found_products.append((product_code, product))
                else:
                    missing_products.append(product_code)
            
            # Якщо немає жодного товару - пропускаємо замовлення
            if not found_products:
                logger.warning(f"Пропускаємо замовлення - жоден з товарів не знайдено: {product_codes}")
                return False
            
            # Логуємо відсутні товари
            if missing_products:
                logger.warning(f"Відсутні товари в замовленні: {missing_products}")
                self.stats['missing_products'] += len(missing_products)
                self.stats['orders_with_missing_products'] += 1
                # Додаємо до коментарів інформацію про відсутні товари
                missing_note = f"Відсутні товари: {', '.join(missing_products)}"
                if order.notes:
                    order.notes += f"; {missing_note}"
                else:
                    order.notes = missing_note
            
            # Створюємо позиції замовлення тільки для знайдених товарів
            for product_code, product in found_products:
                # Створюємо позицію замовлення
                item_price = price / len(found_products) if len(found_products) > 0 else price
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=1,  # Зазвичай по 1 штуці
                    price=item_price
                )
                self.session.add(order_item)
                
                # Реєструємо ціну продажу для оновлення
                if price > 0:
                    self.price_manager.register_sale_price(product_code, item_price, sheet_date)
                
                # Обробляємо уточнення товарів
                if clarification_data:
                    if clarification_data['type'] == 'size_with_code':
                        # Оновлення розміру конкретного товару
                        for size_data in clarification_data['data']:
                            if size_data['product_code'] == product_code:
                                self.price_manager.register_size_update(product_code, size_data['size'])
                    
                    elif clarification_data['type'] == 'size' and len(found_products) == 1:
                        # Якщо один товар в замовленні - оновлюємо його розмір
                        self.price_manager.register_size_update(product_code, clarification_data['data']['size'])
                    
                    elif clarification_data['type'] == 'measurement':
                        # Оновлення замірів
                        self.price_manager.register_measurement_update(product_code, clarification_data['data']['measurement'])
            
            self.stats['successful_orders'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Помилка парсингу рядка замовлення: {e}")
            self.stats['errors'] += 1
            # Якщо помилка БД - робимо rollback сесії
            if 'violates check constraint' in str(e) or 'PendingRollbackError' in str(e):
                try:
                    self.session.rollback()
                    logger.warning("Виконано rollback сесії через constraint violation")
                except:
                    pass
            return False
    
    def parse_orders_sheet(self, worksheet, order_statuses, payment_statuses, delivery_methods):
        """Парсить один аркуш замовлень."""
        try:
            sheet_name = worksheet.title
            logger.info(f"Парсинг аркуша: {sheet_name}")
            
            try:
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
                    
                # Отримуємо дані з аркуша
                all_data = self.parse_sheet(sheet_name)
                
                if len(all_data) < 2:
                    logger.warning(f"Аркуш '{sheet_name}' порожній або містить тільки заголовки")
                    return
                
                headers = all_data[0]
                data_rows = all_data[1:]
            except Exception as e:
                logger.error(f"Помилка парсингу аркуша {sheet_name}: {e}")
                return
            
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
    
    def parse_clients_sheet(self, worksheet):
        """Парсить аркуш клієнтів."""
        try:
            logger.info("Парсинг аркуша 'Клієнти'...")
            
            # Парсинг аркуша "Клієнти"
            clients_worksheet = self.workbook.worksheet("Клієнти")
            logger.info("Парсинг аркуша клієнтів")
            all_data = self.parse_sheet("Клієнти")
            
            if not all_data:
                logger.warning("Аркуш 'Клієнти' порожній")
                return self.stats
            
            headers = all_data[0]
            client_rows = all_data[1:]
            
            logger.info(f"Обробляємо {len(client_rows)} клієнтів")
            
            for row_idx, row in enumerate(client_rows, 1):
                if not any(cell.strip() for cell in row):
                    continue
                
                # Витягуємо дані клієнта
                client_data = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        client_data[header] = row[i]
                
                # Мапуємо поля (назви колонок можуть відрізнятися)
                mapped_data = {
                    'name': client_data.get('Клієнт', '') or client_data.get('name', '') or client_data.get('Name', ''),
                    'phone': client_data.get('phone', '') or client_data.get('Phone', '') or client_data.get('Телефон', ''),
                    'facebook': client_data.get('Facebook', '') or client_data.get('facebook', ''),
                    'viber': client_data.get('Viber', '') or client_data.get('viber', ''),
                    'telegram': client_data.get('Telegram', '') or client_data.get('telegram', ''),
                    'instagram': client_data.get('Instagram', '') or client_data.get('instagram', ''),
                    'email': client_data.get('email', '') or client_data.get('Email', '') or client_data.get('E-mail', '')
                }
                
                # Створюємо/оновлюємо клієнта
                self.client_manager.find_or_create_client(mapped_data)
                
                if row_idx % 1000 == 0:
                    logger.info(f"Оброблено {row_idx} клієнтів")
                    
        except Exception as e:
            logger.error(f"Помилка парсингу аркуша клієнтів: {e}")
    
    async def parse_all_orders(self, max_sheets: int = None):
        """Основний метод парсингу всіх замовлень."""
        logger.info("🚀 ПОЧАТОК КОМПЛЕКСНОГО ПАРСИНГУ ЗАМОВЛЕНЬ")
        logger.info("=" * 70)
        
        # Ініціалізація
        self.session = SessionLocal()
        self.client_manager = ClientManager(self.session)
        self.price_manager = ProductPriceManager(self.session)
        
        try:
            # Підключення до Google Sheets
            client = self.get_google_sheet_client()
            if not client:
                raise Exception("Не вдалося підключитися до Google Sheets")
            
            doc = client.open(ORDERS_DOCUMENT_NAME)
            self.workbook = doc  # Зберігаємо посилання на workbook
            logger.info(f"✅ Документ відкрито: {doc.title}")
            
            # Отримуємо довідкові дані
            order_statuses, payment_statuses, delivery_methods = self.get_or_create_reference_data()
            
            # Отримуємо всі аркуші
            worksheets = doc.worksheets()
            
            # Розділяємо аркуші
            clients_sheet = None
            order_sheets = []
            
            for ws in worksheets:
                if ws.title.lower() == 'клієнти':
                    clients_sheet = ws
                elif any(char.isdigit() for char in ws.title) and '.' in ws.title:
                    order_sheets.append(ws)
            
            # Фаза 1: Парсинг клієнтів
            if clients_sheet:
                self.parse_clients_sheet(clients_sheet)
                self.session.commit()
                logger.info("✅ Клієнти з аркуша 'Клієнти' оброблені")
            
            # Фаза 2: Парсинг замовлень
            logger.info(f"Знайдено {len(order_sheets)} аркушів замовлень")
            
            # Сортуємо аркуші за датою (найновіші спочатку для актуальних цін)
            order_sheets.sort(key=lambda x: x.title, reverse=True)
            
            # Обмежуємо кількість аркушів для тестування
            if max_sheets:
                order_sheets = order_sheets[:max_sheets]
                logger.info(f"Обмежено до {max_sheets} аркушів для тестування")
            
            for i, worksheet in enumerate(order_sheets, 1):
                logger.info(f"Обробляємо аркуш {i}/{len(order_sheets)}: {worksheet.title}")
                self.parse_orders_sheet(worksheet, order_statuses, payment_statuses, delivery_methods)
                
                # Комітимо кожні 10 аркушів
                if i % 10 == 0:
                    self.session.commit()
                    logger.info(f"Збережено зміни після {i} аркушів")
            
            # Фаза 3: Застосовуємо оновлення товарів
            logger.info("Застосовуємо оновлення товарів...")
            self.price_manager.apply_all_updates()
            
            # Фінальний коміт
            self.session.commit()
            logger.info("Всі зміни збережено в базу даних")
            
            # Оновлюємо статистику
            self.stats.update({
                'clients_created': self.client_manager.stats['created'],
                'clients_updated': self.client_manager.stats['updated'],
                'price_updates': len(self.price_manager.price_updates),
                'size_updates': len(self.price_manager.size_updates),
                'measurement_updates': len(self.price_manager.measurement_updates)
            })
            
            # Статистика
            logger.info("\n" + "=" * 70)
            logger.info("📊 СТАТИСТИКА ПАРСИНГУ:")
            logger.info(f"  Всього рядків оброблено: {self.stats['total_orders']}")
            logger.info(f"  Успішних замовлень: {self.stats['successful_orders']}")
            logger.info(f"  Помилок: {self.stats['errors']}")
            logger.info(f"  Відсутніх товарів: {self.stats['missing_products']}")
            logger.info(f"  Замовлень з відсутніми товарами: {self.stats['orders_with_missing_products']}")
            logger.info(f"  Клієнтів створено: {self.stats['clients_created']}")
            logger.info(f"  Клієнтів оновлено: {self.stats['clients_updated']}")
            logger.info(f"  Товарів з оновленими цінами: {self.stats['price_updates']}")
            logger.info(f"  Товарів з оновленими розмірами: {self.stats['size_updates']}")
            logger.info(f"  Товарів з оновленими замірами: {self.stats['measurement_updates']}")
            logger.info(f"  Успішність: {(self.stats['successful_orders']/max(self.stats['total_orders'], 1)*100):.1f}%")
            
        except Exception as e:
            logger.error(f"Критична помилка парсингу: {e}")
            try:
                self.session.rollback()
            except:
                pass  # Ігноруємо помилки rollback
            raise
        finally:
            self.session.close()

    def map_order_status(self, status_text: str, order_statuses: Dict) -> int:
        """Маппить текстовий статус замовлення на ID в БД."""
        status_mapping = {
            'ПІДТВЕРДЖЕННО': 'Підтверджено',
            'ПІДТВЕРДЖЕНО': 'Підтверджено', 
            'НОВЕ': 'Підтверджено',
            'В ОБРОБЦІ': 'В черзі',
            'СКАСОВАНО': 'Відміна',
            'ВІДМІНА': 'Відміна'
        }
        
        mapped_status = status_mapping.get(status_text.upper(), 'Підтверджено')
        status_obj = order_statuses.get(mapped_status)
        if status_obj:
            return status_obj.id
        # Fallback - повертаємо перший доступний статус
        return list(order_statuses.values())[0].id
    
    def map_payment_status(self, status_text: str, payment_statuses: Dict) -> int:
        """Маппить текстовий статус оплати на ID в БД."""
        status_mapping = {
            'ОПЛАЧЕНО': 'Оплачено',
            'НЕ ОПЛАЧЕНО': 'Не оплачено',
            'ДОПЛАТИТИ': 'Доплатити',
            'ЧАСТКОВА ОПЛАТА': 'Доплатити'
        }
        
        mapped_status = status_mapping.get(status_text.upper(), 'Не оплачено')
        status_obj = payment_statuses.get(mapped_status)
        if status_obj:
            return status_obj.id
        # Fallback - повертаємо перший доступний статус
        return list(payment_statuses.values())[0].id
    
    def map_delivery_method(self, method_text: str, delivery_methods: Dict) -> int:
        """Маппить текстовий метод доставки на ID в БД."""
        method_mapping = {
            'НОВА ПОШТА': 'нп',
            'НП': 'нп',
            'УКРПОШТА': 'уп',
            'УП': 'уп',
            'САМОВИВІЗ': 'самовивіз',
            'МАГАЗИН': 'самовивіз'
        }
        
        mapped_method = method_mapping.get(method_text.upper(), 'нп')
        method_obj = delivery_methods.get(mapped_method)
        if method_obj:
            return method_obj.id
        # Fallback - повертаємо перший доступний метод
        return list(delivery_methods.values())[0].id

    def parse_sheet(self, sheet_name: str) -> List[List[str]]:
        """Парсить конкретний аркуш з обробкою quota exceeded."""
        max_retries = 3
        base_delay = 60  # 1 хвилина
        
        for attempt in range(max_retries):
            try:
                sheet = self.workbook.worksheet(sheet_name)
                # Отримуємо всі значення
                data = sheet.get_all_values()
                return data
                
            except Exception as e:
                error_str = str(e)
                if "quota exceeded" in error_str.lower() or "429" in error_str:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"Google API quota exceeded. Чекаємо {delay} секунд...")
                    time.sleep(delay)
                    continue
                else:
                    raise e
        
        # Якщо всі спроби неуспішні
        raise Exception(f"Не вдалося отримати дані з аркуша {sheet_name} після {max_retries} спроб")

async def main():
    """Основна функція."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Комплексний парсинг замовлень з Google Sheets')
    parser.add_argument('--test', type=int, help='Кількість аркушів для тестування (наприклад, --test 5)')
    args = parser.parse_args()
    
    parser_instance = OrdersComprehensiveParser()
    await parser_instance.parse_all_orders(max_sheets=args.test)

if __name__ == "__main__":
    asyncio.run(main()) 
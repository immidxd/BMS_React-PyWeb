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
- Оптимізації для швидкого повторного парсингу
- Дедублікація замовлень
- Кешування даних
"""

import time
import hashlib
import pickle
from datetime import datetime, timedelta
import logging
import argparse
import sys
import os
import re
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Tuple, Any, Set
from types import SimpleNamespace
import asyncio
import gspread
from google.oauth2 import service_account
from dotenv import load_dotenv
from sqlalchemy import text, and_, or_, inspect

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
GOOGLE_SHEETS_JSON_KEY = os.getenv("GOOGLE_SHEETS_JSON_KEY", "newproject2024-419923-working.json")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv(
    "GOOGLE_SHEETS_CREDENTIALS_FILE",
    os.path.join(SCRIPT_DIR, "secure_creds", GOOGLE_SHEETS_JSON_KEY)
)
if not os.path.isabs(GOOGLE_SHEETS_CREDENTIALS_FILE):
    GOOGLE_SHEETS_CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, GOOGLE_SHEETS_CREDENTIALS_FILE)
if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS_FILE):
    project_root = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
    mcp_key = os.path.join(project_root, "mcp-google-sheets", "working_credentials.json")
    if os.path.exists(mcp_key):
        GOOGLE_SHEETS_CREDENTIALS_FILE = mcp_key
ORDERS_DOCUMENT_NAME = os.getenv("GOOGLE_SHEETS_DOCUMENT_NAME_ORDERS")

# Кеш файли
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
SHEETS_CACHE_FILE = os.path.join(CACHE_DIR, "sheets_cache.pkl")
ORDERS_HASH_FILE = os.path.join(CACHE_DIR, "orders_hashes.pkl")
PRODUCTS_CACHE_FILE = os.path.join(CACHE_DIR, "products_cache.pkl")

# Створюємо директорію кешу
os.makedirs(CACHE_DIR, exist_ok=True)

class CacheManager:
    """Клас для управління кешем даних."""
    
    def __init__(self):
        self.sheets_cache = self.load_cache(SHEETS_CACHE_FILE, {})
        self.orders_hashes = self.load_cache(ORDERS_HASH_FILE, set())
        self.products_cache = self.load_cache(PRODUCTS_CACHE_FILE, {})
    
    def load_cache(self, file_path: str, default):
        """Завантажує кеш з файлу."""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            logger.warning(f"Не вдалося завантажити кеш {file_path}: {e}")
        return default
    
    def save_cache(self, file_path: str, data):
        """Зберігає кеш у файл."""
        try:
            with open(file_path, 'wb') as f:
                pickle.dump(data, f)
        except Exception as e:
            logger.error(f"Не вдалося зберегти кеш {file_path}: {e}")
    
    def save_all_caches(self):
        """Зберігає всі кеші."""
        self.save_cache(SHEETS_CACHE_FILE, self.sheets_cache)
        self.save_cache(ORDERS_HASH_FILE, self.orders_hashes)
        self.save_cache(PRODUCTS_CACHE_FILE, self.products_cache)
    
    def get_sheet_hash(self, sheet_name: str, data: List[List[str]]) -> str:
        """Генерує хеш для аркуша."""
        content = str(data)
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def is_sheet_changed(self, sheet_name: str, data: List[List[str]]) -> bool:
        """Перевіряє, чи змінився аркуш."""
        current_hash = self.get_sheet_hash(sheet_name, data)
        cached_hash = self.sheets_cache.get(sheet_name)
        
        if cached_hash != current_hash:
            self.sheets_cache[sheet_name] = current_hash
            return True
        return False
    
    def get_order_hash(self, order_data: Dict) -> str:
        """Генерує хеш для замовлення."""
        # Створюємо унікальний ідентифікатор замовлення
        key_fields = [
            order_data.get('Клієнт', ''),
            order_data.get('Контактний номер', ''),
            order_data.get('Номера товарів', ''),
            order_data.get('Сума', ''),
            order_data.get('_sheet_date', ''),  # Додаємо дату аркуша
        ]
        content = '|'.join(str(field) for field in key_fields)
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def is_order_processed(self, order_hash: str) -> bool:
        """Перевіряє, чи було замовлення вже оброблено."""
        return order_hash in self.orders_hashes
    
    def mark_order_processed(self, order_hash: str):
        """Позначає замовлення як оброблене."""
        self.orders_hashes.add(order_hash)

class ProductCache:
    """Клас для кешування товарів."""
    
    def __init__(self, session):
        self.session = session
        self.cache: Dict[str, int] = {}
        self.load_products()
    
    def load_products(self):
        """Завантажує всі товари в кеш."""
        logger.info("Завантаження товарів у кеш...")
        products = self.session.query(Product).all()
        for product in products:
            # Кешуємо за номером товару (з # і без)
            self.cache[product.productnumber] = product.id
            if product.productnumber.startswith('#'):
                self.cache[product.productnumber[1:]] = product.id
            else:
                self.cache[f"#{product.productnumber}"] = product.id
        logger.info(f"Завантажено {len(products)} товарів у кеш")
    
    def get_product(self, product_code: str) -> Optional[Product]:
        """Отримує товар з кешу."""
        # Спробуємо знайти з # і без
        product_id = self.cache.get(product_code)
        if product_id is None:
            if product_code.startswith('#'):
                product_id = self.cache.get(product_code[1:])
            else:
                product_id = self.cache.get(f"#{product_code}")
        if product_id is None:
            return None
        return self.session.get(Product, product_id)

class SalesChannelDetector:
    """Розпізнавання каналу продажу з коментарів та методу доставки."""

    CHANNELS = {
        'Telegram': [r'\bтг\b', r'\btg\b', r'\btelegram\b', r'\bтелеграм\b'],
        'OLX': [r'\bolx\b', r'\bолх\b'],
        'Viber': [r'\bvb\b', r'\bviber\b', r'\bвайбер\b', r'\bвб\b'],
        'Instagram': [r'\binst\b', r'\binstagram\b', r'\binsta\b', r'\big\b', r'\bінст\b'],
        'GRAILED': [r'\bgrailed\b'],
        'Магазин': [r'\bмагазин\b', r'\bshop\b'],
    }

    @staticmethod
    def detect(comments: str = '', delivery_method: str = '') -> str:
        """Повертає назву каналу продажу. За замовчуванням — 'Ефір'."""
        text = f"{comments} {delivery_method}".lower().strip()
        if not text:
            return 'Ефір'
        if 'магазин' in delivery_method.lower():
            return 'Магазин'
        for channel, patterns in SalesChannelDetector.CHANNELS.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    return channel
        return 'Ефір'


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
        self.load_existing_clients()
    
    def load_existing_clients(self):
        """Завантажує існуючих клієнтів у кеш."""
        logger.info("Завантаження існуючих клієнтів у кеш...")
        clients = self.session.query(Client).all()
        for client in clients:
            self._cache_client(client)
        logger.info(f"Завантажено {len(clients)} клієнтів у кеш")

    def _ensure_session_bound(self, client: Client) -> Client:
        """Гарантує, що об'єкт клієнта підключено до поточної сесії."""
        if client is None:
            return None
        client_state = inspect(client)
        if client_state.session is not self.session:
            client = self.session.merge(client, load=False)
        self._cache_client(client)
        return client
    
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
            existing_client = self._ensure_session_bound(self.phone_cache[phone])
            if self._update_client_info(existing_client, client_data):
                self.stats['updated'] += 1
            else:
                self.stats['found'] += 1
            return existing_client
        
        # Пошук за Facebook
        if facebook and facebook in self.facebook_cache:
            existing_client = self._ensure_session_bound(self.facebook_cache[facebook])
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
    
    def apply_all_updates(self, product_cache: ProductCache):
        """Застосовує всі зібрані оновлення товарів."""
        logger.info("Застосовуємо оновлення товарів...")
        
        # Оновлення цін
        self._apply_price_updates(product_cache)
        
        # Оновлення розмірів
        self._apply_size_updates(product_cache)
        
        # Оновлення замірів
        self._apply_measurement_updates(product_cache)
    
    def _apply_price_updates(self, product_cache: ProductCache):
        """Застосовує оновлення цін: остання ціна продажу → поточна ціна."""
        logger.info(f"Оновлення цін для {len(self.price_updates)} товарів")
        
        for product_code, sales in self.price_updates.items():
            # Сортуємо за датою - остання ціна продажу
            latest_sale = max(sales, key=lambda x: x['date'])
            latest_price = latest_sale['price']
            
            # Знаходимо товар через кеш
            product = product_cache.get_product(product_code)
            
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
    
    def _apply_size_updates(self, product_cache: ProductCache):
        """Застосовує оновлення розмірів товарів."""
        logger.info(f"Оновлення розмірів для {len(self.size_updates)} товарів")
        
        for product_code, sizes in self.size_updates.items():
            # Беремо останній розмір
            latest_size = sizes[-1] if sizes else None
            
            if latest_size:
                product = product_cache.get_product(product_code)
                
                if product:
                    # Оновлюємо тільки якщо розмір був порожній
                    if not product.sizeeu:
                        product.sizeeu = latest_size
                        logger.debug(f"Товар {product_code}: встановлено розмір {latest_size}")
                else:
                    logger.warning(f"Товар {product_code} не знайдено для оновлення розміру")
    
    def _apply_measurement_updates(self, product_cache: ProductCache):
        """Застосовує оновлення замірів товарів."""
        logger.info(f"Оновлення замірів для {len(self.measurement_updates)} товарів")
        
        for product_code, measurements in self.measurement_updates.items():
            # Беремо останній замір
            latest_measurement = measurements[-1] if measurements else None
            
            if latest_measurement:
                product = product_cache.get_product(product_code)
                
                if product:
                    # Оновлюємо тільки якщо замір був порожній
                    if not product.measurementscm:
                        product.measurementscm = latest_measurement
                        logger.debug(f"Товар {product_code}: встановлено замір {latest_measurement}см")
                else:
                    logger.warning(f"Товар {product_code} не знайдено для оновлення заміру")

class OrderDeduplicator:
    """Клас для дедублікації замовлень."""
    
    def __init__(self, session):
        self.session = session
        self.existing_orders = set()
        self.load_existing_orders()
    
    def load_existing_orders(self):
        """Завантажує хеші існуючих замовлень."""
        logger.info("Завантаження існуючих замовлень для дедублікації...")
        
        # Завантажуємо замовлення за останні 30 днів для швидкості
        cutoff_date = datetime.now() - timedelta(days=30)
        orders = self.session.query(Order).filter(Order.order_date >= cutoff_date).all()
        
        for order in orders:
            # Створюємо хеш на основі ключових полів
            order_hash = self._create_order_hash(order)
            self.existing_orders.add(order_hash)
        
        logger.info(f"Завантажено {len(self.existing_orders)} існуючих замовлень")
    
    def _create_order_hash(self, order: Order) -> str:
        """Створює хеш замовлення на основі ключових полів."""
        # Отримуємо клієнта
        client = self.session.query(Client).filter(Client.id == order.client_id).first()
        
        # Отримуємо товари замовлення
        order_items = self.session.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        product_codes = []
        for item in order_items:
            product = self.session.query(Product).filter(Product.id == item.product_id).first()
            if product:
                product_codes.append(product.productnumber)
        
        key_fields = [
            client.phone_number if client else '',
            ';'.join(sorted(product_codes)),
            str(order.total_amount),
            order.order_date.strftime('%Y-%m-%d') if order.order_date else '',
        ]
        content = '|'.join(str(field) for field in key_fields)
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def is_duplicate(self, order_data: Dict, sheet_date: datetime) -> bool:
        """Перевіряє, чи є замовлення дублікатом."""
        # Створюємо хеш для нового замовлення
        key_fields = [
            order_data.get('Контактний номер', ''),
            order_data.get('Номера товарів', ''),
            order_data.get('Сума', ''),
            sheet_date.strftime('%Y-%m-%d'),
        ]
        content = '|'.join(str(field) for field in key_fields)
        order_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        return order_hash in self.existing_orders
    
    def add_order_hash(self, order_data: Dict, sheet_date: datetime):
        """Додає хеш нового замовлення."""
        key_fields = [
            order_data.get('Контактний номер', ''),
            order_data.get('Номера товарів', ''),
            order_data.get('Сума', ''),
            sheet_date.strftime('%Y-%m-%d'),
        ]
        content = '|'.join(str(field) for field in key_fields)
        order_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        self.existing_orders.add(order_hash)

class OrdersComprehensiveParser:
    """Основний клас комплексного парсингу замовлень."""
    
    def __init__(self, use_cache: bool = True, force_reparse: bool = False):
        self.session = None
        self.client_manager = None
        self.price_manager = None
        self.product_cache = None
        self.order_deduplicator = None
        self.cache_manager = CacheManager() if use_cache else None
        self.use_cache = use_cache
        self.force_reparse = force_reparse
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
            'measurement_updates': 0,
            'skipped_sheets': 0,
            'skipped_duplicates': 0,
            'processed_sheets': 0
        }
    
    def get_google_sheet_client(self):
        """Повертає авторизований клієнт Google Sheets."""
        try:
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_SHEETS_CREDENTIALS_FILE,
                scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
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

        def build_lookup(rows, name_index: int) -> Dict[str, SimpleNamespace]:
            lookup: Dict[str, SimpleNamespace] = {}
            for row in rows:
                raw_name = row[name_index] if len(row) > name_index else None
                name = (raw_name or '').strip()
                if not name:
                    continue
                lookup[name.lower()] = SimpleNamespace(id=row[0], name=name)
            return lookup

        order_status_rows = self.session.execute(
            text("SELECT id, status_name FROM order_statuses")
        ).fetchall()
        order_statuses = build_lookup(order_status_rows, 1)

        payment_status_rows = self.session.execute(
            text("SELECT id, status_name FROM payment_statuses")
        ).fetchall()
        payment_statuses = build_lookup(payment_status_rows, 1)

        delivery_method_rows = self.session.execute(
            text("SELECT id, method_name FROM delivery_methods")
        ).fetchall()
        delivery_methods = build_lookup(delivery_method_rows, 1)

        delivery_status_rows = self.session.execute(
            text("SELECT id, status_name FROM delivery_statuses")
        ).fetchall()
        delivery_statuses = build_lookup(delivery_status_rows, 1)

        payment_method_rows = self.session.execute(
            text("SELECT id, method_name FROM payment_methods")
        ).fetchall()
        payment_methods = build_lookup(payment_method_rows, 1)

        if 'термінал' not in payment_methods:
            result = self.session.execute(
                text("INSERT INTO payment_methods (method_name, created_at, updated_at) VALUES (:name, NOW(), NOW()) RETURNING id"),
                {"name": "Термінал"}
            )
            new_id = result.scalar()
            payment_methods['термінал'] = SimpleNamespace(id=new_id, name="Термінал")

        return order_statuses, payment_statuses, delivery_methods, delivery_statuses, payment_methods
    
    def parse_order_row(self, row: List[str], headers: List[str],
                       order_statuses: Dict, payment_statuses: Dict,
                       delivery_methods: Dict, delivery_statuses: Dict,
                       payment_methods: Dict, sheet_date: datetime) -> bool:
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
            
            # Перевіряємо дублікати замовлень
            if self.order_deduplicator.is_duplicate(order_data, sheet_date):
                self.stats['skipped_duplicates'] += 1
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
            
            # Визначаємо статуси/методи
            order_status_text = order_data.get('Статус відповіді', '').strip()
            payment_status_text = order_data.get('Статус оплати', '').strip()
            delivery_method_text = order_data.get('Доставка', '').strip()
            parcel_status_text = order_data.get('Статус посилки', '').strip()
            comments_text = order_data.get('Коментарі', '').strip()
            tracking_number_raw = order_data.get('Номер накладної', '').strip()

            order_status_id = self.map_order_status(order_status_text, order_statuses)
            payment_status_id, payment_status_value = self.map_payment_status(payment_status_text, payment_statuses)
            delivery_method_id = self.map_delivery_method(delivery_method_text, delivery_methods)
            delivery_status_id = self.map_delivery_status(
                tracking_number_raw,
                parcel_status_text,
                order_status_text,
                delivery_statuses
            )
            payment_method_id = self.map_payment_method(
                comments_text,
                clarification_data,
                payment_status_value,
                payment_methods
            )

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

            # Канал продажу
            sales_channel = SalesChannelDetector.detect(
                comments=comments or '',
                delivery_method=delivery_method_text,
            )

            # Створюємо замовлення
            order = Order(
                client_id=client.id,
                order_date=sheet_date,
                order_status_id=order_status_id,
                total_amount=price,
                payment_status_id=payment_status_id,
                payment_status=payment_status_value,
                payment_method_id=payment_method_id,
                delivery_method_id=delivery_method_id,
                delivery_status_id=delivery_status_id,
                tracking_number=tracking_number_raw or None,
                notes=comments,
                priority=int(order_data.get('Пріорітетність', 0) or 0),
                deferred_until=deferred_date,
                sales_channel=sales_channel,
            )

            # Перевіряємо методи оплати в коментарях для історичних нотаток
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
            
            # Перевіряємо, чи всі товари існують в базі даних (використовуємо кеш)
            found_products = []
            missing_products = []
            
            for product_code in product_codes:
                product = self.product_cache.get_product(product_code)
                
                if product:
                    found_products.append((product_code, product))
                else:
                    missing_products.append(product_code)
            
            # Якщо немає жодного товару - пропускаємо замовлення
            if not found_products:
                logger.warning(f"Пропускаємо замовлення - жоден з товарів не знайдено: {product_codes}")
                return False
            
            # Логуємо відсутні товари та створюємо мінімальні картки товарів, щоб не губити замовлення
            if missing_products:
                logger.warning(f"Відсутні товари в замовленні: {missing_products}")
                self.stats['missing_products'] += len(missing_products)
                self.stats['orders_with_missing_products'] += 1
                # Створюємо базові товари на льоту
                from datetime import datetime as _dt
                for mp_code in missing_products:
                    try:
                        # Перевірка ще раз у кеші перед створенням
                        if self.product_cache.get_product(mp_code):
                            found_products.append((mp_code, self.product_cache.get_product(mp_code)))
                            continue
                        new_product = Product(
                            productnumber=mp_code,
                            price=0.0,
                            quantity=1,
                            created_at=_dt.utcnow(),
                            updated_at=_dt.utcnow(),
                        )
                        self.session.add(new_product)
                        self.session.flush()
                        # Оновлюємо кеш для обох варіантів коду (#ХХХ і ХХХ)
                        self.product_cache.cache[mp_code] = new_product.id
                        if mp_code.startswith('#'):
                            self.product_cache.cache[mp_code[1:]] = new_product.id
                        else:
                            self.product_cache.cache[f"#{mp_code}"] = new_product.id
                        found_products.append((mp_code, self.product_cache.get_product(mp_code)))
                        logger.info(f"Створено базовий товар з коду замовлення: {mp_code} (id={new_product.id})")
                    except Exception as _e:
                        logger.error(f"Не вдалося створити товар {mp_code}: {_e}")
                # Додаємо службову нотатку в замовлення
                missing_note = f"Створено {len([x for x in missing_products if self.product_cache.get_product(x)])} відсутніх товарів"
                if order.notes:
                    order.notes += f"; {missing_note}"
                else:
                    order.notes = missing_note
            
            # Створюємо позиції замовлення тільки для знайдених/щойно створених товарів
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
            
            # Додаємо хеш замовлення до дедуплікатора
            self.order_deduplicator.add_order_hash(order_data, sheet_date)
            
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
    
    def parse_orders_sheet(
        self,
        worksheet,
        order_statuses,
        payment_statuses,
        delivery_methods,
        delivery_statuses,
        payment_methods
    ):
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
                
                # Перевіряємо кеш аркуша (якщо увімкнено кешування)
                if self.use_cache and not self.force_reparse:
                    if not self.cache_manager.is_sheet_changed(sheet_name, all_data):
                        logger.info(f"Аркуш '{sheet_name}' не змінився, пропускаємо")
                        self.stats['skipped_sheets'] += 1
                        return
                
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
                success = self.parse_order_row(
                    row,
                    headers,
                    order_statuses,
                    payment_statuses,
                    delivery_methods,
                    delivery_statuses,
                    payment_methods,
                    sheet_date
                )
                
                if success and self.stats['total_orders'] % 100 == 0:
                    logger.info(f"Оброблено {self.stats['total_orders']} замовлень")
            
            self.stats['processed_sheets'] += 1
            logger.info(f"Аркуш '{sheet_name}' успішно оброблено")
                    
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
        self.product_cache = ProductCache(self.session)
        self.order_deduplicator = OrderDeduplicator(self.session)
        
        try:
            # Підключення до Google Sheets
            client = self.get_google_sheet_client()
            if not client:
                raise Exception("Не вдалося підключитися до Google Sheets")
            
            doc = client.open(ORDERS_DOCUMENT_NAME)
            self.workbook = doc  # Зберігаємо посилання на workbook
            logger.info(f"✅ Документ відкрито: {doc.title}")
            
            # Отримуємо довідкові дані
            order_statuses, payment_statuses, delivery_methods, delivery_statuses, payment_methods = self.get_or_create_reference_data()
            
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
                self.parse_orders_sheet(
                    worksheet,
                    order_statuses,
                    payment_statuses,
                    delivery_methods,
                    delivery_statuses,
                    payment_methods
                )
                
                # Комітимо кожні 10 аркушів для збереження пам'яті
                if i % 10 == 0:
                    self.session.commit()
                    logger.info(f"Збережено зміни після {i} аркушів")
                    
                    # Очищуємо кеш SQLAlchemy для економії пам'яті
                    self.session.expunge_all()
            
            # Фаза 3: Застосовуємо оновлення товарів
            logger.info("Застосовуємо оновлення товарів...")
            self.price_manager.apply_all_updates(self.product_cache)
            
            # Фінальний коміт
            self.session.commit()
            logger.info("Всі зміни збережено в базу даних")
            
            # Зберігаємо кеші
            if self.use_cache and self.cache_manager:
                self.cache_manager.save_all_caches()
                logger.info("Кеші збережено")
            
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
            logger.info(f"  Аркушів оброблено: {self.stats['processed_sheets']}")
            logger.info(f"  Аркушів пропущено (без змін): {self.stats['skipped_sheets']}")
            logger.info(f"  Дублікатів пропущено: {self.stats['skipped_duplicates']}")
            logger.info(f"  Успішність: {(self.stats['successful_orders']/max(self.stats['total_orders'], 1)*100):.1f}%")
            
        except Exception as e:
            logger.error(f"Критична помилка парсингу: {e}")
            try:
                self.session.rollback()
            except:
                pass  # Ігноруємо помилки rollback
            
            # Зберігаємо кеші навіть при помилці
            if self.use_cache and self.cache_manager:
                try:
                    self.cache_manager.save_all_caches()
                    logger.info("Кеші збережено після помилки")
                except:
                    pass
            raise
        finally:
            if self.session:
                self.session.close()

    def map_order_status(self, status_text: str, order_statuses: Dict[str, SimpleNamespace]) -> int:
        """Маппить текстовий статус замовлення на ID в БД."""
        normalized = (status_text or '').strip().lower()
        status_aliases = {
            '': 'підтверджено',
            'підтверджено': 'підтверджено',
            'підтвердженно': 'підтверджено',
            'нове': 'підтверджено',
            'в обробці': 'в черзі',
            'в черзі': 'в черзі',
            'очікується': 'в черзі',
            'уточнити': 'уточнити',
            'уточнення': 'уточнити',
            'фото': 'фото',
            'відміна': 'відміна',
            'скасовано': 'відміна',
            'скасоване': 'відміна',
            'ігнорування': 'ігнорування',
            'ігнор': 'ігнорування',
            'подарунок': 'подарунок',
            'повернення': 'повернення',
            'обмін': 'обмін'
        }
        canonical = status_aliases.get(normalized, normalized or 'підтверджено')
        status_obj = order_statuses.get(canonical)
        if status_obj:
            return status_obj.id
        fallback = order_statuses.get('підтверджено') or next(iter(order_statuses.values()))
        return fallback.id
    
    def map_payment_status(self, status_text: str, payment_statuses: Dict[str, SimpleNamespace]) -> Tuple[int, Optional[str]]:
        """Маппить текстовий статус оплати на ID в БД та повертає канонічну назву."""
        normalized = (status_text or '').strip().lower()
        status_aliases = {
            'оплачено': 'оплачено',
            'оплачена': 'оплачено',
            'не оплачено': 'не оплачено',
            'неоплачено': 'не оплачено',
            'доплатити': 'доплатити',
            'борг': 'доплатити',
            'часткова оплата': 'доплатити',
            'відкладено': 'відкладено'
        }
        canonical = status_aliases.get(normalized, 'не оплачено')
        status_obj = payment_statuses.get(canonical)
        if status_obj:
            return status_obj.id, status_obj.name
        fallback = payment_statuses.get('не оплачено') or next(iter(payment_statuses.values()))
        return fallback.id, fallback.name
    
    def map_delivery_method(self, method_text: str, delivery_methods: Dict[str, SimpleNamespace]) -> Optional[int]:
        """Маппить текстовий метод доставки на ID в БД."""
        normalized = (method_text or '').strip().lower()
        method_aliases = {
            'нп': 'нп',
            'нова пошта': 'нп',
            'нова-пошта': 'нп',
            'укрпошта': 'уп',
            'уп': 'уп',
            'міст': 'міст',
            'міст експрес': 'міст',
            'самовивіз': 'самовивіз',
            'магазин': 'магазин',
            'місцевий': 'місцевий',
            'відкладено': 'відкладено'
        }
        canonical = method_aliases.get(normalized)
        if canonical:
            method_obj = delivery_methods.get(canonical)
            if method_obj:
                return method_obj.id
        return None

    def map_delivery_status(
        self,
        tracking_number: str,
        parcel_status_text: str,
        order_status_text: str,
        delivery_statuses: Dict[str, SimpleNamespace]
    ) -> Optional[int]:
        """Визначає статус доставки за правилами."""
        tracking_has_digits = any(ch.isdigit() for ch in tracking_number) if tracking_number else False
        parcel_status_norm = (parcel_status_text or '').strip().lower()
        order_status_norm = (order_status_text or '').strip().lower()

        if order_status_norm == 'повернення':
            status = delivery_statuses.get('повернуто')
            if status:
                return status.id

        if tracking_has_digits or parcel_status_norm == 'створено':
            status = delivery_statuses.get('створено')
            if status:
                return status.id

        return None

    @staticmethod
    def normalize_payment_method_name(name: str) -> Optional[str]:
        """Нормалізує назву методу оплати до ключів довідника."""
        if not name:
            return None
        normalized = name.strip().lower()
        mapping = {
            'карта': 'картка',
            'картка': 'картка',
            'готівка': 'готівка',
            'наличка': 'готівка',
            'наличні': 'готівка',
            'переказ': 'переказ',
            'термінал': 'термінал'
        }
        return mapping.get(normalized)

    def map_payment_method(
        self,
        comments_text: str,
        clarification_data: Optional[Dict[str, Any]],
        payment_status_value: Optional[str],
        payment_methods: Dict[str, SimpleNamespace]
    ) -> Optional[int]:
        """Визначає метод оплати на основі коментарів, уточнень та статусу оплати."""
        comments_lower = (comments_text or '').strip().lower()

        def get_method_id(key: str) -> Optional[int]:
            method = payment_methods.get(key)
            return method.id if method else None

        if 'термінал' in comments_lower:
            method_id = get_method_id('термінал')
            if method_id:
                return method_id

        if 'готівк' in comments_lower:
            method_id = get_method_id('готівка')
            if method_id:
                return method_id

        if any(token in comments_lower for token in ('переказ', 'перевод', 'переказати', 'transfer')):
            method_id = get_method_id('переказ')
            if method_id:
                return method_id

        if clarification_data and clarification_data.get('type') == 'payment':
            clarified = clarification_data['data'].get('method')
            canonical = self.normalize_payment_method_name(clarified)
            if canonical:
                method_id = get_method_id(canonical)
                if method_id:
                    return method_id

        paid_value = (payment_status_value or '').strip().lower()
        if paid_value == 'оплачено':
            return get_method_id('картка')

        return None

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
    parser.add_argument('--no-cache', action='store_true', help='Вимкнути кешування')
    parser.add_argument('--force-reparse', action='store_true', help='Примусово перепарсити всі аркуші')
    parser.add_argument('--clear-cache', action='store_true', help='Очистити кеш перед запуском')
    args = parser.parse_args()
    
    # Очищуємо кеш якщо потрібно
    if args.clear_cache:
        import shutil
        if os.path.exists(CACHE_DIR):
            shutil.rmtree(CACHE_DIR)
            logger.info("Кеш очищено")
        os.makedirs(CACHE_DIR, exist_ok=True)
    
    use_cache = not args.no_cache
    parser_instance = OrdersComprehensiveParser(
        use_cache=use_cache, 
        force_reparse=args.force_reparse
    )
    await parser_instance.parse_all_orders(max_sheets=args.test)

if __name__ == "__main__":
    asyncio.run(main()) 

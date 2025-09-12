#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
КОНФІГУРАЦІЯ СИСТЕМИ ПАРСИНГУ
Централізоване місце для всіх налаштувань парсингу
"""

import os
from dotenv import load_dotenv

# Завантажуємо змінні оточення
load_dotenv()

# ========================================
# БАЗА ДАНИХ
# ========================================
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "bsstorage"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres")
}

# ========================================
# GOOGLE SHEETS
# ========================================
GOOGLE_SHEETS_CONFIG = {
    "credentials_file": os.getenv("GOOGLE_SHEETS_JSON_KEY", "newproject2024-419923-working.json"),
    "products_document": os.getenv("GOOGLE_SHEETS_DOCUMENT_NAME", "Журнал"),
    "orders_document": os.getenv("GOOGLE_SHEETS_DOCUMENT_NAME_ORDERS", "Замовлення"),
    "scopes": [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
}

# ========================================
# ДОВІДНИКИ СТАТУСІВ (IDs в БД)
# ========================================

# Статі
GENDER_IDS = {
    "чоловічий": 1,
    "жіночий": 2,
    "унісекс": 3
}

# Статуси замовлень
ORDER_STATUS_IDS = {
    "підтверджено": 1,
    "очікується": 2,
    "уточнити": 3,
    "фото": 4,
    "відміна": 5,
    "ігнорування": 6,
    "подарунок": 7,
    "в черзі": 8,
    "повернення": 9,
    "обмін": 10,
    "передати": 11
}

# Статуси оплати
PAYMENT_STATUS_IDS = {
    "оплачено": 1,
    "доплатити": 2,
    "відкладено": 3,
    "борг": 4,
    "часткова оплата": 5,
    "очікує оплати": 6
}

# Методи доставки
DELIVERY_METHOD_IDS = {
    "нова пошта": 1,
    "укрпошта": 2,
    "самовивіз": 3,
    "кур'єр": 4,
    "meest": 5,
    "justin": 6
}

# Статуси товарів
PRODUCT_STATUS_IDS = {
    "продано": 1,
    "непродано": 2,
    "заброньовано": 3,
    "в дорозі": 4,
    "повернення": 5
}

# ========================================
# ПАРСИНГ
# ========================================
PARSING_CONFIG = {
    # Кешування
    "cache_dir": "cache",
    "cache_ttl_days": 60,  # Час життя кешу в днях
    "max_cache_size_mb": 100,
    
    # Обробка
    "batch_size": 100,  # Розмір пакету для обробки
    "max_retries": 3,  # Максимум спроб при помилці
    "retry_delay": 5,  # Затримка між спробами (сек)
    
    # Таймаути
    "request_timeout": 30,  # Таймаут запиту (сек)
    "parsing_timeout": 7200,  # Максимальний час парсингу (2 години)
    
    # Логування
    "log_level": "INFO",
    "log_file": "parsing.log",
    "log_to_db": True,  # Зберігати логи в БД
    
    # Фільтри
    "ignored_sheets": ["Suppliers", "Publications", "New"],
    "special_sheets": {
        "Data": "reference",  # Довідковий аркуш
        "Валізи(Андрій)": {"date": "2024-01-01", "supplier": "Андрій"}
    },
    
    # Паралельна обробка
    "parallel": {
        "enabled": True,  # Увімкнути паралельну обробку
        "max_workers": 3,  # Зменшено для економії з'єднань БД
        "batch_size": 25,  # Зменшено для менш агресивної обробки
        "queue_timeout": 30,  # Таймаут черги (сек)
        "db_pool_size": 3,  # Зменшено відповідно до max_workers
        "progress_update_interval": 2.0  # Збільшено для зменшення навантаження
    }
}

# ========================================
# РЕЖИМИ ПАРСИНГУ
# ========================================
PARSING_MODES = {
    "full": {
        "name": "Повний парсинг",
        "description": "Повний імпорт всіх товарів та замовлень",
        "estimated_time": "1-2 години",
        "icon": "🔄"
    },
    "incremental": {
        "name": "Інкрементальний парсинг",
        "description": "Обробка тільки нових/змінених даних",
        "estimated_time": "5-15 хвилин",
        "icon": "📈",
        "params": {
            "days": {
                "type": "number",
                "default": 7,
                "min": 1,
                "max": 30,
                "description": "Кількість днів для обробки"
            }
        }
    },
    "quick_update": {
        "name": "Швидке оновлення",
        "description": "Оновлення за останні 3 дні",
        "estimated_time": "2-5 хвилин",
        "icon": "⚡"
    },
    "products_only": {
        "name": "Тільки товари",
        "description": "Імпорт тільки каталогу товарів",
        "estimated_time": "30-60 хвилин",
        "icon": "📦"
    },
    "orders_only": {
        "name": "Тільки замовлення",
        "description": "Імпорт тільки замовлень",
        "estimated_time": "30-60 хвилин",
        "icon": "🛒"
    },
    "new_products": {
        "name": "Пошук новинок",
        "description": "Знайти та імпортувати нові товари",
        "estimated_time": "10-20 хвилин",
        "icon": "🆕"
    }
}

# ========================================
# ВАЛІДАЦІЯ
# ========================================
VALIDATION_RULES = {
    "product_number": {
        "min_length": 1,
        "max_length": 50,
        "pattern": r"^[#А-ЯA-Z0-9\-\.]+$"
    },
    "price": {
        "min": 0,
        "max": 1000000
    },
    "year": {
        "min": 1900,
        "max": 2030
    },
    "sizes": {
        "eu": {"min": 15, "max": 50},
        "ua": {"min": 15, "max": 50},
        "usa": {"min": 1, "max": 20},
        "uk": {"min": 1, "max": 15},
        "jp": {"min": 10, "max": 35},
        "cn": {"min": 30, "max": 50}
    }
}

# ========================================
# ФУНКЦІЇ-ПОМІЧНИКИ
# ========================================

def get_db_url():
    """Повертає URL для підключення до БД."""
    cfg = DB_CONFIG
    return f"postgresql://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"

def get_gender_id(gender_name: str) -> int:
    """Повертає ID статі за назвою."""
    return GENDER_IDS.get(gender_name.lower(), 3)  # За замовчуванням - унісекс

def get_order_status_id(status_name: str) -> int:
    """Повертає ID статусу замовлення за назвою."""
    return ORDER_STATUS_IDS.get(status_name.lower(), 2)  # За замовчуванням - очікується

def get_payment_status_id(status_name: str) -> int:
    """Повертає ID статусу оплати за назвою."""
    return PAYMENT_STATUS_IDS.get(status_name.lower(), 6)  # За замовчуванням - очікує оплати

def get_delivery_method_id(method_name: str) -> int:
    """Повертає ID методу доставки за назвою."""
    return DELIVERY_METHOD_IDS.get(method_name.lower(), 1)  # За замовчуванням - нова пошта

def get_product_status_id(status_name: str) -> int:
    """Повертає ID статусу товару за назвою."""
    return PRODUCT_STATUS_IDS.get(status_name.lower(), 2)  # За замовчуванням - непродано

def is_ignored_sheet(sheet_name: str) -> bool:
    """Перевіряє чи потрібно ігнорувати аркуш."""
    return sheet_name in PARSING_CONFIG["ignored_sheets"]

def is_special_sheet(sheet_name: str) -> bool:
    """Перевіряє чи це спеціальний аркуш."""
    for special_name in PARSING_CONFIG["special_sheets"]:
        if special_name.lower() in sheet_name.lower():
            return True
    return False

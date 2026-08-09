"""
Розширена система пошуку для BMS
Підтримує складний пошук по всіх полях з токенізацією, ранжуванням та нечітким пошуком
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text, or_, and_, func
from difflib import SequenceMatcher
import unicodedata

logger = logging.getLogger(__name__)

class AdvancedSearchService:
    """Розширений сервіс пошуку з підтримкою складних запитів"""
    
    def __init__(self):
        # Словник синонімів для кращого пошуку
        self.synonyms = {
            'чорний': ['чорний', 'black', 'чорн', 'темний'],
            'білий': ['білий', 'white', 'біл', 'світлий'],
            'червоний': ['червоний', 'red', 'черв', 'рудий'],
            'синій': ['синій', 'blue', 'син', 'голубий'],
            'зелений': ['зелений', 'green', 'зел'],
            'жовтий': ['жовтий', 'yellow', 'жовт'],
            'сірий': ['сірий', 'gray', 'grey', 'сір'],
            'коричневий': ['коричневий', 'brown', 'корич'],
            'рожевий': ['рожевий', 'pink', 'рож'],
            'помаранчевий': ['помаранчевий', 'orange', 'пом'],
            
            # Типи взуття
            'кросівки': ['кросівки', 'кроси', 'sneakers', 'кросс'],
            'туфлі': ['туфлі', 'туфі', 'shoes', 'туф'],
            'босоніжки': ['босоніжки', 'босон', 'sandals'],
            'шльопанці': ['шльопанці', 'шльоп', 'slippers', 'тапки'],
            'балетки': ['балетки', 'балет', 'flats'],
            'мокасини': ['мокасини', 'мокас', 'loafers'],
            
            # Матеріали
            'шкіра': ['шкіра', 'шкір', 'leather', 'натуральна'],
            'текстиль': ['текстиль', 'тканина', 'textile', 'матерія'],
            'сітка': ['сітка', 'mesh', 'сіт', 'перфорація'],
            'замша': ['замша', 'suede', 'замш'],
            'нубук': ['нубук', 'nubuck'],
            
            # Бренди (скорочення)
            'nike': ['nike', 'найк', 'найки'],
            'adidas': ['adidas', 'адідас', 'adi'],
            'puma': ['puma', 'пума'],
            'reebok': ['reebok', 'рібок'],
            
            # Стать
            'чоловічий': ['чоловічий', 'чол', 'мужской', 'men', 'male'],
            'жіночий': ['жіночий', 'жін', 'женский', 'women', 'female'],
            'дитячий': ['дитячий', 'дит', 'детский', 'kids', 'child'],
            'унісекс': ['унісекс', 'unisex', 'універсальний'],
        }
        
        # Ваги для різних полів (для ранжування)
        self.field_weights = {
            'productnumber': 10.0,  # Найвища вага для номера товару
            'model': 8.0,           # Висока вага для моделі
            'brandname': 7.0,       # Висока вага для бренду
            'typename': 6.0,        # Тип товару
            'colorname': 5.0,       # Колір
            'description': 4.0,     # Опис
            'extranote': 3.0,       # Додаткові примітки
            'clonednumbers': 6.0,   # Клоновані номери
            'gendername': 4.0,      # Стать
            'conditionname': 3.0,   # Стан
            'statusname': 3.0,      # Статус
        }

    def normalize_text(self, text: str) -> str:
        """Нормалізація тексту для пошуку"""
        if not text:
            return ""
        
        # Приведення до нижнього регістру
        text = text.lower().strip()
        
        # Видалення діакритичних знаків (але зберігаємо українські літери)
        # Не використовуємо NFD для українського тексту
        
        # Видалення зайвих символів, залишаємо букви (включно з українськими), цифри, пробіли та деякі символи
        text = re.sub(r'[^\w\s\-\.\(\)\/а-яіїєґА-ЯІЇЄҐ]', ' ', text)
        
        # Заміна множинних пробілів на один
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    def tokenize_query(self, query: str) -> List[str]:
        """Токенізація пошукового запиту"""
        normalized = self.normalize_text(query)
        
        # Розбиваємо по пробілах, комах, крапкам з комою
        tokens = re.split(r'[,;\s]+', normalized)
        
        # Видаляємо порожні токени та дуже короткі
        tokens = [token for token in tokens if len(token) >= 2]
        
        return tokens

    def expand_with_synonyms(self, tokens: List[str]) -> List[str]:
        """Розширення токенів синонімами"""
        expanded = set(tokens)  # Використовуємо set для унікальності
        
        for token in tokens:
            for key, synonyms in self.synonyms.items():
                if token in synonyms:
                    expanded.update(synonyms)
                    break
        
        return list(expanded)

    def calculate_similarity(self, text1: str, text2: str) -> float:
        """Розрахунок схожості між двома текстами"""
        if not text1 or not text2:
            return 0.0
        
        return SequenceMatcher(None, 
                             self.normalize_text(text1), 
                             self.normalize_text(text2)).ratio()

    def build_search_conditions(self, tokens: List[str]) -> Tuple[str, List[str]]:
        """Побудова SQL умов для пошуку"""
        conditions = []
        params = []
        
        for token in tokens:
            token_conditions = []
            param_value = f'%{token}%'
            
            # Додаємо параметр для кожного поля (PostgreSQL використовує %s)
            token_conditions.extend([
                "LOWER(p.productnumber) LIKE LOWER(%s)",
                "LOWER(p.clonednumbers) LIKE LOWER(%s)", 
                "LOWER(p.model) LIKE LOWER(%s)",
                "LOWER(p.marking) LIKE LOWER(%s)",
                "LOWER(p.description) LIKE LOWER(%s)",
                "LOWER(p.extranote) LIKE LOWER(%s)",
                "LOWER(p.sizeeu) LIKE LOWER(%s)",
                "LOWER(p.sizeua) LIKE LOWER(%s)",
                "LOWER(p.sizeusa) LIKE LOWER(%s)",
                
                # Пошук по довідкових таблицях
                "LOWER(b.brandname) LIKE LOWER(%s)",
                "LOWER(t.typename) LIKE LOWER(%s)",
                "LOWER(c.colorname) LIKE LOWER(%s)",
                "LOWER(g.gendername) LIKE LOWER(%s)",
                "LOWER(cond.conditionname) LIKE LOWER(%s)",
                "LOWER(s.statusname) LIKE LOWER(%s)",
            ])
            
            # Додаємо параметри для всіх полів цього токена
            params.extend([param_value] * len(token_conditions))
            
            # Об'єднуємо умови для одного токена через OR
            conditions.append(f"({' OR '.join(token_conditions)})")
        
        # Об'єднуємо всі токени через AND (всі токени повинні знайтися)
        final_condition = ' AND '.join(conditions) if conditions else '1=1'
        
        return final_condition, params

    def calculate_relevance_score(self, row: Any, tokens: List[str]) -> float:
        """Розрахунок релевантності результату"""
        score = 0.0
        
        # Перевіряємо кожне поле з відповідною вагою
        fields_to_check = {
            'productnumber': getattr(row, 'productnumber', ''),
            'model': getattr(row, 'model', ''),
            'brand_name': getattr(row, 'brand_name', ''),
            'type_name': getattr(row, 'type_name', ''),
            'color_name': getattr(row, 'color_name', ''),
            'description': getattr(row, 'description', ''),
            'extranote': getattr(row, 'extranote', ''),
            'clonednumbers': getattr(row, 'clonednumbers', ''),
            'gender_name': getattr(row, 'gender_name', ''),
            'condition_name': getattr(row, 'condition_name', ''),
            'status_name': getattr(row, 'status_name', ''),
        }
        
        for field_name, field_value in fields_to_check.items():
            if not field_value:
                continue
                
            field_weight = self.field_weights.get(field_name, 1.0)
            field_text = self.normalize_text(str(field_value))
            
            for token in tokens:
                token_normalized = self.normalize_text(token)
                
                # Точне співпадіння
                if token_normalized == field_text:
                    score += field_weight * 3.0
                # Точне співпадіння на початку
                elif field_text.startswith(token_normalized):
                    score += field_weight * 2.0
                # Містить токен
                elif token_normalized in field_text:
                    score += field_weight * 1.0
                # Схожість через Levenshtein
                else:
                    similarity = self.calculate_similarity(token_normalized, field_text)
                    if similarity > 0.7:  # Поріг схожості
                        score += field_weight * similarity * 0.5
        
        return score

    def advanced_search(
        self, 
        db: Session, 
        query: str, 
        limit: int = 50,
        offset: int = 0,
        min_score: float = 0.1
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Розширений пошук з ранжуванням результатів
        """
        try:
            # Токенізація та розширення запиту
            tokens = self.tokenize_query(query)
            if not tokens:
                return [], 0
            
            logger.info(f"Search tokens: {tokens}")
            
            # Розширення синонімами
            expanded_tokens = self.expand_with_synonyms(tokens)
            logger.info(f"Expanded tokens: {expanded_tokens}")
            
            # Використовуємо прямий psycopg2 підхід
            import psycopg2
            
            # Отримуємо параметри підключення з SQLAlchemy
            db_url = db.bind.url
            
            conn = psycopg2.connect(
                host=db_url.host,
                port=db_url.port,
                database=db_url.database,
                user=db_url.username,
                password=db_url.password
            )
            
            cursor = conn.cursor()
            
            # Створюємо простий SQL для кожного токена
            results = []
            total = 0
            
            for token in expanded_tokens:
                # Простий SQL запит для одного токена
                sql = """
                    SELECT p.id, p.productnumber, p.model, p.description, p.price, p.quantity,
                           p.sizeeu, p.extranote, p.clonednumbers,
                           b.brandname as brand_name,
                           t.typename as type_name,
                           s.statusname as status_name,
                           c.colorname as color_name,
                           cond.conditionname as condition_name,
                           g.gendername as gender_name
                    FROM products p
                    LEFT JOIN brands b ON p.brandid = b.id
                    LEFT JOIN types t ON p.typeid = t.id
                    LEFT JOIN statuses s ON p.statusid = s.id
                    LEFT JOIN colors c ON p.colorid = c.id
                    LEFT JOIN conditions cond ON p.conditionid = cond.id
                    LEFT JOIN genders g ON p.genderid = g.id
                    WHERE (
                        LOWER(p.productnumber) LIKE LOWER(%s) OR
                        LOWER(p.clonednumbers) LIKE LOWER(%s) OR
                        LOWER(p.model) LIKE LOWER(%s) OR
                        LOWER(p.description) LIKE LOWER(%s) OR
                        LOWER(p.extranote) LIKE LOWER(%s) OR
                        LOWER(b.brandname) LIKE LOWER(%s) OR
                        LOWER(t.typename) LIKE LOWER(%s) OR
                        LOWER(c.colorname) LIKE LOWER(%s) OR
                        LOWER(g.gendername) LIKE LOWER(%s) OR
                        LOWER(cond.conditionname) LIKE LOWER(%s) OR
                        LOWER(s.statusname) LIKE LOWER(%s)
                    )
                    ORDER BY p.id DESC
                    LIMIT %s OFFSET %s
                """
                
                param_value = f'%{token}%'
                params = [param_value] * 11 + [limit, offset]  # 11 полів + limit + offset
                
                # Виконання запиту
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                
                # Обробка результатів
                for row in rows:
                    result_dict = {
                        'id': row[0],
                        'productnumber': row[1],
                        'model': row[2],
                        'description': row[3],
                        'price': float(row[4]) if row[4] else None,
                        'quantity': row[5] or 0,
                        'sizeeu': row[6],
                        'extranote': row[7],
                        'clonednumbers': row[8],
                        'brand_name': row[9],
                        'type_name': row[10],
                        'status_name': row[11],
                        'color_name': row[12],
                        'condition_name': row[13],
                        'gender_name': row[14],
                    }
                    
                    # Перевіряємо, чи вже є цей результат
                    if not any(r['id'] == result_dict['id'] for r in results):
                        results.append(result_dict)
                
                # Підрахунок total для першого токена
                if total == 0:
                    count_sql = """
                        SELECT COUNT(DISTINCT p.id)
                        FROM products p
                        LEFT JOIN brands b ON p.brandid = b.id
                        LEFT JOIN types t ON p.typeid = t.id
                        LEFT JOIN statuses s ON p.statusid = s.id
                        LEFT JOIN colors c ON p.colorid = c.id
                        LEFT JOIN conditions cond ON p.conditionid = cond.id
                        LEFT JOIN genders g ON p.genderid = g.id
                        WHERE (
                            LOWER(p.productnumber) LIKE LOWER(%s) OR
                            LOWER(p.clonednumbers) LIKE LOWER(%s) OR
                            LOWER(p.model) LIKE LOWER(%s) OR
                            LOWER(p.description) LIKE LOWER(%s) OR
                            LOWER(p.extranote) LIKE LOWER(%s) OR
                            LOWER(b.brandname) LIKE LOWER(%s) OR
                            LOWER(t.typename) LIKE LOWER(%s) OR
                            LOWER(c.colorname) LIKE LOWER(%s) OR
                            LOWER(g.gendername) LIKE LOWER(%s) OR
                            LOWER(cond.conditionname) LIKE LOWER(%s) OR
                            LOWER(s.statusname) LIKE LOWER(%s)
                        )
                    """
                    count_params = [param_value] * 11
                    cursor.execute(count_sql, count_params)
                    total = cursor.fetchone()[0]
                
                # Обмежуємо кількість результатів
                if len(results) >= limit:
                    break
            
            # Закриваємо з'єднання
            cursor.close()
            conn.close()
            
            # Розрахунок релевантності для всіх результатів
            for result in results:
                relevance_score = self.calculate_relevance_score(
                    type('Row', (), result), expanded_tokens
                )
                result['_relevance_score'] = relevance_score
            
            # Фільтрація по мінімальному score
            filtered_results = [r for r in results if r.get('_relevance_score', 0) >= min_score]
            
            # Сортування по релевантності
            filtered_results.sort(key=lambda x: x.get('_relevance_score', 0), reverse=True)
            
            # Обмеження результатів
            final_results = filtered_results[:limit]
            
            logger.info(f"Advanced search found {len(final_results)} results (total: {total})")
            return final_results, total
            
        except Exception as e:
            logger.error(f"Advanced search error: {e}")
            return [], 0

# Глобальний інстанс сервісу
advanced_search_service = AdvancedSearchService()

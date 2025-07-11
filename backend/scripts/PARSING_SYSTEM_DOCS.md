# 📚 ДОКУМЕНТАЦІЯ СИСТЕМИ ПАРСИНГУ BMS

## 🎯 Загальний огляд

Система парсингу BMS - це комплексне рішення для імпорту та синхронізації даних з Google Sheets. Вона складається з кількох взаємопов'язаних компонентів, які забезпечують ефективний та надійний імпорт даних.

## 🏗️ Архітектура системи

```
┌─────────────────────────────────────────────────────────────┐
│                     Графічний інтерфейс                      │
│                    (Кнопка 🔄 в програмі)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    unified_parser.py                         │
│              (Об'єднаний контролер парсингу)                │
└──────────┬───────────────┴──────────────┬───────────────────┘
           │                              │
           ▼                              ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│   googlesheets_pars.py   │  │ orders_comprehensive_parser.py│
│   (Парсер товарів)       │  │    (Парсер замовлень)        │
└──────────────────────────┘  └──────────────────────────────┘
           │                              │
           └──────────────┬───────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    PostgreSQL Database                       │
│                  (Основне сховище даних)                    │
└─────────────────────────────────────────────────────────────┘
```

## 📁 Структура файлів

### Основні скрипти парсингу:

1. **`unified_parser.py`** - Головний контролер
   - Керує всіма видами парсингу
   - Забезпечує єдиний інтерфейс для GUI
   - Відстежує прогрес та статус

2. **`googlesheets_pars.py`** - Парсер товарів
   - Імпортує каталог товарів
   - Оновлює інформацію про продукти
   - Підтримує пошук новинок

3. **`orders_comprehensive_parser.py`** - Парсер замовлень
   - Комплексний імпорт замовлень
   - Дедуплікація клієнтів
   - Синхронізація цін
   - Обробка уточнень

### Допоміжні скрипти:

4. **`incremental_parser.py`** - Інкрементальний парсинг
   - Обробка тільки нових/змінених даних
   - Швидке оновлення за період

5. **`cache_manager.py`** - Управління кешем
   - Аналіз та очищення кешу
   - Оптимізація продуктивності

6. **`orders_pars.py`** - Старий парсер замовлень (legacy)

### Документація:

- **`README_PARSER_OPTIMIZATION.md`** - Опис оптимізацій
- **`PARSING_SYSTEM_DOCS.md`** - Ця документація

## 🚀 Режими парсингу

### 1. **Повний парсинг** (`full`)
- Повний імпорт всіх товарів та замовлень
- Оновлення всіх даних з нуля
- Час: 1-2 години
- Використання: початкове завантаження, відновлення даних

### 2. **Інкрементальний парсинг** (`incremental`)
- Обробка тільки нових/змінених даних
- Налаштовується період (1-30 днів)
- Час: 5-15 хвилин
- Використання: щоденні оновлення

### 3. **Швидке оновлення** (`quick_update`)
- Фіксований період - 3 дні
- Найшвидший режим оновлення
- Час: 2-5 хвилин
- Використання: часті оновлення протягом дня

### 4. **Тільки товари** (`products_only`)
- Імпорт тільки каталогу товарів
- Час: 30-60 хвилин
- Використання: оновлення асортименту

### 5. **Тільки замовлення** (`orders_only`)
- Імпорт тільки замовлень
- Час: 30-60 хвилин
- Використання: оновлення продажів

### 6. **Пошук новинок** (`new_products`)
- Пошук нових товарів в каталозі
- Час: 15-30 хвилин
- Використання: моніторинг нових надходжень

## 💡 Ключові функції

### Оптимізації продуктивності:

1. **Кешування**
   - Хеші аркушів для пропуску незмінених
   - Кеш товарів для швидкого доступу
   - Кеш клієнтів для дедуплікації

2. **Дедуплікація**
   - Клієнти по телефону/Facebook
   - Замовлення по унікальному хешу
   - Товари по номеру

3. **Пакетна обробка**
   - Коміти кожні 10 аркушів
   - Очищення пам'яті SQLAlchemy
   - Обробка помилок без втрати даних

### Бізнес-логіка:

1. **Синхронізація цін**
   - Остання ціна продажу → поточна ціна товару
   - Історія змін цін

2. **Обробка уточнень**
   - Розпізнавання розмірів
   - Парсинг замірів
   - Витяг методів оплати

3. **Управління клієнтами**
   - Об'єднання дублікатів
   - Оновлення контактів
   - Статистика покупок

## 🛠️ Використання

### Через графічний інтерфейс:

1. Натисніть кнопку 🔄 в програмі
2. Виберіть режим парсингу
3. Налаштуйте параметри (якщо потрібно)
4. Натисніть "Почати парсинг"

### Через командний рядок:

```bash
# Повний парсинг
python unified_parser.py --mode full

# Інкрементальний за 7 днів
python unified_parser.py --mode incremental --days 7

# Швидке оновлення
python unified_parser.py --mode quick_update

# Тільки товари (тест 5 аркушів)
python unified_parser.py --mode products_only --test 5

# Без кешу з примусовим парсингом
python unified_parser.py --mode full --no-cache --force-reparse
```

### Управління кешем:

```bash
# Аналіз кешу
python cache_manager.py analyze

# Очищення старого кешу
python cache_manager.py clean --days 30

# Повне очищення
python cache_manager.py clean --all
```

## 📊 Моніторинг

### Статус в реальному часі:
- WebSocket підключення для live оновлень
- Прогрес по аркушах/записах
- Час виконання
- Помилки та попередження

### Логи:
- `unified_parser.log` - головний лог
- `orders_comprehensive_parser.log` - детальний лог замовлень
- `incremental_parser.log` - лог інкрементальних оновлень
- `sheets_parsing_issues.log` - проблеми парсингу

## 🔧 Налаштування

### Змінні оточення (.env):
```
DATABASE_URL=postgresql://user:pass@localhost/dbname
GOOGLE_SHEETS_CREDENTIALS=path/to/credentials.json
```

### Конфігурація парсерів:
- Таймаути Google API
- Розмір пакетів для комітів
- Параметри кешування
- Рівні логування

## ⚠️ Важливі моменти

1. **Google API ліміти**
   - 100 запитів за 100 секунд
   - Автоматичний retry з backoff
   - Кешування для мінімізації запитів

2. **Пам'ять**
   - Великі документи можуть споживати багато RAM
   - Періодичне очищення сесій SQLAlchemy
   - Пакетна обробка для контролю пам'яті

3. **Безпека**
   - Credentials Google зберігаються в secure_creds/
   - Не комітьте облікові дані в Git
   - Використовуйте змінні оточення

## 🐛 Вирішення проблем

### Парсинг не запускається:
1. Перевірте підключення до БД
2. Перевірте Google credentials
3. Перегляньте логи на помилки

### Повільна робота:
1. Використовуйте інкрементальний режим
2. Очистіть старий кеш
3. Перевірте індекси БД

### Дублікати даних:
1. Запустіть дедуплікацію клієнтів
2. Перевірте унікальні індекси
3. Використовуйте force-reparse

## 📈 Метрики продуктивності

### Типові показники:
- Повний парсинг: ~200 аркушів за 90 хвилин
- Інкрементальний: ~20 аркушів за 5 хвилин
- Обробка замовлення: ~100 мс
- Дедуплікація клієнта: ~50 мс

### Оптимізації дали:
- 3-5x прискорення повторного парсингу
- 80% економія API запитів через кеш
- 90% зменшення дублікатів

## 🔮 Майбутні покращення

1. **Паралельна обробка**
   - Одночасний парсинг кількох аркушів
   - Асинхронні запити до БД

2. **Розумна дедуплікація**
   - ML для виявлення схожих клієнтів
   - Fuzzy matching для назв товарів

3. **Розширена аналітика**
   - Дашборд парсингу
   - Історія змін даних
   - Алерти про аномалії

## 📞 Підтримка

При виникненні проблем:
1. Перевірте цю документацію
2. Перегляньте логи
3. Зверніться до розробника

---

*Остання версія: Червень 2025*
*Автор: BMS Development Team* 
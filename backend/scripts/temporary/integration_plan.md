# ПЛАН ІНТЕГРАЦІЇ ПАРСИНГУ ЗАМОВЛЕНЬ

## 🔧 Поточна архітектура

### Існуючий workflow:
1. **`googlesheets_pars.py`** - парсить товари з документу "Журнал"
2. **В кінці запускає `orders_pars.py`** (старий скрипт замовлень)

### Новий workflow:
1. **`googlesheets_pars.py`** - парсить товари (без змін)
2. **`orders_comprehensive_parser.py`** - новий комплексний парсер замовлень

## 🚀 Варіанти інтеграції

### Варіант 1: Заміна існуючого скрипта (РЕКОМЕНДОВАНИЙ)

**Модифікувати `googlesheets_pars.py`:**
```python
# Замість:
script_path_orders = os.path.join(SCRIPT_DIR, 'orders_pars.py')

# Використовувати:
script_path_orders = os.path.join(SCRIPT_DIR, 'orders_comprehensive_parser.py')
```

**Переваги:**
- Мінімальні зміни в архітектурі
- Автоматичний запуск після парсингу товарів
- Послідовність гарантована

### Варіант 2: Додавання аргументу вибору

**Додати опцію в `googlesheets_pars.py`:**
```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--orders-parser', choices=['old', 'comprehensive'], 
                   default='comprehensive', help='Вибір парсера замовлень')
args = parser.parse_args()

if args.orders_parser == 'comprehensive':
    script_path = 'orders_comprehensive_parser.py'
else:
    script_path = 'orders_pars.py'
```

### Варіант 3: Окремий API ендпоінт

**Додати в API (`routers/parsing.py`):**
```python
@router.post("/parse-orders-comprehensive")
async def parse_orders_comprehensive(max_sheets: Optional[int] = None):
    """Запуск комплексного парсингу замовлень"""
    # Логіка запуску парсера
```

## 📋 Рекомендована реалізація

### Фаза 1: Пряма заміна (ШВИДКО)
Просто замінити виклик старого скрипта на новий у `googlesheets_pars.py`

### Фаза 2: Додання в API (ГНУЧКІСТЬ)
Створити ендпоінт для незалежного запуску

### Фаза 3: Інтеграція в UI (ЗРУЧНІСТЬ)
Додати кнопку в React інтерфейс

## ⚡ Готовий код для інтеграції

### 1. Модифікація `googlesheets_pars.py`
```python
# Додати аргумент
def import_data(use_comprehensive_orders=True):
    # ... існуючий код ...
    
    if use_comprehensive_orders:
        logger.info("Запускаємо orders_comprehensive_parser.py...")
        script_path_orders = os.path.join(SCRIPT_DIR, 'orders_comprehensive_parser.py')
    else:
        logger.info("Запускаємо orders_pars.py...")
        script_path_orders = os.path.join(SCRIPT_DIR, 'orders_pars.py')
```

### 2. Новий ендпоінт API
```python
@router.post("/orders/comprehensive-parse")
async def comprehensive_orders_parse(
    max_sheets: Optional[int] = None,
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Комплексний парсинг замовлень з Google Sheets"""
    
    def run_comprehensive_parser():
        parser = OrdersComprehensiveParser()
        asyncio.run(parser.parse_all_orders(max_sheets=max_sheets))
    
    background_tasks.add_task(run_comprehensive_parser)
    
    return {
        "message": "Комплексний парсинг замовлень запущено",
        "max_sheets": max_sheets or "всі"
    }
```

### 3. React компонент
```tsx
const OrdersParsingButton = () => {
  const [loading, setLoading] = useState(false);
  
  const handleParse = async () => {
    setLoading(true);
    try {
      await api.post('/parsing/orders/comprehensive-parse');
      toast.success('Парсинг замовлень запущено!');
    } catch (error) {
      toast.error('Помилка запуску парсингу');
    } finally {
      setLoading(false);
    }
  };

  return (
    <button onClick={handleParse} disabled={loading}>
      {loading ? 'Парсинг...' : 'Парсити замовлення'}
    </button>
  );
};
```

## 🎯 Переваги нової архітектури

### ✅ Технічні переваги:
- **Розділення відповідальності** - кожен скрипт має чітку функцію
- **Легкість тестування** - можна тестувати окремо
- **Гнучкість** - можна запускати незалежно
- **Масштабованість** - легко додавати нові функції

### ✅ Бізнес переваги:
- **Комплексна обробка даних** - дедуплікація клієнтів, розпізнавання методів оплати
- **Синхронізація цін** - автоматичне оновлення цін товарів
- **Збереження цілісності** - не псує існуючі дані
- **Детальна статистика** - повна інформація про результати

## 🔄 Міграційний план

### День 1: Тестування
- Запустити новий парсер з `--test 5` на невеликій вибірці
- Перевірити результати і статистику

### День 2: Поступовий запуск  
- Замінити виклик в `googlesheets_pars.py`
- Запустити повний цикл парсингу

### День 3: API інтеграція
- Додати ендпоінт в API
- Інтегрувати в React UI

### День 4+: Моніторинг
- Аналіз продуктивності
- Оптимізація за потребою

---
**Статус: ✅ ГОТОВО ДО ВПРОВАДЖЕННЯ** 
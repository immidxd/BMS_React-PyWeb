from sqlalchemy import text
from backend.models.database import get_db

def seed_reference_data():
    """Seed reference tables with initial data"""
    db = next(get_db())
    
    try:
        # Seed types
        db.execute(text("""
            INSERT INTO types (name) VALUES
            ('Обувь'),
            ('Одежда'),
            ('Аксессуары')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed subtypes
        db.execute(text("""
            INSERT INTO subtypes (name) VALUES
            ('Кроссовки'),
            ('Ботинки'),
            ('Туфли'),
            ('Футболки'),
            ('Джинсы'),
            ('Куртки'),
            ('Сумки'),
            ('Ремни')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed brands
        db.execute(text("""
            INSERT INTO brands (name) VALUES
            ('Nike'),
            ('Adidas'),
            ('Puma'),
            ('Reebok'),
            ('New Balance'),
            ('Under Armour'),
            ('The North Face'),
            ('Columbia')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed colors
        db.execute(text("""
            INSERT INTO colors (name) VALUES
            ('Черный'),
            ('Белый'),
            ('Красный'),
            ('Синий'),
            ('Зеленый'),
            ('Желтый'),
            ('Серый'),
            ('Коричневый')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed countries
        db.execute(text("""
            INSERT INTO countries (name) VALUES
            ('Китай'),
            ('Вьетнам'),
            ('Индонезия'),
            ('Турция'),
            ('Бангладеш'),
            ('Индия'),
            ('Малайзия'),
            ('Таиланд')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed statuses
        db.execute(text("""
            INSERT INTO statuses (name) VALUES
            ('В наличии'),
            ('Под заказ'),
            ('Распродан'),
            ('Снят с производства')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed conditions
        db.execute(text("""
            INSERT INTO conditions (name) VALUES
            ('Новый'),
            ('Б/У'),
            ('Восстановленный'),
            ('Дефектный')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed imports
        db.execute(text("""
            INSERT INTO imports (name) VALUES
            ('Официальный'),
            ('Параллельный'),
            ('Серый'),
            ('Рейд')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        # Seed deliveries
        db.execute(text("""
            INSERT INTO deliveries (name) VALUES
            ('Самовывоз'),
            ('Курьер'),
            ('Почта'),
            ('Транспортная компания')
            ON CONFLICT (name) DO NOTHING;
        """))
        
        db.commit()
        print("Reference data seeded successfully")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding reference data: {str(e)}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_reference_data() 
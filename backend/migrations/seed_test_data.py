from sqlalchemy import text
from backend.models.database import get_db

def seed_test_data():
    """Seed test data for products"""
    db = next(get_db())
    
    try:
        # First ensure we have all required reference data
        # Add types if not exists
        db.execute(text("""
            INSERT INTO types (id, name) VALUES
            (1, 'Одежда')
            ON CONFLICT (id) DO NOTHING;
        """))
        
        # Add brands if not exists
        db.execute(text("""
            INSERT INTO brands (id, name) VALUES
            (1, 'Nike'),
            (2, 'Levi''s'),
            (3, 'Adidas')
            ON CONFLICT (id) DO NOTHING;
        """))
        
        # Add colors if not exists
        db.execute(text("""
            INSERT INTO colors (id, name) VALUES
            (1, 'Черный'),
            (2, 'Синий'),
            (3, 'Зеленый')
            ON CONFLICT (id) DO NOTHING;
        """))
        
        # Add statuses if not exists
        db.execute(text("""
            INSERT INTO statuses (id, name) VALUES
            (1, 'В наличии')
            ON CONFLICT (id) DO NOTHING;
        """))
        
        # Add conditions if not exists
        db.execute(text("""
            INSERT INTO conditions (id, name) VALUES
            (1, 'Новый')
            ON CONFLICT (id) DO NOTHING;
        """))
        
        # Add imports if not exists
        db.execute(text("""
            INSERT INTO imports (id, name) VALUES
            (1, 'Импортировано')
            ON CONFLICT (id) DO NOTHING;
        """))
        
        # Add test products
        db.execute(text("""
            INSERT INTO products (
                productnumber, model, price, typeid, brandid, colorid,
                statusid, conditionid, importid, sizeeu,
                quantity, description
            ) VALUES
            ('P001', 'Базова футболка', 1200, 1, 1, 1, 1, 1, 1, 'L', 1, 'Базова футболка...'),
            ('P002', 'Класичні джинси', 2500, 1, 2, 2, 1, 1, 1, '32', 1, 'Класичні джинси...'),
            ('P003', 'Зимова куртка', 3500, 1, 3, 3, 1, 1, 1, 'XL', 1, 'Зимова куртка A...')
            ON CONFLICT (productnumber) DO UPDATE SET
                model = EXCLUDED.model,
                price = EXCLUDED.price,
                typeid = EXCLUDED.typeid,
                brandid = EXCLUDED.brandid,
                colorid = EXCLUDED.colorid,
                statusid = EXCLUDED.statusid,
                conditionid = EXCLUDED.conditionid,
                importid = EXCLUDED.importid,
                sizeeu = EXCLUDED.sizeeu,
                quantity = EXCLUDED.quantity,
                description = EXCLUDED.description;
        """))
        
        db.commit()
        print("Test data seeded successfully")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding test data: {str(e)}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_test_data() 
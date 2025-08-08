from sqlalchemy import text
from backend.models.database import get_db

def update_products_table():
    """Update products table structure"""
    db = next(get_db())
    
    try:
        # Create new tables for reference data
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS types (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS subtypes (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS brands (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS colors (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS countries (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS statuses (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS conditions (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS imports (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS deliveries (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL
            );
        """))
        
        # Drop old table if exists
        db.execute(text("DROP TABLE IF EXISTS products CASCADE;"))
        
        # Create new products table
        db.execute(text("""
            CREATE TABLE products (
                id SERIAL PRIMARY KEY,
                productnumber VARCHAR(50) NOT NULL UNIQUE,
                clonednumbers TEXT,
                model VARCHAR(500),
                marking VARCHAR(500),
                year INTEGER,
                description TEXT,
                extranote TEXT,
                price NUMERIC(10,2),
                oldprice NUMERIC(10,2),
                dateadded TIMESTAMP DEFAULT NOW(),
                sizeeu VARCHAR(50),
                sizeua VARCHAR(50),
                sizeusa VARCHAR(50),
                sizeuk VARCHAR(10),
                sizejp VARCHAR(10),
                sizecn VARCHAR(10),
                measurementscm VARCHAR(50),
                quantity INTEGER DEFAULT 1,
                typeid INTEGER REFERENCES types(id),
                subtypeid INTEGER REFERENCES subtypes(id),
                brandid INTEGER REFERENCES brands(id),
                genderid INTEGER REFERENCES genders(id),
                colorid INTEGER REFERENCES colors(id),
                ownercountryid INTEGER REFERENCES countries(id),
                manufacturercountryid INTEGER REFERENCES countries(id),
                statusid INTEGER REFERENCES statuses(id),
                conditionid INTEGER REFERENCES conditions(id),
                importid INTEGER REFERENCES imports(id),
                deliveryid INTEGER REFERENCES deliveries(id),
                mainimage VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """))
        
        # Create indexes
        db.execute(text("""
            CREATE INDEX idx_products_productnumber ON products(productnumber);
            CREATE INDEX idx_products_typeid ON products(typeid);
            CREATE INDEX idx_products_subtypeid ON products(subtypeid);
            CREATE INDEX idx_products_brandid ON products(brandid);
            CREATE INDEX idx_products_genderid ON products(genderid);
            CREATE INDEX idx_products_colorid ON products(colorid);
            CREATE INDEX idx_products_statusid ON products(statusid);
            CREATE INDEX idx_products_conditionid ON products(conditionid);
            CREATE INDEX idx_products_importid ON products(importid);
            CREATE INDEX idx_products_deliveryid ON products(deliveryid);
        """))
        
        db.commit()
        print("Products table structure updated successfully")
        
    except Exception as e:
        db.rollback()
        print(f"Error updating products table: {str(e)}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    update_products_table() 
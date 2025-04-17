from backend.models.database import get_db
from backend.models.models import Product
from sqlalchemy import update

def update_product_visibility():
    # Get database session
    db = next(get_db())
    
    try:
        # Update all products to be visible
        db.execute(update(Product).values(is_visible=True))
        db.commit()
        print("Successfully updated all products to be visible")
    except Exception as e:
        db.rollback()
        print(f"Error updating product visibility: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    update_product_visibility() 
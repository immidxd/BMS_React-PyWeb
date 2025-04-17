from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text, func, desc, asc
from sqlalchemy.sql.expression import or_, and_
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import logging

from backend.models import models
from backend.schemas import product as schemas

logger = logging.getLogger(__name__)

def get_product(db: Session, product_id: int) -> Optional[models.Product]:
    """Get a single product by ID with all related data"""
    try:
        product = db.query(models.Product).options(
            joinedload(models.Product.type),
            joinedload(models.Product.subtype),
            joinedload(models.Product.brand),
            joinedload(models.Product.gender),
            joinedload(models.Product.color),
            joinedload(models.Product.owner_country),
            joinedload(models.Product.manufacturer_country),
            joinedload(models.Product.status),
            joinedload(models.Product.condition),
            joinedload(models.Product.import_record),
            joinedload(models.Product.delivery)
        ).filter(models.Product.id == product_id).first()
        logger.debug(f"Retrieved product: {product}")
        return product
    except Exception as e:
        logger.error(f"Error getting product {product_id}: {str(e)}")
        raise

def get_product_by_number(db: Session, product_number: str) -> Optional[models.Product]:
    """Get a product by its product number"""
    try:
        product = db.query(models.Product).options(
            joinedload(models.Product.type),
            joinedload(models.Product.subtype),
            joinedload(models.Product.brand),
            joinedload(models.Product.gender),
            joinedload(models.Product.color),
            joinedload(models.Product.owner_country),
            joinedload(models.Product.manufacturer_country),
            joinedload(models.Product.status),
            joinedload(models.Product.condition),
            joinedload(models.Product.import_record),
            joinedload(models.Product.delivery)
        ).filter(models.Product.productnumber == product_number).first()
        logger.debug(f"Retrieved product by number: {product}")
        return product
    except Exception as e:
        logger.error(f"Error getting product by number {product_number}: {str(e)}")
        raise

def get_products(
    db: Session, 
    skip: int = 0, 
    limit: int = 100,
    filters: Optional[schemas.ProductFilter] = None,
    sort_by: str = "id",
    sort_dir: str = "desc"
) -> Dict[str, Any]:
    """Get a list of products with pagination and filtering"""
    try:
        logger.debug(f"Getting products with filters: {filters}")
        query = db.query(models.Product).options(
            joinedload(models.Product.type),
            joinedload(models.Product.subtype),
            joinedload(models.Product.brand),
            joinedload(models.Product.gender),
            joinedload(models.Product.color),
            joinedload(models.Product.owner_country),
            joinedload(models.Product.manufacturer_country),
            joinedload(models.Product.status),
            joinedload(models.Product.condition),
            joinedload(models.Product.import_record),
            joinedload(models.Product.delivery)
        )
        
        if filters:
            if filters.search:
                search = f"%{filters.search}%"
                query = query.filter(
                    or_(
                        models.Product.productnumber.ilike(search),
                        models.Product.model.ilike(search),
                        models.Product.description.ilike(search),
                        models.Product.marking.ilike(search)
                    )
                )
                
            if filters.typeid:
                query = query.filter(models.Product.typeid == filters.typeid)
                
            if filters.subtypeid:
                query = query.filter(models.Product.subtypeid == filters.subtypeid)
                
            if filters.brandid:
                query = query.filter(models.Product.brandid == filters.brandid)
                
            if filters.genderid:
                query = query.filter(models.Product.genderid == filters.genderid)
                
            if filters.colorid:
                query = query.filter(models.Product.colorid == filters.colorid)
                
            if filters.statusid:
                query = query.filter(models.Product.statusid == filters.statusid)
                
            if filters.conditionid:
                query = query.filter(models.Product.conditionid == filters.conditionid)
                
            if filters.min_price is not None:
                query = query.filter(models.Product.price >= filters.min_price)
                
            if filters.max_price is not None:
                query = query.filter(models.Product.price <= filters.max_price)
            
            if filters.is_visible is not None:
                query = query.filter(models.Product.is_visible == filters.is_visible)
            
            if filters.with_stock_only:
                query = query.filter(models.Product.quantity > 0)
        
        total = query.count()
        
        if sort_by and hasattr(models.Product, sort_by):
            sort_column = getattr(models.Product, sort_by)
            if sort_dir.lower() == "asc":
                query = query.order_by(asc(sort_column))
            else:
                query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(desc(models.Product.id))
        
        items = query.offset(skip).limit(limit).all()
        
        result = {
            "items": items,
            "total": total,
            "page": skip // limit + 1 if limit > 0 else 1,
            "size": limit,
            "pages": (total + limit - 1) // limit if limit > 0 else 1
        }
        logger.debug(f"Retrieved products: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting products: {str(e)}")
        raise

def create_product(db: Session, product: schemas.ProductCreate) -> models.Product:
    """Create a new product"""
    try:
        db_product = models.Product(**product.dict())
        db.add(db_product)
        db.commit()
        db.refresh(db_product)
        logger.debug(f"Created product: {db_product}")
        return db_product
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating product: {str(e)}")
        raise

def update_product(db: Session, product_id: int, product: schemas.ProductUpdate) -> Optional[models.Product]:
    """Update an existing product"""
    try:
        db_product = db.query(models.Product).filter(models.Product.id == product_id).first()
        if db_product:
            update_data = product.dict(exclude_unset=True)
            for key, value in update_data.items():
                setattr(db_product, key, value)
            db.commit()
            db.refresh(db_product)
            logger.debug(f"Updated product: {db_product}")
        return db_product
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating product {product_id}: {str(e)}")
        raise

def delete_product(db: Session, product_id: int) -> Optional[models.Product]:
    """Delete a product"""
    try:
        db_product = db.query(models.Product).filter(models.Product.id == product_id).first()
        if db_product:
            db.delete(db_product)
            db.commit()
            logger.debug(f"Deleted product: {db_product}")
        return db_product
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting product {product_id}: {str(e)}")
        raise

def get_product_filters(db: Session) -> Dict[str, Any]:
    """Get all available filters for products"""
    try:
        types = db.query(models.Type.id, models.Type.name).all()
        subtypes = db.query(models.Subtype.id, models.Subtype.name, models.Subtype.typeid).all()
        brands = db.query(models.Brand.id, models.Brand.name).all()
        genders = db.query(models.Gender.id, models.Gender.name).all()
        colors = db.query(models.Color.id, models.Color.name).all()
        statuses = db.query(models.Status.id, models.Status.name).all()
        conditions = db.query(models.Condition.id, models.Condition.name).all()
        
        min_price = db.query(func.min(models.Product.price)).scalar() or 0
        max_price = db.query(func.max(models.Product.price)).scalar() or 0
        
        result = {
            "types": [{"id": t[0], "name": t[1]} for t in types],
            "subtypes": [{"id": s[0], "name": s[1], "typeid": s[2]} for s in subtypes],
            "brands": [{"id": b[0], "name": b[1]} for b in brands],
            "genders": [{"id": g[0], "name": g[1]} for g in genders],
            "colors": [{"id": c[0], "name": c[1]} for c in colors],
            "statuses": [{"id": s[0], "name": s[1]} for s in statuses],
            "conditions": [{"id": c[0], "name": c[1]} for c in conditions],
            "min_price": min_price,
            "max_price": max_price
        }
        logger.debug(f"Retrieved filters: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting filters: {str(e)}")
        raise

def get_product_with_relations(db: Session, product_id: int) -> Optional[Dict[str, Any]]:
    """
    Отримати товар з усіма зв'язаними даними
    """
    try:
        # SQL запит з JOIN для отримання пов'язаних даних
        query = text("""
            SELECT p.*, 
                   t.name as type_name,
                   st.name as subtype_name,
                   b.name as brand_name,
                   g.name as gender_name,
                   c.name as color_name,
                   oc.name as owner_country_name,
                   mc.name as manufacturer_country_name,
                   s.name as status_name,
                   cond.name as condition_name,
                   i.name as import_name,
                   d.name as delivery_name
            FROM products p
            LEFT JOIN types t ON p.typeid = t.id
            LEFT JOIN subtypes st ON p.subtypeid = st.id
            LEFT JOIN brands b ON p.brandid = b.id
            LEFT JOIN genders g ON p.genderid = g.id
            LEFT JOIN colors c ON p.colorid = c.id
            LEFT JOIN countries oc ON p.ownercountryid = oc.id
            LEFT JOIN countries mc ON p.manufacturercountryid = mc.id
            LEFT JOIN statuses s ON p.statusid = s.id
            LEFT JOIN conditions cond ON p.conditionid = cond.id
            LEFT JOIN imports i ON p.importid = i.id
            LEFT JOIN deliveries d ON p.deliveryid = d.id
            WHERE p.id = :id
        """)
        
        # Виконання запиту
        result = db.execute(query, {"id": product_id}).mappings().first()
        
        if not result:
            logger.warning(f"Product with ID {product_id} not found with relations")
            return None
        
        return dict(result)
    except Exception as e:
        logger.error(f"Error fetching product ID {product_id} with relations: {str(e)}")
        raise

def update_product_visibility(db: Session, product_id: int, is_visible: bool) -> bool:
    """
    Оновити видимість товару
    """
    try:
        db_product = db.query(models.Product).filter(models.Product.id == product_id).first()
        
        if not db_product:
            logger.warning(f"Product with ID {product_id} not found for visibility update")
            return False
        
        db_product.is_visible = is_visible
        db.commit()
        logger.info(f"Updated visibility for product ID {product_id}: {is_visible}")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating visibility for product ID {product_id}: {str(e)}")
        raise

def bulk_update_products(db: Session, product_ids: List[int], update_data: Dict[str, Any]) -> int:
    """
    Масове оновлення товарів
    """
    try:
        # Фільтруємо тільки валідні поля для оновлення
        valid_fields = [c.name for c in models.Product.__table__.columns]
        filtered_data = {k: v for k, v in update_data.items() if k in valid_fields}
        
        if not filtered_data:
            logger.warning("No valid fields to update")
            return 0
        
        # Виконуємо оновлення
        result = db.query(models.Product).filter(models.Product.id.in_(product_ids)).update(
            filtered_data, 
            synchronize_session=False
        )
        
        db.commit()
        logger.info(f"Bulk updated {result} products")
        return result
    except Exception as e:
        db.rollback()
        logger.error(f"Error during bulk update: {str(e)}")
        raise 
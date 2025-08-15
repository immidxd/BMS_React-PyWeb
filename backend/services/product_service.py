from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text, func, desc, asc
from sqlalchemy.sql.expression import or_, and_
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import logging

from models import models
from schemas import product as schemas

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
    sort_dir: str = "desc",
) -> Dict[str, Any]:
    """Get a list of products with pagination and filtering with related data."""
    try:
        logger.debug(f"Getting products with filters: {filters}")
        # Hot reload trigger
        
        # Use direct SQL query to get all product data with related names
        from sqlalchemy import text
        
        base_sql = """
        SELECT p.*, 
               t.typename as type_name,
               b.brandname as brand_name, 
               s.statusname as status_name,
               c.colorname as color_name,
               cond.conditionname as condition_name,
               g.gendername as gender_name
        FROM products p
        LEFT JOIN types t ON p.typeid = t.id
        LEFT JOIN brands b ON p.brandid = b.id  
        LEFT JOIN statuses s ON p.statusid = s.id
        LEFT JOIN colors c ON p.colorid = c.id
        LEFT JOIN conditions cond ON p.conditionid = cond.id
        LEFT JOIN genders g ON p.genderid = g.id
        """
        
        where_conditions = []
        params = {}
        
        if filters:
            if filters.search:
                search = f"%{filters.search}%"
                where_conditions.append("""
                    (p.productnumber ILIKE :search OR 
                     p.model ILIKE :search OR 
                     p.description ILIKE :search OR 
                     p.marking ILIKE :search OR
                     p.extranote ILIKE :search OR
                     b.brandname ILIKE :search OR
                     t.typename ILIKE :search OR
                     c.colorname ILIKE :search OR
                     g.gendername ILIKE :search OR
                     cond.conditionname ILIKE :search OR
                     s.statusname ILIKE :search)
                """)
                params['search'] = search
                
            if filters.typeid:
                where_conditions.append("p.typeid = :typeid")
                params['typeid'] = filters.typeid
                
            if filters.subtypeid:
                where_conditions.append("p.subtypeid = :subtypeid") 
                params['subtypeid'] = filters.subtypeid
                
            if filters.brandid:
                where_conditions.append("p.brandid = :brandid")
                params['brandid'] = filters.brandid
                
            if filters.genderid:
                where_conditions.append("p.genderid = :genderid")
                params['genderid'] = filters.genderid
                
            if filters.colorid:
                where_conditions.append("p.colorid = :colorid")
                params['colorid'] = filters.colorid
                
            if filters.statusid:
                where_conditions.append("p.statusid = :statusid")
                params['statusid'] = filters.statusid
                
            if filters.conditionid:
                where_conditions.append("p.conditionid = :conditionid")
                params['conditionid'] = filters.conditionid
                
            if filters.min_price is not None:
                where_conditions.append("p.price >= :min_price")
                params['min_price'] = filters.min_price
                
            if filters.max_price is not None:
                where_conditions.append("p.price <= :max_price")
                params['max_price'] = filters.max_price
            
            # Note: is_visible column doesn't exist in the actual database
            
            if filters.with_stock_only:
                where_conditions.append("p.quantity > 0")
        
        # Build WHERE clause
        if where_conditions:
            base_sql += " WHERE " + " AND ".join(where_conditions)
        
        # Count total
        count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS subquery"
        total_result = db.execute(text(count_sql), params)
        total = total_result.scalar()
        
        # Add ORDER BY
        allowed_sort_columns = {"id", "dateadded", "price", "created_at", "updated_at"}
        if sort_by in allowed_sort_columns:
            sort_col = f"p.{sort_by}"
            order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
            base_sql += f" ORDER BY {sort_col} {order_dir}"
        else:
            base_sql += " ORDER BY p.id DESC"
        
        # Add LIMIT and OFFSET
        base_sql += " LIMIT :limit OFFSET :offset"
        params['limit'] = limit
        params['offset'] = skip
        
        # Execute query
        rows = db.execute(text(base_sql), params).fetchall()
        
        # Convert rows to dictionaries (since we're using raw SQL)
        items = []
        for row in rows:
            # Raw SQL returns Row objects - convert to dict
            product_dict = {
                'id': row[0],  # id
                'productnumber': row[1],  # productnumber
                'clonednumbers': row[2],  # clonednumbers
                'model': row[3],  # model
                'marking': row[4],  # marking
                'year': row[5],  # year
                'description': row[6],  # description
                'extranote': row[7],  # extranote
                'price': row[8],  # price
                'oldprice': row[9],  # oldprice
                'dateadded': row[10],  # dateadded
                'sizeeu': row[11],  # sizeeu
                'sizeua': row[12],  # sizeua
                'sizeusa': row[13],  # sizeusa
                'sizeuk': row[14],  # sizeuk
                'sizejp': row[15],  # sizejp
                'sizecn': row[16],  # sizecn
                'measurementscm': row[17],  # measurementscm
                'quantity': row[18],  # quantity
                'typeid': row[19],  # typeid
                'subtypeid': row[20],  # subtypeid
                'brandid': row[21],  # brandid
                'genderid': row[22],  # genderid
                'colorid': row[23],  # colorid
                'ownercountryid': row[24],  # ownercountryid
                'manufacturercountryid': row[25],  # manufacturercountryid
                'statusid': row[26],  # statusid
                'conditionid': row[27],  # conditionid
                'importid': row[28],  # importid
                'deliveryid': row[29],  # deliveryid
                'mainimage': row[30],  # mainimage
                'created_at': row[31],  # created_at
                'updated_at': row[32],  # updated_at
                # Related names from JOINs (after all product columns)
                'type_name': row[33],  # type_name
                'brand_name': row[34],  # brand_name
                'status_name': row[35],  # status_name
                'color_name': row[36],  # color_name
                'condition_name': row[37],  # condition_name
                'gender_name': row[38],  # gender_name
            }
            items.append(product_dict)
        
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
    """Get all available filters for products using raw SQL (tables may not have ORM models)."""
    try:
        def fetch_pairs(sql: str):
            return db.execute(text(sql)).fetchall()

        types = fetch_pairs("SELECT id, typename FROM types ORDER BY typename")
        subtypes_rows = db.execute(text("SELECT id, subtypename, typeid FROM subtypes ORDER BY subtypename")).fetchall()
        brands = fetch_pairs("SELECT id, brandname FROM brands ORDER BY brandname")
        genders = fetch_pairs("SELECT id, gendername FROM genders ORDER BY gendername")
        colors = fetch_pairs("SELECT id, colorname FROM colors ORDER BY colorname")
        statuses = fetch_pairs("SELECT id, statusname FROM statuses ORDER BY statusname")
        conditions = fetch_pairs("SELECT id, conditionname FROM conditions ORDER BY conditionname")

        price_min_max = db.execute(text("SELECT COALESCE(min(price),0) AS min_price, COALESCE(max(price),0) AS max_price FROM products")).mappings().first()
        min_price = float(price_min_max["min_price"]) if price_min_max else 0
        max_price = float(price_min_max["max_price"]) if price_min_max else 0

        result = {
            "types": [{"id": t[0], "name": t[1]} for t in types],
            "subtypes": [{"id": s[0], "name": s[1], "typeid": s[2]} for s in subtypes_rows],
            "brands": [{"id": b[0], "name": b[1]} for b in brands],
            "genders": [{"id": g[0], "name": g[1]} for g in genders],
            "colors": [{"id": c[0], "name": c[1]} for c in colors],
            "statuses": [{"id": s[0], "name": s[1]} for s in statuses],
            "conditions": [{"id": c[0], "name": c[1]} for c in conditions],
            "min_price": min_price,
            "max_price": max_price,
        }
        logger.debug("Retrieved filters via raw SQL")
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
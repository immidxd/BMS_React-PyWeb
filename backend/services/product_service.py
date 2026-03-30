from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text, func, desc, asc
from sqlalchemy.sql.expression import or_, and_
from typing import Optional, List, Dict, Any, Tuple, Set
from datetime import datetime
import logging
import re
import math

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
            joinedload(models.Product.delivery)
        ).filter(models.Product.productnumber == product_number).first()
        logger.debug(f"Retrieved product by number: {product}")
        return product
    except Exception as e:
        logger.error(f"Error getting product by number {product_number}: {str(e)}")
        raise

def _fmt_size(v: float) -> str:
    """Format a numeric size value: 41.0→'41', 37.5→'37.5', 36.6→'36.6'."""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


_RANGE_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$')


def _expand_sizes_for_filters(raw_sizes: List[str]) -> List[str]:
    """Expand range sizes into individual sizes for the filter panel.

    '41-42' → adds 41, 42  (integer step when both ends are integers)
    '36.6-37.5' → adds 36.6, 37, 37.5  (0.5 step, keep exact endpoints)
    '39-43' → adds 39, 40, 41, 42, 43
    Non-range values (e.g. '41', 'XL') are kept as-is.
    """
    individual: Set[str] = set()
    for s in raw_sizes:
        m = _RANGE_RE.match(s.strip())
        if not m:
            individual.add(s.strip())
            continue
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            individual.add(s.strip())
            continue
        # Always include exact endpoints
        individual.add(_fmt_size(lo))
        individual.add(_fmt_size(hi))
        # Step: 1 when both ends are integers, else 0.5
        both_int = (lo == int(lo) and hi == int(hi))
        step = 1.0 if both_int else 0.5
        cur = lo + step
        while cur < hi - 0.001:
            individual.add(_fmt_size(cur))
            cur += step

    # Sort: numeric values first (by value), then text values alphabetically
    nums = []
    texts = []
    for s in individual:
        try:
            nums.append((float(s), s))
        except ValueError:
            texts.append(s)
    nums.sort(key=lambda x: x[0])
    texts.sort()
    return [s for _, s in nums] + texts


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
               g.gendername as gender_name,
               st.subtypename as subtype_name,
               COALESCE(sold.sold_count, 0) AS sold_count,
               GREATEST(COALESCE(p.quantity, 0) - COALESCE(sold.sold_count, 0), 0) AS available_qty,
               COALESCE(dup.dup_brands, 0) AS pnum_dup_brands,
               -- Ростовка: quantity>1 АБО extranote містить "ростовка" АБО (n)-суфікс
               (
                   p.quantity > 1
                   OR LOWER(COALESCE(p.extranote, '')) LIKE '%ростовка%'
                   OR p.productnumber ~ '^.+\\([0-9]+\\)$'
                   OR EXISTS (
                       SELECT 1 FROM products p2
                       WHERE p2.productnumber = p.productnumber || '(1)'
                   )
               ) AS is_rostovka
        FROM products p
        LEFT JOIN types t ON p.typeid = t.id
        LEFT JOIN brands b ON p.brandid = b.id  
        LEFT JOIN statuses s ON p.statusid = s.id
        LEFT JOIN colors c ON p.colorid = c.id
        LEFT JOIN conditions cond ON p.conditionid = cond.id
        LEFT JOIN genders g ON p.genderid = g.id
        LEFT JOIN subtypes st ON p.subtypeid = st.id
        LEFT JOIN (
            SELECT oi.product_id, COUNT(*) AS sold_count
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id IN (1, 7)
            GROUP BY oi.product_id
        ) sold ON sold.product_id = p.id
        LEFT JOIN (
            SELECT productnumber, COUNT(DISTINCT COALESCE(brandid, 0)) AS dup_brands
            FROM products
            GROUP BY productnumber
            HAVING COUNT(DISTINCT COALESCE(brandid, 0)) > 1
        ) dup ON dup.productnumber = p.productnumber
        LEFT JOIN deliveries d ON p.deliveryid = d.id
        LEFT JOIN (
            SELECT oi.product_id, MAX(o.order_date) AS last_sale_date
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id IN (1, 7)
            GROUP BY oi.product_id
        ) last_sale ON last_sale.product_id = p.id
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
                
            # Single ID filters
            if filters.typeid and not filters.typeids:
                where_conditions.append("p.typeid = :typeid")
                params['typeid'] = filters.typeid

            if filters.subtypeid and not filters.subtypeids:
                where_conditions.append("p.subtypeid = :subtypeid")
                params['subtypeid'] = filters.subtypeid

            if filters.brandid and not filters.brandids:
                where_conditions.append("p.brandid = :brandid")
                params['brandid'] = filters.brandid

            if filters.genderid and not filters.genderids:
                where_conditions.append("p.genderid = :genderid")
                params['genderid'] = filters.genderid

            if filters.colorid and not filters.colorids:
                where_conditions.append("p.colorid = :colorid")
                params['colorid'] = filters.colorid

            if filters.statusid and not filters.statusids:
                where_conditions.append("p.statusid = :statusid")
                params['statusid'] = filters.statusid

            if filters.conditionid and not filters.conditionids:
                where_conditions.append("p.conditionid = :conditionid")
                params['conditionid'] = filters.conditionid

            # Multi-ID filters (arrays) — use ANY(:arr)
            if filters.typeids:
                where_conditions.append("p.typeid = ANY(:typeids)")
                params['typeids'] = filters.typeids

            if filters.subtypeids:
                where_conditions.append("p.subtypeid = ANY(:subtypeids)")
                params['subtypeids'] = filters.subtypeids

            if filters.brandids:
                where_conditions.append("p.brandid = ANY(:brandids)")
                params['brandids'] = filters.brandids

            if filters.genderids:
                where_conditions.append("p.genderid = ANY(:genderids)")
                params['genderids'] = filters.genderids

            if filters.colorids:
                where_conditions.append("p.colorid = ANY(:colorids)")
                params['colorids'] = filters.colorids

            # Фільтр по кольоровій групі (базовий колір)
            if hasattr(filters, 'color_group_ids') and filters.color_group_ids:
                where_conditions.append("""
                    EXISTS (
                        SELECT 1 FROM color_group_members cgm
                        WHERE cgm.color_id = p.colorid
                        AND cgm.group_id = ANY(:color_group_ids)
                    )
                """)
                params['color_group_ids'] = filters.color_group_ids

            if filters.statusids:
                where_conditions.append("p.statusid = ANY(:statusids)")
                params['statusids'] = filters.statusids

            if filters.conditionids:
                where_conditions.append("p.conditionid = ANY(:conditionids)")
                params['conditionids'] = filters.conditionids

            # Price range
            if filters.min_price is not None:
                where_conditions.append("p.price >= :min_price")
                params['min_price'] = filters.min_price

            if filters.max_price is not None:
                where_conditions.append("p.price <= :max_price")
                params['max_price'] = filters.max_price

            # Size EU filter (multi-select) — also matches range sizes
            if filters.sizeeu:
                # Exact match for selected sizes
                # PLUS range match: product "39-43" matches filter "41"
                numeric_vals = []
                for s in filters.sizeeu:
                    try:
                        numeric_vals.append(float(s))
                    except (ValueError, TypeError):
                        pass
                if numeric_vals:
                    # Build individual range checks for each numeric size
                    range_checks = []
                    for i, v in enumerate(numeric_vals):
                        pname = f"sz_num_{i}"
                        range_checks.append(f"""(
                            CAST(split_part(p.sizeeu, '-', 1) AS numeric) <= :{pname}
                            AND CAST(split_part(p.sizeeu, '-', 2) AS numeric) >= :{pname}
                        )""")
                        params[pname] = v
                    range_sql = " OR ".join(range_checks)
                    where_conditions.append(f"""(
                        p.sizeeu = ANY(:sizeeu)
                        OR (
                            p.sizeeu ~ '^[0-9]+\\.?[0-9]*-[0-9]+\\.?[0-9]*$'
                            AND ({range_sql})
                        )
                    )""")
                else:
                    where_conditions.append("p.sizeeu = ANY(:sizeeu)")
                params['sizeeu'] = filters.sizeeu

            if filters.with_stock_only:
                where_conditions.append("p.quantity > 0")

            if filters.only_unsold:
                # Must match frontend display logic (ProductsTable.tsx):
                # Frontend shows "Продано" when sold_count >= quantity && quantity > 0,
                # regardless of DB statusname. So we exclude those too.
                where_conditions.append("""(
                    (s.statusname IS NULL OR s.statusname = 'Непродано')
                    AND COALESCE(sold.sold_count, 0) = 0
                )""")

            if filters.only_problematic:
                where_conditions.append("""(
                    p.productnumber IS NULL
                    OR p.productnumber = '???'
                    OR p.productnumber LIKE '???\\_%'
                    OR p.productnumber LIKE '__tmp_rename\\_%'
                    OR p.typeid IS NULL
                    OR p.price IS NULL OR p.price = 0
                    OR COALESCE(sold.sold_count, 0) > COALESCE(p.quantity, 0)
                    OR COALESCE(dup.dup_brands, 0) > 1
                )""")

            if filters.only_rostovka:
                # Ростовка = набір розмірів:
                # 1) quantity > 1 (кілька одиниць/розмірів в одному записі)
                # 2) extranote містить "ростовка" (явна мітка)
                # 3) productnumber з (n) суфіксом (варіанти, розбиті по рядках)
                # 4) базовий продукт з (n) дочірнім записом
                where_conditions.append("""(
                    p.quantity > 1
                    OR LOWER(COALESCE(p.extranote, '')) LIKE '%ростовка%'
                    OR p.productnumber ~ '^.+\\([0-9]+\\)$'
                    OR EXISTS (
                        SELECT 1 FROM products p_sib
                        WHERE p_sib.productnumber = p.productnumber || '(1)'
                    )
                )""")

            # shipment_id and is_visible columns don't exist in real DB — removed
        
        # Build WHERE clause
        if where_conditions:
            base_sql += " WHERE " + " AND ".join(where_conditions)
        
        # Count total
        count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS subquery"
        total_result = db.execute(text(count_sql), params)
        total = total_result.scalar()
        
        # Add ORDER BY — compound sort modes + simple column fallback
        sort_map = {
            # d.deliverydate = дата з delivery (назва аркуша журналу = реальна дата завозу)
            # p.dateadded = fallback дата, для аркушів без парсованої дати = date.today()
            "created_at":     "d.deliverydate DESC NULLS LAST, p.dateadded DESC NULLS LAST, p.id DESC",
            "created_at_asc": "d.deliverydate ASC NULLS LAST, p.dateadded ASC NULLS LAST, p.id ASC",
            "delivery_date":  "d.deliverydate DESC NULLS LAST, p.id DESC",
            "last_sold":      "last_sale.last_sale_date DESC NULLS LAST, p.id DESC",
            "price_desc":     "p.price DESC NULLS LAST",
            "price_asc":      "p.price ASC NULLS LAST",
        }
        sort_key = sort_by if sort_by in sort_map else f"{sort_by}_{sort_dir}"
        if sort_key in sort_map:
            base_sql += f" ORDER BY {sort_map[sort_key]}"
        else:
            # Fallback for simple column sorts
            allowed_simple = {"id", "dateadded", "price", "created_at", "updated_at"}
            if sort_by in allowed_simple:
                order_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
                base_sql += f" ORDER BY p.{sort_by} {order_dir}"
            else:
                base_sql += " ORDER BY p.created_at DESC"
        
        # Add LIMIT and OFFSET
        base_sql += " LIMIT :limit OFFSET :offset"
        params['limit'] = limit
        params['offset'] = skip
        
        # Execute query
        rows = db.execute(text(base_sql), params).fetchall()
        
        # Convert rows to dictionaries using _mapping (safe regardless of column order)
        items = []
        for row in rows:
            m = row._mapping
            product_dict = {
                'id': m.get('id'),
                'productnumber': m.get('productnumber'),
                'clonednumbers': m.get('clonednumbers'),
                'model': m.get('model'),
                'marking': m.get('marking'),
                'year': m.get('year'),
                'description': m.get('description'),
                'extranote': m.get('extranote'),
                'price': m.get('price'),
                'oldprice': m.get('oldprice'),
                'dateadded': str(m.get('dateadded')) if m.get('dateadded') else None,
                'sizeeu': m.get('sizeeu'),
                'sizeua': m.get('sizeua'),
                'sizeusa': m.get('sizeusa'),
                'sizeuk': m.get('sizeuk'),
                'sizejp': m.get('sizejp'),
                'sizecn': m.get('sizecn'),
                'measurementscm': m.get('measurementscm'),
                'quantity': m.get('quantity'),
                'mainimage': m.get('mainimage'),
                'is_visible': m.get('is_visible'),
                'typeid': m.get('typeid'),
                'subtypeid': m.get('subtypeid'),
                'brandid': m.get('brandid'),
                'genderid': m.get('genderid'),
                'colorid': m.get('colorid'),
                'ownercountryid': m.get('ownercountryid'),
                'manufacturercountryid': m.get('manufacturercountryid'),
                'statusid': m.get('statusid'),
                'conditionid': m.get('conditionid'),
                'importid': m.get('importid'),
                'deliveryid': m.get('deliveryid'),
                'created_at': str(m.get('created_at')) if m.get('created_at') else None,
                'updated_at': str(m.get('updated_at')) if m.get('updated_at') else None,
                'type_name': m.get('type_name'),
                'brand_name': m.get('brand_name'),
                'status_name': m.get('status_name'),
                'color_name': m.get('color_name'),
                'condition_name': m.get('condition_name'),
                'gender_name': m.get('gender_name'),
                'supplier_name': None,
                'subtype_name': m.get('subtype_name'),
                'sold_count': m.get('sold_count', 0),
                'available_qty': m.get('available_qty'),
                'pnum_dup_brands': m.get('pnum_dup_brands', 0),
                'is_rostovka': bool(m.get('is_rostovka', False)),
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
        # Кольорові групи + відтінки (для нового UI фільтра)
        color_groups_rows = db.execute(text("""
            SELECT cg.id, cg.name, cg.hex_code, cg.display_order,
                   COUNT(DISTINCT p.id) as product_count
            FROM color_groups cg
            LEFT JOIN color_group_members cgm ON cgm.group_id = cg.id
            LEFT JOIN products p ON p.colorid = cgm.color_id
            GROUP BY cg.id, cg.name, cg.hex_code, cg.display_order
            ORDER BY cg.display_order
        """)).fetchall()
        statuses = fetch_pairs("SELECT id, statusname FROM statuses ORDER BY statusname")
        conditions = fetch_pairs("SELECT id, conditionname FROM conditions ORDER BY conditionname")

        price_min_max = db.execute(text("SELECT COALESCE(min(price),0) AS min_price, COALESCE(max(price),0) AS max_price FROM products")).mappings().first()
        min_price = float(price_min_max["min_price"]) if price_min_max else 0
        max_price = float(price_min_max["max_price"]) if price_min_max else 0

        countries = fetch_pairs("SELECT id, countryname FROM countries ORDER BY countryname")

        shipments_rows = db.execute(text(
            """SELECT d.id, d.deliveryname, d.deliverydate, COUNT(p.id) AS product_count
               FROM deliveries d
               JOIN products p ON p.deliveryid = d.id
               GROUP BY d.id, d.deliveryname, d.deliverydate
               HAVING COUNT(p.id) > 0
               ORDER BY d.deliverydate DESC NULLS LAST, d.id DESC"""
        )).fetchall()

        # Size ranges per system — expand range values into individual sizes
        def fetch_sizes(col: str):
            rows = db.execute(text(f"SELECT DISTINCT {col} FROM products WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}")).fetchall()
            raw = [r[0] for r in rows]
            return _expand_sizes_for_filters(raw)

        result = {
            "types": [{"id": t[0], "name": t[1]} for t in types],
            "subtypes": [{"id": s[0], "name": s[1], "typeid": s[2]} for s in subtypes_rows],
            "brands": [{"id": b[0], "name": b[1]} for b in brands],
            "genders": [{"id": g[0], "name": g[1]} for g in genders],
            "colors": [{"id": c[0], "name": c[1]} for c in colors],
            "color_groups": [
                {"id": cg[0], "name": cg[1], "hex": cg[2], "order": cg[3], "count": cg[4]}
                for cg in color_groups_rows
            ],
            "statuses": [{"id": s[0], "name": s[1]} for s in statuses],
            "conditions": [{"id": c[0], "name": c[1]} for c in conditions],
            "countries": [{"id": c[0], "name": c[1]} for c in countries],
            "shipments": [{"id": s[0], "name": s[1], "date": str(s[2]) if s[2] else None, "count": s[3]} for s in shipments_rows],
            "price_range": {"min_price": min_price, "max_price": max_price},
            "size_ranges": {
                "eu": fetch_sizes("sizeeu"),
                "ua": fetch_sizes("sizeua"),
                "usa": fetch_sizes("sizeusa"),
                "uk": fetch_sizes("sizeuk"),
                "jp": fetch_sizes("sizejp"),
                "cn": fetch_sizes("sizecn"),
            },
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


def sync_product_statuses(db: Session) -> Dict[str, int]:
    """
    Синхронізує статуси товарів після парсингу.

    Логіка:
      - "Продано"   якщо товар є в order_items АБО журнал вже встановив "Продано"
      - "Непродано" в усіх інших випадках

    Повертає: {'prodano': <кількість>, 'neprodano': <кількість>}
    """
    try:
        # Отримуємо ID статусів; якщо немає — створюємо
        prodano_row = db.execute(
            text("SELECT id FROM statuses WHERE statusname = 'Продано' LIMIT 1")
        ).fetchone()
        if not prodano_row:
            db.execute(text("INSERT INTO statuses (statusname) VALUES ('Продано')"))
            db.commit()
            prodano_row = db.execute(
                text("SELECT id FROM statuses WHERE statusname = 'Продано' LIMIT 1")
            ).fetchone()

        neprodano_row = db.execute(
            text("SELECT id FROM statuses WHERE statusname = 'Непродано' LIMIT 1")
        ).fetchone()
        if not neprodano_row:
            db.execute(text("INSERT INTO statuses (statusname) VALUES ('Непродано')"))
            db.commit()
            neprodano_row = db.execute(
                text("SELECT id FROM statuses WHERE statusname = 'Непродано' LIMIT 1")
            ).fetchone()

        prodano_id   = prodano_row[0]
        neprodano_id = neprodano_row[0]

        # Рахуємо кількість продажів по product_id (точний метод)
        # та по notes для не-ростовок (де тільки 1 товар з таким номером)
        # Статус "Продано" — тільки якщо sold_count >= quantity (всі одиниці продано)
        # Статус "Непродано" — якщо 0 < sold_count < quantity (частково продано)
        # Без продажів — не чіпаємо (залишаємо статус з журналу)
        r1 = db.execute(text("""
            WITH
            id_sold AS (
                SELECT oi.product_id, COUNT(*) AS cnt
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.product_id IS NOT NULL
                  AND o.order_status_id IN (1, 7)
                GROUP BY oi.product_id
            ),
            notes_sold AS (
                SELECT p.id, COUNT(oi.id) AS cnt
                FROM products p
                JOIN order_items oi
                    ON oi.product_id IS NULL
                   AND oi.notes IS NOT NULL
                   AND oi.notes != ''
                   AND oi.notes NOT LIKE '% %'
                   AND '#' || LTRIM(oi.notes, '#') = p.productnumber
                WHERE (
                    SELECT COUNT(*) FROM products p2
                    WHERE p2.productnumber = p.productnumber
                ) = 1
                GROUP BY p.id
            ),
            sold_counts AS (
                SELECT
                    p.id,
                    COALESCE(i.cnt, 0) + COALESCE(n.cnt, 0) AS sold_count,
                    COALESCE(p.quantity, 1)                  AS qty
                FROM products p
                LEFT JOIN id_sold    i ON i.product_id = p.id
                LEFT JOIN notes_sold n ON n.id          = p.id
                WHERE COALESCE(i.cnt, 0) + COALESCE(n.cnt, 0) > 0
            )
            UPDATE products p
            SET statusid = CASE
                WHEN sc.sold_count >= sc.qty THEN :prodano_id
                ELSE :neprodano_id
            END
            FROM sold_counts sc
            WHERE p.id = sc.id
        """), {"prodano_id": prodano_id, "neprodano_id": neprodano_id})
        total_updated = r1.rowcount

        # Крок 2: скинути хибний "Продано" з журналу для товарів де
        # фактично sold_count < quantity (або взагалі 0 продажів).
        # Товари зі спеціальними статусами (Повернуто, Пошкоджений тощо) — не чіпаємо.
        r2 = db.execute(text("""
            UPDATE products p
            SET statusid = :neprodano_id
            WHERE p.statusid = :prodano_id
              AND p.id NOT IN (
                  SELECT sc.id FROM (
                      SELECT
                          p2.id,
                          COALESCE(
                              (SELECT COUNT(*) FROM order_items oi
                               JOIN orders o ON o.id = oi.order_id
                               WHERE oi.product_id = p2.id
                                 AND o.order_status_id IN (1, 7)), 0
                          ) AS sold_count,
                          COALESCE(p2.quantity, 1) AS qty
                      FROM products p2
                      WHERE p2.statusid = :prodano_id
                  ) sc
                  WHERE sc.sold_count >= sc.qty AND sc.qty > 0
              )
        """), {"prodano_id": prodano_id, "neprodano_id": neprodano_id})
        total_updated += r2.rowcount
        logger.info(f"sync_product_statuses: fixed {r2.rowcount} false 'Продано' → 'Непродано'")

        # Рахуємо фінальний розподіл для логу
        counts = db.execute(text("""
            SELECT s.statusname, COUNT(p.id) as cnt
            FROM products p JOIN statuses s ON p.statusid = s.id
            GROUP BY s.statusname
        """)).fetchall()
        prodano_count   = next((r.cnt for r in counts if r.statusname == 'Продано'),   0)
        neprodano_count = next((r.cnt for r in counts if r.statusname == 'Непродано'), 0)

        db.commit()
        logger.info(
            f"sync_product_statuses: Продано={prodano_count}, Непродано={neprodano_count}"
        )
        return {"prodano": prodano_count, "neprodano": neprodano_count}

    except Exception as e:
        db.rollback()
        logger.error(f"sync_product_statuses error: {e}")
        raise
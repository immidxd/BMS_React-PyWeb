from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Path, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import logging
from datetime import datetime

from backend.models.database import get_db
from backend.models.models import Order, OrderItem, Client, Product, OrderStatus, PaymentStatus, DeliveryMethod
from backend.schemas import product as schemas
from backend.services import product_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/products", response_model=schemas.ProductListResponse)
async def get_products(
    page: int = Query(1, description="Current page (starts from 1)"),
    per_page: int = Query(20, description="Number of items per page"),
    search: Optional[str] = Query(None, description="Search term for products"),
    db: Session = Depends(get_db)
):
    """
    Get products with pagination as expected by the frontend
    """
    try:
        logger.info(f"Requesting products with params: page={page}, per_page={per_page}, search={search}")
        
        # Calculate skip based on page number and per_page
        skip = (page - 1) * per_page
        
        # Create sample product data
        sample_products = [
            {
                "id": 1,
                "productnumber": "P001",
                "model": "Nike Футболка",
                "price": 1200.0,
                "oldprice": None,
                "quantity": 10,
                "description": "Базова футболка Nike чорного кольору",
                "typeid": 1,
                "subtypeid": 3,
                "brandid": 1,
                "statusid": 1,
                "is_visible": True,
                "dateadded": datetime.now().isoformat(),
                "created_at": "2024-03-30T00:00:00",
                "updated_at": "2024-03-30T00:00:00"
            },
            {
                "id": 2,
                "productnumber": "P002",
                "model": "Levi's Джинси",
                "price": 2500.0,
                "oldprice": None,
                "quantity": 5,
                "description": "Класичні джинси Levi's синього кольору",
                "typeid": 2,
                "subtypeid": 5,
                "brandid": 6,
                "statusid": 1,
                "is_visible": True,
                "dateadded": datetime.now().isoformat(),
                "created_at": "2024-03-30T00:00:00",
                "updated_at": "2024-03-30T00:00:00"
            },
            {
                "id": 3,
                "productnumber": "P003",
                "model": "Adidas Куртка",
                "price": 3500.0,
                "oldprice": None,
                "quantity": 3,
                "description": "Зимова куртка Adidas зеленого кольору",
                "typeid": 3,
                "subtypeid": 2,
                "brandid": 2,
                "statusid": 1,
                "is_visible": True,
                "dateadded": datetime.now().isoformat(),
                "created_at": "2024-03-30T00:00:00",
                "updated_at": "2024-03-30T00:00:00"
            }
        ]
        
        # Return in format expected by frontend
        result = {
            "items": sample_products,
            "total": len(sample_products),
            "page": page,
            "size": per_page,
            "pages": 1
        }
        
        logger.info(f"Returning {len(sample_products)} products")
        return result
    except Exception as e:
        logger.error(f"Error getting products: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні товарів: {str(e)}")

@router.get("/api/products/filters", response_model=schemas.FilterOptions)
async def get_product_filters(db: Session = Depends(get_db)):
    """
    Get product filter options as expected by the frontend
    """
    try:
        logger.info(f"Returning filter options")
        
        # Create sample filter options
        filters = {
            "types": [{"id": 1, "name": "Футболка"}, {"id": 2, "name": "Джинси"}, {"id": 3, "name": "Куртка"}],
            "subtypes": [{"id": 1, "name": "Літня"}, {"id": 2, "name": "Зимова"}, {"id": 3, "name": "Базова"}],
            "brands": [{"id": 1, "name": "Nike"}, {"id": 2, "name": "Adidas"}, {"id": 6, "name": "Levi's"}],
            "genders": [{"id": 1, "name": "Чоловіча"}, {"id": 2, "name": "Жіноча"}, {"id": 3, "name": "Унісекс"}],
            "colors": [{"id": 1, "name": "Червоний"}, {"id": 2, "name": "Синій"}, {"id": 3, "name": "Чорний"}],
            "statuses": [{"id": 1, "name": "В наявності"}, {"id": 2, "name": "Закінчується"}, {"id": 3, "name": "Немає в наявності"}],
            "conditions": [{"id": 1, "name": "Нове"}, {"id": 2, "name": "Ідеальний стан"}, {"id": 3, "name": "Добрий стан"}],
            "min_price": 1200,
            "max_price": 3500
        }
        
        return filters
    except Exception as e:
        logger.error(f"Error getting product filters: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні фільтрів: {str(e)}")

@router.get("/{product_id}", response_model=schemas.Product)
async def get_product(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """
    Отримати деталі товару за його ID
    """
    try:
        product = product_service.get_product_with_relations(db, product_id)
        
        if not product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        return product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при отриманні товару: {str(e)}")

@router.post("/", response_model=schemas.Product, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: schemas.ProductCreate,
    db: Session = Depends(get_db)
):
    """
    Створити новий товар
    """
    try:
        # Перевіряємо, чи вже існує товар з таким номером
        existing_product = product_service.get_product_by_number(db, product_data.productnumber)
        
        if existing_product:
            raise HTTPException(
                status_code=400, 
                detail=f"Товар з номером {product_data.productnumber} вже існує"
            )
        
        # Створюємо новий товар
        product = product_service.create_product(db, product_data)
        return product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating product: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при створенні товару: {str(e)}")

@router.put("/{product_id}", response_model=schemas.Product)
async def update_product(
    product_id: int = Path(..., ge=1, description="ID товару"),
    product_data: schemas.ProductUpdate = Body(...),
    db: Session = Depends(get_db)
):
    """
    Оновити існуючий товар
    """
    try:
        # Перевіряємо, чи існує товар з таким ID
        existing_product = product_service.get_product(db, product_id)
        
        if not existing_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Якщо змінюється номер товару, перевіряємо його унікальність
        if product_data.productnumber and product_data.productnumber != existing_product.productnumber:
            duplicate = product_service.get_product_by_number(db, product_data.productnumber)
            
            if duplicate and duplicate.id != product_id:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Товар з номером {product_data.productnumber} вже існує"
                )
        
        # Оновлюємо товар
        updated_product = product_service.update_product(db, product_id, product_data)
        
        if not updated_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        return updated_product
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні товару: {str(e)}")

@router.delete("/{product_id}", response_model=Dict[str, Any])
async def delete_product(
    product_id: int = Path(..., ge=1, description="ID товару"),
    db: Session = Depends(get_db)
):
    """
    Видалити товар
    """
    try:
        # Перевіряємо, чи існує товар з таким ID
        existing_product = product_service.get_product(db, product_id)
        
        if not existing_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Видаляємо товар
        success = product_service.delete_product(db, product_id)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Не вдалося видалити товар з ID {product_id}")
        
        return {"success": True, "message": f"Товар з ID {product_id} видалено"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при видаленні товару: {str(e)}")

@router.patch("/{product_id}/visibility", response_model=Dict[str, Any])
async def update_product_visibility(
    product_id: int = Path(..., ge=1, description="ID товару"),
    is_visible: bool = Body(..., embed=True),
    db: Session = Depends(get_db)
):
    """
    Оновити видимість товару
    """
    try:
        # Перевіряємо, чи існує товар з таким ID
        existing_product = product_service.get_product(db, product_id)
        
        if not existing_product:
            raise HTTPException(status_code=404, detail=f"Товар з ID {product_id} не знайдено")
        
        # Оновлюємо видимість
        success = product_service.update_product_visibility(db, product_id, is_visible)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Не вдалося оновити видимість товару з ID {product_id}")
        
        return {
            "success": True, 
            "message": f"Видимість товару з ID {product_id} оновлено",
            "is_visible": is_visible
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating product visibility {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при оновленні видимості товару: {str(e)}")

@router.post("/bulk-update", response_model=Dict[str, Any])
async def bulk_update_products(
    product_ids: List[int] = Body(..., min_items=1),
    update_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db)
):
    """
    Масове оновлення товарів
    """
    try:
        # Перевіряємо, що є хоча б один товар для оновлення
        if not product_ids:
            raise HTTPException(status_code=400, detail="Потрібно вказати хоча б один ID товару")
        
        # Перевіряємо, що є дані для оновлення
        if not update_data:
            raise HTTPException(status_code=400, detail="Потрібно вказати дані для оновлення")
        
        # Оновлюємо товари
        updated_count = product_service.bulk_update_products(db, product_ids, update_data)
        
        return {
            "success": True,
            "message": f"Оновлено {updated_count} товарів",
            "updated_count": updated_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk updating products: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Помилка при масовому оновленні товарів: {str(e)}") 
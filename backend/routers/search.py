"""
Глобальна система пошуку для BMS
Підтримує пошук по всіх сутностях системи з інтелектуальними інсайтами
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text, or_, and_, func
from typing import Optional, Dict, Any, List
import logging

from backend.models.database import get_db
from backend.models import models
from backend.schemas.search import (
    GlobalSearchRequest, 
    GlobalSearchResponse, 
    SearchInsights,
    CategorySearchResult
)
from backend.services.advanced_search_service import advanced_search_service

router = APIRouter(prefix="/api/search", tags=["search"])
logger = logging.getLogger(__name__)

@router.get("/global", response_model=GlobalSearchResponse)
async def global_search(
    q: str = Query(..., min_length=1, description="Пошуковий запит"),
    scope: Optional[str] = Query(None, description="Обмежити пошук категорією: products, orders, clients, suppliers, deliveries"),
    limit: int = Query(5, ge=1, le=20, description="Кількість результатів для превью"),
    include_insights: bool = Query(True, description="Включити інсайти та статистику"),
    db: Session = Depends(get_db)
):
    """
    Глобальний пошук по всіх сутностях системи
    Повертає результати з усіх категорій + інсайти
    """
    try:
        logger.info(f"Global search query: '{q}', scope: {scope}")
        
        results = {}
        insights = {}
        
        # Пошук товарів
        if not scope or scope == "products":
            products_result = await _search_products(db, q, limit)
            results["products"] = products_result
            
            if include_insights:
                insights["products"] = {
                    "total_found": products_result["total"],
                    "brands_found": await _count_brands_in_search(db, q),
                    "avg_price": await _get_avg_price_in_search(db, q)
                }
        
        # Пошук замовлень (якщо таблиця готова)
        if not scope or scope == "orders":
            try:
                orders_result = await _search_orders(db, q, limit)
                results["orders"] = orders_result
                
                if include_insights:
                    insights["orders"] = {
                        "total_found": orders_result["total"],
                        "active_orders": await _count_active_orders_in_search(db, q),
                        "total_value": await _get_total_orders_value_in_search(db, q)
                    }
            except Exception as e:
                logger.warning(f"Orders search failed: {e}")
                results["orders"] = {"items": [], "total": 0}
        
        # Пошук клієнтів (якщо таблиця готова)
        if not scope or scope == "clients":
            try:
                clients_result = await _search_clients(db, q, limit)
                results["clients"] = clients_result
                
                if include_insights:
                    insights["clients"] = {
                        "total_found": clients_result["total"],
                        "active_clients": await _count_active_clients_in_search(db, q)
                    }
            except Exception as e:
                logger.warning(f"Clients search failed: {e}")
                results["clients"] = {"items": [], "total": 0}
        
        # Заглушки для інших категорій
        if not scope or scope == "suppliers":
            results["suppliers"] = {"items": [], "total": 0}
        
        if not scope or scope == "deliveries":
            results["deliveries"] = {"items": [], "total": 0}
        
        return GlobalSearchResponse(
            query=q,
            scope=scope,
            results=results,
            insights=insights if include_insights else {}
        )
        
    except Exception as e:
        logger.error(f"Global search error: {e}")
        raise HTTPException(status_code=500, detail="Помилка пошуку")


async def _search_products(db: Session, query: str, limit: int) -> CategorySearchResult:
    """Розширений пошук товарів з використанням AdvancedSearchService"""
    try:
        # Використовуємо новий розширений сервіс пошуку
        results, total = await advanced_search_service.advanced_search(
            db=db,
            query=query,
            limit=limit,
            offset=0,
            min_score=0.1
        )
        
        # Форматування результатів для API
        items = []
        for result in results:
            item = {
                "id": result["id"],
                "productnumber": result["productnumber"],
                "model": result["model"],
                "description": result["description"],
                "price": result["price"],
                "brand_name": result["brand_name"],
                "type_name": result["type_name"],
                "status_name": result["status_name"],
                "color_name": result["color_name"],
                "gender_name": result["gender_name"],
                "condition_name": result["condition_name"],
                "quantity": result["quantity"],
                "sizeeu": result["sizeeu"],
                "extranote": result["extranote"],
                "_relevance_score": result.get("_relevance_score", 0)
            }
            items.append(item)
        
        logger.info(f"Advanced search found {len(items)} products for query: '{query}'")
        return CategorySearchResult(items=items, total=total)
        
    except Exception as e:
        logger.error(f"Advanced products search error: {e}")
        # Fallback до простого пошуку
        return await _search_products_simple(db, query, limit)


async def _search_products_simple(db: Session, query: str, limit: int) -> CategorySearchResult:
    """Простий fallback пошук товарів"""
    try:
        search_conditions = or_(
            models.Product.productnumber.ilike(f"%{query}%"),
            models.Product.model.ilike(f"%{query}%"),
            models.Brand.brandname.ilike(f"%{query}%")
        )
        
        total = db.query(func.count(models.Product.id)).join(
            models.Brand, models.Product.brandid == models.Brand.id, isouter=True
        ).filter(search_conditions).scalar() or 0
        
        products = db.query(models.Product, models.Brand.brandname.label('brand_name')).join(
            models.Brand, models.Product.brandid == models.Brand.id, isouter=True
        ).filter(search_conditions).limit(limit).all()
        
        items = []
        for row in products:
            product = row[0]
            item = {
                "id": product.id,
                "productnumber": product.productnumber,
                "model": product.model,
                "price": float(product.price) if product.price else None,
                "brand_name": row.brand_name,
                "quantity": product.quantity or 0
            }
            items.append(item)
        
        return CategorySearchResult(items=items, total=total)
        
    except Exception as e:
        logger.error(f"Simple products search error: {e}")
        return CategorySearchResult(items=[], total=0)


async def _search_orders(db: Session, query: str, limit: int) -> CategorySearchResult:
    """Пошук замовлень (заглушка)"""
    # TODO: Реалізувати після налаштування таблиці замовлень
    return CategorySearchResult(items=[], total=0)


async def _search_clients(db: Session, query: str, limit: int) -> CategorySearchResult:
    """Пошук клієнтів"""
    try:
        search_conditions = or_(
            models.Client.first_name.ilike(f"%{query}%"),
            models.Client.last_name.ilike(f"%{query}%"),
            models.Client.phone_number.ilike(f"%{query}%"),
            models.Client.email.ilike(f"%{query}%")
        )
        
        total = db.query(func.count(models.Client.id)).filter(search_conditions).scalar() or 0
        
        clients = db.query(models.Client).filter(search_conditions).limit(limit).all()
        
        items = []
        for client in clients:
            item = {
                "id": client.id,
                "full_name": f"{client.first_name} {client.last_name}".strip(),
                "phone_number": client.phone_number,
                "email": client.email
            }
            items.append(item)
        
        return CategorySearchResult(items=items, total=total)
        
    except Exception as e:
        logger.error(f"Clients search error: {e}")
        return CategorySearchResult(items=[], total=0)


# Допоміжні функції для інсайтів
async def _count_brands_in_search(db: Session, query: str) -> int:
    """Підрахунок кількості унікальних брендів у результатах пошуку"""
    try:
        search_conditions = or_(
            models.Product.productnumber.ilike(f"%{query}%"),
            models.Product.model.ilike(f"%{query}%"),
            models.Brand.brandname.ilike(f"%{query}%")
        )
        
        count = db.query(func.count(func.distinct(models.Product.brandid))).join(
            models.Brand, models.Product.brandid == models.Brand.id, isouter=True
        ).filter(search_conditions).scalar()
        
        return count or 0
    except:
        return 0


async def _get_avg_price_in_search(db: Session, query: str) -> Optional[float]:
    """Середня ціна товарів у результатах пошуку"""
    try:
        search_conditions = or_(
            models.Product.productnumber.ilike(f"%{query}%"),
            models.Product.model.ilike(f"%{query}%"),
            models.Brand.brandname.ilike(f"%{query}%")
        )
        
        avg_price = db.query(func.avg(models.Product.price)).join(
            models.Brand, models.Product.brandid == models.Brand.id, isouter=True
        ).filter(
            and_(search_conditions, models.Product.price.isnot(None))
        ).scalar()
        
        return float(avg_price) if avg_price else None
    except:
        return None


async def _count_active_orders_in_search(db: Session, query: str) -> int:
    """Підрахунок активних замовлень (заглушка)"""
    # TODO: Реалізувати після налаштування таблиці замовлень
    return 0


async def _get_total_orders_value_in_search(db: Session, query: str) -> Optional[float]:
    """Загальна вартість замовлень у пошуку (заглушка)"""
    # TODO: Реалізувати після налаштування таблиці замовлень
    return None


async def _count_active_clients_in_search(db: Session, query: str) -> int:
    """Підрахунок активних клієнтів (заглушка)"""
    # TODO: Реалізувати з урахуванням логіки активності
    return 0


@router.get("/advanced", response_model=CategorySearchResult)
async def advanced_products_search(
    q: str = Query(..., min_length=1, description="Пошуковий запит"),
    limit: int = Query(20, ge=1, le=100, description="Кількість результатів"),
    offset: int = Query(0, ge=0, description="Зміщення для пагінації"),
    min_score: float = Query(0.1, ge=0.0, le=10.0, description="Мінімальний рейтинг релевантності"),
    db: Session = Depends(get_db)
):
    """
    Розширений пошук товарів з ранжуванням та токенізацією
    Підтримує складні запити з синонімами та нечітким пошуком
    """
    try:
        logger.info(f"Advanced search query: '{q}', limit: {limit}, offset: {offset}")
        
        results, total = await advanced_search_service.advanced_search(
            db=db,
            query=q,
            limit=limit,
            offset=offset,
            min_score=min_score
        )
        
        # Форматування результатів
        items = []
        for result in results:
            item = {
                "id": result["id"],
                "productnumber": result["productnumber"],
                "model": result["model"],
                "description": result["description"],
                "price": result["price"],
                "brand_name": result["brand_name"],
                "type_name": result["type_name"],
                "status_name": result["status_name"],
                "color_name": result["color_name"],
                "gender_name": result["gender_name"],
                "condition_name": result["condition_name"],
                "quantity": result["quantity"],
                "sizeeu": result["sizeeu"],
                "extranote": result["extranote"],
                "_relevance_score": result.get("_relevance_score", 0)
            }
            items.append(item)
        
        return CategorySearchResult(items=items, total=total)
        
    except Exception as e:
        logger.error(f"Advanced search error: {e}")
        raise HTTPException(status_code=500, detail="Помилка розширеного пошуку")

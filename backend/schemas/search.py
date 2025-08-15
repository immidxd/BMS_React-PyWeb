"""
Схеми Pydantic для глобальної системи пошуку
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, Union
from datetime import datetime


class GlobalSearchRequest(BaseModel):
    """Запит глобального пошуку"""
    query: str = Field(..., min_length=1, description="Пошуковий запит")
    scope: Optional[str] = Field(None, description="Обмежити пошук категорією")
    limit: int = Field(5, ge=1, le=20, description="Кількість результатів для превью")
    include_insights: bool = Field(True, description="Включити інсайти")


class CategorySearchResult(BaseModel):
    """Результати пошуку в одній категорії"""
    items: List[Dict[str, Any]] = Field(default_factory=list, description="Знайдені елементи")
    total: int = Field(0, description="Загальна кількість знайдених елементів")


class SearchInsights(BaseModel):
    """Інсайти та статистика пошуку"""
    products: Optional[Dict[str, Any]] = None
    orders: Optional[Dict[str, Any]] = None
    clients: Optional[Dict[str, Any]] = None
    suppliers: Optional[Dict[str, Any]] = None
    deliveries: Optional[Dict[str, Any]] = None


class GlobalSearchResponse(BaseModel):
    """Відповідь глобального пошуку"""
    query: str = Field(..., description="Пошуковий запит")
    scope: Optional[str] = Field(None, description="Обмеження пошуку")
    results: Dict[str, CategorySearchResult] = Field(default_factory=dict, description="Результати по категоріях")
    insights: Dict[str, Any] = Field(default_factory=dict, description="Інсайти та статистика")
    timestamp: datetime = Field(default_factory=datetime.now, description="Час виконання пошуку")


class ProductSearchResult(BaseModel):
    """Результат пошуку товару"""
    id: int
    productnumber: str
    model: Optional[str] = None
    price: Optional[float] = None
    brand_name: Optional[str] = None
    type_name: Optional[str] = None
    status_name: Optional[str] = None
    color_name: Optional[str] = None
    quantity: int = 0


class ClientSearchResult(BaseModel):
    """Результат пошуку клієнта"""
    id: int
    full_name: str
    phone_number: Optional[str] = None
    email: Optional[str] = None


class OrderSearchResult(BaseModel):
    """Результат пошуку замовлення"""
    id: int
    order_number: Optional[str] = None
    client_name: Optional[str] = None
    total_amount: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class SupplierSearchResult(BaseModel):
    """Результат пошуку постачальника"""
    id: int
    name: str
    contact_info: Optional[str] = None


class DeliverySearchResult(BaseModel):
    """Результат пошуку поставки"""
    id: int
    delivery_name: str
    delivery_date: Optional[datetime] = None
    supplier_name: Optional[str] = None


class SearchSuggestion(BaseModel):
    """Підказка для пошуку"""
    text: str = Field(..., description="Текст підказки")
    category: str = Field(..., description="Категорія підказки")
    count: int = Field(0, description="Кількість результатів")


class SearchSuggestionsResponse(BaseModel):
    """Відповідь з підказками для пошуку"""
    query: str = Field(..., description="Пошуковий запит")
    suggestions: List[SearchSuggestion] = Field(default_factory=list, description="Список підказок")


class SearchHistoryItem(BaseModel):
    """Елемент історії пошуку"""
    query: str = Field(..., description="Пошуковий запит")
    category: Optional[str] = Field(None, description="Категорія пошуку")
    timestamp: datetime = Field(default_factory=datetime.now, description="Час пошуку")
    results_count: int = Field(0, description="Кількість знайдених результатів")


class SearchHistoryResponse(BaseModel):
    """Відповідь з історією пошуку"""
    history: List[SearchHistoryItem] = Field(default_factory=list, description="Історія пошуків")
    total: int = Field(0, description="Загальна кількість записів в історії")

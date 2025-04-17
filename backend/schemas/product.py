from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
import re

class ReferenceItem(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

# Базова модель товару
class ProductBase(BaseModel):
    productnumber: str = Field(..., min_length=1, max_length=50, description="Унікальний номер товару")
    clonednumbers: Optional[str] = Field(None, description="Номери клонів, розділені комою")
    model: Optional[str] = Field(None, max_length=500, description="Назва моделі товару")
    marking: Optional[str] = Field(None, max_length=500, description="Маркування товару")
    year: Optional[int] = Field(None, description="Рік випуску")
    description: Optional[str] = Field(None, description="Детальний опис товару")
    extranote: Optional[str] = Field(None, description="Додаткові примітки")
    price: Optional[float] = Field(None, ge=0, description="Поточна ціна товару")
    oldprice: Optional[float] = Field(None, ge=0, description="Стара ціна товару")
    sizeeu: Optional[str] = Field(None, max_length=50, description="Розмір за європейською шкалою")
    sizeua: Optional[str] = Field(None, max_length=50, description="Розмір за українською шкалою")
    sizeusa: Optional[str] = Field(None, max_length=50, description="Розмір за американською шкалою")
    sizeuk: Optional[str] = Field(None, max_length=10, description="Розмір за британською шкалою")
    sizejp: Optional[str] = Field(None, max_length=10, description="Розмір за японською шкалою")
    sizecn: Optional[str] = Field(None, max_length=10, description="Розмір за китайською шкалою")
    measurementscm: Optional[str] = Field(None, max_length=50, description="Розміри виробу в сантиметрах")
    quantity: int = Field(1, ge=0, description="Кількість товару в наявності")
    mainimage: Optional[str] = Field(None, max_length=255, description="URL основного зображення товару")
    is_visible: bool = Field(True, description="Чи відображається товар")
    
    # Foreign keys
    typeid: Optional[int] = Field(None, description="ID типу товару")
    subtypeid: Optional[int] = Field(None, description="ID підтипу товару")
    brandid: Optional[int] = Field(None, description="ID бренду")
    genderid: Optional[int] = Field(None, description="ID статі")
    colorid: Optional[int] = Field(None, description="ID кольору")
    ownercountryid: Optional[int] = Field(None, description="ID країни власника")
    manufacturercountryid: Optional[int] = Field(None, description="ID країни виробника")
    statusid: Optional[int] = Field(None, description="ID статусу товару")
    conditionid: Optional[int] = Field(None, description="ID стану товару")
    importid: Optional[int] = Field(None, description="ID імпорту товару")
    deliveryid: Optional[int] = Field(None, description="ID доставки товару")
    
    @validator('productnumber')
    def validate_productnumber(cls, v):
        if not v or not v.strip():
            raise ValueError('Номер товару не може бути порожнім')
        if not re.match(r'^[A-Za-z0-9_\-\.]+$', v):
            raise ValueError('Номер товару може містити лише латинські літери, цифри, дефіс, крапку та підкреслення')
        return v.strip()
    
    @validator('price', 'oldprice')
    def validate_price(cls, v):
        if v is not None and v < 0:
            raise ValueError('Ціна не може бути від\'ємною')
        return v
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v < 0:
            raise ValueError('Кількість не може бути від\'ємною')
        return v
    
    @validator('year')
    def validate_year(cls, v):
        if v is not None:
            current_year = datetime.now().year
            if v < 1900 or v > current_year + 1:
                raise ValueError(f'Рік має бути між 1900 та {current_year + 1}')
        return v

# Модель для створення товару
class ProductCreate(ProductBase):
    pass

# Модель для оновлення товару
class ProductUpdate(BaseModel):
    productnumber: Optional[str] = Field(None, min_length=1, max_length=50)
    clonednumbers: Optional[str] = None
    model: Optional[str] = None
    marking: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None
    extranote: Optional[str] = None
    price: Optional[float] = None
    oldprice: Optional[float] = None
    sizeeu: Optional[str] = None
    sizeua: Optional[str] = None
    sizeusa: Optional[str] = None
    sizeuk: Optional[str] = None
    sizejp: Optional[str] = None
    sizecn: Optional[str] = None
    measurementscm: Optional[str] = None
    quantity: Optional[int] = None
    mainimage: Optional[str] = None
    is_visible: Optional[bool] = None
    typeid: Optional[int] = None
    subtypeid: Optional[int] = None
    brandid: Optional[int] = None
    genderid: Optional[int] = None
    colorid: Optional[int] = None
    ownercountryid: Optional[int] = None
    manufacturercountryid: Optional[int] = None
    statusid: Optional[int] = None
    conditionid: Optional[int] = None
    importid: Optional[int] = None
    deliveryid: Optional[int] = None
    
    @validator('productnumber')
    def validate_productnumber(cls, v):
        if v is not None:
            if not v or not v.strip():
                raise ValueError('Номер товару не може бути порожнім')
            if not re.match(r'^[A-Za-z0-9_\-\.]+$', v):
                raise ValueError('Номер товару може містити лише латинські літери, цифри, дефіс, крапку та підкреслення')
            return v.strip()
        return v
    
    @validator('price', 'oldprice')
    def validate_price(cls, v):
        if v is not None and v < 0:
            raise ValueError('Ціна не може бути від\'ємною')
        return v
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v is not None and v < 0:
            raise ValueError('Кількість не може бути від\'ємною')
        return v
    
    @validator('year')
    def validate_year(cls, v):
        if v is not None:
            current_year = datetime.now().year
            if v < 1900 or v > current_year + 1:
                raise ValueError(f'Рік має бути між 1900 та {current_year + 1}')
        return v

# Повна модель товару з бази даних
class Product(ProductBase):
    id: int
    dateadded: Optional[datetime] = Field(default_factory=datetime.now)
    created_at: datetime
    updated_at: datetime
    
    # Додаткові поля з пов'язаних таблиць
    type_name: Optional[str] = None
    subtype_name: Optional[str] = None
    brand_name: Optional[str] = None
    gender_name: Optional[str] = None
    color_name: Optional[str] = None
    owner_country_name: Optional[str] = None
    manufacturer_country_name: Optional[str] = None
    status_name: Optional[str] = None
    condition_name: Optional[str] = None
    import_name: Optional[str] = None
    delivery_name: Optional[str] = None
    
    class Config:
        orm_mode = True

# Модель для відображення в списку
class ProductList(BaseModel):
    id: int
    productnumber: str
    model: Optional[str] = None
    price: Optional[float] = None
    quantity: int
    typeid: Optional[int] = None
    brandid: Optional[int] = None
    statusid: Optional[int] = None
    is_visible: bool
    
    type_name: Optional[str] = None
    brand_name: Optional[str] = None
    status_name: Optional[str] = None
    
    class Config:
        orm_mode = True

# Модель для пагінації та списку товарів
class ProductListResponse(BaseModel):
    items: List[Product]
    total: int
    page: int
    size: int
    pages: int

# Модель для фільтрації товарів
class ProductFilter(BaseModel):
    search: Optional[str] = None
    typeid: Optional[int] = None
    subtypeid: Optional[int] = None
    brandid: Optional[int] = None
    genderid: Optional[int] = None
    colorid: Optional[int] = None
    statusid: Optional[int] = None
    conditionid: Optional[int] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    is_visible: Optional[bool] = None
    with_stock_only: Optional[bool] = None

# Модель для опцій фільтрів
class FilterOptions(BaseModel):
    types: List[Dict[str, Any]]
    subtypes: List[Dict[str, Any]]
    brands: List[Dict[str, Any]]
    genders: List[Dict[str, Any]]
    colors: List[Dict[str, Any]]
    statuses: List[Dict[str, Any]]
    conditions: List[Dict[str, Any]]
    min_price: Optional[float] = None
    max_price: Optional[float] = None

# Schema for Product filters
class ProductFilters(BaseModel):
    brands: List[ReferenceItem]
    types: List[ReferenceItem]
    subtypes: List[ReferenceItem]
    colors: List[ReferenceItem]
    countries: List[ReferenceItem]
    statuses: List[ReferenceItem]
    conditions: List[ReferenceItem]
    genders: List[ReferenceItem]
    price_range: Dict[str, float]
    size_ranges: Dict[str, List[str]]

    class Config:
        from_attributes = True 
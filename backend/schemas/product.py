from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
import re

class ReferenceItem(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class ProductMaterialEntry(BaseModel):
    """One material assignment on a product (e.g. upper = шкіра)."""
    position: str           # upper|middle|insole|sole|membrane
    material_id: int
    materialname: Optional[str] = None   # convenience for API consumers
    category: Optional[str] = None
    ord: int = 0

    class Config:
        from_attributes = True


# Common measurement/materials fields shared by Base/List/Response.
# Keeping them as a flat mixin avoids drift between schemas.
_MEASUREMENT_FIELDS = {
    "measurements_length_min", "measurements_length_max",
    "measurements_pog_min", "measurements_pog_max",
    "measurements_pob_min", "measurements_pob_max",
    "measurements_pot_min", "measurements_pot_max",
    "measurements_sleeve_min", "measurements_sleeve_max",
    "measurements_height_min", "measurements_height_max",
    "measurements_sole_thickness_min", "measurements_sole_thickness_max",
}

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
    size_letter: Optional[str] = Field(None, max_length=10, description="Літерний розмір (XS/S/M/L/XL/XXL/XXXL)")
    measurementscm: Optional[str] = Field(None, max_length=50, description="Розміри виробу в сантиметрах (display, legacy)")
    measurementscm_min: Optional[float] = Field(None, description="СМ (min) — для числових range-фільтрів")
    measurementscm_max: Optional[float] = Field(None, description="СМ (max) — для числових range-фільтрів")
    season: Optional[str] = Field(None, max_length=100, description="Сезон (multi-value, через кому): \"Зима, Осінь\"")
    dimensions: Optional[str] = Field(None, max_length=50, description="Габарити: \"40x20x5\"")
    width: Optional[str] = Field(None, max_length=20, description="Ширина ніжки: 'Вузька'/'Стандартна'/'Широка' або B/D/EE")
    # Заміри (см) — всі min/max; single value = min == max; діапазон = min<max
    measurements_length_min: Optional[float] = Field(None, description="Довжина, см (min)")
    measurements_length_max: Optional[float] = Field(None, description="Довжина, см (max)")
    measurements_pog_min: Optional[float] = Field(None, description="Напівобхват грудей, см (min)")
    measurements_pog_max: Optional[float] = Field(None, description="Напівобхват грудей, см (max)")
    measurements_pob_min: Optional[float] = Field(None, description="Напівобхват бедер, см (min)")
    measurements_pob_max: Optional[float] = Field(None, description="Напівобхват бедер, см (max)")
    measurements_pot_min: Optional[float] = Field(None, description="Напівобхват талії, см (min)")
    measurements_pot_max: Optional[float] = Field(None, description="Напівобхват талії, см (max)")
    measurements_sleeve_min: Optional[float] = Field(None, description="Довжина рукава, см (min)")
    measurements_sleeve_max: Optional[float] = Field(None, description="Довжина рукава, см (max)")
    measurements_height_min: Optional[float] = Field(None, description="Висота взуття, см (min)")
    measurements_height_max: Optional[float] = Field(None, description="Висота взуття, см (max)")
    measurements_sole_thickness_min: Optional[float] = Field(None, description="Товщина підошви/платформи, см (min)")
    measurements_sole_thickness_max: Optional[float] = Field(None, description="Товщина підошви/платформи, см (max)")
    measurements_heel_min: Optional[float] = Field(None, description="Висота каблука/підбора, см (min)")
    measurements_heel_max: Optional[float] = Field(None, description="Висота каблука/підбора, см (max)")
    # Shoe-specific lookup FKs (single value per product)
    soletypeid: Optional[int] = Field(None, description="ID типу підошви")
    toeshapeid: Optional[int] = Field(None, description="ID форми носка")
    fasteningtypeid: Optional[int] = Field(None, description="ID типу застібки")
    liningid: Optional[int] = Field(None, description="ID типу підкладки")
    quantity: int = Field(1, ge=0, description="Кількість товару в наявності")
    mainimage: Optional[str] = Field(None, max_length=255, description="URL основного зображення товару")
    is_visible: bool = Field(True, description="Чи відображається товар")
    
    # Foreign keys
    typeid: Optional[int] = Field(None, description="ID типу товару")
    subtypeid: Optional[int] = Field(None, description="ID підтипу товару")
    styleid: Optional[int] = Field(None, description="ID стилю товару")
    brandid: Optional[int] = Field(None, description="ID бренду")
    genderid: Optional[int] = Field(None, description="ID статі")
    colorid: Optional[int] = Field(None, description="ID кольору")
    ownercountryid: Optional[int] = Field(None, description="ID країни власника")
    manufacturercountryid: Optional[int] = Field(None, description="ID країни виробника")
    statusid: Optional[int] = Field(None, description="ID статусу товару")
    conditionid: Optional[int] = Field(None, description="ID стану товару (при завезенні)")
    current_conditionid: Optional[int] = Field(None, description="ID поточного стану товару")
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
    size_letter: Optional[str] = None
    measurementscm: Optional[str] = None
    measurementscm_min: Optional[float] = None
    measurementscm_max: Optional[float] = None
    measurements_length_min: Optional[float] = None
    measurements_length_max: Optional[float] = None
    measurements_pog_min: Optional[float] = None
    measurements_pog_max: Optional[float] = None
    measurements_pob_min: Optional[float] = None
    measurements_pob_max: Optional[float] = None
    measurements_pot_min: Optional[float] = None
    measurements_pot_max: Optional[float] = None
    measurements_sleeve_min: Optional[float] = None
    measurements_sleeve_max: Optional[float] = None
    measurements_height_min: Optional[float] = None
    measurements_height_max: Optional[float] = None
    measurements_sole_thickness_min: Optional[float] = None
    measurements_sole_thickness_max: Optional[float] = None
    measurements_heel_min: Optional[float] = None
    measurements_heel_max: Optional[float] = None
    soletypeid: Optional[int] = None
    toeshapeid: Optional[int] = None
    fasteningtypeid: Optional[int] = None
    liningid: Optional[int] = None
    materials: Optional[List[ProductMaterialEntry]] = None   # full replace on PUT if provided
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
    current_condition_name: Optional[str] = None
    import_name: Optional[str] = None
    delivery_name: Optional[str] = None
    sole_type_name: Optional[str] = None
    toe_shape_name: Optional[str] = None
    fastening_type_name: Optional[str] = None
    lining_name: Optional[str] = None
    materials: List[ProductMaterialEntry] = []

    class Config:
        orm_mode = True

# Модель для відображення в списку
class ProductList(BaseModel):
    id: int
    productnumber: str
    clonednumbers: Optional[str] = None
    model: Optional[str] = None
    marking: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None
    extranote: Optional[str] = None
    price: Optional[float] = None
    oldprice: Optional[float] = None
    quantity: int
    sizeeu: Optional[str] = None
    sizeua: Optional[str] = None
    sizeusa: Optional[str] = None
    sizeuk: Optional[str] = None
    sizejp: Optional[str] = None
    sizecn: Optional[str] = None
    size_letter: Optional[str] = None
    measurementscm: Optional[str] = None
    measurementscm_min: Optional[float] = None
    measurementscm_max: Optional[float] = None
    mainimage: Optional[str] = None
    is_visible: Optional[bool] = None
    dateadded: Optional[str] = None
    typeid: Optional[int] = None
    subtypeid: Optional[int] = None
    brandid: Optional[int] = None
    statusid: Optional[int] = None
    colorid: Optional[int] = None
    conditionid: Optional[int] = None
    current_conditionid: Optional[int] = None
    genderid: Optional[int] = None
    styleid: Optional[int] = None
    season: Optional[str] = None
    dimensions: Optional[str] = None
    width: Optional[str] = None
    # Заміри (см) — всі min/max
    measurements_length_min: Optional[float] = None
    measurements_length_max: Optional[float] = None
    measurements_pog_min: Optional[float] = None
    measurements_pog_max: Optional[float] = None
    measurements_pob_min: Optional[float] = None
    measurements_pob_max: Optional[float] = None
    measurements_pot_min: Optional[float] = None
    measurements_pot_max: Optional[float] = None
    measurements_sleeve_min: Optional[float] = None
    measurements_sleeve_max: Optional[float] = None
    measurements_height_min: Optional[float] = None
    measurements_height_max: Optional[float] = None
    measurements_sole_thickness_min: Optional[float] = None
    measurements_sole_thickness_max: Optional[float] = None
    measurements_heel_min: Optional[float] = None
    measurements_heel_max: Optional[float] = None
    soletypeid: Optional[int] = None
    toeshapeid: Optional[int] = None
    fasteningtypeid: Optional[int] = None
    liningid: Optional[int] = None
    sole_type_name: Optional[str] = None
    toe_shape_name: Optional[str] = None
    fastening_type_name: Optional[str] = None
    lining_name: Optional[str] = None
    materials: List[ProductMaterialEntry] = []
    importid: Optional[int] = None
    deliveryid: Optional[int] = None

    # Related names from JOIN queries
    type_name: Optional[str] = None
    brand_name: Optional[str] = None
    status_name: Optional[str] = None
    color_name: Optional[str] = None
    condition_name: Optional[str] = None
    current_condition_name: Optional[str] = None
    gender_name: Optional[str] = None
    supplier_name: Optional[str] = None
    subtype_name: Optional[str] = None
    style_name: Optional[str] = None

    # Computed from order_items
    sold_count: int = 0
    available_qty: Optional[int] = None
    pnum_dup_brands: int = 0
    pending_candidates_count: int = 0  # merge-candidate UX badge
    
    class Config:
        from_attributes = True

# Модель для пагінації та списку товарів
class ProductListResponse(BaseModel):
    # Для списку використовуємо спрощену модель без жорсткої валідації productnumber
    items: List[ProductList]
    total: int
    page: int
    per_page: int
    pages: int

# Модель для фільтрації товарів
class ProductFilter(BaseModel):
    search: Optional[str] = None
    # Single ID filters (legacy)
    typeid: Optional[int] = None
    subtypeid: Optional[int] = None
    brandid: Optional[int] = None
    genderid: Optional[int] = None
    colorid: Optional[int] = None
    statusid: Optional[int] = None
    conditionid: Optional[int] = None
    # Multi-ID filters (arrays)
    typeids: Optional[List[int]] = None
    subtypeids: Optional[List[int]] = None
    brandids: Optional[List[int]] = None
    genderids: Optional[List[int]] = None
    colorids: Optional[List[int]] = None
    color_group_ids: Optional[List[int]] = None
    statusids: Optional[List[int]] = None
    conditionids: Optional[List[int]] = None
    # Нові фільтри: стиль, поточний стан, сезон, ширина
    styleid: Optional[int] = None
    styleids: Optional[List[int]] = None
    current_conditionid: Optional[int] = None
    current_conditionids: Optional[List[int]] = None
    seasons: Optional[List[str]] = None
    widths: Optional[List[str]] = None
    # Price range
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    # Size filter (multi-select exact) + range
    sizeeu: Optional[List[str]] = None
    min_sizeeu: Optional[float] = None   # range filter: from
    max_sizeeu: Optional[float] = None   # range filter: to
    size_letter: Optional[List[str]] = None  # multi-select XS/S/M/L/XL/XXL/...
    # Measurements CM range filter
    min_measurementscm: Optional[float] = None
    max_measurementscm: Optional[float] = None
    # Visibility
    is_visible: Optional[bool] = None
    with_stock_only: Optional[bool] = None
    only_unsold: Optional[bool] = None
    only_problematic: Optional[bool] = None
    only_rostovka: Optional[bool] = None
    shipment_id: Optional[int] = None

# Модель для опцій фільтрів
class FilterOptions(BaseModel):
    types: List[Dict[str, Any]]
    subtypes: List[Dict[str, Any]]
    brands: List[Dict[str, Any]]
    genders: List[Dict[str, Any]]
    colors: List[Dict[str, Any]]
    color_groups: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]]
    conditions: List[Dict[str, Any]]
    styles: List[Dict[str, Any]] = []
    seasons: List[str] = []
    widths: List[str] = []
    countries: List[Dict[str, Any]] = []
    shipments: List[Dict[str, Any]] = []
    price_range: Dict[str, float] = {"min_price": 0, "max_price": 0}
    size_ranges: Dict[str, List[str]] = {}
    size_letters: List[str] = []

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
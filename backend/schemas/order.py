from typing import Optional, List, Dict, Any
from datetime import datetime, date
from pydantic import BaseModel, Field
from .product import Product

# OrderItem Schema
class OrderItemBase(BaseModel):
    product_id: int
    quantity: int = 1
    price: float
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    additional_operation: Optional[str] = None
    additional_operation_value: Optional[float] = None
    notes: Optional[str] = None

class OrderItemCreate(OrderItemBase):
    pass

class OrderItemUpdate(OrderItemBase):
    pass

class OrderItem(OrderItemBase):
    id: int
    order_id: int
    created_at: datetime
    updated_at: datetime
    product_number: Optional[str] = None  # For display
    product_name: Optional[str] = None    # For display

    class Config:
        from_attributes = True

# Order Schema with nested OrderItem
class OrderBase(BaseModel):
    client_id: int
    order_date: date = Field(default_factory=date.today)
    order_status_id: Optional[int] = None
    total_amount: float = 0.0
    payment_method_id: Optional[int] = None
    payment_status: Optional[str] = None
    payment_status_id: Optional[int] = None
    delivery_method_id: Optional[int] = None
    delivery_address_id: Optional[int] = None
    tracking_number: Optional[str] = None
    delivery_status_id: Optional[int] = None
    notes: Optional[str] = None
    deferred_until: Optional[date] = None
    priority: int = 0
    broadcast_id: Optional[int] = None

class OrderCreate(OrderBase):
    order_items: List[OrderItemCreate]

class OrderUpdate(BaseModel):
    client_id: Optional[int] = None
    order_date: Optional[date] = None
    order_status_id: Optional[int] = None
    total_amount: Optional[float] = None
    payment_method_id: Optional[int] = None
    payment_status: Optional[str] = None
    payment_status_id: Optional[int] = None
    delivery_method_id: Optional[int] = None
    delivery_address_id: Optional[int] = None
    tracking_number: Optional[str] = None
    delivery_status_id: Optional[int] = None
    notes: Optional[str] = None
    deferred_until: Optional[date] = None
    priority: Optional[int] = None
    broadcast_id: Optional[int] = None
    order_items: Optional[List[OrderItemCreate]] = None

class Order(OrderBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class OrderResponse(Order):
    order_items: List[OrderItem] = []
    client: Dict[str, Any]
    order_status: Optional[Dict[str, Any]] = None
    payment_status: Optional[Dict[str, Any]] = None
    payment_method: Optional[Dict[str, Any]] = None
    delivery_method: Optional[Dict[str, Any]] = None
    delivery_status: Optional[Dict[str, Any]] = None
    delivery_address: Optional[Dict[str, Any]] = None
    broadcast: Optional[Dict[str, Any]] = None

class OrderListItem(BaseModel):
    id: int
    client_id: int
    client_name: str
    order_status_id: Optional[int] = None
    order_status_name: Optional[str] = None
    payment_status_id: Optional[int] = None
    payment_status_name: Optional[str] = None
    payment_status: Optional[str] = None
    payment_method_id: Optional[int] = None
    payment_method_name: Optional[str] = None
    delivery_method_id: Optional[int] = None
    delivery_method_name: Optional[str] = None
    delivery_status_id: Optional[int] = None
    delivery_status_name: Optional[str] = None
    order_date: date
    deferred_until: Optional[date] = None
    total_amount: float
    tracking_number: Optional[str] = None
    items_count: int
    priority: int = 0
    created_at: datetime
    updated_at: datetime

# Enhanced Order for display with related information
class OrderWithDetails(Order):
    client_name: str  # Combined first_name and last_name
    order_status_name: Optional[str] = None
    payment_status_name: Optional[str] = None
    payment_method_name: Optional[str] = None
    delivery_method_name: Optional[str] = None
    delivery_status_name: Optional[str] = None
    delivery_address_details: Optional[Dict[str, Any]] = None
    broadcast_name: Optional[str] = None
    order_items: List[OrderItem] = []
    
    class Config:
        from_attributes = True

# Order list with pagination
class OrderList(BaseModel):
    items: List[OrderWithDetails] = []
    total: int
    page: int
    per_page: int
    pages: int

# Order filters
class OrderFilters(BaseModel):
    order_status_ids: Optional[List[int]] = None
    payment_status_ids: Optional[List[int]] = None
    payment_method_ids: Optional[List[int]] = None
    delivery_method_ids: Optional[List[int]] = None
    delivery_status_ids: Optional[List[int]] = None
    client_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    month_min: Optional[int] = None
    month_max: Optional[int] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    search: Optional[str] = None
    priority_min: Optional[int] = None
    priority_max: Optional[int] = None
    has_tracking: Optional[bool] = None
    is_deferred: Optional[bool] = None

# Order with Products
class OrderWithProducts(Order):
    products: List[Product] = []
    
    class Config:
        from_attributes = True

# Filter options for UI
class FilterOptions(BaseModel):
    order_statuses: List[Dict[str, Any]] = []
    payment_statuses: List[Dict[str, Any]] = []
    payment_methods: List[Dict[str, Any]] = []
    delivery_methods: List[Dict[str, Any]] = []
    delivery_statuses: List[Dict[str, Any]] = []
    clients: List[Dict[str, Any]] = [] 
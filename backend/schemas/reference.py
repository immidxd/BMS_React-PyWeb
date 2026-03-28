from typing import Optional, List
from pydantic import BaseModel

# Base schema for reference entities with ID and name
class ReferenceBase(BaseModel):
    id: int
    # name: str
    # Для OrderStatus, PaymentStatus, DeliveryMethod треба окремо

# Gender schemas
class Gender(ReferenceBase):
    pass

class GenderList(BaseModel):
    items: List[Gender]

# OrderStatus schemas
class OrderStatusBase(BaseModel):
    id: int
    status_name: str
    description: Optional[str] = None

class OrderStatus(OrderStatusBase):
    pass

class OrderStatusCreate(BaseModel):
    status_name: str
    description: Optional[str] = None

class OrderStatusUpdate(BaseModel):
    status_name: Optional[str] = None
    description: Optional[str] = None

class OrderStatusList(BaseModel):
    items: List[OrderStatus]

# PaymentStatus schemas
class PaymentStatusBase(ReferenceBase):
    description: Optional[str] = None
    color_code: Optional[str] = None

class PaymentStatus(PaymentStatusBase):
    pass

class PaymentStatusCreate(BaseModel):
    name: str
    description: Optional[str] = None
    color_code: Optional[str] = None

class PaymentStatusUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color_code: Optional[str] = None

class PaymentStatusList(BaseModel):
    items: List[PaymentStatus]

# DeliveryMethod schemas
class DeliveryMethodBase(ReferenceBase):
    description: Optional[str] = None
    color_code: Optional[str] = None

class DeliveryMethod(DeliveryMethodBase):
    pass

class DeliveryMethodCreate(BaseModel):
    name: str
    description: Optional[str] = None
    color_code: Optional[str] = None

class DeliveryMethodUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color_code: Optional[str] = None

class DeliveryMethodList(BaseModel):
    items: List[DeliveryMethod]

# Client schemas
class ClientBase(BaseModel):
    first_name: str
    last_name: str
    phone_number: Optional[str] = None
    email: Optional[str] = None
    gender_id: Optional[int] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientUpdate(ClientBase):
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class Client(ClientBase):
    id: int
    full_name: str
    middle_name: Optional[str] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    telegram: Optional[str] = None
    viber: Optional[str] = None
    olx: Optional[str] = None
    city_of_residence: Optional[str] = None
    order_count: Optional[int] = None
    total_order_amount: Optional[float] = None
    average_order_value: Optional[float] = None
    # Order breakdown counts
    confirmed_orders: int = 0
    cancelled_count: int = 0
    ignored_count: int = 0
    return_exchange_count: int = 0
    has_deferred: bool = False
    # Client rating (0-10)
    rating: Optional[float] = None

    class Config:
        from_attributes = True

class ClientList(BaseModel):
    items: List[Client]
    total: int = 0
    page: int = 1
    per_page: int = 20
    pages: int = 1
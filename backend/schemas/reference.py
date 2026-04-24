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

class ClientUpdate(BaseModel):
    # Усі поля опціональні: модалка може редагувати будь-які окремо
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_name: Optional[str] = None
    nickname: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    gender_id: Optional[int] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    city_of_residence: Optional[str] = None
    client_discount: Optional[float] = None
    bonus_account: Optional[float] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    telegram: Optional[str] = None
    viber: Optional[str] = None
    messenger: Optional[str] = None
    tiktok: Optional[str] = None
    olx: Optional[str] = None

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


# ── Адресна книга клієнта ───────────────────────────────────────────────────
class ClientAddressBase(BaseModel):
    label: Optional[str] = None
    delivery_type: str = "np_warehouse"
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    city: Optional[str] = None
    city_ref: Optional[str] = None
    region: Optional[str] = None
    warehouse_number: Optional[str] = None
    warehouse_ref: Optional[str] = None
    street: Optional[str] = None
    building: Optional[str] = None
    apartment: Optional[str] = None
    postal_code: Optional[str] = None
    is_primary: bool = False
    is_active: bool = True
    notes: Optional[str] = None


class ClientAddressCreate(ClientAddressBase):
    pass


class ClientAddressUpdate(BaseModel):
    label: Optional[str] = None
    delivery_type: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    city: Optional[str] = None
    city_ref: Optional[str] = None
    region: Optional[str] = None
    warehouse_number: Optional[str] = None
    warehouse_ref: Optional[str] = None
    street: Optional[str] = None
    building: Optional[str] = None
    apartment: Optional[str] = None
    postal_code: Optional[str] = None
    is_primary: Optional[bool] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class ClientAddress(ClientAddressBase):
    id: int
    client_id: int
    source: Optional[str] = "manual"
    source_order_id: Optional[int] = None
    fingerprint: Optional[str] = None
    usage_count: int = 0
    last_used_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


# ── Звʼязки клієнтів (родичі/друзі/разом замовляють) ──────────────────────
class ClientRelationBase(BaseModel):
    related_id: int
    relation_type: str = "together"  # together | family | friend | spouse | other
    label: Optional[str] = None
    notes: Optional[str] = None


class ClientRelationCreate(ClientRelationBase):
    pass


class ClientRelationUpdate(BaseModel):
    relation_type: Optional[str] = None
    label: Optional[str] = None
    notes: Optional[str] = None
    confirmed: Optional[bool] = None


class ClientRelation(BaseModel):
    id: int
    client_id: int
    related_id: int
    related_full_name: Optional[str] = None
    related_nickname: Optional[str] = None
    relation_type: str = "together"
    label: Optional[str] = None
    source: Optional[str] = "manual"
    confirmed: bool = False
    notes: Optional[str] = None
    joint_orders: int = 0
    last_order_id: Optional[int] = None
    last_order_date: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


# ── Identity & Aliases (Step 4) ─────────────────────────────────────────────
class ClientAlias(BaseModel):
    id: int
    client_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nickname: Optional[str] = None
    full_raw: Optional[str] = None
    source: Optional[str] = "parser"
    seen_count: int = 1
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None

    class Config:
        from_attributes = True


class ClientAliasCreate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nickname: Optional[str] = None
    full_raw: Optional[str] = None


class ClientFlag(BaseModel):
    id: int
    client_id: int
    flag_type: str
    severity: str = "warn"
    peer_client_ids: Optional[List[int]] = None
    details: Optional[str] = None
    dismissed: bool = False
    dismissed_at: Optional[str] = None
    dismissed_by: Optional[str] = None
    created_at: Optional[str] = None
    # Hydrated for UI:
    peer_clients: Optional[List[dict]] = None  # [{id, full_name, nickname}]

    class Config:
        from_attributes = True


class ClientMergeRequest(BaseModel):
    """POST /api/clients/{id}/merge — об'єднати source у target.
    target = клієнт, що залишиться; source = той, чиї дані переносяться і він видаляється.
    """
    target_id: int  # хто залишиться


class ClientFlagDismiss(BaseModel):
    note: Optional[str] = None  # причина (опц.): "це різні люди"
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Date, func
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base

# Reference tables
class Gender(Base):
    __tablename__ = "genders"
    
    id = Column(Integer, primary_key=True, index=True)
    gendername = Column(String(100), unique=True, nullable=False)
    
    # Relationships
    clients = relationship("Client", back_populates="gender")

class Brand(Base):
    __tablename__ = "brands"
    
    id = Column(Integer, primary_key=True, index=True)
    brandname = Column(String(100), unique=True, nullable=False)

class Type(Base):
    __tablename__ = "types"
    
    id = Column(Integer, primary_key=True, index=True)
    typename = Column(String(100), unique=True, nullable=False)

class Subtype(Base):
    __tablename__ = "subtypes"
    
    id = Column(Integer, primary_key=True, index=True)
    subtypename = Column(String(100), unique=True, nullable=False)
    type_id = Column(Integer, ForeignKey("types.id"), nullable=True)

class Country(Base):
    __tablename__ = "countries"
    
    id = Column(Integer, primary_key=True, index=True)
    countryname = Column(String(100), unique=True, nullable=False)
    countrycode = Column(String(10), nullable=True)

class Status(Base):
    __tablename__ = "statuses"
    
    id = Column(Integer, primary_key=True, index=True)
    statusname = Column(String(100), unique=True, nullable=False)
    statusdescription = Column(Text, nullable=True)

class Color(Base):
    __tablename__ = "colors"
    
    id = Column(Integer, primary_key=True, index=True)
    colorname = Column(String(100), unique=True, nullable=False)

class Condition(Base):
    __tablename__ = "conditions"
    
    id = Column(Integer, primary_key=True, index=True)
    conditionname = Column(String(100), unique=True, nullable=False)
    conditiondescription = Column(Text, nullable=True)

class Client(Base):
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    middle_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    email = Column(String, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    gender_id = Column(Integer, ForeignKey("genders.id"), nullable=True)
    
    # Social media fields
    facebook = Column(String, nullable=True)
    instagram = Column(String, nullable=True)
    telegram = Column(String, nullable=True)
    viber = Column(String, nullable=True)
    messenger = Column(String, nullable=True)
    tiktok = Column(String, nullable=True)
    olx = Column(String, nullable=True)
    
    # Order tracking fields
    first_order_date = Column(Date, nullable=True)
    last_order_date = Column(Date, nullable=True)
    last_order_address_id = Column(Integer, nullable=True)
    order_count = Column(Integer, default=0)
    average_order_value = Column(Float, nullable=True)
    total_order_amount = Column(Float, nullable=True)
    largest_purchase = Column(Float, nullable=True)
    
    # Client management fields
    client_discount = Column(Float, nullable=True)
    bonus_account = Column(Float, nullable=True)
    city_of_residence = Column(String, nullable=True)
    country_of_residence = Column(Integer, ForeignKey("countries.id"), nullable=True)
    preferred_delivery_method_id = Column(Integer, nullable=True)
    preferred_payment_method_id = Column(Integer, nullable=True)
    address_id = Column(Integer, nullable=True)
    client_type_id = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    status_id = Column(Integer, nullable=True)
    priority = Column(Integer, default=0)
    number_of_purchased_lots = Column(Integer, default=0)
    
    # Timestamps
    registration_date = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    gender = relationship("Gender", back_populates="clients")
    orders = relationship("Order", back_populates="client")
    addresses = relationship("Address", back_populates="client")

class DeliveryMethod(Base):
    __tablename__ = "delivery_methods"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color_code = Column(String, default="#808080")
    
    # Relationships
    orders = relationship("Order", back_populates="delivery_method")

class PaymentStatus(Base):
    __tablename__ = "payment_statuses"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color_code = Column(String, default="#808080")
    
    # Relationships
    orders = relationship("Order", back_populates="payment_status_rel")

class OrderStatus(Base):
    __tablename__ = "order_statuses"
    
    id = Column(Integer, primary_key=True, index=True)
    status_name = Column(String, unique=True, index=True)
    
    # Relationships
    orders = relationship("Order", back_populates="order_status")

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    order_date = Column(Date, default=func.current_date(), nullable=False)
    order_status_id = Column(Integer, ForeignKey("order_statuses.id"))
    total_amount = Column(Float, default=0.0, nullable=False)
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"))
    payment_status = Column(String, nullable=True)
    payment_status_id = Column(Integer, ForeignKey("payment_statuses.id"))
    delivery_method_id = Column(Integer, ForeignKey("delivery_methods.id"))
    delivery_address_id = Column(Integer, ForeignKey("addresses.id"))
    tracking_number = Column(String(100))
    delivery_status_id = Column(Integer, ForeignKey("delivery_statuses.id"))
    notes = Column(Text, nullable=True)
    deferred_until = Column(Date)
    priority = Column(Integer, default=0)
    broadcast_id = Column(Integer, ForeignKey("broadcasts.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    client = relationship("Client", back_populates="orders")
    order_status = relationship("OrderStatus", back_populates="orders")
    payment_status_rel = relationship("PaymentStatus", back_populates="orders")
    payment_method = relationship("PaymentMethod", back_populates="payment_orders")
    delivery_method = relationship("DeliveryMethod", back_populates="orders")
    delivery_address = relationship("Address", foreign_keys=[delivery_address_id])
    delivery_status = relationship("DeliveryStatus", back_populates="orders")
    broadcast = relationship("Broadcast", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

class Product(Base):
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True)
    productnumber = Column(String(50), unique=True, index=True, nullable=False)
    clonednumbers = Column(Text)
    model = Column(String(100))
    marking = Column(String(100))
    year = Column(Integer)
    description = Column(Text)
    extranote = Column(Text)
    price = Column(Float, default=0.0)
    oldprice = Column(Float)
    dateadded = Column(Date, default=func.current_date())
    sizeeu = Column(String(20))
    sizeua = Column(String(20))
    sizeusa = Column(String(20))
    sizeuk = Column(String(20))
    sizejp = Column(String(20))
    sizecn = Column(String(20))
    measurementscm = Column(String(20))
    quantity = Column(Integer, default=1)
    mainimage = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Foreign Keys
    typeid = Column(Integer, ForeignKey("types.id"), nullable=True)
    subtypeid = Column(Integer, ForeignKey("subtypes.id"), nullable=True)
    brandid = Column(Integer, ForeignKey("brands.id"), nullable=True)
    genderid = Column(Integer, ForeignKey("genders.id"), nullable=True)
    colorid = Column(Integer, ForeignKey("colors.id"), nullable=True)
    ownercountryid = Column(Integer, ForeignKey("countries.id"), nullable=True)
    manufacturercountryid = Column(Integer, ForeignKey("countries.id"), nullable=True)
    statusid = Column(Integer, ForeignKey("statuses.id"), nullable=True)
    conditionid = Column(Integer, ForeignKey("conditions.id"), nullable=True)
    importid = Column(Integer, nullable=True)
    deliveryid = Column(Integer, nullable=True)
    
    # Relationships
    order_items = relationship("OrderItem", back_populates="product")
    brand = relationship("Brand", foreign_keys=[brandid], primaryjoin="Product.brandid == Brand.id")
    type = relationship("Type", foreign_keys=[typeid], primaryjoin="Product.typeid == Type.id")
    subtype = relationship("Subtype", foreign_keys=[subtypeid], primaryjoin="Product.subtypeid == Subtype.id")
    color = relationship("Color", foreign_keys=[colorid], primaryjoin="Product.colorid == Color.id")
    gender = relationship("Gender", foreign_keys=[genderid], primaryjoin="Product.genderid == Gender.id")
    owner_country = relationship("Country", foreign_keys=[ownercountryid], primaryjoin="Product.ownercountryid == Country.id")
    manufacturer_country = relationship("Country", foreign_keys=[manufacturercountryid], primaryjoin="Product.manufacturercountryid == Country.id")
    status = relationship("Status", foreign_keys=[statusid], primaryjoin="Product.statusid == Status.id")
    condition = relationship("Condition", foreign_keys=[conditionid], primaryjoin="Product.conditionid == Condition.id")

class ParsingSource(Base):
    __tablename__ = "parsing_sources"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    url = Column(String, nullable=False)
    description = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    
    # Relationships
    parsing_logs = relationship("ParsingLog", back_populates="source")

class ParsingStyle(Base):
    __tablename__ = "parsing_styles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    include_images = Column(Boolean, default=True)
    deep_details = Column(Boolean, default=False)
    
class ParsingLog(Base):
    __tablename__ = "parsing_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("parsing_sources.id"))
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    items_processed = Column(Integer, default=0)
    items_added = Column(Integer, default=0)
    items_updated = Column(Integer, default=0)
    items_failed = Column(Integer, default=0)
    status = Column(String, default="in_progress")  # in_progress, completed, failed, cancelled
    message = Column(Text, nullable=True)
    
    # Relationships
    source = relationship("ParsingSource", back_populates="parsing_logs")

class ParsingSchedule(Base):
    __tablename__ = "parsing_schedules"
    
    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("parsing_sources.id"))
    style_id = Column(Integer, ForeignKey("parsing_styles.id"))
    frequency = Column(String, nullable=False)  # daily, weekly, monthly
    time_of_day = Column(String, nullable=False)  # HH:MM format
    days_of_week = Column(String, nullable=True)  # For weekly: mon,tue,wed,etc
    day_of_month = Column(Integer, nullable=True)  # For monthly
    enabled = Column(Boolean, default=True)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)

class ParsingJob(Base):
    __tablename__ = "parsing_jobs"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String, nullable=False)
    status = Column(String, default="queued")
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    total_items = Column(Integer, nullable=True)
    processed_items = Column(Integer, default=0)
    percent = Column(Integer, default=0)
    items_per_sec = Column(Float, nullable=True)
    eta_seconds = Column(Integer, nullable=True)
    current_step = Column(String, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    error_summary = Column(Text, nullable=True)
    logs_head = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, default=False)

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    price = Column(Float, default=0.0, nullable=False)
    discount_type = Column(String(50))
    discount_value = Column(Float)
    additional_operation = Column(String(100))
    additional_operation_value = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color_code = Column(String, default="#808080")
    
    # Relationships
    payment_orders = relationship("Order", back_populates="payment_method")

class Address(Base):
    __tablename__ = "addresses"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    city = Column(String(100))
    street = Column(String(100))
    building = Column(String(20))
    apartment = Column(String(20))
    postal_code = Column(String(20))
    is_default = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    client = relationship("Client", back_populates="addresses")

class DeliveryStatus(Base):
    __tablename__ = "delivery_statuses"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color_code = Column(String, default="#808080")
    
    # Relationships
    orders = relationship("Order", back_populates="delivery_status")

class Broadcast(Base):
    __tablename__ = "broadcasts"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(Text)
    start_date = Column(Date)
    end_date = Column(Date)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    orders = relationship("Order", back_populates="broadcast") 

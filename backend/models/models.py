from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Date, func
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base

class Gender(Base):
    __tablename__ = "genders"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    # Relationships
    clients = relationship("Client", back_populates="gender")
    products = relationship("Product", back_populates="gender")

class Client(Base):
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone_number = Column(String)
    email = Column(String)
    date_of_birth = Column(Date, nullable=True)
    gender_id = Column(Integer, ForeignKey("genders.id"))
    address = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
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
    name = Column(String, unique=True, index=True)
    color_code = Column(String, default="#808080")
    
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
    payment_status_text = Column(String(50))
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
    productnumber = Column(String(50), unique=True, nullable=False)
    clonednumbers = Column(Text, nullable=True)
    model = Column(String(500), nullable=True)
    marking = Column(String(500), nullable=True)
    year = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    extranote = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    oldprice = Column(Float, nullable=True)
    dateadded = Column(DateTime, default=datetime.utcnow)
    sizeeu = Column(String(50), nullable=True)
    sizeua = Column(String(50), nullable=True)
    sizeusa = Column(String(50), nullable=True)
    sizeuk = Column(String(10), nullable=True)
    sizejp = Column(String(10), nullable=True)
    sizecn = Column(String(10), nullable=True)
    measurementscm = Column(String(50), nullable=True)
    quantity = Column(Integer, default=1)
    mainimage = Column(String(255), nullable=True)
    is_visible = Column(Boolean, default=True)  # Видимість товару
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
    
    # Foreign keys
    typeid = Column(Integer, ForeignKey("types.id"), nullable=True)
    subtypeid = Column(Integer, ForeignKey("subtypes.id"), nullable=True)
    brandid = Column(Integer, ForeignKey("brands.id"), nullable=True)
    genderid = Column(Integer, ForeignKey("genders.id"), nullable=True)
    colorid = Column(Integer, ForeignKey("colors.id"), nullable=True)
    ownercountryid = Column(Integer, ForeignKey("countries.id"), nullable=True)
    manufacturercountryid = Column(Integer, ForeignKey("countries.id"), nullable=True)
    statusid = Column(Integer, ForeignKey("statuses.id"), nullable=True)
    conditionid = Column(Integer, ForeignKey("conditions.id"), nullable=True)
    importid = Column(Integer, ForeignKey("imports.id"), nullable=True)
    deliveryid = Column(Integer, ForeignKey("deliveries.id"), nullable=True)
    
    # Relationships
    type = relationship("Type", back_populates="products")
    subtype = relationship("Subtype", back_populates="products")
    brand = relationship("Brand", back_populates="products")
    gender = relationship("Gender", back_populates="products")
    color = relationship("Color", back_populates="products")
    owner_country = relationship("Country", foreign_keys=[ownercountryid], back_populates="owner_products")
    manufacturer_country = relationship("Country", foreign_keys=[manufacturercountryid], back_populates="manufacturer_products")
    status = relationship("Status", back_populates="products")
    condition = relationship("Condition", back_populates="products")
    import_record = relationship("Import", back_populates="products")
    delivery = relationship("Delivery", back_populates="products")
    order_items = relationship("OrderItem", back_populates="product")

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

# Додаємо нові моделі для довідників
class Type(Base):
    __tablename__ = "types"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="type")

class Subtype(Base):
    __tablename__ = "subtypes"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="subtype")

class Brand(Base):
    __tablename__ = "brands"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="brand")

class Color(Base):
    __tablename__ = "colors"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="color")

class Country(Base):
    __tablename__ = "countries"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    owner_products = relationship("Product", foreign_keys="Product.ownercountryid", back_populates="owner_country")
    manufacturer_products = relationship("Product", foreign_keys="Product.manufacturercountryid", back_populates="manufacturer_country")

class Status(Base):
    __tablename__ = "statuses"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="status")

class Condition(Base):
    __tablename__ = "conditions"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="condition")

class Import(Base):
    __tablename__ = "imports"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="import_record")

class Delivery(Base):
    __tablename__ = "deliveries"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    products = relationship("Product", back_populates="delivery")

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
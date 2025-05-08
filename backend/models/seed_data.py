import logging
import random
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select

from backend.models.models import (
    OrderStatus, PaymentStatus, DeliveryMethod, 
    Client, Product, Order, OrderItem, Gender,
    Type, Subtype, Brand, Color, Country,
    Status, Condition, Import, Delivery,
    Base, ParsingSource, ParsingStyle
)
from backend.models.database import engine

logger = logging.getLogger(__name__)

def populate_initial_data(db: Session):
    """Populate the database with initial reference data"""
    # Check if data already exists
    order_status_count = db.query(OrderStatus).count()
    if order_status_count > 0:
        logger.info("Initial reference data already exists, skipping...")
        
        # Check if parsing source exists
        parsing_source_count = db.query(ParsingSource).count()
        if parsing_source_count == 0:
            logger.info("Adding missing parsing source...")
            # Create a parsing source for Google Sheets
            parsing_source = ParsingSource(
                name="Google Sheets",
                url="https://docs.google.com/spreadsheets",
                description="Google Sheets Data Source",
                enabled=True
            )
            db.add(parsing_source)
            
            # Create parsing styles
            parsing_styles = [
                ParsingStyle(
                    name="Orders Import", 
                    description="Import orders from Google Sheets",
                    include_images=False,
                    deep_details=False
                ),
                ParsingStyle(
                    name="Products Import", 
                    description="Import products from Google Sheets",
                    include_images=True,
                    deep_details=True
                )
            ]
            db.add_all(parsing_styles)
            
            try:
                db.commit()
                logger.info("Added missing parsing source and styles")
            except IntegrityError as e:
                db.rollback()
                logger.error(f"Error adding parsing source: {e}")
        
        return

    logger.info("Populating database with basic reference data only (no test data)")
    
    # Create order statuses
    order_statuses = [
        OrderStatus(status_name="Нове"),
        OrderStatus(status_name="В обробці"),
        OrderStatus(status_name="Доставляється"),
        OrderStatus(status_name="Доставлено"),
        OrderStatus(status_name="Скасовано"),
    ]
    db.add_all(order_statuses)
    
    # Create payment statuses
    payment_statuses = [
        PaymentStatus(name="Оплачено", color_code="#28a745"),
        PaymentStatus(name="Частково оплачено", color_code="#fd7e14"),
        PaymentStatus(name="Очікує оплати", color_code="#ffc107"),
        PaymentStatus(name="Не оплачено", color_code="#dc3545"),
    ]
    db.add_all(payment_statuses)
    
    # Create delivery methods
    delivery_methods = [
        DeliveryMethod(name="Самовивіз", color_code="#6c757d"),
        DeliveryMethod(name="Нова пошта", color_code="#007bff"),
        DeliveryMethod(name="Укрпошта", color_code="#fd7e14"),
        DeliveryMethod(name="Кур'єр", color_code="#28a745"),
    ]
    db.add_all(delivery_methods)
    
    # Create gender options
    genders = [
        Gender(name="Чоловіча"),
        Gender(name="Жіноча"),
        Gender(name="Унісекс"),
    ]
    db.add_all(genders)
    
    # Create product types
    product_types = ["Футболка", "Джинси", "Куртка", "Сукня", "Шорти", "Светр", "Пальто"]
    db.add_all([Type(name=name) for name in product_types])
    
    # Create product subtypes
    product_subtypes = ["Літня", "Зимова", "Базова", "Спортивна", "Повсякденна", "Святкова", "Ділова"]
    db.add_all([Subtype(name=name) for name in product_subtypes])
    
    # Create brands
    product_brands = ["Nike", "Adidas", "Zara", "H&M", "Puma", "Levi's", "Calvin Klein", "Gucci", "Versace"]
    db.add_all([Brand(name=name) for name in product_brands])
    
    # Create colors
    product_colors = ["Червоний", "Синій", "Чорний", "Білий", "Зелений", "Жовтий", "Сірий", "Рожевий", "Фіолетовий"]
    db.add_all([Color(name=name) for name in product_colors])
    
    # Create countries
    product_countries = ["Україна", "Китай", "Італія", "Франція", "Польща", "Туреччина", "США", "Іспанія"]
    db.add_all([Country(name=name) for name in product_countries])
    
    # Create statuses
    product_statuses = ["В наявності", "Закінчується", "Немає в наявності", "Під замовлення"]
    db.add_all([Status(name=name) for name in product_statuses])
    
    # Create conditions
    product_conditions = ["Нове", "Ідеальний стан", "Добрий стан", "Задовільний стан"]
    db.add_all([Condition(name=name) for name in product_conditions])
    
    # Create import statuses
    import_statuses = ["Imported", "Pending", "Rejected"]
    db.add_all([Import(name=name) for name in import_statuses])
    
    # Create deliveries
    delivery_types = ["Express", "Standard", "Economy"]
    db.add_all([Delivery(name=name) for name in delivery_types])
    
    # Create a parsing source for Google Sheets
    parsing_source = ParsingSource(
        name="Google Sheets",
        url="https://docs.google.com/spreadsheets",
        description="Google Sheets Data Source",
        enabled=True
    )
    db.add(parsing_source)
    
    # Create parsing styles
    parsing_styles = [
        ParsingStyle(
            name="Orders Import", 
            description="Import orders from Google Sheets",
            include_images=False,
            deep_details=False
        ),
        ParsingStyle(
            name="Products Import", 
            description="Import products from Google Sheets",
            include_images=True,
            deep_details=True
        )
    ]
    db.add_all(parsing_styles)
    
    try:
        db.commit()
        logger.info("Reference data has been populated")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Error populating reference data: {e}")
        return
    
    # Note: No longer adding test data
    # populate_test_data(db)

def populate_test_data(db: Session):
    """Populate the database with test data: products, clients, orders"""
    logger.info("Populating database with test data")
    
    # Create some clients
    clients = []
    for i in range(50):
        client = Client(
            first_name=f"Ім'я{i+1}",
            last_name=f"Прізвище{i+1}",
            phone_number=f"+380{random.randint(950000000, 999999999)}",
            email=f"client{i+1}@example.com",
            address=f"вул. Тестова, {i+1}, м. Київ",
            gender_id=random.randint(1, 3),
            notes=f"Тестовий клієнт {i+1}"
        )
        clients.append(client)
    
    db.add_all(clients)
    try:
        db.commit()
        logger.info(f"Added {len(clients)} test clients")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Error adding test clients: {e}")
        return
    
    # Get reference data IDs for use in product creation
    types = db.query(Type).all()
    subtypes = db.query(Subtype).all()
    brands = db.query(Brand).all()
    colors = db.query(Color).all()
    countries = db.query(Country).all()
    statuses = db.query(Status).all()
    conditions = db.query(Condition).all()
    imports = db.query(Import).all()
    deliveries = db.query(Delivery).all()
    genders = db.query(Gender).all()
    
    # Create some products
    products = []
    for i in range(200):
        type_obj = random.choice(types)
        subtype_obj = random.choice(subtypes)
        brand_obj = random.choice(brands)
        color_obj = random.choice(colors)
        country_obj = random.choice(countries)
        status_obj = random.choice(statuses)
        condition_obj = random.choice(conditions)
        import_obj = random.choice(imports)
        delivery_obj = random.choice(deliveries)
        gender_obj = random.choice(genders)
        
        price = random.randint(300, 5000)
        old_price = price + random.randint(100, 1000) if random.random() > 0.7 else None
        
        product = Product(
            productnumber=f"P{i+1:05d}",
            model=f"{brand_obj.name} {type_obj.name} {subtype_obj.name}",
            price=price,
            oldprice=old_price,
            quantity=random.randint(0, 50),
            mainimage=None,
            description=f"Опис товару {type_obj.name} {subtype_obj.name} від {brand_obj.name}. Колір: {color_obj.name}, Країна: {country_obj.name}. Товар має гарну якість та стильний дизайн.",
            typeid=type_obj.id,
            subtypeid=subtype_obj.id,
            brandid=brand_obj.id,
            genderid=gender_obj.id,
            colorid=color_obj.id,
            ownercountryid=country_obj.id,
            manufacturercountryid=country_obj.id,
            statusid=status_obj.id,
            conditionid=condition_obj.id,
            importid=import_obj.id,
            deliveryid=delivery_obj.id,
            is_visible=True
        )
        products.append(product)
    
    db.add_all(products)
    try:
        db.commit()
        logger.info(f"Added {len(products)} test products")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Error adding test products: {e}")
        return
    
    # Create some orders
    orders = []
    
    # Підготуємо всі замовлення спочатку
    for i in range(100):
        client_id = random.randint(1, len(clients))
        order_date = datetime.now() - timedelta(days=random.randint(0, 365))
        delivery_date = order_date + timedelta(days=random.randint(1, 14)) if random.random() > 0.2 else None
        
        order = Order(
            client_id=client_id,
            order_status_id=random.randint(1, 5),
            payment_status_id=random.randint(1, 4),
            delivery_method_id=random.randint(1, 4),
            order_date=order_date,
            delivery_date=delivery_date,
            notes=f"Тестове замовлення {i+1}"
        )
        orders.append(order)
    
    # Додамо всі замовлення разом
    db.add_all(orders)
    db.flush()  # Для отримання ID замовлень
    
    # Тепер додамо товари до замовлень
    all_order_items = []
    for order in orders:
        # Вибираємо випадкову кількість товарів для замовлення
        num_items = random.randint(1, 5)
        selected_products = random.sample(range(1, len(products) + 1), min(num_items, len(products)))
        
        total_price = 0
        for product_id in selected_products:
            quantity = random.randint(1, 3)
            product = db.query(Product).filter(Product.id == product_id).first()
            if product:  # Перевіряємо, що продукт існує
                price = product.price
                
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=product_id,
                    quantity=quantity,
                    price=price
                )
                all_order_items.append(order_item)
                total_price += price * quantity
        
        # Оновлюємо загальну суму замовлення
        order.total = total_price
    
    # Додаємо всі елементи замовлень разом
    db.add_all(all_order_items)
    
    try:
        db.commit()
        logger.info(f"Added {len(orders)} test orders with items")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Error adding test orders: {e}")

def reset_database(db: Session):
    """Clear all data from the database and re-initialize"""
    logger.warning("Resetting database - all data will be lost!")
    
    try:
        # Скидання і створення всіх таблиць
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        
        logger.info("Database reset complete")
        
        # Re-populate with fresh data
        populate_initial_data(db)
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting database: {e}") 
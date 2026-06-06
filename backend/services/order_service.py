from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, extract, func
from typing import List, Optional, Dict, Any
from datetime import date, datetime
import logging

from sqlalchemy import text
from models.models import (
    Order, OrderItem, Client, Product,
    OrderStatus, PaymentMethod,
    DeliveryMethod, DeliveryStatus, Address,
    Broadcast
)
from schemas.order import OrderCreate, OrderUpdate, OrderFilters

logger = logging.getLogger(__name__)

# Order fields that, when edited in the app, get locked against parser overwrite
# (orders parser restores them on reparse). Keep in sync with ORDER_LOCK_FIELDS
# in sheets_parser and the order edit UI.
LOCKABLE_ORDER_FIELDS = {
    "notes", "tracking_number", "sales_channel",
    "order_status_id", "payment_status_id", "delivery_method_id",
}


class OrderDAO:
    """Data Access Object for Orders"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def get_all_orders(self, skip: int = 0, limit: int = 100) -> List[Order]:
        """Get all orders with pagination"""
        return self.db.query(Order).order_by(Order.created_at.desc()).offset(skip).limit(limit).all()
    
    def get_order_by_id(self, order_id: int) -> Optional[Order]:
        """Get a specific order by ID with all related data"""
        return (
            self.db.query(Order)
            .filter(Order.id == order_id)
            .options(
                joinedload(Order.client),
                joinedload(Order.order_status),
                # Do not eager load payment_status_rel due to schema mismatch
                joinedload(Order.payment_method),
                joinedload(Order.delivery_method),
                joinedload(Order.delivery_status),
                joinedload(Order.delivery_address),
                joinedload(Order.broadcast),
                joinedload(Order.items).joinedload(OrderItem.product),
            )
            .first()
        )
    
    def get_orders_by_client_id(self, client_id: int) -> List[Order]:
        """Get all orders for a specific client"""
        return self.db.query(Order).filter(Order.client_id == client_id).all()
    
    def create_order(self, order_data: Dict[str, Any]) -> Order:
        """Create a new order with order items"""
        order_items_data = order_data.pop('order_items', [])
        
        # Create the order
        new_order = Order(**order_data)
        self.db.add(new_order)
        self.db.flush()  # Get the order ID
        
        # Create order items
        for item_data in order_items_data:
            item_data['order_id'] = new_order.id
            new_item = OrderItem(**item_data)
            self.db.add(new_item)
        
        self.db.commit()
        return new_order
    
    def update_order(self, order_id: int, order_data: Dict[str, Any]) -> Optional[Order]:
        """Update an existing order with its items"""
        order_items_data = order_data.pop('order_items', None)
        
        # Get the order
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return None
        
        # Update order fields. Lock ONLY fields that actually CHANGED — the edit
        # modal sends the whole form, so locking every sent lockable field would
        # over-lock (freeze status/payment from the parser even if untouched).
        newly_locked = set()
        for key, value in order_data.items():
            if hasattr(order, key):
                if key in LOCKABLE_ORDER_FIELDS and value != getattr(order, key):
                    newly_locked.add(key)
                setattr(order, key, value)

        # Record locks so the orders parser won't overwrite them on reparse
        # (snapshot-restore). Mirrors products.manually_edited_*.
        if newly_locked:
            existing = set()
            if order.manually_edited_fields:
                existing = {x.strip() for x in order.manually_edited_fields.split(",") if x.strip()}
            order.manually_edited_fields = ",".join(sorted(existing | newly_locked))
            order.manually_edited_at = datetime.utcnow()

        # Update order items if provided
        if order_items_data is not None:
            # Delete existing items
            self.db.query(OrderItem).filter(OrderItem.order_id == order_id).delete()
            
            # Add new items
            for item_data in order_items_data:
                item_data['order_id'] = order_id
                new_item = OrderItem(**item_data)
                self.db.add(new_item)
        
        self.db.commit()
        return order
    
    def delete_order(self, order_id: int) -> bool:
        """Delete an order by ID"""
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if order:
            self.db.delete(order)
            self.db.commit()
            return True
        return False
    
    def get_orders_with_filters(
        self, 
        filters: OrderFilters, 
        page: int = 1, 
        per_page: int = 20,
        sort_by: str = "order_date",
        sort_dir: str = "desc",
    ) -> Dict[str, Any]:
        """Get orders with filters and pagination"""
        # Build base query with joins
        query = self.db.query(Order).\
            join(Client, Order.client_id == Client.id)
            
        # Apply optional joins based on filters
        if filters.order_status_ids:
            query = query.join(OrderStatus, Order.order_status_id == OrderStatus.id)
            
        # avoid joining payment_statuses table because of column-name mismatch in DB

        # Skip joins to payment/delivery reference tables due to schema mismatch in DB
            
        # Add eager loading for related entities
        query = query.options(
            joinedload(Order.client),
            joinedload(Order.order_status),
            joinedload(Order.items),
        )
        
        # Apply filters
        if filters.search:
            search_term = f"%{filters.search}%"
            query = query.filter(
                or_(
                    Client.first_name.ilike(search_term),
                    Client.last_name.ilike(search_term),
                    Client.phone_number.ilike(search_term),
                    Client.email.ilike(search_term),
                    Order.tracking_number.ilike(search_term),
                    Order.notes.ilike(search_term)
                )
            )
        
        if filters.client_id:
            query = query.filter(Order.client_id == filters.client_id)
            
        if filters.order_status_ids:
            query = query.filter(Order.order_status_id.in_(filters.order_status_ids))
            
        if filters.payment_status_ids:
            query = query.filter(Order.payment_status_id.in_(filters.payment_status_ids))
            
        if filters.payment_method_ids:
            query = query.filter(Order.payment_method_id.in_(filters.payment_method_ids))
            
        if filters.delivery_method_ids:
            query = query.filter(Order.delivery_method_id.in_(filters.delivery_method_ids))
            
        if filters.delivery_status_ids:
            query = query.filter(Order.delivery_status_id.in_(filters.delivery_status_ids))
            
        if filters.date_from:
            query = query.filter(Order.order_date >= filters.date_from)
            
        if filters.date_to:
            query = query.filter(Order.order_date <= filters.date_to)
            
        if filters.month_min is not None:
            query = query.filter(extract('month', Order.order_date) >= filters.month_min)
            
        if filters.month_max is not None:
            query = query.filter(extract('month', Order.order_date) <= filters.month_max)
            
        if filters.year_min is not None:
            query = query.filter(extract('year', Order.order_date) >= filters.year_min)
            
        if filters.year_max is not None:
            query = query.filter(extract('year', Order.order_date) <= filters.year_max)
            
        if filters.priority_min is not None:
            query = query.filter(Order.priority >= filters.priority_min)
            
        if filters.priority_max is not None:
            query = query.filter(Order.priority <= filters.priority_max)
            
        if filters.has_tracking is not None:
            if filters.has_tracking:
                query = query.filter(Order.tracking_number != None, Order.tracking_number != '')
            else:
                query = query.filter(or_(Order.tracking_number == None, Order.tracking_number == ''))
            
        if filters.is_deferred:
            query = query.filter(Order.deferred_until != None)

        if hasattr(filters, 'amount_min') and filters.amount_min is not None:
            query = query.filter(Order.total_amount >= filters.amount_min)
        if hasattr(filters, 'amount_max') and filters.amount_max is not None:
            query = query.filter(Order.total_amount <= filters.amount_max)

        if hasattr(filters, 'sales_channels') and filters.sales_channels:
            query = query.filter(Order.sales_channel.in_(filters.sales_channels))

        # Get total count for pagination
        total = query.count()

        # Apply ordering
        allowed = {
            "id": Order.id,
            "order_date": Order.order_date,
            "total_amount": Order.total_amount,
            "priority": Order.priority,
            "client_name": Client.first_name,
        }
        sort_col = allowed.get(sort_by, Order.order_date)
        if sort_dir.lower() == 'asc':
            query = query.order_by(sort_col.asc(), Order.id.asc())
        else:
            query = query.order_by(sort_col.desc(), Order.id.desc())

        # Apply pagination
        query = query.offset((page - 1) * per_page).limit(per_page)
        
        # Get orders
        orders = query.all()
        
        # Calculate total pages
        pages = (total + per_page - 1) // per_page if total > 0 else 1
        
        return {
            "items": orders,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages
        }
        
    def get_filter_options(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all options for order filters.

        Примітка: через відмінності в схемі БД не використовуємо таблиці оплат/доставки.
        Використовуємо сирий SQL з реальними назвами колонок, щоб повернути коректні
        опції фільтрів.
        """
        order_statuses = self.db.query(OrderStatus).all()
        order_statuses_list = [{"id": s.id, "status_name": s.status_name} for s in order_statuses]
        # NB: «Невідомо» (порожній статус, order_status_id IS NULL) додається ЛИШЕ у
        # фільтрі замовлень на фронті (id=0), щоб НЕ засмічувати edit-дропдани статусу.
        # Роутер /api/orders мапить order_status_ids=0 → order_status_id IS NULL.

        # payment_statuses: id, status_name
        ps = self.db.execute(text("SELECT id, status_name FROM payment_statuses ORDER BY id")).fetchall()
        payment_statuses_list = [{"id": r[0], "name": r[1]} for r in ps]

        # payment_methods: id, method_name
        pm = self.db.execute(text("SELECT id, method_name FROM payment_methods ORDER BY id")).fetchall()
        payment_methods_list = [{"id": r[0], "name": r[1]} for r in pm]

        # delivery_methods: id, method_name
        dm = self.db.execute(text("SELECT id, method_name FROM delivery_methods WHERE method_name != 'ㅤ' ORDER BY id")).fetchall()
        delivery_methods_list = [{"id": r[0], "name": r[1]} for r in dm]

        # delivery_statuses: id, status_name
        ds = self.db.execute(text("SELECT id, status_name FROM delivery_statuses ORDER BY id")).fetchall()
        delivery_statuses_list = [{"id": r[0], "name": r[1]} for r in ds]

        clients = self.db.query(Client).order_by(Client.updated_at.desc()).limit(100).all()
        clients_list = [{"id": c.id, "name": f"{c.first_name or ''} {c.last_name or ''}".strip()} for c in clients]

        return {
            "order_statuses": order_statuses_list,
            "payment_statuses": payment_statuses_list,
            "payment_methods": payment_methods_list,
            "delivery_methods": delivery_methods_list,
            "delivery_statuses": delivery_statuses_list,
            "clients": clients_list,
        }
    
    def recalculate_order_total(self, order_id: int) -> float:
        """Recalculate the total amount of an order based on its items"""
        items = self.db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
        
        total_amount = 0.0
        for item in items:
            item_total = item.price * item.quantity
            
            # Apply discount
            if item.discount_type == 'Відсоток' and item.discount_value:
                item_total = item_total * (1 - item.discount_value / 100)
            elif item.discount_type == 'Фіксована' and item.discount_value:
                item_total = item_total - item.discount_value
                
            # Apply additional operation
            if item.additional_operation_value:
                item_total += item.additional_operation_value
                
            total_amount += item_total
        
        # Update order total
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if order:
            order.total_amount = total_amount
            self.db.commit()
            
        return total_amount 
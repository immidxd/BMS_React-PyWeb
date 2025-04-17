from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, extract, func
from typing import List, Optional, Dict, Any
from datetime import date, datetime
import logging

from backend.models.models import (
    Order, OrderItem, Client, Product, 
    OrderStatus, PaymentStatus, PaymentMethod, 
    DeliveryMethod, DeliveryStatus, Address,
    Broadcast
)
from backend.schemas.order import OrderCreate, OrderUpdate, OrderFilters

logger = logging.getLogger(__name__)

class OrderDAO:
    """Data Access Object for Orders"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def get_all_orders(self, skip: int = 0, limit: int = 100) -> List[Order]:
        """Get all orders with pagination"""
        return self.db.query(Order).order_by(Order.created_at.desc()).offset(skip).limit(limit).all()
    
    def get_order_by_id(self, order_id: int) -> Optional[Order]:
        """Get a specific order by ID with all related data"""
        return self.db.query(Order).\
            filter(Order.id == order_id).\
            options(
                joinedload(Order.client),
                joinedload(Order.order_status),
                joinedload(Order.payment_status_rel),
                joinedload(Order.payment_method),
                joinedload(Order.delivery_method),
                joinedload(Order.delivery_status),
                joinedload(Order.delivery_address),
                joinedload(Order.broadcast),
                joinedload(Order.items).joinedload(OrderItem.product)
            ).first()
    
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
        
        # Update order fields
        for key, value in order_data.items():
            if hasattr(order, key):
                setattr(order, key, value)
        
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
        per_page: int = 20
    ) -> Dict[str, Any]:
        """Get orders with filters and pagination"""
        # Build base query with joins
        query = self.db.query(Order).\
            join(Client, Order.client_id == Client.id)
            
        # Apply optional joins based on filters
        if filters.order_status_ids:
            query = query.join(OrderStatus, Order.order_status_id == OrderStatus.id)
            
        if filters.payment_status_ids or filters.payment_method_ids:
            query = query.join(PaymentStatus, Order.payment_status_id == PaymentStatus.id, isouter=True)
            
        if filters.payment_method_ids:
            query = query.join(PaymentMethod, Order.payment_method_id == PaymentMethod.id, isouter=True)
            
        if filters.delivery_method_ids:
            query = query.join(DeliveryMethod, Order.delivery_method_id == DeliveryMethod.id, isouter=True)
            
        if filters.delivery_status_ids:
            query = query.join(DeliveryStatus, Order.delivery_status_id == DeliveryStatus.id, isouter=True)
            
        # Add eager loading for related entities
        query = query.options(
            joinedload(Order.client),
            joinedload(Order.order_status),
            joinedload(Order.payment_status_rel),
            joinedload(Order.payment_method),
            joinedload(Order.delivery_method),
            joinedload(Order.delivery_status),
            joinedload(Order.items)
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
            
        if filters.has_tracking:
            query = query.filter(Order.tracking_number != None)
            
        if filters.is_deferred:
            query = query.filter(Order.deferred_until != None)
            
        # Get total count for pagination
        total = query.count()
        
        # Apply pagination and ordering
        query = query.order_by(Order.order_date.desc())
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
        """Get all options for order filters"""
        # Get order statuses
        order_statuses = self.db.query(OrderStatus).all()
        order_statuses_list = [{"id": status.id, "name": status.name, "color": status.color_code} 
                              for status in order_statuses]
        
        # Get payment statuses
        payment_statuses = self.db.query(PaymentStatus).all()
        payment_statuses_list = [{"id": status.id, "name": status.name, "color": status.color_code}
                               for status in payment_statuses]
        
        # Get payment methods
        payment_methods = self.db.query(PaymentMethod).all()
        payment_methods_list = [{"id": method.id, "name": method.name, "color": method.color_code}
                              for method in payment_methods]
        
        # Get delivery methods
        delivery_methods = self.db.query(DeliveryMethod).all()
        delivery_methods_list = [{"id": method.id, "name": method.name, "color": method.color_code}
                               for method in delivery_methods]
        
        # Get delivery statuses
        delivery_statuses = self.db.query(DeliveryStatus).all()
        delivery_statuses_list = [{"id": status.id, "name": status.name, "color": status.color_code}
                                for status in delivery_statuses]
        
        # Get clients (limited to most recent ones to avoid too much data)
        clients = self.db.query(Client).order_by(Client.updated_at.desc()).limit(100).all()
        clients_list = [{"id": client.id, "name": f"{client.first_name} {client.last_name}"}
                       for client in clients]
        
        return {
            "order_statuses": order_statuses_list,
            "payment_statuses": payment_statuses_list,
            "payment_methods": payment_methods_list,
            "delivery_methods": delivery_methods_list,
            "delivery_statuses": delivery_statuses_list,
            "clients": clients_list
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
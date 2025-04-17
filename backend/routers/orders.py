from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from datetime import date, datetime
import logging

from backend.models.database import get_db
from backend.models.models import Order, OrderItem, Client, Product
from backend.services.order_service import OrderDAO
from backend.schemas.order import (
    OrderCreate, OrderUpdate, OrderResponse, OrderWithDetails, 
    OrderList, OrderFilters, FilterOptions, OrderListItem
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/api/orders", response_model=OrderList)
async def get_orders(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    order_status_ids: Optional[List[int]] = Query(None),
    payment_status_ids: Optional[List[int]] = Query(None),
    payment_method_ids: Optional[List[int]] = Query(None),
    delivery_method_ids: Optional[List[int]] = Query(None),
    delivery_status_ids: Optional[List[int]] = Query(None),
    client_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    month_min: Optional[int] = Query(None),
    month_max: Optional[int] = Query(None),
    year_min: Optional[int] = Query(None),
    year_max: Optional[int] = Query(None),
    priority_min: Optional[int] = Query(None),
    priority_max: Optional[int] = Query(None),
    has_tracking: Optional[bool] = Query(None),
    is_deferred: Optional[bool] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Get a paginated list of orders with filtering options
    """
    # Create filter object
    filters = OrderFilters(
        search=search,
        order_status_ids=order_status_ids,
        payment_status_ids=payment_status_ids,
        payment_method_ids=payment_method_ids,
        delivery_method_ids=delivery_method_ids,
        delivery_status_ids=delivery_status_ids,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
        month_min=month_min,
        month_max=month_max,
        year_min=year_min,
        year_max=year_max,
        priority_min=priority_min,
        priority_max=priority_max,
        has_tracking=has_tracking,
        is_deferred=is_deferred
    )
    
    # Use the DAO to get filtered orders
    order_dao = OrderDAO(db)
    result = order_dao.get_orders_with_filters(filters, page, per_page)
    
    # Transform ORM objects to API response
    items = []
    for order in result["items"]:
        # Get client name
        client_name = f"{order.client.first_name} {order.client.last_name}" if order.client else "Unknown"
        
        # Get order status name
        order_status_name = order.order_status.name if order.order_status else None
        
        # Get payment status name
        payment_status_name = order.payment_status_rel.name if order.payment_status_rel else None
        
        # Get payment method name
        payment_method_name = order.payment_method.name if order.payment_method else None
        
        # Get delivery method name
        delivery_method_name = order.delivery_method.name if order.delivery_method else None
        
        # Get delivery status name
        delivery_status_name = order.delivery_status.name if order.delivery_status else None
        
        # Get broadcast name
        broadcast_name = order.broadcast.name if order.broadcast else None
        
        # Prepare order items
        order_items = []
        for item in order.items:
            # Get product details
            product_number = item.product.productnumber if item.product else "Unknown"
            product_name = f"{item.product.model or ''} {item.product.marking or ''}".strip() or "Unknown"
            
            order_items.append({
                "id": item.id,
                "order_id": item.order_id,
                "product_id": item.product_id,
                "product_number": product_number,
                "product_name": product_name,
                "quantity": item.quantity,
                "price": item.price,
                "discount_type": item.discount_type,
                "discount_value": item.discount_value,
                "additional_operation": item.additional_operation,
                "additional_operation_value": item.additional_operation_value,
                "notes": item.notes,
                "created_at": item.created_at,
                "updated_at": item.updated_at
            })
        
        # Create delivery address details
        address_details = None
        if order.delivery_address:
            address = order.delivery_address
            address_details = {
                "id": address.id,
                "city": address.city,
                "street": address.street,
                "building": address.building,
                "apartment": address.apartment,
                "postal_code": address.postal_code,
                "notes": address.notes
            }
        
        # Create order details
        order_dict = {
            "id": order.id,
            "client_id": order.client_id,
            "client_name": client_name,
            "order_date": order.order_date,
            "order_status_id": order.order_status_id,
            "order_status_name": order_status_name,
            "payment_status_id": order.payment_status_id,
            "payment_status_name": payment_status_name,
            "payment_status": order.payment_status,
            "payment_method_id": order.payment_method_id,
            "payment_method_name": payment_method_name,
            "delivery_method_id": order.delivery_method_id,
            "delivery_method_name": delivery_method_name,
            "delivery_status_id": order.delivery_status_id,
            "delivery_status_name": delivery_status_name,
            "delivery_address_id": order.delivery_address_id,
            "delivery_address_details": address_details,
            "tracking_number": order.tracking_number,
            "total_amount": order.total_amount,
            "notes": order.notes,
            "deferred_until": order.deferred_until,
            "priority": order.priority,
            "broadcast_id": order.broadcast_id,
            "broadcast_name": broadcast_name,
            "created_at": order.created_at,
            "updated_at": order.updated_at,
            "order_items": order_items
        }
        
        items.append(order_dict)
    
    # Create the response
    return {
        "items": items,
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
        "pages": result["pages"]
    }

@router.get("/api/orders/filters", response_model=FilterOptions)
async def get_order_filters(db: Session = Depends(get_db)):
    """
    Get all filter options for orders
    """
    order_dao = OrderDAO(db)
    return order_dao.get_filter_options()

@router.get("/api/orders/{order_id}", response_model=OrderWithDetails)
async def get_order(order_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """
    Get a specific order by ID with all details
    """
    order_dao = OrderDAO(db)
    order = order_dao.get_order_by_id(order_id)
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get client name
    client_name = f"{order.client.first_name} {order.client.last_name}" if order.client else "Unknown"
    
    # Get order status name
    order_status_name = order.order_status.name if order.order_status else None
    
    # Get payment status name
    payment_status_name = order.payment_status_rel.name if order.payment_status_rel else None
    
    # Get payment method name
    payment_method_name = order.payment_method.name if order.payment_method else None
    
    # Get delivery method name
    delivery_method_name = order.delivery_method.name if order.delivery_method else None
    
    # Get delivery status name
    delivery_status_name = order.delivery_status.name if order.delivery_status else None
    
    # Get broadcast name
    broadcast_name = order.broadcast.name if order.broadcast else None
    
    # Prepare order items
    order_items = []
    for item in order.items:
        # Get product details
        product_number = item.product.productnumber if item.product else "Unknown"
        product_name = f"{item.product.model or ''} {item.product.marking or ''}".strip() or "Unknown"
        
        order_items.append({
            "id": item.id,
            "order_id": item.order_id,
            "product_id": item.product_id,
            "product_number": product_number,
            "product_name": product_name,
            "quantity": item.quantity,
            "price": item.price,
            "discount_type": item.discount_type,
            "discount_value": item.discount_value,
            "additional_operation": item.additional_operation,
            "additional_operation_value": item.additional_operation_value,
            "notes": item.notes,
            "created_at": item.created_at,
            "updated_at": item.updated_at
        })
    
    # Create delivery address details
    address_details = None
    if order.delivery_address:
        address = order.delivery_address
        address_details = {
            "id": address.id,
            "city": address.city,
            "street": address.street,
            "building": address.building,
            "apartment": address.apartment,
            "postal_code": address.postal_code,
            "notes": address.notes
        }
    
    # Return transformed order
    return {
        "id": order.id,
        "client_id": order.client_id,
        "client_name": client_name,
        "order_date": order.order_date,
        "order_status_id": order.order_status_id,
        "order_status_name": order_status_name,
        "payment_status_id": order.payment_status_id,
        "payment_status_name": payment_status_name,
        "payment_status": order.payment_status,
        "payment_method_id": order.payment_method_id,
        "payment_method_name": payment_method_name,
        "delivery_method_id": order.delivery_method_id,
        "delivery_method_name": delivery_method_name,
        "delivery_status_id": order.delivery_status_id,
        "delivery_status_name": delivery_status_name,
        "delivery_address_id": order.delivery_address_id,
        "delivery_address_details": address_details,
        "tracking_number": order.tracking_number,
        "total_amount": order.total_amount,
        "notes": order.notes,
        "deferred_until": order.deferred_until,
        "priority": order.priority,
        "broadcast_id": order.broadcast_id,
        "broadcast_name": broadcast_name,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "order_items": order_items
    }

@router.post("/api/orders", response_model=OrderWithDetails)
async def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    """
    Create a new order with order items
    """
    # Validate client exists
    client = db.query(Client).filter(Client.id == order.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate products exist
    for item in order.order_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product with ID {item.product_id} not found")
    
    # Create order with DAO
    order_dao = OrderDAO(db)
    new_order = order_dao.create_order(order.dict())
    
    # Recalculate order total
    order_dao.recalculate_order_total(new_order.id)
    
    # Get complete order with details
    return await get_order(new_order.id, db)

@router.put("/api/orders/{order_id}", response_model=OrderWithDetails)
async def update_order(
    order: OrderUpdate, 
    order_id: int = Path(..., ge=1), 
    db: Session = Depends(get_db)
):
    """
    Update an existing order
    """
    # Validate order exists
    order_dao = OrderDAO(db)
    existing_order = order_dao.get_order_by_id(order_id)
    if not existing_order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Validate client exists if changing
    if order.client_id is not None:
        client = db.query(Client).filter(Client.id == order.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate products exist
    if order.order_items:
        for item in order.order_items:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Product with ID {item.product_id} not found")
    
    # Update order
    updated_order = order_dao.update_order(order_id, order.dict(exclude_unset=True))
    
    # Recalculate order total
    order_dao.recalculate_order_total(order_id)
    
    # Get complete order with details
    return await get_order(order_id, db)

@router.delete("/api/orders/{order_id}")
async def delete_order(order_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """
    Delete an order by ID
    """
    order_dao = OrderDAO(db)
    success = order_dao.delete_order(order_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return {"message": "Order successfully deleted", "id": order_id} 
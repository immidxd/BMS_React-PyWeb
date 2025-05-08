from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.models import OrderStatus
from backend.schemas.reference import OrderStatus as OrderStatusSchema
from backend.schemas.reference import OrderStatusCreate, OrderStatusUpdate, OrderStatusList

router = APIRouter()

@router.get("/order-statuses", response_model=OrderStatusList, tags=["order_statuses"])
async def get_order_statuses(db: Session = Depends(get_db)):
    """
    Get all order statuses
    """
    order_statuses = db.query(OrderStatus).all()
    return {"items": order_statuses}

@router.get("/order-statuses/{order_status_id}", response_model=OrderStatusSchema, tags=["order_statuses"])
async def get_order_status(order_status_id: int, db: Session = Depends(get_db)):
    """
    Get order status by ID
    """
    order_status = db.query(OrderStatus).filter(OrderStatus.id == order_status_id).first()
    if not order_status:
        raise HTTPException(status_code=404, detail="Order status not found")
    return order_status

@router.post("/order-statuses", response_model=OrderStatusSchema, tags=["order_statuses"])
async def create_order_status(order_status: OrderStatusCreate, db: Session = Depends(get_db)):
    """
    Create a new order status
    """
    # Check if order status with the same name already exists
    existing_order_status = db.query(OrderStatus).filter(OrderStatus.status_name == order_status.status_name).first()
    if existing_order_status:
        raise HTTPException(status_code=400, detail=f"Order status with name '{order_status.status_name}' already exists")
    
    # Create new order status
    db_order_status = OrderStatus(**order_status.dict())
    db.add(db_order_status)
    db.commit()
    db.refresh(db_order_status)
    return db_order_status

@router.put("/order-statuses/{order_status_id}", response_model=OrderStatusSchema, tags=["order_statuses"])
async def update_order_status(order_status_id: int, order_status: OrderStatusUpdate, db: Session = Depends(get_db)):
    """
    Update an existing order status
    """
    db_order_status = db.query(OrderStatus).filter(OrderStatus.id == order_status_id).first()
    if not db_order_status:
        raise HTTPException(status_code=404, detail="Order status not found")
    
    # Check if name is changed and if new name already exists
    if order_status.status_name and order_status.status_name != db_order_status.status_name:
        existing_order_status = db.query(OrderStatus).filter(OrderStatus.status_name == order_status.status_name).first()
        if existing_order_status:
            raise HTTPException(status_code=400, detail=f"Order status with name '{order_status.status_name}' already exists")
    
    # Update order status fields
    update_data = order_status.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_order_status, key, value)
    
    db.commit()
    db.refresh(db_order_status)
    return db_order_status

@router.delete("/order-statuses/{order_status_id}", tags=["order_statuses"])
async def delete_order_status(order_status_id: int, db: Session = Depends(get_db)):
    """
    Delete an order status
    """
    db_order_status = db.query(OrderStatus).filter(OrderStatus.id == order_status_id).first()
    if not db_order_status:
        raise HTTPException(status_code=404, detail="Order status not found")
    
    # Check if order status is used in any orders
    if db_order_status.orders:
        raise HTTPException(status_code=400, detail="Cannot delete order status that is used in orders")
    
    db.delete(db_order_status)
    db.commit()
    return {"message": "Order status deleted successfully"} 
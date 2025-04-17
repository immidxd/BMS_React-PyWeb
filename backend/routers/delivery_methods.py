from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.models import DeliveryMethod
from backend.schemas.reference import DeliveryMethod as DeliveryMethodSchema
from backend.schemas.reference import DeliveryMethodCreate, DeliveryMethodUpdate, DeliveryMethodList

router = APIRouter()

@router.get("/delivery-methods", response_model=DeliveryMethodList, tags=["delivery_methods"])
async def get_delivery_methods(db: Session = Depends(get_db)):
    """
    Get all delivery methods
    """
    delivery_methods = db.query(DeliveryMethod).all()
    return {"items": delivery_methods}

@router.get("/delivery-methods/{delivery_method_id}", response_model=DeliveryMethodSchema, tags=["delivery_methods"])
async def get_delivery_method(delivery_method_id: int, db: Session = Depends(get_db)):
    """
    Get delivery method by ID
    """
    delivery_method = db.query(DeliveryMethod).filter(DeliveryMethod.id == delivery_method_id).first()
    if not delivery_method:
        raise HTTPException(status_code=404, detail="Delivery method not found")
    return delivery_method

@router.post("/delivery-methods", response_model=DeliveryMethodSchema, tags=["delivery_methods"])
async def create_delivery_method(delivery_method: DeliveryMethodCreate, db: Session = Depends(get_db)):
    """
    Create a new delivery method
    """
    # Check if delivery method with the same name already exists
    existing_delivery_method = db.query(DeliveryMethod).filter(DeliveryMethod.name == delivery_method.name).first()
    if existing_delivery_method:
        raise HTTPException(status_code=400, detail=f"Delivery method with name '{delivery_method.name}' already exists")
    
    # Create new delivery method
    db_delivery_method = DeliveryMethod(**delivery_method.dict())
    db.add(db_delivery_method)
    db.commit()
    db.refresh(db_delivery_method)
    return db_delivery_method

@router.put("/delivery-methods/{delivery_method_id}", response_model=DeliveryMethodSchema, tags=["delivery_methods"])
async def update_delivery_method(delivery_method_id: int, delivery_method: DeliveryMethodUpdate, db: Session = Depends(get_db)):
    """
    Update an existing delivery method
    """
    db_delivery_method = db.query(DeliveryMethod).filter(DeliveryMethod.id == delivery_method_id).first()
    if not db_delivery_method:
        raise HTTPException(status_code=404, detail="Delivery method not found")
    
    # Check if name is changed and if new name already exists
    if delivery_method.name and delivery_method.name != db_delivery_method.name:
        existing_delivery_method = db.query(DeliveryMethod).filter(DeliveryMethod.name == delivery_method.name).first()
        if existing_delivery_method:
            raise HTTPException(status_code=400, detail=f"Delivery method with name '{delivery_method.name}' already exists")
    
    # Update delivery method fields
    update_data = delivery_method.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_delivery_method, key, value)
    
    db.commit()
    db.refresh(db_delivery_method)
    return db_delivery_method

@router.delete("/delivery-methods/{delivery_method_id}", tags=["delivery_methods"])
async def delete_delivery_method(delivery_method_id: int, db: Session = Depends(get_db)):
    """
    Delete a delivery method
    """
    db_delivery_method = db.query(DeliveryMethod).filter(DeliveryMethod.id == delivery_method_id).first()
    if not db_delivery_method:
        raise HTTPException(status_code=404, detail="Delivery method not found")
    
    # Check if delivery method is used in any orders
    if db_delivery_method.orders:
        raise HTTPException(status_code=400, detail="Cannot delete delivery method that is used in orders")
    
    db.delete(db_delivery_method)
    db.commit()
    return {"message": "Delivery method deleted successfully"} 
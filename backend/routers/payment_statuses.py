from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.models import PaymentStatus
from backend.schemas.reference import PaymentStatus as PaymentStatusSchema
from backend.schemas.reference import PaymentStatusCreate, PaymentStatusUpdate, PaymentStatusList

router = APIRouter()

@router.get("/payment-statuses", response_model=PaymentStatusList, tags=["payment_statuses"])
async def get_payment_statuses(db: Session = Depends(get_db)):
    """
    Get all payment statuses
    """
    payment_statuses = db.query(PaymentStatus).all()
    return {"items": payment_statuses}

@router.get("/payment-statuses/{payment_status_id}", response_model=PaymentStatusSchema, tags=["payment_statuses"])
async def get_payment_status(payment_status_id: int, db: Session = Depends(get_db)):
    """
    Get payment status by ID
    """
    payment_status = db.query(PaymentStatus).filter(PaymentStatus.id == payment_status_id).first()
    if not payment_status:
        raise HTTPException(status_code=404, detail="Payment status not found")
    return payment_status

@router.post("/payment-statuses", response_model=PaymentStatusSchema, tags=["payment_statuses"])
async def create_payment_status(payment_status: PaymentStatusCreate, db: Session = Depends(get_db)):
    """
    Create a new payment status
    """
    # Check if payment status with the same name already exists
    existing_payment_status = db.query(PaymentStatus).filter(PaymentStatus.name == payment_status.name).first()
    if existing_payment_status:
        raise HTTPException(status_code=400, detail=f"Payment status with name '{payment_status.name}' already exists")
    
    # Create new payment status
    db_payment_status = PaymentStatus(**payment_status.dict())
    db.add(db_payment_status)
    db.commit()
    db.refresh(db_payment_status)
    return db_payment_status

@router.put("/payment-statuses/{payment_status_id}", response_model=PaymentStatusSchema, tags=["payment_statuses"])
async def update_payment_status(payment_status_id: int, payment_status: PaymentStatusUpdate, db: Session = Depends(get_db)):
    """
    Update an existing payment status
    """
    db_payment_status = db.query(PaymentStatus).filter(PaymentStatus.id == payment_status_id).first()
    if not db_payment_status:
        raise HTTPException(status_code=404, detail="Payment status not found")
    
    # Check if name is changed and if new name already exists
    if payment_status.name and payment_status.name != db_payment_status.name:
        existing_payment_status = db.query(PaymentStatus).filter(PaymentStatus.name == payment_status.name).first()
        if existing_payment_status:
            raise HTTPException(status_code=400, detail=f"Payment status with name '{payment_status.name}' already exists")
    
    # Update payment status fields
    update_data = payment_status.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_payment_status, key, value)
    
    db.commit()
    db.refresh(db_payment_status)
    return db_payment_status

@router.delete("/payment-statuses/{payment_status_id}", tags=["payment_statuses"])
async def delete_payment_status(payment_status_id: int, db: Session = Depends(get_db)):
    """
    Delete a payment status
    """
    db_payment_status = db.query(PaymentStatus).filter(PaymentStatus.id == payment_status_id).first()
    if not db_payment_status:
        raise HTTPException(status_code=404, detail="Payment status not found")
    
    # Check if payment status is used in any orders
    if db_payment_status.orders:
        raise HTTPException(status_code=400, detail="Cannot delete payment status that is used in orders")
    
    db.delete(db_payment_status)
    db.commit()
    return {"message": "Payment status deleted successfully"} 
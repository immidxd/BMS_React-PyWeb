from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from backend.models.database import get_db
from backend.models.models import Client, Gender
from backend.schemas.reference import Client as ClientSchema, ClientCreate, ClientUpdate, ClientList

router = APIRouter()

@router.get("/clients", response_model=ClientList, tags=["clients"])
async def get_clients(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    gender_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Get list of clients with pagination and optional filtering
    """
    query = db.query(Client)
    
    # Apply search filter if provided
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Client.first_name.ilike(search_term),
                Client.last_name.ilike(search_term),
                Client.phone_number.ilike(search_term),
                Client.email.ilike(search_term),
                Client.address.ilike(search_term)
            )
        )
    
    # Filter by gender if provided
    if gender_id:
        query = query.filter(Client.gender_id == gender_id)
    
    # Get total count for pagination
    total = query.count()
    
    # Apply pagination
    query = query.order_by(Client.last_name, Client.first_name)
    query = query.offset((page - 1) * per_page).limit(per_page)
    
    # Get clients
    clients = query.all()
    
    # Add full_name field to each client
    client_list = []
    for client in clients:
        client_dict = client.__dict__.copy()
        client_dict["full_name"] = f"{client.first_name} {client.last_name}"
        client_list.append(client_dict)
    
    return {
        "items": client_list,
        "total": total,
    }

@router.get("/clients/{client_id}", response_model=ClientSchema, tags=["clients"])
async def get_client(client_id: int, db: Session = Depends(get_db)):
    """
    Get client by ID
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Add full_name field
    client_dict = client.__dict__.copy()
    client_dict["full_name"] = f"{client.first_name} {client.last_name}"
    
    return client_dict

@router.post("/clients", response_model=ClientSchema, tags=["clients"])
async def create_client(client: ClientCreate, db: Session = Depends(get_db)):
    """
    Create a new client
    """
    # Validate gender if provided
    if client.gender_id:
        gender = db.query(Gender).filter(Gender.id == client.gender_id).first()
        if not gender:
            raise HTTPException(status_code=404, detail="Gender not found")
    
    # Create new client
    db_client = Client(**client.dict())
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    
    # Add full_name field
    client_dict = db_client.__dict__.copy()
    client_dict["full_name"] = f"{db_client.first_name} {db_client.last_name}"
    
    return client_dict

@router.put("/clients/{client_id}", response_model=ClientSchema, tags=["clients"])
async def update_client(client_id: int, client: ClientUpdate, db: Session = Depends(get_db)):
    """
    Update an existing client
    """
    db_client = db.query(Client).filter(Client.id == client_id).first()
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate gender if provided
    if client.gender_id:
        gender = db.query(Gender).filter(Gender.id == client.gender_id).first()
        if not gender:
            raise HTTPException(status_code=404, detail="Gender not found")
    
    # Update client fields
    update_data = client.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_client, key, value)
    
    db.commit()
    db.refresh(db_client)
    
    # Add full_name field
    client_dict = db_client.__dict__.copy()
    client_dict["full_name"] = f"{db_client.first_name} {db_client.last_name}"
    
    return client_dict

@router.delete("/clients/{client_id}", tags=["clients"])
async def delete_client(client_id: int, db: Session = Depends(get_db)):
    """
    Delete a client
    """
    db_client = db.query(Client).filter(Client.id == client_id).first()
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    db.delete(db_client)
    db.commit()
    return {"message": "Client deleted successfully"} 
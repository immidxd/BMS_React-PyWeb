from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

# Parsing source schemas
class ParsingSourceBase(BaseModel):
    name: str
    url: str
    description: Optional[str] = None
    enabled: bool = True

class ParsingSourceCreate(ParsingSourceBase):
    pass

class ParsingSourceUpdate(ParsingSourceBase):
    name: Optional[str] = None
    url: Optional[str] = None
    enabled: Optional[bool] = None

class ParsingSource(ParsingSourceBase):
    id: int
    
    class Config:
        orm_mode = True

# Parsing style schemas
class ParsingStyleBase(BaseModel):
    name: str
    description: Optional[str] = None
    include_images: bool = True
    deep_details: bool = False

class ParsingStyleCreate(ParsingStyleBase):
    pass

class ParsingStyleUpdate(ParsingStyleBase):
    name: Optional[str] = None
    include_images: Optional[bool] = None
    deep_details: Optional[bool] = None

class ParsingStyle(ParsingStyleBase):
    id: int
    
    class Config:
        orm_mode = True

# Parsing log schemas
class ParsingLogBase(BaseModel):
    source_id: int
    items_processed: int = 0
    items_added: int = 0
    items_updated: int = 0
    items_failed: int = 0
    status: str = "in_progress"
    message: Optional[str] = None

class ParsingLogCreate(ParsingLogBase):
    pass

class ParsingLogUpdate(BaseModel):
    items_processed: Optional[int] = None
    items_added: Optional[int] = None
    items_updated: Optional[int] = None
    items_failed: Optional[int] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None
    message: Optional[str] = None

class ParsingLog(ParsingLogBase):
    id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    source: ParsingSource
    
    class Config:
        orm_mode = True

# Parsing schedule schemas
class ParsingScheduleBase(BaseModel):
    source_id: int
    style_id: int
    frequency: str  # daily, weekly, monthly
    time_of_day: str  # HH:MM format
    days_of_week: Optional[str] = None  # For weekly: mon,tue,wed,etc
    day_of_month: Optional[int] = None  # For monthly
    enabled: bool = True

class ParsingScheduleCreate(ParsingScheduleBase):
    pass

class ParsingScheduleUpdate(ParsingScheduleBase):
    source_id: Optional[int] = None
    style_id: Optional[int] = None
    frequency: Optional[str] = None
    time_of_day: Optional[str] = None
    days_of_week: Optional[str] = None
    day_of_month: Optional[int] = None
    enabled: Optional[bool] = None

class ParsingSchedule(ParsingScheduleBase):
    id: int
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    
    class Config:
        orm_mode = True

# Parsing request schema
class ParsingRequest(BaseModel):
    source_id: int
    style_id: int
    categories: Optional[List[str]] = None
    request_interval: float = 1.0  # seconds between requests
    max_items: Optional[int] = None
    custom_options: Optional[Dict[str, Any]] = None 
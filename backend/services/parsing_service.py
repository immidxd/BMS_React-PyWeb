import asyncio
import logging
import time
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text
from contextlib import contextmanager
from datetime import datetime

from models import models
from models.database import db_session
from schemas import product as schemas
from models.models import ParsingLog, Product, ParsingSource, ParsingStyle

logger = logging.getLogger(__name__)

# Dictionary to store active parsing tasks
active_tasks = {}
parsing_statuses = {}

async def fetch_items_from_source(
    source_url: str, 
    categories: Optional[List[str]] = None, 
    include_images: bool = True,
    deep_details: bool = False,
    request_interval: float = 1.0,
    max_items: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Simulated function to fetch items from a source
    In a real implementation, this would use HTTP requests, BeautifulSoup, Selenium, etc.
    """
    # Simulate network delay
    await asyncio.sleep(request_interval)
    
    # In a real implementation, this would be an actual web scraping logic
    # For now we'll just return some mock data
    items = []
    for i in range(1, (max_items or 10) + 1):
        item = {
            "productnumber": f"ITEM{i:04d}",
            "typename": categories[0] if categories and len(categories) > 0 else "Default Type",
            "subtypename": "Subtype A",
            "brandname": "Brand X",
            "model": f"Model {i}",
            "price": 100.0 + i * 10,
            "color": ["Red", "Blue", "Green", "Black"][i % 4],
            "country": "Ukraine",
            "size": str(30 + i),
            "quantity": 1,
            "description": f"This is a description for item {i}"
        }
        
        if include_images:
            item["image_url"] = f"https://example.com/images/item{i}.jpg"
            
        if deep_details:
            item["dimensions"] = f"{i+10}x{i+20}x{i+5}"
            item["material"] = "Cotton"
            item["weight"] = f"{i/2} kg"
            
        items.append(item)
        
        # Simulate network delay between requests
        await asyncio.sleep(request_interval)
        
    return items

def get_db_session():
    """Get a new database session"""
    return db_session()

@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = get_db_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

async def process_items(
    log_id: int, 
    items: List[Dict[str, Any]],
    session: Session
) -> Dict[str, int]:
    """Process scraped items and add them to the database"""
    stats = {
        "processed": 0,
        "added": 0,
        "updated": 0,
        "failed": 0
    }
    
    for item in items:
        try:
            # Check if product with this productnumber already exists
            existing_product = session.query(Product).filter(
                Product.productnumber == item["productnumber"]
            ).first()
            
            if existing_product:
                # Update existing product
                for key, value in item.items():
                    if hasattr(existing_product, key):
                        setattr(existing_product, key, value)
                stats["updated"] += 1
            else:
                # Create new product
                new_product = Product(**item)
                session.add(new_product)
                stats["added"] += 1
                
            stats["processed"] += 1
            
            # Update parsing log with progress
            parsing_log = session.query(ParsingLog).filter(ParsingLog.id == log_id).first()
            if parsing_log:
                parsing_log.items_processed = stats["processed"]
                parsing_log.items_added = stats["added"]
                parsing_log.items_updated = stats["updated"]
                parsing_log.items_failed = stats["failed"]
                session.commit()
                
            # Update status
            parsing_statuses[log_id] = {
                "total_items": len(items),
                "current_item": stats["processed"],
                "added": stats["added"],
                "updated": stats["updated"],
                "failed": stats["failed"],
                "progress": int(stats["processed"] / len(items) * 100) if len(items) > 0 else 0
            }
            
            # Simulate some processing time
            await asyncio.sleep(0.2)
            
        except Exception as e:
            logger.error(f"Error processing item {item.get('productnumber', 'unknown')}: {e}")
            stats["failed"] += 1
            continue
    
    return stats

async def parsing_task(
    log_id: int,
    source: ParsingSource,
    style: ParsingStyle,
    categories: Optional[List[str]] = None,
    request_interval: float = 1.0,
    max_items: Optional[int] = None,
    custom_options: Optional[Dict[str, Any]] = None
):
    """Main parsing task that runs in the background"""
    try:
        with session_scope() as session:
            # Mark parsing as started
            parsing_log = session.query(ParsingLog).filter(ParsingLog.id == log_id).first()
            if not parsing_log:
                logger.error(f"Parsing log {log_id} not found")
                return

            # Initialize status
            parsing_statuses[log_id] = {
                "status": "in_progress",
                "message": "Starting parsing...",
                "progress": 0
            }

            # Update status to indicate fetching items
            parsing_statuses[log_id]["message"] = f"Fetching items from {source.name}..."
            
            # Fetch items from source
            try:
                items = await fetch_items_from_source(
                    source_url=source.url,
                    categories=categories,
                    include_images=style.include_images,
                    deep_details=style.deep_details,
                    request_interval=request_interval,
                    max_items=max_items
                )
            except Exception as e:
                logger.error(f"Error fetching items: {e}")
                parsing_log.status = "failed"
                parsing_log.end_time = datetime.datetime.utcnow()
                parsing_log.message = f"Failed to fetch items: {str(e)}"
                session.commit()
                
                parsing_statuses[log_id] = {
                    "status": "failed",
                    "message": f"Failed to fetch items: {str(e)}",
                    "progress": 0
                }
                return
            
            if not items:
                parsing_log.status = "completed"
                parsing_log.end_time = datetime.datetime.utcnow()
                parsing_log.message = "No items found to process"
                session.commit()
                
                parsing_statuses[log_id] = {
                    "status": "completed",
                    "message": "No items found to process",
                    "progress": 100
                }
                return
            
            # Update status to indicate processing items
            parsing_statuses[log_id]["message"] = f"Processing {len(items)} items..."
            
            # Process items
            stats = await process_items(log_id, items, session)
            
            # Mark parsing as completed
            parsing_log.items_processed = stats["processed"]
            parsing_log.items_added = stats["added"]
            parsing_log.items_updated = stats["updated"]
            parsing_log.items_failed = stats["failed"]
            parsing_log.status = "completed"
            parsing_log.end_time = datetime.datetime.utcnow()
            parsing_log.message = f"Completed processing {stats['processed']} items"
            session.commit()
            
            # Update final status
            parsing_statuses[log_id] = {
                "status": "completed",
                "message": f"Completed processing {stats['processed']} items",
                "progress": 100,
                "stats": stats
            }
    except asyncio.CancelledError:
        logger.info(f"Parsing task {log_id} was cancelled")
        with session_scope() as session:
            parsing_log = session.query(ParsingLog).filter(ParsingLog.id == log_id).first()
            if parsing_log:
                parsing_log.status = "cancelled"
                parsing_log.end_time = datetime.datetime.utcnow()
                parsing_log.message = "Parsing cancelled by user"
                session.commit()
        
        parsing_statuses[log_id] = {
            "status": "cancelled",
            "message": "Parsing cancelled by user",
            "progress": 0
        }
    except Exception as e:
        logger.error(f"Error in parsing task: {e}")
        with session_scope() as session:
            parsing_log = session.query(ParsingLog).filter(ParsingLog.id == log_id).first()
            if parsing_log:
                parsing_log.status = "failed"
                parsing_log.end_time = datetime.datetime.utcnow()
                parsing_log.message = f"Parsing failed: {str(e)}"
                session.commit()
        
        parsing_statuses[log_id] = {
            "status": "failed",
            "message": f"Parsing failed: {str(e)}",
            "progress": 0
        }
    finally:
        # Remove task from active tasks
        if log_id in active_tasks:
            del active_tasks[log_id]

def start_parsing(
    log_id: int,
    source: ParsingSource,
    style: ParsingStyle,
    categories: Optional[List[str]] = None,
    request_interval: float = 1.0,
    max_items: Optional[int] = None,
    custom_options: Optional[Dict[str, Any]] = None
):
    """Start a parsing task and return immediately"""
    # Cancel any existing task with the same log_id
    if log_id in active_tasks:
        active_tasks[log_id].cancel()
    
    # Create a new task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    task = loop.create_task(
        parsing_task(
            log_id=log_id,
            source=source,
            style=style,
            categories=categories,
            request_interval=request_interval,
            max_items=max_items,
            custom_options=custom_options
        )
    )
    
    # Store the task
    active_tasks[log_id] = task
    
    # Run the event loop in a separate thread
    def run_event_loop(loop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(task)
        finally:
            loop.close()
    
    import threading
    thread = threading.Thread(target=run_event_loop, args=(loop,), daemon=True)
    thread.start()

def stop_parsing(log_id: int):
    """Stop a running parsing task"""
    if log_id in active_tasks:
        task = active_tasks[log_id]
        task.cancel()
        return True
    return False

def get_parsing_status(log_id: int) -> Dict[str, Any]:
    """Get the status of a parsing task"""
    if log_id in parsing_statuses:
        return parsing_statuses[log_id]
    return {"status": "unknown", "message": "No status found for this task"}

def get_parsing_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent parsing logs"""
    with session_scope() as session:
        logs = session.query(ParsingLog).order_by(ParsingLog.start_time.desc()).limit(limit).all()
        return [
            {
                "id": log.id,
                "source_id": log.source_id,
                "source_name": log.source.name if log.source else "Unknown",
                "start_time": log.start_time,
                "end_time": log.end_time,
                "status": log.status,
                "items_processed": log.items_processed,
                "items_added": log.items_added,
                "items_updated": log.items_updated,
                "items_failed": log.items_failed,
                "message": log.message
            }
            for log in logs
        ] 

def calculate_next_run(schedule):
    """
    Calculate the next run time based on the schedule parameters
    """
    now = datetime.datetime.now()
    hour, minute = map(int, schedule.time_of_day.split(':'))
    
    if schedule.frequency == "daily":
        # Next run is today at the specified time if it's in the future, otherwise tomorrow
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
    
    elif schedule.frequency == "weekly":
        # Get the days of the week to run (0=Monday, 6=Sunday)
        days_of_week = []
        if schedule.days_of_week:
            dow_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            days = schedule.days_of_week.split(',')
            days_of_week = [dow_map.get(day.lower().strip(), 0) for day in days]
        
        if not days_of_week:
            days_of_week = [0]  # Default to Monday
        
        # Find the next day to run
        current_dow = now.weekday()
        
        # Sort to find the next day that's greater than the current day
        future_days = [d for d in days_of_week if d > current_dow]
        
        if future_days:
            # There's a day this week to run
            days_to_add = future_days[0] - current_dow
        else:
            # We need to go to next week
            days_to_add = 7 - current_dow + days_of_week[0]
        
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        next_run += datetime.timedelta(days=days_to_add)
        
    elif schedule.frequency == "monthly":
        # Run on a specific day of the month
        day = schedule.day_of_month or 1  # Default to the 1st if not specified
        
        # Get the next month's date
        if now.day < day:
            # The target day is later this month
            next_run = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        else:
            # The target day has passed this month, go to next month
            if now.month == 12:
                next_run = now.replace(year=now.year + 1, month=1, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            else:
                next_run = now.replace(month=now.month + 1, day=day, hour=hour, minute=minute, second=0, microsecond=0)
    
    else:
        # Default to daily if frequency is unknown
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
    
    return next_run 
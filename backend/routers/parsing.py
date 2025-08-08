from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import asyncio
import datetime
import logging
import json
import sys
import os

# Додаємо шлях до backend для правильного імпорту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import get_db
from models.models import ParsingSource, ParsingStyle, ParsingLog, ParsingSchedule
from schemas.parsing import (
    ParsingSource as ParsingSourceSchema,
    ParsingSourceCreate, ParsingSourceUpdate,
    ParsingStyle as ParsingStyleSchema,
    ParsingStyleCreate, ParsingStyleUpdate,
    ParsingLog as ParsingLogSchema,
    ParsingLogCreate, ParsingLogUpdate,
    ParsingSchedule as ParsingScheduleSchema,
    ParsingScheduleCreate, ParsingScheduleUpdate,
    ParsingRequest
)
from services.parsing_service import (
    start_parsing,
    stop_parsing,
    get_parsing_status,
    get_parsing_logs,
    calculate_next_run
)

# Додаємо шлях до scripts
scripts_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.append(scripts_path)

try:
    from unified_parser import UnifiedParser, ParsingMode, get_parsing_modes
except ImportError as e:
    logger.error(f"Failed to import unified_parser: {e}")
    # Fallback implementations
    class ParsingMode:
        FULL = "full"
        INCREMENTAL = "incremental"
        QUICK_UPDATE = "quick_update"
        PRODUCTS_ONLY = "products_only"
        ORDERS_ONLY = "orders_only"
        NEW_PRODUCTS = "new_products"
    
    class UnifiedParser:
        def __init__(self, callback=None):
            pass
        
        async def parse(self, mode, **kwargs):
            pass
        
        def cancel(self):
            pass
    
    def get_parsing_modes():
        return [
            {
                "id": "full",
                "name": "Повний парсинг",
                "description": "Повний парсинг всіх товарів та замовлень",
                "icon": "🔄",
                "estimated_time": "1-2 години"
            }
        ]

router = APIRouter()
logger = logging.getLogger(__name__)

# Active parsing tasks
active_parsing_tasks = {}

# Глобальні змінні для відстеження парсингу
current_parser: Optional[UnifiedParser] = None
parsing_status = {
    "is_running": False,
    "task": "",
    "current": 0,
    "total": 0,
    "elapsed_time": 0,
    "errors": []
}

# WebSocket клієнти для оновлення статусу
websocket_clients: List[WebSocket] = []

async def broadcast_status(status: Dict):
    """Відправляє статус всім підключеним клієнтам."""
    global parsing_status
    parsing_status = status
    
    # Видаляємо відключених клієнтів
    disconnected = []
    for client in websocket_clients:
        try:
            await client.send_json(status)
        except:
            disconnected.append(client)
    
    for client in disconnected:
        websocket_clients.remove(client)

@router.get("/parsing/sources", response_model=List[ParsingSourceSchema], tags=["parsing"])
async def get_parsing_sources(db: Session = Depends(get_db)):
    """
    Get all available parsing sources
    """
    sources = db.query(ParsingSource).all()
    return sources

@router.post("/parsing/sources", response_model=ParsingSourceSchema, tags=["parsing"])
async def create_parsing_source(source: ParsingSourceCreate, db: Session = Depends(get_db)):
    """
    Create a new parsing source
    """
    db_source = ParsingSource(**source.dict())
    db.add(db_source)
    db.commit()
    db.refresh(db_source)
    return db_source

@router.put("/parsing/sources/{source_id}", response_model=ParsingSourceSchema, tags=["parsing"])
async def update_parsing_source(source_id: int, source: ParsingSourceUpdate, db: Session = Depends(get_db)):
    """
    Update an existing parsing source
    """
    db_source = db.query(ParsingSource).filter(ParsingSource.id == source_id).first()
    if not db_source:
        raise HTTPException(status_code=404, detail="Parsing source not found")
    
    update_data = source.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_source, key, value)
    
    db.commit()
    db.refresh(db_source)
    return db_source

@router.delete("/parsing/sources/{source_id}", tags=["parsing"])
async def delete_parsing_source(source_id: int, db: Session = Depends(get_db)):
    """
    Delete a parsing source
    """
    db_source = db.query(ParsingSource).filter(ParsingSource.id == source_id).first()
    if not db_source:
        raise HTTPException(status_code=404, detail="Parsing source not found")
    
    db.delete(db_source)
    db.commit()
    return {"message": "Parsing source deleted successfully"}

@router.get("/parsing/styles", response_model=List[ParsingStyleSchema], tags=["parsing"])
async def get_parsing_styles(db: Session = Depends(get_db)):
    """
    Get all available parsing styles
    """
    styles = db.query(ParsingStyle).all()
    return styles

@router.post("/parsing/styles", response_model=ParsingStyleSchema, tags=["parsing"])
async def create_parsing_style(style: ParsingStyleCreate, db: Session = Depends(get_db)):
    """
    Create a new parsing style
    """
    db_style = ParsingStyle(**style.dict())
    db.add(db_style)
    db.commit()
    db.refresh(db_style)
    return db_style

@router.put("/parsing/styles/{style_id}", response_model=ParsingStyleSchema, tags=["parsing"])
async def update_parsing_style(style_id: int, style: ParsingStyleUpdate, db: Session = Depends(get_db)):
    """
    Update an existing parsing style
    """
    db_style = db.query(ParsingStyle).filter(ParsingStyle.id == style_id).first()
    if not db_style:
        raise HTTPException(status_code=404, detail="Parsing style not found")
    
    update_data = style.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_style, key, value)
    
    db.commit()
    db.refresh(db_style)
    return db_style

@router.delete("/parsing/styles/{style_id}", tags=["parsing"])
async def delete_parsing_style(style_id: int, db: Session = Depends(get_db)):
    """
    Delete a parsing style
    """
    db_style = db.query(ParsingStyle).filter(ParsingStyle.id == style_id).first()
    if not db_style:
        raise HTTPException(status_code=404, detail="Parsing style not found")
    
    db.delete(db_style)
    db.commit()
    return {"message": "Parsing style deleted successfully"}

@router.post("/parsing/start")
async def start_parsing(background_tasks: BackgroundTasks, mode: str, params: Optional[Dict] = None):
    """Запускає парсинг у фоновому режимі."""
    global current_parser
    
    if parsing_status.get("is_running", False):
        raise HTTPException(status_code=400, detail="Парсинг вже виконується")
    
    try:
        parsing_mode = ParsingMode(mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Невідомий режим парсингу: {mode}")
    
    # Створюємо парсер з callback для оновлення статусу
    current_parser = UnifiedParser(broadcast_status)
    
    # Запускаємо парсинг у фоновому режимі
    background_tasks.add_task(run_parsing, parsing_mode, params or {})
    
    return {
        "status": "started",
        "mode": mode,
        "message": "Парсинг запущено у фоновому режимі"
    }

@router.post("/parsing/cancel")
async def cancel_parsing():
    """Скасовує поточний парсинг."""
    global current_parser
    
    if not parsing_status.get("is_running", False):
        raise HTTPException(status_code=400, detail="Немає активного парсингу")
    
    if current_parser:
        current_parser.cancel()
        return {"status": "cancelling", "message": "Запит на скасування відправлено"}
    
    return {"status": "error", "message": "Парсер не знайдено"}

@router.websocket("/parsing/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket для отримання оновлень статусу в реальному часі."""
    await websocket.accept()
    websocket_clients.append(websocket)
    
    # Відправляємо поточний статус
    await websocket.send_json(parsing_status)
    
    try:
        while True:
            # Чекаємо на повідомлення від клієнта (для підтримки з'єднання)
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_clients.remove(websocket)

async def run_parsing(mode: ParsingMode, params: Dict):
    """Запускає парсинг у фоновому режимі."""
    global current_parser
    
    try:
        await current_parser.parse(mode, **params)
    except Exception as e:
        await broadcast_status({
            "is_running": False,
            "task": "Помилка парсингу",
            "current": 0,
            "total": 0,
            "elapsed_time": 0,
            "errors": [str(e)]
        })
    finally:
        current_parser = None

@router.get("/parsing/modes")
async def get_available_modes():
    """Повертає доступні режими парсингу."""
    return get_parsing_modes()

@router.get("/parsing/status")
async def get_parsing_status():
    """Повертає поточний статус парсингу."""
    return parsing_status

@router.post("/parsing/stop/{log_id}", tags=["parsing"])
async def stop_parsing_task(log_id: int, db: Session = Depends(get_db)):
    """
    Stop a running parsing task
    """
    # Check if log exists
    parsing_log = db.query(ParsingLog).filter(ParsingLog.id == log_id).first()
    if not parsing_log:
        raise HTTPException(status_code=404, detail="Parsing log not found")
    
    # Check if task is active
    if log_id not in active_parsing_tasks:
        raise HTTPException(status_code=400, detail="No active parsing task with this ID")
    
    # Stop parsing
    stop_parsing(log_id)
    
    # Update log status
    parsing_log.status = "cancelled"
    parsing_log.end_time = datetime.datetime.utcnow()
    parsing_log.message = "Parsing cancelled by user"
    db.commit()
    
    # Remove from active tasks
    if log_id in active_parsing_tasks:
        del active_parsing_tasks[log_id]
    
    return {
        "log_id": log_id,
        "status": "stopped",
        "message": "Parsing task stopped successfully"
    }

@router.get("/parsing/status/{log_id}", tags=["parsing"])
async def get_parsing_task_status(log_id: int, db: Session = Depends(get_db)):
    """
    Get status of a parsing task
    """
    # Check if log exists
    parsing_log = db.query(ParsingLog).filter(ParsingLog.id == log_id).first()
    if not parsing_log:
        raise HTTPException(status_code=404, detail="Parsing log not found")
    
    # Get status
    status = get_parsing_status(log_id)
    
    return {
        "log_id": log_id,
        "status": parsing_log.status,
        "items_processed": parsing_log.items_processed,
        "items_added": parsing_log.items_added,
        "items_updated": parsing_log.items_updated,
        "items_failed": parsing_log.items_failed,
        "start_time": parsing_log.start_time,
        "end_time": parsing_log.end_time,
        "message": parsing_log.message,
        "details": status
    }

@router.get("/parsing/logs", response_model=List[ParsingLogSchema], tags=["parsing"])
async def get_all_parsing_logs(limit: int = 50, db: Session = Depends(get_db)):
    """
    Get parsing logs
    """
    logs = db.query(ParsingLog).order_by(ParsingLog.start_time.desc()).limit(limit).all()
    return logs

@router.get("/parsing/schedule", response_model=List[ParsingScheduleSchema], tags=["parsing"])
async def get_parsing_schedules(db: Session = Depends(get_db)):
    """
    Get all parsing schedules
    """
    schedules = db.query(ParsingSchedule).all()
    return schedules

@router.post("/parsing/schedule", response_model=ParsingScheduleSchema, tags=["parsing"])
async def create_parsing_schedule(schedule: ParsingScheduleCreate, db: Session = Depends(get_db)):
    """
    Create a new parsing schedule
    """
    # Validate source and style
    source = db.query(ParsingSource).filter(ParsingSource.id == schedule.source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Parsing source not found")
    
    style = db.query(ParsingStyle).filter(ParsingStyle.id == schedule.style_id).first()
    if not style:
        raise HTTPException(status_code=404, detail="Parsing style not found")
    
    # Calculate next run time
    next_run = calculate_next_run(schedule)
    
    # Create schedule
    db_schedule = ParsingSchedule(**schedule.dict(), next_run=next_run)
    db.add(db_schedule)
    db.commit()
    db.refresh(db_schedule)
    
    return db_schedule

@router.put("/parsing/schedule/{schedule_id}", response_model=ParsingScheduleSchema, tags=["parsing"])
async def update_parsing_schedule(schedule_id: int, schedule: ParsingScheduleUpdate, db: Session = Depends(get_db)):
    """
    Update an existing parsing schedule
    """
    db_schedule = db.query(ParsingSchedule).filter(ParsingSchedule.id == schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Parsing schedule not found")
    
    # Update fields
    update_data = schedule.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_schedule, key, value)
    
    # Recalculate next run time if frequency, time_of_day, days_of_week, or day_of_month changed
    if any(key in update_data for key in ["frequency", "time_of_day", "days_of_week", "day_of_month"]):
        db_schedule.next_run = calculate_next_run(db_schedule)
    
    db.commit()
    db.refresh(db_schedule)
    return db_schedule

@router.delete("/parsing/schedule/{schedule_id}", tags=["parsing"])
async def delete_parsing_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """
    Delete a parsing schedule
    """
    db_schedule = db.query(ParsingSchedule).filter(ParsingSchedule.id == schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Parsing schedule not found")
    
    db.delete(db_schedule)
    db.commit()
    return {"message": "Parsing schedule deleted successfully"}

@router.post("/parsing/orders", tags=["parsing"])
async def run_orders_parsing(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Run the orders_pars.py script to import orders from Google Sheets
    """
    import subprocess
    import os
    import sys
    from datetime import datetime
    
    # Create parsing log
    parsing_log = ParsingLog(
        source_id=1,  # Assuming 1 is the Google Sheets source
        status="in_progress",
        start_time=datetime.utcnow(),
        message="Running orders_pars.py script"
    )
    db.add(parsing_log)
    db.commit()
    db.refresh(parsing_log)
    
    # Function to run in background
    def run_orders_script():
        try:
            script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                       "scripts", "orders_pars.py")
            
            # Start the subprocess
            process = subprocess.Popen([sys.executable, script_path], 
                                        stdout=subprocess.PIPE, 
                                        stderr=subprocess.PIPE)
            
            # Wait for completion
            stdout, stderr = process.communicate()
            
            # Update parsing log
            db_session = next(get_db())
            log = db_session.query(ParsingLog).filter(ParsingLog.id == parsing_log.id).first()
            
            if process.returncode == 0:
                log.status = "completed"
                log.message = "Orders parsing completed successfully"
            else:
                log.status = "failed"
                log.message = f"Orders parsing failed: {stderr.decode('utf-8')}"
            
            log.end_time = datetime.utcnow()
            db_session.commit()
            
        except Exception as e:
            logger.error(f"Error running orders script: {e}")
            # Update parsing log with error
            db_session = next(get_db())
            log = db_session.query(ParsingLog).filter(ParsingLog.id == parsing_log.id).first()
            log.status = "failed"
            log.message = f"Error: {str(e)}"
            log.end_time = datetime.utcnow()
            db_session.commit()
    
    # Add task to background tasks
    background_tasks.add_task(run_orders_script)
    
    return {
        "log_id": parsing_log.id,
        "status": "started",
        "message": "Orders parsing script started"
    }

@router.post("/parsing/googlesheets", tags=["parsing"])
async def run_googlesheets_parsing(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Run the googlesheets_pars.py script to import products from Google Sheets
    """
    import subprocess
    import os
    import sys
    from datetime import datetime
    
    # Create parsing log
    parsing_log = ParsingLog(
        source_id=1,  # Assuming 1 is the Google Sheets source
        status="in_progress",
        start_time=datetime.utcnow(),
        message="Running googlesheets_pars.py script"
    )
    db.add(parsing_log)
    db.commit()
    db.refresh(parsing_log)
    
    # Function to run in background
    def run_googlesheets_script():
        try:
            script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                       "scripts", "googlesheets_pars.py")
            
            # Start the subprocess
            process = subprocess.Popen([sys.executable, script_path], 
                                        stdout=subprocess.PIPE, 
                                        stderr=subprocess.PIPE)
            
            # Wait for completion
            stdout, stderr = process.communicate()
            
            # Update parsing log
            db_session = next(get_db())
            log = db_session.query(ParsingLog).filter(ParsingLog.id == parsing_log.id).first()
            
            if process.returncode == 0:
                log.status = "completed"
                log.message = "Google Sheets parsing completed successfully"
            else:
                log.status = "failed"
                log.message = f"Google Sheets parsing failed: {stderr.decode('utf-8')}"
            
            log.end_time = datetime.utcnow()
            db_session.commit()
            
        except Exception as e:
            logger.error(f"Error running Google Sheets script: {e}")
            # Update parsing log with error
            db_session = next(get_db())
            log = db_session.query(ParsingLog).filter(ParsingLog.id == parsing_log.id).first()
            log.status = "failed"
            log.message = f"Error: {str(e)}"
            log.end_time = datetime.utcnow()
            db_session.commit()
    
    # Add task to background tasks
    background_tasks.add_task(run_googlesheets_script)
    
    return {
        "log_id": parsing_log.id,
        "status": "started",
        "message": "Google Sheets parsing script started"
    } 

@router.post("/parsing/orders-comprehensive", tags=["parsing"])
async def run_comprehensive_orders_parsing(
    max_sheets: Optional[int] = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db)
):
    """
    Запуск комплексного парсингу замовлень з Google Sheets
    Включає дедуплікацію клієнтів, розпізнавання методів оплати, 
    синхронізацію цін та оновлення розмірів товарів
    """
    import subprocess
    import os
    import sys
    from datetime import datetime
    
    # Create parsing log
    parsing_log = ParsingLog(
        source_id=1,  # Google Sheets source
        status="in_progress",
        start_time=datetime.utcnow(),
        message=f"Running comprehensive orders parser{' (limited to ' + str(max_sheets) + ' sheets)' if max_sheets else ''}"
    )
    db.add(parsing_log)
    db.commit()
    db.refresh(parsing_log)
    
    # Function to run in background
    def run_comprehensive_orders_script():
        try:
            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "scripts", 
                "orders_comprehensive_parser.py"
            )
            
            # Build command with optional test parameter
            cmd = [sys.executable, script_path]
            if max_sheets:
                cmd.extend(["--test", str(max_sheets)])
            
            # Start the subprocess
            logger.info(f"Запуск команди: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for completion
            stdout, stderr = process.communicate()
            
            # Update parsing log
            db_session = next(get_db())
            log = db_session.query(ParsingLog).filter(ParsingLog.id == parsing_log.id).first()
            
            if process.returncode == 0:
                log.status = "completed"
                log.message = f"Комплексний парсинг замовлень завершено успішно{' (' + str(max_sheets) + ' аркушів)' if max_sheets else ''}"
                
                # Try to extract statistics from stdout
                if "СТАТИСТИКА ПАРСИНГУ:" in stdout:
                    stats_section = stdout.split("СТАТИСТИКА ПАРСИНГУ:")[1]
                    log.message += f"\n\nСтатистика:\n{stats_section[:500]}"  # Limit message length
                
            else:
                log.status = "failed"
                log.message = f"Помилка комплексного парсингу замовлень: {stderr}"
            
            log.end_time = datetime.utcnow()
            db_session.commit()
            db_session.close()
            
            logger.info(f"Комплексний парсинг замовлень завершено з кодом: {process.returncode}")
            
        except Exception as e:
            logger.error(f"Помилка запуску комплексного парсера замовлень: {e}")
            # Update parsing log with error
            try:
                db_session = next(get_db())
                log = db_session.query(ParsingLog).filter(ParsingLog.id == parsing_log.id).first()
                if log:
                    log.status = "failed"
                    log.message = f"Помилка: {str(e)}"
                    log.end_time = datetime.utcnow()
                    db_session.commit()
                db_session.close()
            except Exception as db_error:
                logger.error(f"Помилка оновлення лога: {db_error}")
    
    # Add task to background tasks
    background_tasks.add_task(run_comprehensive_orders_script)
    
    return {
        "log_id": parsing_log.id,
        "status": "started",
        "message": f"Комплексний парсинг замовлень запущено{' (обмежено до ' + str(max_sheets) + ' аркушів)' if max_sheets else ''}",
        "max_sheets": max_sheets or "всі аркуші",
        "features": [
            "Дедуплікація клієнтів по телефону/Facebook",
            "Розпізнавання методів оплати",
            "Парсинг уточнень (розміри, заміри, коментарі)",
            "Синхронізація цін товарів",
            "Оновлення розмірів та замірів"
        ]
    }

# Старі ендпоінти для сумісності
@router.post("/parsing/products")
async def parse_products(background_tasks: BackgroundTasks):
    """Запускає парсинг товарів (для сумісності)."""
    return await start_parsing(background_tasks, ParsingMode.PRODUCTS_ONLY.value)

@router.post("/parsing/orders")
async def parse_orders(background_tasks: BackgroundTasks):
    """Запускає парсинг замовлень (для сумісності)."""
    return await start_parsing(background_tasks, ParsingMode.ORDERS_ONLY.value) 
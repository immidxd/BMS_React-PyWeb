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
from models.models import ParsingJob
from models.database import get_db, SessionLocal

# Додаємо шлях до scripts
scripts_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.append(scripts_path)

try:
    from unified_parser import UnifiedParser, ParsingMode, get_parsing_modes
except ImportError as e:
    # Defer logging until logger defined below
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
if 'UnifiedParser' not in globals():
    logger.warning("UnifiedParser not available, using fallbacks")

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

@router.get("/sources", response_model=List[ParsingSourceSchema], tags=["parsing"])
async def get_parsing_sources(db: Session = Depends(get_db)):
    """
    Get all available parsing sources
    """
    sources = db.query(ParsingSource).all()
    return sources

@router.post("/sources", response_model=ParsingSourceSchema, tags=["parsing"])
async def create_parsing_source(source: ParsingSourceCreate, db: Session = Depends(get_db)):
    """
    Create a new parsing source
    """
    db_source = ParsingSource(**source.dict())
    db.add(db_source)
    db.commit()
    db.refresh(db_source)
    return db_source

@router.put("/sources/{source_id}", response_model=ParsingSourceSchema, tags=["parsing"])
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

@router.delete("/sources/{source_id}", tags=["parsing"])
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

@router.get("/styles", response_model=List[ParsingStyleSchema], tags=["parsing"])
async def get_parsing_styles(db: Session = Depends(get_db)):
    """
    Get all available parsing styles
    """
    styles = db.query(ParsingStyle).all()
    return styles

@router.post("/styles", response_model=ParsingStyleSchema, tags=["parsing"])
async def create_parsing_style(style: ParsingStyleCreate, db: Session = Depends(get_db)):
    """
    Create a new parsing style
    """
    db_style = ParsingStyle(**style.dict())
    db.add(db_style)
    db.commit()
    db.refresh(db_style)
    return db_style

@router.put("/styles/{style_id}", response_model=ParsingStyleSchema, tags=["parsing"])
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

@router.delete("/styles/{style_id}", tags=["parsing"])
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

@router.post("/run", tags=["parsing"])
async def run_parsing_job(mode: str = "quick_update", params: Optional[Dict] = None, db: Session = Depends(get_db)):
    """Запускає реальний UnifiedParser і оновлює `parsing_jobs` по callback/WS."""
    # 1) Створюємо запис job
    job = ParsingJob(mode=mode, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)

    # 2) Готуємо перетворення mode -> ParsingMode
    try:
        parsed_mode = None
        if 'ParsingMode' in globals():
            try:
                parsed_mode = ParsingMode(mode)
            except Exception:
                # alias-и
                alias = {
                    'quick': 'quick_update',
                    'full_parse': 'full',
                }.get(mode, mode)
                parsed_mode = ParsingMode(alias)
        else:
            raise RuntimeError("UnifiedParser not available")

        # 3) Callback: оновлюємо рядок jobs кожне повідомлення + акумулюємо лог
        async def status_cb(payload: Dict[str, Any]):
            # ВАЖЛИВО: відкриваємо окрему сесію на кожен callback, бо request-сесія вже закрита
            sess = SessionLocal()
            try:
                j = sess.query(ParsingJob).filter(ParsingJob.id == job.id).first()
                if not j:
                    sess.close()
                    return
                is_running = bool(payload.get('is_running', True))
                task_text = str(payload.get('task') or '')
                lower_task = task_text.lower()
                # статус
                now = datetime.datetime.utcnow()
                if is_running:
                    j.status = 'running'
                else:
                    if 'скасовано' in lower_task:
                        j.status = 'canceled'
                        j.ended_at = now
                        j.eta_seconds = 0
                    elif 'помилка' in lower_task or 'error' in lower_task:
                        j.status = 'failed'
                        j.ended_at = now
                    elif 'завершено' in lower_task:
                        j.status = 'succeeded'
                        j.ended_at = now
                        # добити до 100%, якщо маємо повний прогрес
                        try:
                            cur_tmp = int(payload.get('current') or 0)
                            tot_tmp = int(payload.get('total') or 0)
                            if tot_tmp > 0 and cur_tmp >= tot_tmp:
                                j.percent = 100
                        except Exception:
                            pass
                j.updated_at = now
                j.current_step = task_text[:255]
                cur = int(payload.get('current') or 0)
                tot = int(payload.get('total') or 0)
                if tot > 0:
                    j.total_items = tot
                    j.processed_items = cur
                    j.percent = max(0, min(100, int(cur / tot * 100)))
                else:
                    # при невідомому тоталі — просто оновлюємо heartbeat/крок
                    j.processed_items = j.processed_items or 0
                j.last_heartbeat_at = j.updated_at
                # акумулюємо короткий лог у БД (тільки останні ~8КБ)
                try:
                    line = task_text.strip()
                    if line:
                        existing = j.logs_head or ""
                        appended = (existing + ("\n" if existing else "") + line)[-8000:]
                        j.logs_head = appended
                except Exception:
                    pass
                sess.commit()
            except Exception:
                sess.rollback()
            finally:
                sess.close()
            # Також бродкастимо глобальний статус для старого віджета, якщо є
            try:
                await broadcast_status({
                    "is_running": bool(payload.get('is_running', True)),
                    "task": payload.get('task') or '',
                    "current": payload.get('current') or 0,
                    "total": payload.get('total') or 0,
                    "elapsed_time": payload.get('elapsed_time') or 0,
                    "errors": payload.get('errors') or [],
                })
            except Exception:
                pass

        # 4) Запускаємо UnifiedParser у фоні
        parser = UnifiedParser(status_callback=status_cb)

        def _describe_mode(m: str) -> str:
            m = m or ''
            mapping = {
                'full': "scripts: googlesheets_pars.py → orders_comprehensive_parser.py",
                'incremental': "scripts: incremental_parser.py (orders, recent days)",
                'quick_update': "scripts: incremental_parser.py --days 3",
                'products_only': "scripts: googlesheets_pars.py",
                'orders_only': "scripts: orders_comprehensive_parser.py",
                'new_products': "scripts: googlesheets_pars.py --new-only",
            }
            return mapping.get(m, f"mode: {m}")

        async def runner():
            # set started
            job_row = db.query(ParsingJob).filter(ParsingJob.id == job.id).first()
            if job_row:
                job_row.status = 'running'
                job_row.started_at = datetime.datetime.utcnow()
                job_row.updated_at = job_row.started_at
                job_row.current_step = 'initializing'
                desc = _describe_mode(parsed_mode.value if hasattr(parsed_mode, 'value') else str(parsed_mode))
                job_row.logs_head = ((job_row.logs_head or '') + ("\n" if job_row.logs_head else '') + f"START {job_row.started_at.isoformat()} — {desc}")[-8000:]
                db.commit()
            try:
                await parser.parse(parsed_mode, **(params or {}))
                job_row = db.query(ParsingJob).filter(ParsingJob.id == job.id).first()
                if job_row:
                    # Виставляємо succeeded лише якщо не failed/canceled
                    if job_row.status not in ('failed', 'canceled'):
                        job_row.status = 'succeeded'
                        if (job_row.total_items or 0) > 0 and (job_row.processed_items or 0) >= (job_row.total_items or 0):
                            job_row.percent = 100
                        job_row.ended_at = datetime.datetime.utcnow()
                        job_row.updated_at = job_row.ended_at
                        job_row.current_step = 'done'
                        job_row.eta_seconds = 0
                        job_row.logs_head = ((job_row.logs_head or '') + f"\nEND {job_row.ended_at.isoformat()} — ok")[-8000:]
                        db.commit()
            except Exception as e:
                job_row = db.query(ParsingJob).filter(ParsingJob.id == job.id).first()
                if job_row:
                    job_row.status = 'failed'
                    job_row.error_summary = str(e)
                    job_row.ended_at = datetime.datetime.utcnow()
                    job_row.updated_at = job_row.ended_at
                    job_row.current_step = 'failed'
                    job_row.logs_head = ((job_row.logs_head or '') + f"\nEND {job_row.ended_at.isoformat()} — failed: {e}")[-8000:]
                    db.commit()
                raise

        # Фонова корутина — не завершуємо HTTP-відповідь завершеним статусом,
        # просто повертаємо jobId; фінальний статус виставить runner() після parse().
        asyncio.create_task(runner())
        return {"jobId": job.id}
    except Exception as e:
        job.status = "failed"
        job.error_summary = str(e)
        job.updated_at = datetime.datetime.utcnow()
        db.commit()
        raise
@router.get("/jobs/{job_id}", tags=["parsing"])
async def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ParsingJob).filter(ParsingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "mode": job.mode,
        "status": job.status,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "ended_at": job.ended_at,
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "percent": job.percent,
        "items_per_sec": job.items_per_sec,
        "eta_seconds": job.eta_seconds,
        "current_step": job.current_step,
        "last_heartbeat_at": job.last_heartbeat_at,
        "error_summary": job.error_summary,
        "logs_head": job.logs_head,
    }

@router.post("/jobs/{job_id}/cancel", tags=["parsing"])
async def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ParsingJob).filter(ParsingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.cancel_requested = True
    job.status = "canceled"  # cooperative
    job.ended_at = datetime.datetime.utcnow()
    job.updated_at = job.ended_at
    db.commit()
    return {"message": "Cancel requested"}

@router.websocket("/jobs/{job_id}/stream")
async def job_stream(websocket: WebSocket, job_id: int, db: Session = Depends(get_db)):
    await websocket.accept()
    try:
        while True:
            job = db.query(ParsingJob).filter(ParsingJob.id == job_id).first()
            if not job:
                await websocket.send_json({"error": "not_found"})
                break
            payload = {
                "id": job.id,
                "mode": job.mode,
                "status": job.status,
                "started_at": str(job.started_at) if job.started_at else None,
                "updated_at": str(job.updated_at) if job.updated_at else None,
                "ended_at": str(job.ended_at) if job.ended_at else None,
                "total_items": job.total_items,
                "processed_items": job.processed_items,
                "percent": job.percent,
                "items_per_sec": job.items_per_sec,
                "eta_seconds": job.eta_seconds,
                "current_step": job.current_step,
                "last_heartbeat_at": str(job.last_heartbeat_at) if job.last_heartbeat_at else None,
                "error_summary": job.error_summary,
            }
            await websocket.send_json(payload)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass

@router.post("/parsing/cancel")
async def cancel_parsing():
    """Скасовує поточний парсинг."""
    global current_parser
    
    if not parsing_status.get("is_running", False):
        raise HTTPException(status_code=400, detail="Немає активного парсингу")
    
    if current_parser:
        # 1) Просимо парсер зупинитися
        current_parser.cancel()
        # 2) Показуємо "Скасування..." лише якщо парсер ще в стані is_running,
        #    щоб не перезатирати фінальний статус (false) якщо він уже встиг завершитися
        try:
            if getattr(current_parser, 'status', None) and getattr(current_parser.status, 'is_running', False):
                await broadcast_status({
                    "is_running": True,
                    "task": "Скасування парсингу...",
                    "current": 0,
                    "total": 0,
                    "elapsed_time": 0,
                    "errors": []
                })
        except Exception:
            pass
        return {"status": "cancelling", "message": "Запит на скасування відправлено"}
    
    return {"status": "error", "message": "Парсер не знайдено"}

@router.websocket("/ws")
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
        # Якщо користувач натиснув "Скасувати", гарантуємо зупинку підпроцесу
        try:
            if current_parser and getattr(current_parser, 'current_process', None) and current_parser.current_process.returncode is None:
                current_parser.current_process.terminate()
        except Exception:
            pass
        current_parser = None

@router.get("/modes")
async def get_available_modes():
    """Повертає доступні режими парсингу."""
    return get_parsing_modes()

@router.get("/status")
async def get_parsing_status():
    """Повертає поточний статус парсингу."""
    return parsing_status

@router.post("/stop/{log_id}", tags=["parsing"])
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

@router.get("/status/{log_id}", tags=["parsing"])
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

@router.get("/logs", response_model=List[ParsingLogSchema], tags=["parsing"])
async def get_all_parsing_logs(limit: int = 50, db: Session = Depends(get_db)):
    """
    Get parsing logs
    """
    logs = db.query(ParsingLog).order_by(ParsingLog.start_time.desc()).limit(limit).all()
    return logs

@router.get("/schedule", response_model=List[ParsingScheduleSchema], tags=["parsing"])
async def get_parsing_schedules(db: Session = Depends(get_db)):
    """
    Get all parsing schedules
    """
    schedules = db.query(ParsingSchedule).all()
    return schedules

@router.post("/schedule", response_model=ParsingScheduleSchema, tags=["parsing"])
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

@router.put("/schedule/{schedule_id}", response_model=ParsingScheduleSchema, tags=["parsing"])
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

@router.delete("/schedule/{schedule_id}", tags=["parsing"])
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

@router.post("/orders", tags=["parsing"])
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

@router.post("/googlesheets", tags=["parsing"])
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

@router.post("/orders-comprehensive", tags=["parsing"])
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
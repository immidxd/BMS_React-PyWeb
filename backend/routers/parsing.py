from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import asyncio
import datetime
import logging
import json
import threading
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
    from backend.scripts.unified_parser import UnifiedParser, ParsingMode, get_parsing_modes
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
                "id": "sheets_products_quick",
                "name": "Товари — швидко (30 аркушів)",
                "description": "Парсинг товарів з Google Sheets «Журнал» — останні 30 партій",
                "icon": "⚡",
                "estimated_time": "~2 хвилини"
            },
            {
                "id": "sheets_products_full",
                "name": "Товари — повний",
                "description": "Парсинг усіх партій товарів з Google Sheets «Журнал»",
                "icon": "📦",
                "estimated_time": "~6 хвилин"
            },
            {
                "id": "sheets_orders_quick",
                "name": "Замовлення — швидко (30 аркушів)",
                "description": "Парсинг замовлень з Google Sheets «Замовлення» — останні 30 аркушів",
                "icon": "🛒",
                "estimated_time": "~2 хвилини"
            },
            {
                "id": "sheets_orders_full",
                "name": "Замовлення — повний",
                "description": "Парсинг усіх замовлень з Google Sheets «Замовлення»",
                "icon": "🛒",
                "estimated_time": "~6 хвилин"
            },
            {
                "id": "sheets_full_quick",
                "name": "Все — швидко (товари + замовлення)",
                "description": "Швидкий парсинг і товарів, і замовлень (останні 30 аркушів кожного)",
                "icon": "🔄",
                "estimated_time": "~4 хвилини"
            },
            {
                "id": "sheets_full_full",
                "name": "Все — повний парсинг",
                "description": "Повний парсинг усіх товарів і замовлень з Google Sheets",
                "icon": "🔄",
                "estimated_time": "~12 хвилин"
            },
            {
                "id": "sheets_workspace",
                "name": "Воркспейс — злиття / додавання",
                "description": "Парсинг Воркспейс1: товари зі збігом ≥4 з 5 характеристик зливаються (номер → клони), решта додаються як нові (без номеру → '???')",
                "icon": "🔀",
                "estimated_time": "~1-2 хвилини"
            },
        ]

router = APIRouter()
logger = logging.getLogger(__name__)
if 'UnifiedParser' not in globals():
    logger.warning("UnifiedParser not available, using fallbacks")

# Active parsing tasks
active_parsing_tasks = {}

# Thread-safe lock для захисту глобального стану парсингу
_parser_lock = threading.Lock()

# Зв'язка job_id -> UnifiedParser для адресного скасування
job_parsers: Dict[int, "UnifiedParser"] = {}

# Auto-startup parsing (sheets_full_quick) — запускається при старті backend.
# Якщо користувач запускає manual парсинг — auto скасовується.
_auto_lock = threading.Lock()
_auto_job_id: Optional[int] = None


def _cancel_auto_if_running() -> None:
    """Кооперативне скасування auto-job: ставимо cancel_requested,
    progress_cb у _run_sheets_job побачить і кине виняток на наступному tick.
    Викликається на початку кожного manual-endpoint."""
    global _auto_job_id
    with _auto_lock:
        jid = _auto_job_id
    if not jid:
        return
    sess = SessionLocal()
    try:
        j = sess.query(ParsingJob).filter(ParsingJob.id == jid).first()
        if j and j.status in ("queued", "running"):
            j.cancel_requested = True
            j.status = "canceled"
            j.ended_at = datetime.datetime.utcnow()
            j.updated_at = j.ended_at
            j.current_step = "superseded by manual"
            existing = j.logs_head or ""
            j.logs_head = (existing + "\n[AUTO] superseded by manual parse")[-8000:]
            sess.commit()
            logger.info(f"Auto-parse job {jid} canceled — manual parse takes priority")
    except Exception:
        sess.rollback()
    finally:
        sess.close()
    with _auto_lock:
        if _auto_job_id == jid:
            _auto_job_id = None


def start_auto_full_quick() -> Optional[int]:
    """Запускає sheets_full_quick у фоні при старті backend.
    Скіпає, якщо вже є активний parsing job (queued/running).
    Job невидимий у UI: _run_sheets_job не бродкастить на legacy WS,
    а frontend не має jobId щоб показати ParsingStatus widget."""
    global _auto_job_id
    sess = SessionLocal()
    try:
        # Sweep orphaned jobs: status='running' but process is dead (older than 1h with no end).
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        orphans = (
            sess.query(ParsingJob)
            .filter(ParsingJob.status.in_(["queued", "running"]))
            .filter(ParsingJob.started_at < cutoff)
            .all()
        )
        for o in orphans:
            o.status = "failed"
            o.ended_at = datetime.datetime.utcnow()
            o.updated_at = o.ended_at
            o.current_step = "orphaned (sweep on boot)"
        if orphans:
            sess.commit()
            logger.info(f"Auto-parse swept {len(orphans)} orphaned jobs")

        active = (
            sess.query(ParsingJob)
            .filter(ParsingJob.status.in_(["queued", "running"]))
            .order_by(ParsingJob.id.desc())
            .first()
        )
        if active:
            logger.info(f"Auto-parse skipped: job {active.id} already {active.status}")
            return None
        job = ParsingJob(
            mode="sheets_full_quick",
            status="queued",
            current_step="auto-startup",
            logs_head="[AUTO] Started on app boot",
        )
        sess.add(job)
        sess.commit()
        sess.refresh(job)
        jid = job.id
    except Exception as e:
        sess.rollback()
        logger.warning(f"Auto-parse failed to create job: {e}")
        return None
    finally:
        sess.close()

    with _auto_lock:
        _auto_job_id = jid
    t = threading.Thread(target=_run_sheets_job, args=(jid, "full", "quick"), daemon=True)
    t.start()
    logger.info(f"Auto-parse started: job {jid} (sheets_full_quick, background)")
    return jid

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

@router.post("/test", tags=["parsing"])
async def test_parsing_job(mode: str = "quick_update", db: Session = Depends(get_db)):
    """ТЕСТ: Створює job і одразу повертає jobId без запуску парсингу."""
    job = ParsingJob(mode=mode, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"jobId": job.id, "message": "Test job created successfully"}

@router.post("/run", tags=["parsing"])
async def run_parsing_job(mode: str = "quick_update", params: Optional[Dict] = None, db: Session = Depends(get_db)):
    """Запускає парсинг. Sheets-режими (sheets_*) делегуються окремим handlers."""
    # Manual parse → скасовуємо auto-job (якщо є), щоб не дублювати навантаження
    _cancel_auto_if_running()
    # ── Sheets режими: sheets_<target>_<speed> ──────────────────────────────
    # mode examples: sheets_products_quick, sheets_orders_full, sheets_full_quick
    if mode.startswith("sheets_"):
        import threading
        parts = mode.split("_")  # ['sheets', target, speed]
        target = parts[1] if len(parts) > 1 else "full"
        speed  = parts[2] if len(parts) > 2 else "quick"
        job = ParsingJob(mode=mode, status="queued")
        db.add(job)
        db.commit()
        db.refresh(job)
        t = threading.Thread(target=_run_sheets_job, args=(job.id, target, speed), daemon=True)
        t.start()
        return {"jobId": job.id, "mode": mode, "target": target, "speed": speed}
    # Non-sheets modes: legacy UnifiedParser branch was removed — недосяжний з UI.
    raise HTTPException(status_code=400, detail=f"Unsupported parsing mode '{mode}'. Use one of sheets_products_*, sheets_orders_*, sheets_full_*.")

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
    # Якщо парсер ще працює – просимо його зупинитися
    try:
        parser = job_parsers.get(job_id)
        if parser:
            parser.cancel()
    except Exception:
        pass
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

@router.post("/cancel")
async def cancel_parsing():
    """Скасовує поточний парсинг незалежно від поточного індикатора стану."""
    global current_parser
    try:
        with _parser_lock:
            parser = current_parser
        if parser:
            logger.info("Cancel requested: invoking parser.cancel()")
            parser.cancel()
            try:
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
        logger.info("Cancel requested: no current_parser found; will mark latest queued/running job as canceled if present")
        # Спроба позначити останню queued/running джобу як скасовану, щоб runner не стартував
        try:
            sess = SessionLocal()
            try:
                job = (
                    sess.query(ParsingJob)
                    .filter(ParsingJob.status.in_(['queued', 'running']))
                    .order_by(ParsingJob.id.desc())
                    .first()
                )
                if job:
                    job.cancel_requested = True
                    job.status = 'canceled'
                    job.ended_at = datetime.datetime.utcnow()
                    job.updated_at = job.ended_at
                    job.current_step = 'canceled'
                    sess.commit()
                    return {"status": "canceled", "message": f"Job {job.id} canceled before start"}
            finally:
                sess.close()
        except Exception:
            logger.debug("Failed to mark latest job canceled", exc_info=True)
        return {"status": "idle", "message": "Активний парсер не знайдено"}
    except Exception as e:
        logger.error(f"Cancel error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

# (видалено мертві endpoints: /orders, /googlesheets, /orders-comprehensive
#  — superseded by POST /api/parsing/run?mode=sheets_*)

# ── Sheets parser endpoints ──────────────────────────────────────────────────
def _run_sheets_job(job_id: int, target: str, mode: str):
    """Background thread: run sheets_parser and update ParsingJob row."""
    import threading
    try:
        from scripts.sheets_parser import run_products_parsing, run_orders_parsing, run_full_parsing, run_workspace_parsing
    except ImportError:
        from backend.scripts.sheets_parser import run_products_parsing, run_orders_parsing, run_full_parsing, run_workspace_parsing

    sess = SessionLocal()
    try:
        job = sess.query(ParsingJob).filter(ParsingJob.id == job_id).first()
        if not job:
            return
        job.status = "running"
        job.started_at = datetime.datetime.utcnow()
        job.updated_at = job.started_at
        job.current_step = "initializing"
        sess.commit()

        import time as _time
        import re as _re
        _job_start = _time.time()
        _last_sheet = [None]
        _sheet_start = [_time.time()]

        def progress_cb(pct, msg):
            # Кооперативне скасування: окрема сесія тільки на read,
            # щоб виняток не перехопився внутрішнім except нижче
            cancel_check = SessionLocal()
            try:
                j_chk = cancel_check.query(ParsingJob).filter(ParsingJob.id == job_id).first()
                cancel_now = bool(j_chk and j_chk.cancel_requested)
            finally:
                cancel_check.close()
            if cancel_now:
                raise RuntimeError("canceled")

            s = SessionLocal()
            try:
                j = s.query(ParsingJob).filter(ParsingJob.id == job_id).first()
                if j:
                    j.percent = pct
                    j.current_step = str(msg)[:255]
                    j.updated_at = datetime.datetime.utcnow()
                    j.last_heartbeat_at = j.updated_at

                    # Parse done/total from message like "sheet_title: 60/72"
                    m = _re.search(r'(\d+)/(\d+)', str(msg))
                    if m:
                        done, total = int(m.group(1)), int(m.group(2))
                        j.processed_items = done
                        j.total_items = total

                        # Detect sheet change → reset per-sheet timer
                        sheet_id = str(msg).split(':')[0].strip() if ':' in str(msg) else ''
                        if sheet_id != _last_sheet[0]:
                            _last_sheet[0] = sheet_id
                            _sheet_start[0] = _time.time()

                        sheet_elapsed = _time.time() - _sheet_start[0]
                        if sheet_elapsed > 1 and done > 0:
                            j.items_per_sec = round(done / sheet_elapsed, 1)

                    # ETA from overall percent
                    elapsed = _time.time() - _job_start
                    if elapsed > 1 and pct > 0:
                        j.eta_seconds = max(0, int(elapsed * (100 - pct) / pct))

                    s.commit()
            except Exception:
                s.rollback()
            finally:
                s.close()

        if target == "products":
            result = run_products_parsing(sess, mode=mode, progress_cb=progress_cb)
        elif target == "orders":
            result = run_orders_parsing(sess, mode=mode, progress_cb=progress_cb)
        elif target == "workspace":
            result = run_workspace_parsing(sess, progress_cb=progress_cb)
        else:
            result = run_full_parsing(sess, mode=mode, progress_cb=progress_cb)

        # Strip internal tracking sets before logging
        for _key in ("seen_product_ids", "touched_product_ids"):
            if isinstance(result, dict):
                result.pop(_key, None)

        job = sess.query(ParsingJob).filter(ParsingJob.id == job_id).first()
        if job:
            job.status = "succeeded"
            job.percent = 100
            job.ended_at = datetime.datetime.utcnow()
            job.updated_at = job.ended_at
            job.current_step = "syncing statuses"
            job.logs_head = str(result)[:8000]
            sess.commit()

        # НЕ запускаємо sync_product_statuses — журнал є джерелом правди
        # для статусів товарів. sync_product_statuses перезаписувала статуси
        # на основі кількості order_items, ігноруючи ручні зміни користувача
        # в журналі (наприклад, повернення товару до "Непродано").
        # Колонка "В наявності" (available_qty) вже коректно показує
        # sold_count vs quantity незалежно від statusid.

        job = sess.query(ParsingJob).filter(ParsingJob.id == job_id).first()
        if job:
            job.current_step = "done"
            sess.commit()

    except Exception as e:
        is_cancel = (str(e) == "canceled")
        if is_cancel:
            logger.info(f"Sheets job {job_id} canceled cooperatively")
        else:
            logger.exception(f"Sheets job {job_id} failed: {e}")
        s2 = SessionLocal()
        try:
            j = s2.query(ParsingJob).filter(ParsingJob.id == job_id).first()
            if j:
                if is_cancel or j.status == "canceled":
                    j.status = "canceled"
                    j.current_step = "canceled"
                else:
                    j.status = "failed"
                    j.error_summary = str(e)[:500]
                j.ended_at = datetime.datetime.utcnow()
                j.updated_at = j.ended_at
                s2.commit()
        finally:
            s2.close()
    finally:
        # Очищаємо посилання на auto-job, якщо це був він
        global _auto_job_id
        with _auto_lock:
            if _auto_job_id == job_id:
                _auto_job_id = None
        sess.close()


@router.post("/sheets/products", tags=["parsing"])
async def sheets_parse_products(mode: str = "quick", db: Session = Depends(get_db)):
    """
    Парсинг товарів з Google Sheets (Журнал) → products.
    mode: quick (останні 30 аркушів) | full (всі аркуші)
    """
    import threading
    _cancel_auto_if_running()
    job = ParsingJob(mode=f"sheets_products_{mode}", status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    t = threading.Thread(target=_run_sheets_job, args=(job.id, "products", mode), daemon=True)
    t.start()
    return {"jobId": job.id, "mode": mode, "target": "products"}


@router.post("/sheets/orders", tags=["parsing"])
async def sheets_parse_orders(mode: str = "quick", db: Session = Depends(get_db)):
    """
    Парсинг замовлень з Google Sheets (Замовлення) → orders + order_items + clients.
    mode: quick (останні 30 аркушів) | full (всі аркуші)
    """
    import threading
    _cancel_auto_if_running()
    job = ParsingJob(mode=f"sheets_orders_{mode}", status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    t = threading.Thread(target=_run_sheets_job, args=(job.id, "orders", mode), daemon=True)
    t.start()
    return {"jobId": job.id, "mode": mode, "target": "orders"}


@router.post("/sheets/full", tags=["parsing"])
async def sheets_parse_full(mode: str = "quick", db: Session = Depends(get_db)):
    """
    Повний парсинг: спочатку товари, потім замовлення, потім воркспейс.
    mode: quick | full
    """
    import threading
    _cancel_auto_if_running()
    job = ParsingJob(mode=f"sheets_full_{mode}", status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    t = threading.Thread(target=_run_sheets_job, args=(job.id, "full", mode), daemon=True)
    t.start()
    return {"jobId": job.id, "mode": mode, "target": "full"}


@router.post("/sheets/workspace", tags=["parsing"])
async def sheets_parse_workspace(db: Session = Depends(get_db)):
    """
    Парсинг Воркспейс1 → merge/додавання в products.
    Товари зі співпадінням ≥4 з 5 характеристик → merge (номер до clonednumbers).
    Без співпадіння → новий запис (без номеру → productnumber='???').
    """
    import threading
    _cancel_auto_if_running()
    job = ParsingJob(mode="sheets_workspace", status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    t = threading.Thread(target=_run_sheets_job, args=(job.id, "workspace", "quick"), daemon=True)
    t.start()
    return {"jobId": job.id, "target": "workspace"}


@router.post("/sheets/reset-products", tags=["parsing"])
async def reset_products_for_reparse(db: Session = Depends(get_db)):
    """
    Безпечне очищення таблиці products (і order_items що посилаються на неї)
    перед чистим перепарсингом з новою логікою ростовок/дублікатів.
    НЕ видаляє clients, orders, reference tables.
    """
    from sqlalchemy import text
    try:
        # Step 1: nullify FK references so DELETE doesn't fail (ON DELETE NO ACTION)
        db.execute(text("UPDATE order_items SET product_id = NULL WHERE product_id IS NOT NULL"))
        # Step 2: delete all products (safe — no more FK references point at them)
        db.execute(text("DELETE FROM products"))
        # Step 3: reset PK sequence so IDs start from 1 again
        db.execute(text("ALTER SEQUENCE products_id_seq RESTART WITH 1"))
        db.commit()
        # Verify orders and order_items are intact
        oi_count = db.execute(text("SELECT COUNT(*) FROM order_items")).scalar()
        o_count  = db.execute(text("SELECT COUNT(*) FROM orders")).scalar()
        return {
            "status": "ok",
            "message": "Таблиця products очищена. Замовлення та позиції збережено.",
            "orders_intact": o_count,
            "order_items_intact": oi_count,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Помилка очищення: {e}")


# (видалено: /parsing/products, /parsing/orders — недосяжні з UI,
#  superseded by POST /api/parsing/run?mode=sheets_products_*/sheets_orders_*)

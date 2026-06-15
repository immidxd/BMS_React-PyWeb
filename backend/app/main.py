import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import sys
import os
# Ensure both backend package and project root are on sys.path for absolute imports
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)
if PROJECT_ROOT_DIR not in sys.path:
    sys.path.append(PROJECT_ROOT_DIR)

from models.database import engine, Base, init_db
from routers import (
    products,
    clients,
    orders,
    payment_statuses,
    order_statuses,
    delivery_methods,
    parsing,
    search,
)
try:
    from routers import deliveries  # optional
except Exception:
    deliveries = None
try:
    from routers import suppliers  # optional
except Exception:
    suppliers = None
try:
    from routers import shipments  # optional
except Exception:
    shipments = None
try:
    from routers import statistics  # optional
except Exception:
    statistics = None
try:
    from routers import brands  # optional
except Exception:
    brands = None
try:
    from routers import publications  # optional — Telegram/social media publications
except Exception:
    publications = None
try:
    from routers import merge_candidates  # optional — workspace merge UX
except Exception:
    merge_candidates = None

# НАЛАШТУВАННЯ ЛОГУВАННЯ
# Використовуємо абсолютний шлях і гарантуємо наявність директорії,
# щоб запуск не ламався, коли CWD відрізняється (наприклад, у PyWebView)
LOG_DIR = os.path.join(PROJECT_ROOT_DIR, 'backend', 'app')
LOG_FILE = os.path.join(LOG_DIR, 'app.log')
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    pass

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# Ensure Google Sheets creds are available to parsers (single source of truth)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DEFAULT_MCP_KEY = os.path.join(PROJECT_ROOT, 'mcp-google-sheets', 'working_credentials.json')
if not os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE') and os.path.exists(DEFAULT_MCP_KEY):
    os.environ['GOOGLE_SHEETS_CREDENTIALS_FILE'] = DEFAULT_MCP_KEY
    logger.info(f"GOOGLE_SHEETS_CREDENTIALS_FILE set to {DEFAULT_MCP_KEY}")

# Database initialization moved to separate script for faster startup
logger.info("Database connection ready")

app = FastAPI()

# Add CORS middleware with explicit origins
allowed_origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]
logger.info(f"Setting up CORS middleware with origins: {allowed_origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prevent caching of API responses + index.html (PyWebView кешує index.html → 404 після нового білду)
@app.middleware("http")
async def no_cache_api(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    is_api = path.startswith("/api/")
    is_index = path in ("/", "/index.html") or (not path.startswith("/static/") and "." not in path.split("/")[-1])
    if is_api or is_index:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception handler caught: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

# Health check endpoint
@app.get("/api/health")
async def health_check():
    logger.debug("Health check endpoint called")
    return {"status": "ok"}

# Include routers directly (no prefix needed as routes are now fully specified)
app.include_router(products.router)
app.include_router(clients.router, tags=["clients"])  # routes define full /api prefix
app.include_router(orders.router, tags=["orders"])   # routes define full /api prefix
app.include_router(payment_statuses.router, tags=["payment-statuses"])  # routes define full /api prefix
app.include_router(order_statuses.router, tags=["order-statuses"])      # routes define full /api prefix
app.include_router(delivery_methods.router, tags=["delivery-methods"])  # routes define full /api prefix
app.include_router(parsing.router, prefix="/api/parsing", tags=["parsing"])  # router exposes paths like /parsing/.. → final /api/parsing/..
app.include_router(search.router, tags=["search"])  # search router already has /api/search prefix
if suppliers:
    app.include_router(suppliers.router, tags=["suppliers"])  # routes already prefixed with /api
if deliveries:
    app.include_router(deliveries.router, tags=["deliveries"])  # routes already prefixed with /api
if shipments:
    app.include_router(shipments.router, tags=["shipments"])  # routes already prefixed with /api
if statistics:
    app.include_router(statistics.router, tags=["statistics"])  # routes already prefixed with /api
if brands:
    app.include_router(brands.router, tags=["brands"])  # routes already prefixed with /api
if publications:
    app.include_router(publications.router, tags=["publications"])  # routes already prefixed with /api
if merge_candidates:
    app.include_router(merge_candidates.router, tags=["merge-candidates"])  # routes already prefixed with /api

# Mount product images directory (local + Google Drive overlay; abstraction in services/product_images.py)
try:
    from services.product_images import get_images_dir, URL_PREFIX as IMG_URL_PREFIX
except ImportError:
    from backend.services.product_images import get_images_dir, URL_PREFIX as IMG_URL_PREFIX
_images_dir = get_images_dir()
if os.path.isdir(_images_dir):
    logger.info(f"Mounting LOCAL product images from {_images_dir} → {IMG_URL_PREFIX}")
    app.mount(IMG_URL_PREFIX, StaticFiles(directory=_images_dir), name="product-images")
else:
    logger.info(f"Local product images dir not found ({_images_dir}) — using Drive only")

# Drive image proxy: streams bytes from Google Drive with disk-cache
try:
    from services.product_images_drive import (
        get_drive_file_bytes, get_drive_index_stats, invalidate_drive_index,
        URL_PREFIX_DRIVE,
    )
except ImportError:
    try:
        from backend.services.product_images_drive import (
            get_drive_file_bytes, get_drive_index_stats, invalidate_drive_index,
            URL_PREFIX_DRIVE,
        )
    except ImportError:
        get_drive_file_bytes = None
        URL_PREFIX_DRIVE = "/product-images-drive"

if get_drive_file_bytes is not None:
    from fastapi import Response, HTTPException

    @app.get(URL_PREFIX_DRIVE + "/{file_id}")
    async def stream_drive_image(file_id: str):
        """Proxy: GET image bytes from Google Drive (cached on disk)."""
        result = get_drive_file_bytes(file_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Image not found in Drive")
        data, mime = result
        return Response(
            content=data, media_type=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.post("/api/product-images-drive/refresh")
    async def refresh_drive_index():
        """Manually rebuild Drive index (e.g. after uploading new photos)."""
        invalidate_drive_index()
        return {"ok": True, "stats": get_drive_index_stats()}

    @app.get("/api/product-images-drive/stats")
    async def drive_index_stats():
        return get_drive_index_stats()

    logger.info(f"Drive image proxy mounted: {URL_PREFIX_DRIVE}/<file_id>")

    @app.on_event("startup")
    async def _prewarm_drive_index():
        """Прогріти Drive-індекс фото у фоні на старті, щоб перша відкрита
        картка не чекала повний скан Drive синхронно."""
        try:
            try:
                from services.product_images_drive import prewarm_drive_index
            except ImportError:
                from backend.services.product_images_drive import prewarm_drive_index
            prewarm_drive_index()
            logger.info("Drive image index prewarm triggered (background)")
        except Exception as e:
            logger.warning(f"Drive index prewarm failed: {e}")

# Mount static files from frontend build if available
frontend_build_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/build"))


class SPAStaticFiles(StaticFiles):
    """StaticFiles, що віддає index.html (і будь-який HTML) з no-cache.

    Чому: CRA-білд хешує імена JS/CSS-чанків (їх можна кешувати вічно), АЛЕ
    index.html посилається на ці хеші й має оновлюватись щобілда. Без
    Cache-Control браузер/PyWebView застосовує евристичне кешування і тримає
    старий index.html → старий бандл (симптом: зміни в UI «не застосовуються»).
    no-cache змушує ревалідувати index.html щоразу; хешовані ассети лишаються
    кешованими (immutable).
    """
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        ctype = response.headers.get("content-type", "")
        if "text/html" in ctype:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif "/static/" in (scope.get("path") or ""):
            # Хешовані ассети — безпечно кешувати надовго
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if os.path.exists(frontend_build_dir):
    logger.info(f"Mounting static files from {frontend_build_dir}")
    app.mount("/", SPAStaticFiles(directory=frontend_build_dir, html=True))
else:
    logger.warning("Frontend build directory not found, static files will not be served")

# ── Auto-startup parse ────────────────────────────────────────────────────────
# При кожному запуску backend автоматично запускаємо "Швидкий повний парсинг
# (товар+замовлення)" у фоновому режимі. Користувач не бачить цього в UI
# (немає jobId у frontend стейті). Якщо користувач вручну запустить парсинг —
# auto-job скасується кооперативно (пріоритет manual).
@app.on_event("startup")
async def _auto_startup_parse():
    import asyncio

    async def _delayed_start():
        # Невелика затримка щоб додаток повністю стартував і встиг ініціалізуватись
        await asyncio.sleep(8)
        try:
            from routers.parsing import start_auto_full_quick
            start_auto_full_quick()
        except Exception as e:
            logger.warning(f"Auto-parse startup failed: {e}")

    asyncio.create_task(_delayed_start())


# ── Auto-sync publications (Telegram) ─────────────────────────────────────────
# Single sync cycle: scan channels → relink → recovery.
# Triggered on startup (after 15s delay) and periodically every PERIOD seconds.
# Failures are logged and do not crash the process; next cycle retries.
async def _publications_sync_cycle() -> None:
    import os
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    if not all([api_id, api_hash, phone]):
        logger.info("Publications-sync skipped: TG creds not set")
        return

    try:
        from services.telegram_service import TelegramScanner
        from models.database import SessionLocal
    except ImportError:
        from backend.services.telegram_service import TelegramScanner
        from backend.models.database import SessionLocal
    from sqlalchemy import text as _sql_text

    scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
    if not await scanner.connect():
        logger.warning("Publications-sync: TG connect failed — will retry next cycle")
        return

    db = SessionLocal()
    try:
        totals = {"posts_scanned": 0, "new_posts_saved": 0}
        for chat_id, info in TelegramScanner.KNOWN_CHANNELS.items():
            try:
                res = await scanner.scan_channel(db, str(chat_id), info["type"])
                if isinstance(res, dict) and "error" not in res:
                    totals["posts_scanned"] += int(res.get("posts_scanned", 0) or 0)
                    totals["new_posts_saved"] += int(res.get("new_posts_saved", 0) or 0)
                else:
                    logger.warning(f"Pub-sync: channel {chat_id} returned {res}")
            except Exception as ce:
                logger.warning(f"Pub-sync: channel {chat_id} failed: {ce}")
        logger.info(f"Pub-sync: {totals}")

        # Auto-relink
        try:
            try:
                from routers.publications import _RELINK_SQL
            except ImportError:
                from backend.routers.publications import _RELINK_SQL
            r = db.execute(_sql_text(_RELINK_SQL))
            db.commit()
            logger.info(f"Pub-relink: {r.rowcount} posts relinked")
        except Exception as re:
            logger.warning(f"Pub-relink failed: {re}")
            db.rollback()

        # Recovery — phantom archives back to published
        stats = await scanner.verify_archived_posts(db, limit=1000)
        logger.info(f"Pub-recovery: {stats}")
    except Exception as e:
        logger.warning(f"Publications-sync inner failed: {e}")
    finally:
        db.close()
        try:
            await scanner.disconnect()
        except Exception:
            pass


# ── Auto-sync OLX adverts ─────────────────────────────────────────────────────
# Незалежний від Telegram (TG може бути не налаштований). No-op якщо OLX не
# сконфігуровано/не авторизовано. Блокуючі HTTP-запити OLX — у thread executor,
# щоб не стопорити event loop. refresh-токен оновлюється всередині sync_adverts.
async def _olx_sync_cycle() -> None:
    import asyncio

    def _run():
        try:
            from services import olx_service
            from models.database import SessionLocal
            from routers.publications import _RELINK_OLX_SQL
        except ImportError:
            from backend.services import olx_service
            from backend.models.database import SessionLocal
            from backend.routers.publications import _RELINK_OLX_SQL
        from sqlalchemy import text as _sql_text
        if not olx_service.is_configured():
            return
        db = SessionLocal()
        try:
            res = olx_service.sync_adverts(db)
            if res.get("ok"):
                try:
                    r = db.execute(_sql_text(_RELINK_OLX_SQL))
                    db.commit()
                    logger.info(f"OLX-sync: {res} relinked={r.rowcount}")
                except Exception as re:
                    db.rollback()
                    logger.warning(f"OLX-relink failed: {re}")
            else:
                logger.info(f"OLX-sync skipped: {res.get('error')}")
        finally:
            db.close()

    try:
        await asyncio.get_event_loop().run_in_executor(None, _run)
    except Exception as e:
        logger.warning(f"OLX-sync failed: {e}")


@app.on_event("startup")
async def _auto_startup_publications_refresh():
    """Initial sync at startup + periodic loop every N seconds."""
    import asyncio
    import os

    # Configurable via env (default 30 minutes)
    period_sec = int(os.getenv("PUBLICATIONS_SYNC_PERIOD_SEC", "1800"))

    async def _run_all_cycles():
        # Telegram + OLX — кожен у власному try, щоб збій одного не блокував інший.
        for label, fn in (("Publications", _publications_sync_cycle), ("OLX", _olx_sync_cycle)):
            try:
                await fn()
            except Exception as e:
                logger.warning(f"{label}-sync cycle failed: {e}")

    async def _initial_then_periodic():
        # Initial run: wait 15s for the rest of the app to come up
        await asyncio.sleep(15)
        await _run_all_cycles()
        # Periodic loop
        while True:
            await asyncio.sleep(period_sec)
            await _run_all_cycles()

    asyncio.create_task(_initial_then_periodic())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
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

# Mount product images directory (local for now, cloud-ready abstraction in services/product_images.py)
try:
    from services.product_images import get_images_dir, URL_PREFIX as IMG_URL_PREFIX
except ImportError:
    from backend.services.product_images import get_images_dir, URL_PREFIX as IMG_URL_PREFIX
_images_dir = get_images_dir()
if os.path.isdir(_images_dir):
    logger.info(f"Mounting product images from {_images_dir} → {IMG_URL_PREFIX}")
    app.mount(IMG_URL_PREFIX, StaticFiles(directory=_images_dir), name="product-images")
else:
    logger.warning(f"Product images dir not found: {_images_dir} — image gallery will be empty")

# Mount static files from frontend build if available
frontend_build_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/build"))
if os.path.exists(frontend_build_dir):
    logger.info(f"Mounting static files from {frontend_build_dir}")
    app.mount("/", StaticFiles(directory=frontend_build_dir, html=True))
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
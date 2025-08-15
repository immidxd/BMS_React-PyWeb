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
)
try:
    from routers import deliveries  # optional
except Exception:
    deliveries = None
try:
    from routers import suppliers  # optional
except Exception:
    suppliers = None

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('backend/app/app.log')
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
# В розробці дозволяємо запити з розширеного списку origins
logger.info("Setting up CORS middleware")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Simplify by allowing all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception handler caught: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
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
if suppliers:
    app.include_router(suppliers.router, tags=["suppliers"])  # routes already prefixed with /api
if deliveries:
    app.include_router(deliveries.router, tags=["deliveries"])  # routes already prefixed with /api

# Mount static files from frontend build if available
frontend_build_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/build"))
if os.path.exists(frontend_build_dir):
    logger.info(f"Mounting static files from {frontend_build_dir}")
    app.mount("/", StaticFiles(directory=frontend_build_dir, html=True))
else:
    logger.warning("Frontend build directory not found, static files will not be served")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 
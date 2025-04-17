import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.models.database import engine, Base, init_db
from backend.routers import (
    products,
    clients,
    orders,
    payment_statuses,
    order_statuses,
    delivery_methods,
    parsing
)

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

# Create tables and initialize database
try:
    # Create database tables
    Base.metadata.create_all(bind=engine)
    # Initialize with seed data
    init_db()
    logger.info("Database initialized successfully")
except Exception as e:
    logger.error(f"Error initializing database: {e}")

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
app.include_router(clients.router, prefix="/api/clients", tags=["clients"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(payment_statuses.router, prefix="/api/payment-statuses", tags=["payment-statuses"])
app.include_router(order_statuses.router, prefix="/api/order-statuses", tags=["order-statuses"])
app.include_router(delivery_methods.router, prefix="/api/delivery-methods", tags=["delivery-methods"])
app.include_router(parsing.router, prefix="/api/parsing", tags=["parsing"])

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
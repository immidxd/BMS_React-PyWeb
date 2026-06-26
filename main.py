#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import threading
import time
import uvicorn
import webview
from dotenv import load_dotenv
import socket
import http.client

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def setup_environment():
    # Load environment variables from .env file
    load_dotenv()
    
def start_backend():
    """Start the FastAPI backend server"""
    try:
        # Set host to 0.0.0.0 to allow connections from other devices
        uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, log_level="info")
    except Exception as e:
        logger.error(f"Failed to start backend server: {e}")
        sys.exit(1)

def wait_for_backend(max_retries=240, delay=0.5):
    """Wait for backend server to become available.

    Uses 127.0.0.1 explicitly (not 'localhost') to avoid macOS IPv6 resolution
    issues where localhost → ::1 but uvicorn listens on 127.0.0.1 only.

    max_retries=240 (≈120с): стартові події бекенда (Telegram pub-sync + парсинг,
    подеколи з повторами через SSL) блокують відповідь /api/health на ~60-90с.
    15с (старе значення) здавалося завчасно й гасило daemon-бекенд, який інакше
    піднявся б. Збільшено, щоб лаунчер дочекався. (Прискорення старту — окремо:
    винести блокуючу синхронізацію у фон, щоб /api/health відповідав одразу.)
    """
    for i in range(max_retries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=2)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            conn.close()
            logger.info(f"Backend is available! (HTTP {resp.status})")
            return True
        except Exception as e:
            logger.debug(f"Health check failed: {type(e).__name__}: {e}")
        logger.info(f"Waiting for backend to start (attempt {i+1}/{max_retries})...")
        time.sleep(delay)
    logger.error("Backend failed to start within expected time")
    return False

def main():
    """
    Main entry point for the application.
    Starts the FastAPI backend and loads the React frontend in a PyWebView window.
    """
    setup_environment()
    
    # Start the backend server in a separate thread
    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()
    
    # Wait for backend to become available
    if not wait_for_backend():
        logger.error("Exiting due to backend unavailability")
        sys.exit(1)
    
    # Get frontend URL (always use the backend's static file server in production).
    # Додаємо унікальний ?v=<час старту> — WKWebView не зможе віддати закешований
    # index.html для нової (унікальної) URL, тож завжди вантажиться свіжий бандл.
    # SPA на "/" віддає index.html незалежно від query, тож параметр безпечний.
    frontend_url = f"http://localhost:8000/?v={int(time.time())}"
    logger.info(f"Connecting to frontend at {frontend_url}")

    # ── КРИТИЧНО: чистимо HTTP-кеш WKWebView на диску ДО старту вікна ──────────
    # WKWebView евристично кешує index.html у ~/Library/Caches/org.python.python/WebKit
    # і цей кеш переживає рестарти процесу. Через це новий білд «не застосовується»
    # (старий index.html → старі JS-чанки → старий UI). caches.delete() (Cache
    # Storage API) це НЕ чистить. Видаляємо HTTP-кеш на рівні файлів.
    # localStorage (налаштування колонок) лежить в ІНШІЙ теці
    # (~/Library/WebKit/org.python.python/.../LocalStorage) — її НЕ чіпаємо.
    def clear_http_cache_on_disk():
        import shutil
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, "Library/Caches/org.python.python/WebKit"),
            os.path.join(home, "Library/Caches/com.apple.python/WebKit"),
        ]
        for path in candidates:
            if os.path.isdir(path):
                try:
                    shutil.rmtree(path)
                    logger.info(f"Cleared WKWebView HTTP cache: {path}")
                except Exception as e:
                    logger.warning(f"Could not clear cache {path}: {e}")

    clear_http_cache_on_disk()

    # Очистити Cache Storage API (доповнює дискову чистку вище)
    def clear_webview_cache(window):
        try:
            window.evaluate_js(
                "if(window.caches){caches.keys().then(ks=>ks.forEach(k=>caches.delete(k)));}"
            )
        except Exception:
            pass

    # Create the window
    logger.info("Starting PyWebView window")
    window = webview.create_window(
        "Product and Order Management System",
        frontend_url,
        width=1200,
        height=800,
        min_size=(800, 600)
    )

    # Start the PyWebView application
    webview.start(clear_webview_cache, window, debug=False)

if __name__ == "__main__":
    main()
    
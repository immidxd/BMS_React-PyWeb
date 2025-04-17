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
import requests

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

def wait_for_backend(max_retries=30, delay=1):
    """Wait for backend server to become available"""
    for i in range(max_retries):
        try:
            response = requests.get("http://localhost:8000/api/health")
            if response.status_code == 200:
                logger.info("Backend is available!")
                return True
        except requests.exceptions.ConnectionError:
            pass
        
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
    
    # Get frontend URL (always use the backend's static file server in production)
    frontend_url = "http://localhost:8000"
    logger.info(f"Connecting to frontend at {frontend_url}")
    
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
    webview.start(debug=True)

if __name__ == "__main__":
    main() 
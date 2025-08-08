#!/bin/bash

# Script to run the Product and Order Management System
# Usage: ./run.sh [dev|prod]

set -e

# Navigate to the project directory
cd "$(dirname "$0")"

# Check for virtual environment and create if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install requirements if needed
if [ ! -f "venv/.requirements_installed" ]; then
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
    touch venv/.requirements_installed
fi

# Install frontend dependencies if needed
if [ ! -d "frontend/node_modules" ]; then
    echo "Installing frontend dependencies..."
    cd frontend
    npm install
    cd ..
fi

# Determine mode (default: dev)
MODE="dev"
if [ "$1" == "prod" ]; then
    MODE="prod"
fi

# Run the application
echo "Starting application in $MODE mode..."
python run.py --mode $MODE 
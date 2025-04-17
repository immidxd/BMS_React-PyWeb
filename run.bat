@echo off
REM Script to run the Product and Order Management System on Windows
REM Usage: run.bat [dev|prod]

REM Navigate to the project directory
cd /d "%~dp0"

REM Check for virtual environment and create if needed
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install requirements if needed
if not exist venv\.requirements_installed (
    echo Installing Python dependencies...
    pip install -r requirements.txt
    echo. > venv\.requirements_installed
)

REM Install frontend dependencies if needed
if not exist frontend\node_modules (
    echo Installing frontend dependencies...
    cd frontend
    call npm install
    cd ..
)

REM Determine mode (default: dev)
set MODE=dev
if "%1"=="prod" (
    set MODE=prod
)

REM Run the application
echo Starting application in %MODE% mode...
python run.py --mode %MODE% 
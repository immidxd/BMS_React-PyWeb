# Product and Order Management System

A desktop application built with React, FastAPI, and PyWebView that allows users to manage products and orders. This project is a migration from a PyQt6 desktop application to a modern web-based architecture while maintaining a desktop experience.

## Features

- **Products Management**: Browse, filter, search, and manage products
- **Orders Management**: Track, filter, and update orders
- **Parsing Integration**: Scrape and import product data from various sources
- **Desktop Experience**: Runs as a desktop application while using web technologies
- **Dark/Light Theme**: Supports both dark and light themes with seamless switching
- **Responsive UI**: Modern interface with responsive design
- **Asynchronous Operations**: Non-blocking UI during operations like filtering and parsing

## Architecture

- **Frontend**: React with TypeScript, styled-components, React Query
- **Backend**: FastAPI with SQLAlchemy ORM
- **Desktop Integration**: PyWebView to embed web content in a native window
- **Database**: SQLite (default) with support for other SQL databases via SQLAlchemy

## Project Structure

```
react-fastapi-app/
├── backend/
│   ├── app/
│   │   └── main.py              # FastAPI application entry point
│   ├── models/                  # SQLAlchemy models
│   ├── routers/                 # API endpoints
│   ├── schemas/                 # Pydantic schemas
│   ├── services/                # Business logic
│   │   └── parsing_service.py   # Parsing functionality
│   └── utils/                   # Utility functions
├── frontend/
│   ├── build/                   # Production build output
│   ├── public/                  # Static assets
│   └── src/
│       ├── components/          # React components
│       │   ├── common/          # Shared components
│       │   └── parsing/         # Parsing-related components
│       ├── contexts/            # React contexts
│       ├── hooks/               # Custom React hooks
│       ├── pages/               # Page components
│       ├── services/            # API services
│       └── utils/               # Utility functions
└── main.py                      # Application entry point
```

## Prerequisites

- Python 3.9+
- Node.js 14+
- npm or yarn

## Installation

1. Clone the repository:

```bash
git clone https://github.com/yourusername/product-order-management.git
cd product-order-management
```

2. Set up the Python environment:

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

3. Set up the frontend:

```bash
cd frontend
npm install
# or
yarn install
```

## Development

### Running in Development Mode

1. Start the FastAPI backend:

```bash
# From the project root
python main.py
```

2. In a separate terminal, start the React development server:

```bash
cd frontend
npm start
# or
yarn start
```

3. The application should open automatically, or you can navigate to `http://localhost:3000` in your browser.

### Environment Variables

Create a `.env` file in the project root with the following variables:

```
DATABASE_URL=sqlite:///./local_database.db
DEV_MODE=true
```

## Building for Production

1. Build the React frontend:

```bash
cd frontend
npm run build
# or
yarn build
```

2. Run the application:

```bash
# From the project root
python main.py
```

## Running the Application

The application can be run in two modes:

### Development Mode

Development mode runs the React application using the React development server with hot reloading and debugging tools enabled.

**On macOS/Linux:**
```bash
# Make the script executable (first time only)
chmod +x run.sh

# Run in development mode
./run.sh
```

**On Windows:**
```bash
# Run in development mode
run.bat
```

### Production Mode

Production mode builds the React application as static files and serves them through the FastAPI backend.

**On macOS/Linux:**
```bash
# Run in production mode
./run.sh prod
```

**On Windows:**
```bash
# Run in production mode
run.bat prod
```

### Manual Setup

If you prefer to set up and run the application manually:

1. **Setup Python Environment:**
   ```bash
   # Create a virtual environment
   python -m venv venv
   
   # Activate the virtual environment (macOS/Linux)
   source venv/bin/activate
   
   # Activate the virtual environment (Windows)
   venv\Scripts\activate
   
   # Install requirements
   pip install -r requirements.txt
   ```

2. **Setup Frontend:**
   ```bash
   # Navigate to frontend directory
   cd frontend
   
   # Install dependencies
   npm install
   
   # For development mode, start the React development server
   npm start
   
   # For production mode, build the React application
   npm run build
   ```

3. **Run the Application:**
   ```bash
   # In a new terminal with the virtual environment activated
   python run.py --mode dev  # For development mode
   # OR
   python run.py --mode prod  # For production mode
   ```

## Packaging for Distribution

You can create a standalone executable using PyInstaller:

```bash
# Install PyInstaller
pip install pyinstaller

# Create a single executable file
pyinstaller --name="ProductOrderManager" --onefile --windowed --add-data "frontend/build:frontend/build" run.py
```

The executable will be created in the `dist` directory.

## License

[MIT License](LICENSE)

## Credits

This project is a migration of a PyQt6 desktop application to a modern web-based architecture, retaining all the original functionality while improving the user experience and maintainability. 
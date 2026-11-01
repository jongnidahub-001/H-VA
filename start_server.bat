@echo off
echo Starting Nida Bot FastAPI Server...
echo.
echo Opening browser to http://localhost:8000
echo Press Ctrl+C to stop the server
echo.

call venv\Scripts\activate
start http://localhost:8000
python main.py

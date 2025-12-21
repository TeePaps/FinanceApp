@echo off
REM Restart the Flask development server

echo Stopping existing server...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq app.py" 2>nul
timeout /t 2 /nobreak >nul

echo Starting server...
cd /d "%~dp0"
start /B python app.py

echo Server restarting on http://localhost:8080
timeout /t 3 /nobreak >nul
echo Server started.

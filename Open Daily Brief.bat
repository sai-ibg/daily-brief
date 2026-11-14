@echo off
rem Starts the local Daily Brief server (if it is not running) and opens the page.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" server.py
timeout /t 2 /nobreak >nul
start "" "http://localhost:8765/"

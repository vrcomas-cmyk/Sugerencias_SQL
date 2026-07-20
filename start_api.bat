@echo off
cd /d "%~dp0"
uvicorn api:app --port 8000 >> "%~dp0api_log.txt" 2>&1

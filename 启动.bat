@echo off
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "PYTHONPATH=%ROOT%src"

if not exist "%PY%" (
    echo [ERROR] Missing .venv - run: python -m venv .venv ^& pip install -r requirements.txt
    pause
    exit /b 1
)

"%PY%" -m hdr_converter
if errorlevel 1 (
    echo.
    echo [ERROR] Exit code: %errorlevel%
    pause
)

@echo off
TITLE USMA v0.4.3 - Portable Launcher

cd /d "%~dp0"

echo ========================================
echo USMA v0.4.2 - Portable Edition
echo ========================================
echo.

REM Just launch directly
python\python.exe monitor_app.py

echo.
echo Application closed.
pause

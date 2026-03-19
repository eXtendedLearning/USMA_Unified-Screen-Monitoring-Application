@echo off
cd /d "%~dp0"
REM Extract version from line 20 of monitor_app.py (docstring: "USMA ... - vX.Y.Z (...)")
for /f "delims=" %%i in ('python\python.exe -c "line=open('monitor_app.py',encoding='utf-8').readlines()[19]; v=line.split(' - v')[1].split(' ')[0].strip(); print(v)"') do set APP_VERSION=%%i
TITLE USMA v%APP_VERSION% - Portable Launcher

echo ========================================
echo USMA v%APP_VERSION% - Portable Edition
echo ========================================
echo.

REM Just launch directly
python\python.exe monitor_app.py

echo.
echo Application closed.
pause

@echo off
cd /d "%~dp0"

IF EXIST "python\python.exe" (
    SET PYTHON_EXEC=python\python.exe
) ELSE (
    SET PYTHON_EXEC=python
)

SET PYTHONPATH=%~dp0

REM Extract version dynamically from the new package architecture
for /f "delims=" %%i in ('%PYTHON_EXEC% -c "import sys,os; sys.path.insert(0, os.path.dirname(os.path.abspath('monitor_app.py'))); from usma import __version__; print(__version__)"') do set APP_VERSION=%%i
TITLE USMA v%APP_VERSION% - Launcher

echo ========================================
echo USMA v%APP_VERSION% 
echo ========================================
echo.

REM Just launch directly via proxy payload
%PYTHON_EXEC% monitor_app.py

echo.
echo Application closed.
pause

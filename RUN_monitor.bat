@echo off
TITLE USMA Monitor Application Launcher

:: ============================================================================
::  USMA - Robust Application Launcher & Setup Wizard (v2)
::
::  This script ensures the environment is set up correctly before running.
::  It will:
::  1. Verify Python is installed and in the PATH.
::  2. Create a virtual environment ('sm_venv') if it doesn't exist.
::  3. Install all required Python packages from 'requirements.txt'.
::  4. Verify Tesseract-OCR (external program) is installed in the PATH.
::  5. Launch the main application.
:: ============================================================================

REM --- Step 1: Navigate to the script's directory ---
echo Navigating to script directory...
cd /d "%~dp0"
cls

REM --- Step 2: Check for Python installation ---
echo ============================================================================
echo [STEP 1/5] Checking for Python...
echo ============================================================================
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not found in the system PATH.
    echo Please install Python 3.x from python.org
    echo Make sure to check the box "Add Python to PATH" during installation.
    pause
    exit /b
)
echo Python found!
echo.
timeout /t 1 /nobreak >nul

REM --- Step 3: Check for and create the virtual environment ---
echo ============================================================================
echo [STEP 2/5] Setting up Python virtual environment...
echo ============================================================================
IF NOT EXIST "sm_venv" (
    echo [SETUP] Virtual environment 'sm_venv' not found. Creating it now...
    python -m venv sm_venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b
    )
    echo [SETUP] Virtual environment created successfully.
) ELSE (
    echo Virtual environment 'sm_venv' already exists.
)
echo.
timeout /t 1 /nobreak >nul

REM --- Step 4: Activate the virtual environment ---
echo ============================================================================
echo [STEP 3/5] Activating virtual environment...
echo ============================================================================
call "sm_venv\Scripts\activate.bat"
echo.
timeout /t 1 /nobreak >nul

REM --- Step 5: Install dependencies from requirements.txt ---
echo ============================================================================
echo [STEP 4/5] Installing Python packages from requirements.txt...
echo (This may take a few minutes)...
echo ============================================================================
IF NOT EXIST "requirements.txt" (
    echo [WARNING] requirements.txt not found. Cannot verify dependencies.
) ELSE (
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install required packages.
        echo Please check your internet connection and try again.
        pause
        exit /b
    )
    echo.
    echo All Python packages are installed.
    echo [INFO] The 'sounddevice' package (for audio feedback) will be
    echo        checked by the application at runtime.
)
echo.
timeout /t 1 /nobreak >nul

REM --- Step 6: Check for external dependency: Tesseract-OCR ---
echo ============================================================================
echo [STEP 5/5] Checking for Tesseract-OCR...
echo ============================================================================
where tesseract >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Tesseract-OCR not found in your system PATH.
    echo.
    echo This program is REQUIRED for all OCR (text-reading) features.
    echo.
    echo --- TO FIX THIS ---
    echo 1. Download the Tesseract installer from:
    echo    https://github.com/UB-Mannheim/tesseract/wiki
    echo.
    echo 2. Run the installer.
    echo 3. **IMPORTANT:** During installation, you MUST check the box to
    echo    "Add Tesseract to system PATH".
    echo.
    echo 4. After installation is complete, re-run this 'RUN_monitor.bat' file.
    echo.
    pause
    exit /b
)
echo Tesseract-OCR found!
echo.
timeout /t 2 /nobreak >nul

REM --- Step 7: Launch the main application ---
echo ============================================================================
echo All checks passed. Launching the USMA monitor application (v.0.4.1)...
echo ============================================================================
python monitor_app.py

echo.
echo Application closed.
pause


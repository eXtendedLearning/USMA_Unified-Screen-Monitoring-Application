@echo off
TITLE USMA Monitor Application (Portable Launcher)

REM ============================================================================
REM  USMA - Portable Launcher (for v0.4.2 - Pre-Release)
REM ============================================================================

REM --- Step 1: Navigate to the script's directory & clear old log ---
cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"

echo [USMA LAUNCHER LOG] > "run_log.txt"
echo Launcher started at %DATE% %TIME% >> "run_log.txt"
echo. >> "run_log.txt"
echo [STEP 1] Navigated to directory: %SCRIPT_DIR% >> "run_log.txt"

REM --- Step 2: Define other file paths ---
set "TESSERACT_EXE=%SCRIPT_DIR%external\tesseract\tesseract.exe"
set "APP_FILE=%SCRIPT_DIR%monitor_app.py"

REM --- Step 3: Find the Python Executable (Direct Test) ---
echo [STEP 2] Locating Python executable... >> "run_log.txt"

set "PYTHON_EXE="
set "VENV_PYTHON_PATH=%SCRIPT_DIR%python\Scripts\python.exe"
set "EMBED_PYTHON_PATH=%SCRIPT_DIR%python\python.exe"

echo    - Attempt 1: Testing venv-style path: "%VENV_PYTHON_PATH%" >> "run_log.txt"
"%VENV_PYTHON_PATH%" --version >> "run_log.txt" 2>&1

IF %ERRORLEVEL% EQU 0 (
    echo    - SUCCESS: venv-style Python found. >> "run_log.txt"
    set "PYTHON_EXE=%VENV_PYTHON_PATH%"
) ELSE (
    echo    - FAILED: venv-style Python not found or failed. Errorlevel: %ERRORLEVEL% >> "run_log.txt"
    echo    - Attempt 2: Testing embeddable-style path: "%EMBED_PYTHON_PATH%" >> "run_log.txt"
    "%EMBED_PYTHON_PATH%" --version >> "run_log.txt" 2>&1
    
    IF %ERRORLEVEL% EQU 0 (
        echo    - SUCCESS: Embeddable-style Python found. >> "run_log.txt"
        set "PYTHON_EXE=%EMBED_PYTHON_PATH%"
    ) ELSE (
         echo    - FAILED: Embeddable-style Python not found or failed. Errorlevel: %ERRORLEVEL% >> "run_log.txt"
    )
)

echo. >> "run_log.txt"

REM --- Step 4: Check for all required components ---
echo [STEP 3] Checking for required components... >> "run_log.txt"
echo.
echo Checking for required files...
echo (Logging checks to run_log.txt)

IF NOT DEFINED PYTHON_EXE (
    echo [ERROR] Portable Python executable not found! >> "run_log.txt"
    echo.
    echo [ERROR] Portable Python executable not found!
    echo.
    echo Both venv and embeddable path checks failed.
    echo Check 'run_log.txt' for detailed error messages.
    echo.
    echo Please ensure the 'python' folder exists and is either a
    echo renamed 'sm_venv' folder or a patched embeddable package.
    echo.
    pause
    exit /b
)
echo    - Python... OK. (%PYTHON_EXE%) >> "run_log.txt"
echo    - Tesseract... (Checking) >> "run_log.txt"

IF NOT EXIST "%TESSERACT_EXE%" (
    echo [ERROR] Portable Tesseract executable not found! >> "run_log.txt"
    echo.
    echo [ERROR] Portable Tesseract executable not found!
    echo.
    echo Expected to find it at:
    echo %TESSERACT_EXE%
    echo.
    echo Please ensure your 'external\tesseract' folder contains the
    echo complete Tesseract-OCR installation.
    echo.
    pause
    exit /b
)
echo    - Tesseract... OK. >> "run_log.txt"

IF NOT EXIST "%APP_FILE%" (
    echo [ERROR] Main application file 'monitor_app.py' not found! >> "run_log.txt"
    echo.
    echo [ERROR] Main application file 'monitor_app.py' not found!
    echo.
    echo Expected to find it at:
    echo %APP_FILE%
    echo.
    pause
    exit /b
)
echo    - App File... OK. >> "run_log.txt"
echo. >> "run_log.txt"

REM --- Step 5: Launch the application & LOG THE OUTPUT ---
echo [STEP 4] All components found. Launching application... >> "run_log.txt"
echo All components found. Launching USMA Application...
echo (App output and errors will be saved to 'run_log.txt')
echo.

REM All output (stdout) and errors (stderr) from Python will be APPENDED (>>) to the log.
"%PYTHON_EXE%" "%APP_FILE%" >> "run_log.txt" 2>&1

echo.
echo Application closed.
echo A log file has been created at 'run_log.txt'.
echo Please check it for any application errors.
echo.
pause


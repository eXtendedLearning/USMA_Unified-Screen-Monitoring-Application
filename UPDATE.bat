@echo off
setlocal EnableDelayedExpansion
title USMA — Update

echo.
echo ===================================================
echo   USMA Update Script
echo ===================================================
echo.

:: ── Check git is available ───────────────────────────────────────────────────
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git non trovato nel PATH. Installa Git e riprova.
    pause
    exit /b 1
)

:: ── Move to repo root (parent of scripts\) ──────────────────────────────────
cd /d "%~dp0.."
echo [INFO] Directory: %CD%
echo.

:: ── Fetch remote branches ────────────────────────────────────────────────────
echo [INFO] Recupero aggiornamenti da GitHub...
git fetch --all --prune
if errorlevel 1 (
    echo [ERROR] Impossibile raggiungere GitHub. Controlla la connessione.
    pause
    exit /b 1
)

:: ── List available remote branches ──────────────────────────────────────────
echo.
echo Rami disponibili su GitHub:
echo.
set idx=0
for /f "tokens=*" %%B in ('git branch -r --format "%%(refname:short)" ^| findstr /v "HEAD"') do (
    set /a idx+=1
    set "branch_!idx!=%%B"
    echo   [!idx!] %%B
)

if !idx! == 0 (
    echo [ERROR] Nessun ramo remoto trovato.
    pause
    exit /b 1
)

echo.
set /p choice="Seleziona il numero del ramo da aggiornare [1-%idx%]: "

:: Validate input
if "%choice%"=="" (
    echo [ERROR] Nessuna selezione effettuata.
    pause
    exit /b 1
)
if !choice! LSS 1 goto :invalid_choice
if !choice! GTR %idx% goto :invalid_choice

set "selected=!branch_%choice%!"
echo.
echo [INFO] Ramo selezionato: %selected%

:: Strip "origin/" prefix to get local branch name
set "local_branch=%selected:origin/=%"

:: ── Show last 3 commits on that branch ──────────────────────────────────────
echo.
echo Ultimi 3 commit su %selected%:
git log %selected% --oneline -3
echo.

set /p confirm="Procedere con l'aggiornamento a '%local_branch%'? [S/n]: "
if /i "%confirm%"=="n" (
    echo Aggiornamento annullato.
    pause
    exit /b 0
)

:: ── Switch to branch and pull ────────────────────────────────────────────────
echo.
echo [INFO] Cambio ramo a: %local_branch%
git checkout "%local_branch%" 2>nul || git checkout -b "%local_branch%" --track "%selected%"
if errorlevel 1 (
    echo [ERROR] Impossibile passare al ramo %local_branch%.
    pause
    exit /b 1
)

echo [INFO] Pull da %selected%...
git pull origin "%local_branch%"
if errorlevel 1 (
    echo [ERROR] Pull fallito. Verifica eventuali conflitti con git status.
    pause
    exit /b 1
)

echo.
echo [OK] Codice aggiornato all'ultimo commit di '%local_branch%'.
echo.

:: ── Clean caches and temp files ──────────────────────────────────────────────
echo [INFO] Pulizia cache e file temporanei...

:: Python bytecode
if exist __pycache__ (
    for /d /r . %%D in (__pycache__) do (
        rd /s /q "%%D" 2>nul
    )
    echo      __pycache__ rimossi
)

:: pytest cache
if exist .pytest_cache (
    rd /s /q .pytest_cache 2>nul
    echo      .pytest_cache rimosso
)

:: Log file — truncate to avoid confusion with pre-update logs
if exist logs\monitor_app.log (
    > logs\monitor_app.log
    echo      logs\monitor_app.log svuotato
)

:: ── Dependencies: install any new packages ───────────────────────────────────
echo.
echo [INFO] Aggiornamento dipendenze Python...
if exist venv\Scripts\pip.exe (
    venv\Scripts\pip.exe install -r requirements.txt --quiet
    if errorlevel 1 (
        echo [WARN] pip install ha segnalato degli errori. Controlla manualmente.
    ) else (
        echo      Dipendenze aggiornate.
    )
) else (
    echo [WARN] venv non trovato. Creane uno con: python -m venv venv
)

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo ===================================================
echo   Aggiornamento completato!
echo   Per avviare USMA: RUN_USMA_PORTABLE.bat
echo ===================================================
echo.
pause
exit /b 0

:invalid_choice
echo [ERROR] Selezione non valida: %choice%
pause
exit /b 1

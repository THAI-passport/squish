@echo off
rem Run Squish locally on Windows: docker if available, else a native venv.
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "MARKER=-squish"
if "%PORT%"=="" set "PORT=8000"
if "%MODE%"=="" set "MODE=auto"

echo == 1/4 verify source ==
findstr /C:"APP_VERSION" backend\app.py >nul 2>&1
if errorlevel 1 (
    echo FAIL: backend\app.py has no APP_VERSION
    exit /b 1
)

for /f "tokens=2 delims==" %%i in ('findstr /C:"APP_VERSION" backend\app.py') do (
    set "RAW_VER=%%i"
)
rem Clean quotes and whitespace
for /f "tokens=1 delims= " %%a in ("%RAW_VER%") do set "VERSION=%%~a"

echo %VERSION% | findstr /C:"%MARKER%" >nul 2>&1
if errorlevel 1 (
    echo FAIL: APP_VERSION must contain '%MARKER%'
    exit /b 1
)
echo source version: %VERSION%

echo == 2/4 stop anything on :%PORT% ==
rem Kill any existing process listening on PORT
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo killing process %%p on port %PORT%
    taskkill /F /PID %%p >nul 2>&1
)

if "%MODE%"=="auto" (
    docker info >nul 2>&1
    if not errorlevel 1 (
        set "MODE=docker"
    ) else (
        set "MODE=native"
    )
)
echo mode: %MODE%

echo == 3/4 start ==
if "%MODE%"=="docker" (
    docker compose down --remove-orphans >nul 2>&1
    docker compose up -d --build
    if errorlevel 1 (
        echo FAIL: docker compose up failed
        exit /b 1
    )
) else (
    rem Detect Python
    set "PY_CMD="
    where py >nul 2>&1 && set "PY_CMD=py -3"
    if "!PY_CMD!"=="" (
        where python >nul 2>&1 && set "PY_CMD=python"
    )
    if "!PY_CMD!"=="" (
        echo FAIL: python or py launcher missing
        exit /b 1
    )

    rem Check engines (informational)
    set "MISSING="
    where gs >nul 2>&1 || where gswin64c >nul 2>&1 || (
        echo note: Ghostscript not found -- compression/grayscale tools will use browser fallback
        set "MISSING=1"
    )
    where soffice >nul 2>&1 || if not exist "%ProgramFiles%\LibreOffice\program\soffice.exe" (
        echo note: LibreOffice not found -- office conversion disabled
        set "MISSING=1"
    )
    where qpdf >nul 2>&1 || (
        echo note: qpdf not found -- damaged file recovery will use fitz fallback
        set "MISSING=1"
    )
    where ocrmypdf >nul 2>&1 || (
        echo note: ocrmypdf not found -- OCR disabled
        set "MISSING=1"
    )
    if defined MISSING (
        echo   to enable them, see the Running on Windows section in README.md
    )

    if not exist .venv (
        echo creating virtualenv in .venv ...
        !PY_CMD! -m venv .venv
        if errorlevel 1 (
            echo FAIL: virtualenv creation failed
            exit /b 1
        )
    )

    echo installing dependencies ...
    .venv\Scripts\python.exe -m pip install -q --upgrade pip
    .venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt
    if errorlevel 1 (
        echo FAIL: pip install failed
        exit /b 1
    )

    echo starting uvicorn server in background ...
    set "SQUISH_DIR=%CD%"
    pushd backend
    start /B "" "..\\.venv\Scripts\uvicorn.exe" app:app --host 127.0.0.1 --port %PORT% --timeout-keep-alive 120 > "..\\squish.log" 2>&1
    popd
    echo logs: %CD%\squish.log
)

echo == 4/4 wait for health ==
set "HEALTH_OK="
for /L %%i in (1,1,60) do (
    curl -sf "http://localhost:%PORT%/api/health" >nul 2>&1
    if not errorlevel 1 (
        set "HEALTH_OK=1"
        goto :health_ready
    )
    powershell -NoProfile -Command "(Invoke-RestMethod -Uri 'http://localhost:%PORT%/api/health' -TimeoutSec 1).ok" >nul 2>&1
    if not errorlevel 1 (
        set "HEALTH_OK=1"
        goto :health_ready
    )
    timeout /t 1 /nobreak >nul 2>&1
)

:health_ready
if not defined HEALTH_OK (
    echo FAIL: no health response.
    if "%MODE%"=="native" (
        if exist squish.log type squish.log
    ) else (
        docker compose logs --tail 40
    )
    exit /b 1
)

powershell -NoProfile -Command "Invoke-RestMethod -Uri 'http://localhost:%PORT%/api/health' | ConvertTo-Json -Compress"
curl -s "http://localhost:%PORT%/api/health" 2>nul | findstr /C:"%MARKER%" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Invoke-RestMethod -Uri 'http://localhost:%PORT%/api/health' | Select-Object -ExpandProperty version" | findstr /C:"%MARKER%" >nul 2>&1
    if errorlevel 1 (
        echo VERDICT: OLD SERVER still answering on :%PORT%
        exit /b 1
    )
)
echo VERDICT: NEW CODE RUNNING (%VERSION%)
start http://localhost:%PORT%
echo Open http://localhost:%PORT%
exit /b 0

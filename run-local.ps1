<#
.SYNOPSIS
    Run Squish locally on Windows: docker if available, else a native venv.
.PARAMETER Port
    Port number to listen on (default: 8000).
.PARAMETER Mode
    Execution mode: auto, docker, or native (default: auto).
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [ValidateSet("auto", "docker", "native")]
    [string]$Mode = "auto"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$Marker = "-squish"

Write-Host "== 1/4 verify source ==" -ForegroundColor Cyan
if (-not (Test-Path "backend/app.py")) {
    Write-Error "FAIL: backend/app.py not found"
    exit 1
}

$appPy = Get-Content "backend/app.py" -Raw
if ($appPy -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
    Write-Error "FAIL: backend/app.py has no APP_VERSION"
    exit 1
}
$Version = $Matches[1]
if ($Version -notlike "*$Marker*") {
    Write-Error "FAIL: APP_VERSION must contain '$Marker'"
    exit 1
}
Write-Host "source version: $Version"

Write-Host "== 2/4 stop anything on :$Port ==" -ForegroundColor Cyan
# Kill any existing process on $Port
try {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) {
            Write-Host "killing process $p on port $Port"
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
} catch {
    $netstat = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    foreach ($line in $netstat) {
        $parts = $line.Line.Trim() -split '\s+'
        $p = $parts[-1]
        if ($p -match '^\d+$') {
            Stop-Process -Id [int]$p -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($Mode -eq "auto") {
    $dockerFound = $false
    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -eq 0) { $dockerFound = $true }
    } catch {
        $dockerFound = $false
    }
    if ($dockerFound) {
        $Mode = "docker"
    } else {
        $Mode = "native"
    }
}
Write-Host "mode: $Mode"

Write-Host "== 3/4 start ==" -ForegroundColor Cyan
if ($Mode -eq "docker") {
    docker compose down --remove-orphans 2>$null
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) {
        Write-Error "FAIL: docker compose up failed"
        exit 1
    }
} else {
    # Find Python
    $pythonCmd = $null
    $pyArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonCmd = "py"
        $pyArgs = @("-3")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $pythonCmd = "python"
    } else {
        Write-Error "FAIL: python or py launcher missing"
        exit 1
    }

    # Informational engine check
    $missingEngines = $false
    if (-not (Get-Command gs -ErrorAction SilentlyContinue) -and -not (Get-Command gswin64c -ErrorAction SilentlyContinue)) {
        Write-Host "note: Ghostscript not found -- compression/grayscale tools will use browser fallback" -ForegroundColor Yellow
        $missingEngines = $true
    }
    $loPath = "${env:ProgramFiles}\LibreOffice\program\soffice.exe"
    if (-not (Get-Command soffice -ErrorAction SilentlyContinue) -and -not (Test-Path $loPath)) {
        Write-Host "note: LibreOffice not found -- office conversion disabled" -ForegroundColor Yellow
        $missingEngines = $true
    }
    if (-not (Get-Command qpdf -ErrorAction SilentlyContinue)) {
        Write-Host "note: qpdf not found -- damaged file recovery will use fitz fallback" -ForegroundColor Yellow
        $missingEngines = $true
    }
    if (-not (Get-Command ocrmypdf -ErrorAction SilentlyContinue)) {
        Write-Host "note: ocrmypdf not found -- OCR disabled" -ForegroundColor Yellow
        $missingEngines = $true
    }
    if ($missingEngines) {
        Write-Host "  to enable them, see the Running on Windows section in README.md" -ForegroundColor DarkGray
    }

    if (-not (Test-Path ".venv")) {
        Write-Host "creating virtualenv in .venv ..."
        & $pythonCmd @pyArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            Write-Error "FAIL: virtualenv creation failed"
            exit 1
        }
    }

    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $venvUvicorn = Join-Path $PSScriptRoot ".venv\Scripts\uvicorn.exe"

    Write-Host "installing dependencies ..."
    & $venvPython -m pip install -q --upgrade pip
    & $venvPython -m pip install -q -r backend/requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Error "FAIL: pip install failed"
        exit 1
    }

    $logFile = Join-Path $PSScriptRoot "squish.log"
    $backendDir = Join-Path $PSScriptRoot "backend"

    Write-Host "starting uvicorn server in background ..."
    Start-Process -FilePath $venvUvicorn `
        -ArgumentList "app:app --host 127.0.0.1 --port $Port --timeout-keep-alive 120" `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError $logFile `
        -WindowStyle Hidden

    Write-Host "logs: $logFile"
}

Write-Host "== 4/4 wait for health ==" -ForegroundColor Cyan
$healthUrl = "http://localhost:$Port/api/health"
$healthOk = $false
$healthResponse = $null

for ($i = 1; $i -le 60; $i++) {
    try {
        $healthResponse = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 -ErrorAction Stop
        if ($healthResponse.ok) {
            $healthOk = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $healthOk) {
    Write-Error "FAIL: no health response."
    if ($Mode -eq "native" -and (Test-Path "squish.log")) {
        Get-Content "squish.log" -Tail 30
    } elseif ($Mode -eq "docker") {
        docker compose logs --tail 40
    }
    exit 1
}

$healthJson = $healthResponse | ConvertTo-Json -Compress
Write-Host $healthJson

if ($healthResponse.version -notlike "*$Marker*") {
    Write-Error "VERDICT: OLD SERVER still answering on :$Port"
    exit 1
}

Write-Host "VERDICT: NEW CODE RUNNING ($Version)" -ForegroundColor Green
Start-Process "http://localhost:$Port"
Write-Host "Open http://localhost:$Port"

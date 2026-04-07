$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Write-Host ".env not found. Copying from .env.example"
    Copy-Item .env.example .env
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Entry = Join-Path $ProjectRoot "src\linebot_app\__main__.py"

if (-not (Test-Path $Pythonw)) {
    Write-Host "pythonw not found: $Pythonw" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Entry)) {
    Write-Host "entry point not found: $Entry" -ForegroundColor Red
    exit 1
}

Write-Host "Starting LineBot UI mode..." -ForegroundColor Cyan
Start-Process -FilePath $Pythonw -ArgumentList @($Entry) -WorkingDirectory $ProjectRoot | Out-Null
Write-Host "LineBot started in background (UI only)." -ForegroundColor Green

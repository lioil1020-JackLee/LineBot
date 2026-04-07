# LineBot build script (onedir / onefile)
# Usage:
#   .\scripts\build.ps1
#   .\scripts\build.ps1 -OneFile
#   .\scripts\build.ps1 -Clean

param(
    [switch]$OneFile,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $PSCommandPath
}
$ProjectRoot = Split-Path -Parent $ScriptDir
$SpecFile = Join-Path $ProjectRoot "linebot_clean_onefile.spec"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$PyInstallerCache = Join-Path $ProjectRoot ".pyinstaller"
$VenvPath = Join-Path $ProjectRoot ".venv"
$PyInstallerExe = Join-Path $VenvPath "Scripts\pyinstaller.exe"

Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "LineBot Build Tool" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan

if ($Clean) {
    Write-Host "[*] Cleaning old outputs..." -ForegroundColor Yellow
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
    if (Test-Path $PyInstallerCache) { Remove-Item -Recurse -Force $PyInstallerCache }
}

if (-not (Test-Path $SpecFile)) {
    Write-Host "[x] Spec file not found: $SpecFile" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $VenvPath)) {
    Write-Host "[x] Virtual environment not found: $VenvPath" -ForegroundColor Red
    Write-Host "    Run: uv sync --group dev" -ForegroundColor Gray
    exit 1
}

if (-not (Test-Path $PyInstallerExe)) {
    Write-Host "[*] Installing PyInstaller..." -ForegroundColor Yellow
    & "$VenvPath\Scripts\pip.exe" install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[x] Failed to install PyInstaller" -ForegroundColor Red
        exit 1
    }
}

if ($OneFile) {
    Write-Host "[*] Switching spec to onefile mode..." -ForegroundColor Yellow
    (Get-Content $SpecFile) -replace 'ONE_FILE = False', 'ONE_FILE = True' | Set-Content $SpecFile
    $Mode = "onefile"
}
else {
    Write-Host "[*] Switching spec to onedir mode..." -ForegroundColor Yellow
    (Get-Content $SpecFile) -replace 'ONE_FILE = True', 'ONE_FILE = False' | Set-Content $SpecFile
    $Mode = "onedir"
}

Write-Host "[*] Building ($Mode)..." -ForegroundColor Yellow
Push-Location $ProjectRoot
& $PyInstallerExe $SpecFile --noconfirm
$BuildResult = $LASTEXITCODE
Pop-Location

if ($BuildResult -ne 0) {
    Write-Host "[x] Build failed" -ForegroundColor Red
    exit 1
}

Write-Host "[ok] Build succeeded" -ForegroundColor Green
if ($OneFile) {
    Write-Host "Output: $DistDir\linebot-app.exe" -ForegroundColor White
}
else {
    Write-Host "Output: $DistDir\linebot-app\linebot-app.exe" -ForegroundColor White
}

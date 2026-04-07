# LineBot 打包腳本（onedir 或 onefile）
# 使用方式：
#   .\build.ps1              # 預設 onedir 模式
#   .\build.ps1 -OneFile     # onefile 模式
#   .\build.ps1 -Clean       # 先清理舊輸出

param(
    [switch]$OneFile,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

# ============================================================================
# 路徑與設定
# ============================================================================
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommandPath
$SpecFile = Join-Path $ProjectRoot "linebot_clean_onefile.spec"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$PyInstallerCache = Join-Path $ProjectRoot ".pyinstaller"

Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "LineBot 打包工具" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ============================================================================
# 清理模式
# ============================================================================
if ($Clean) {
    Write-Host "[*] 清理舊的打包輸出..." -ForegroundColor Yellow
    if (Test-Path $DistDir) {
        Remove-Item -Recurse -Force $DistDir
        Write-Host "[✓] 已刪除 dist/" -ForegroundColor Green
    }
    if (Test-Path $BuildDir) {
        Remove-Item -Recurse -Force $BuildDir
        Write-Host "[✓] 已刪除 build/" -ForegroundColor Green
    }
    if (Test-Path $PyInstallerCache) {
        Remove-Item -Recurse -Force $PyInstallerCache
        Write-Host "[✓] 已刪除 .pyinstaller/" -ForegroundColor Green
    }
}

# ============================================================================
# 前置檢查
# ============================================================================
Write-Host "[*] 檢查環境..." -ForegroundColor Yellow

# 檢查 spec 檔案
if (-not (Test-Path $SpecFile)) {
    Write-Host "[✗] linebot.spec 不存在！" -ForegroundColor Red
    exit 1
}
Write-Host "[✓] spec 檔案存在" -ForegroundColor Green

# 檢查虛擬環境
$VenvPath = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "[✗] 虛擬環境不存在！請先執行 uv sync" -ForegroundColor Red
    exit 1
}
Write-Host "[✓] 虛擬環境存在" -ForegroundColor Green

# 檢查 PyInstaller
$PyInstallerExe = Join-Path $VenvPath "Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstallerExe)) {
    Write-Host "[!] 正在安裝 PyInstaller..." -ForegroundColor Yellow
    & "$VenvPath\Scripts\pip.exe" install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[✗] PyInstaller 安裝失敗" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[✓] PyInstaller 可用" -ForegroundColor Green

# ============================================================================
# 編輯 spec 檔案模式
# ============================================================================
if ($OneFile) {
    Write-Host "[*] 設定 onefile 模式..." -ForegroundColor Yellow
    (Get-Content $SpecFile) -replace 'ONE_FILE = False', 'ONE_FILE = True' | Set-Content $SpecFile
    $Mode = "onefile"
} else {
    Write-Host "[*] 設定 onedir 模式..." -ForegroundColor Yellow
    (Get-Content $SpecFile) -replace 'ONE_FILE = True', 'ONE_FILE = False' | Set-Content $SpecFile
    $Mode = "onedir"
}

# ============================================================================
# 執行打包
# ============================================================================
Write-Host ""
Write-Host "[*] 開始打包 ($Mode 模式)..." -ForegroundColor Yellow
Write-Host "    這可能需要 1-3 分鐘，請耐心等待" -ForegroundColor Gray

Push-Location $ProjectRoot
& $PyInstallerExe $SpecFile --noconfirm
$BuildResult = $LASTEXITCODE
Pop-Location

if ($BuildResult -ne 0) {
    Write-Host "[✗] 打包失敗！" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "[✓] 打包成功！" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

# ============================================================================
# 輸出位置提示
# ============================================================================
if ($OneFile) {
    $OutputPath = Join-Path $DistDir "linebot-app.exe"
    Write-Host "📦 可執行檔位置：" -ForegroundColor Cyan
    Write-Host "   $OutputPath" -ForegroundColor White
    Write-Host ""
    Write-Host "🚀 啟動方式：" -ForegroundColor Cyan
    Write-Host "   $OutputPath" -ForegroundColor White
} else {
    $OutputPath = Join-Path $DistDir "linebot-app"
    Write-Host "📦 輸出資料夾：" -ForegroundColor Cyan
    Write-Host "   $OutputPath\" -ForegroundColor White
    Write-Host ""
    Write-Host "🚀 啟動方式：" -ForegroundColor Cyan
    Write-Host "   $OutputPath\linebot-app.exe" -ForegroundColor White
}

Write-Host ""
Write-Host "⚠️  前置條件：" -ForegroundColor Yellow
Write-Host "   1. LM Studio 需要在 http://127.0.0.1:1234/v1 執行" -ForegroundColor Gray
Write-Host "   2. .env 檔案需要在同目錄或輸出目錄下" -ForegroundColor Gray
Write-Host "   3. ngrok tunnel 需要單獨啟動（如需要 webhook）" -ForegroundColor Gray
Write-Host ""
Write-Host "💡 打包後若要快速開發，建議用虛擬環境執行 uv run linebot" -ForegroundColor Gray
Write-Host ""

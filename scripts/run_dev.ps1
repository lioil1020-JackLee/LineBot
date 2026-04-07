$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Write-Host ".env not found. Copying from .env.example"
    Copy-Item .env.example .env
}

uv run linebot

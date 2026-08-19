# [agent_cs] Backend Packaging Script
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "[agent_cs] Packaging backend into Docker image (.tar deliverable)..." -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan

# 1. Build image
Write-Host "`n[1/2] Building production Docker image (agent_cs_backend:latest)..." -ForegroundColor Yellow
docker build -t agent_cs_backend:latest -f backend/Dockerfile backend/

# 2. Export tar
$TarPath = Join-Path $ScriptDir "agent_cs_backend.tar"
Write-Host "`n[2/2] Exporting Docker image to $TarPath ..." -ForegroundColor Yellow
docker save -o $TarPath agent_cs_backend:latest

if (Test-Path $TarPath) {
    $FileSizeMB = [math]::Round((Get-Item $TarPath).Length / 1MB, 2)
    Write-Host "`n==============================================================================" -ForegroundColor Green
    Write-Host "Packaging successful! Output file: agent_cs_backend.tar ($FileSizeMB MB)" -ForegroundColor Green
    Write-Host "Location: $TarPath" -ForegroundColor Green
    Write-Host "`nNext steps for Alibaba Cloud server deployment:" -ForegroundColor White
    Write-Host "1. Upload these files in deploy/ to server (/opt/microbiome_ai_engine/):"
    Write-Host "   - agent_cs_backend.tar"
    Write-Host "   - docker-compose.prod.yml"
    Write-Host "   - .env.prod.example (copy to .env.prod on server and fill credentials)"
    Write-Host "   - deploy_on_server.sh"
    Write-Host "2. On the server, run: bash deploy_on_server.sh"
    Write-Host "==============================================================================" -ForegroundColor Green
} else {
    Write-Error "Failed to locate generated $TarPath"
}

@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo   Hermiss single-user one-click deploy
echo ==========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='%~f0'; $lines=Get-Content -LiteralPath $p; $i=[Array]::IndexOf($lines,'### POWERSHELL ###'); if($i -lt 0){ throw 'script marker not found' }; $code=$lines[($i+1)..($lines.Count-1)] -join [Environment]::NewLine; Invoke-Expression $code"
set ERR=%ERRORLEVEL%

echo.
echo Deployment script finished. If there was an error, check the message above.
pause
exit /b %ERR%

### POWERSHELL ###
$ErrorActionPreference = "Stop"

function Step($text) {
  Write-Host ""
  Write-Host "-- $text" -ForegroundColor Cyan
}

function Fail($text) {
  Write-Host ""
  Write-Host "Deploy failed: $text" -ForegroundColor Red
  Write-Host "Please make sure Docker Desktop is installed and running, then run this script again."
  exit 1
}

Write-Host ""
Write-Host "Hermiss single-user one-click deploy" -ForegroundColor Magenta
Write-Host "Docker Desktop is required. Images will be pulled automatically."

Step "Checking Docker"
$DockerCmd = $null
$dockerDesktopBin = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
if (Test-Path $dockerDesktopBin) {
  $env:PATH = "$dockerDesktopBin;$env:PATH"
}
$dockerInPath = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerInPath) { $DockerCmd = $dockerInPath.Source }
if (-not $DockerCmd) {
  $dockerDesktopPath = Join-Path $dockerDesktopBin "docker.exe"
  if (Test-Path $dockerDesktopPath) { $DockerCmd = $dockerDesktopPath }
}
if (-not $DockerCmd) { Fail "docker command was not found." }
try { & $DockerCmd version | Out-Null } catch { Fail "Docker is not running or is not accessible." }

Step "Preparing .env"
if (!(Test-Path ".env")) {
  $secretBytes = New-Object byte[] 48
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($secretBytes)
    $secretKey = [Convert]::ToBase64String($secretBytes)
  } finally {
    $rng.Dispose()
  }
  @(
    "PANEL_HOST=127.0.0.1"
    "PANEL_PORT=8788"
    "PANEL_USERNAME=hermiss"
    "PANEL_PASSWORD=hermiss"
    "SECRET_KEY=$secretKey"
    "HERMISS_CONTAINER=hermiss-single"
    "HERMISS_CONTAINER_PORT=8770"
    "DOCKER_IMAGE=ghcr.io/linmumupro/hermiss:single"
  ) | Set-Content -Path ".env" -Encoding UTF8
}

if ($env:HERMISS_DEPLOY_DRY_RUN -eq "1") {
  Write-Host "Dry run passed." -ForegroundColor Green
  exit 0
}

Step "Pulling Hermiss image"
& $DockerCmd pull ghcr.io/linmumupro/hermiss:single
if ($LASTEXITCODE -ne 0) { Fail "failed to pull ghcr.io/linmumupro/hermiss:single. Please check whether the GitHub package is public." }

Step "Pulling Milvus image"
& $DockerCmd pull milvusdb/milvus:v2.4.0
if ($LASTEXITCODE -ne 0) { Fail "failed to pull milvusdb/milvus:v2.4.0." }

Step "Starting Hermiss panel"
& $DockerCmd compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed." }

Step "Waiting for panel"
$url = "http://127.0.0.1:8788"
$ok = $false
for ($i = 1; $i -le 45; $i++) {
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 2
    if ($resp.Content -match '"ok"') { $ok = $true; break }
  } catch { Start-Sleep -Seconds 2 }
}

if ($ok) {
  Write-Host ""
  Write-Host "Deploy completed." -ForegroundColor Green
  Write-Host "URL: $url"
  Write-Host "Username: hermiss"
  Write-Host "Password: hermiss"
  Start-Process $url
} else {
  Write-Host ""
  Write-Host "The panel may still be starting. Open later: $url" -ForegroundColor Yellow
  & $DockerCmd compose ps
}

Write-Host ""
Write-Host "Common commands:"
Write-Host "Status: docker compose ps"
Write-Host "Stop: docker compose down"

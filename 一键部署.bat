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

function InvokeDockerPull($image) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $DockerCmd
  $psi.Arguments = "pull $image"
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  [void]$proc.Start()
  $stdout = $proc.StandardOutput.ReadToEnd()
  $stderr = $proc.StandardError.ReadToEnd()
  $proc.WaitForExit()
  if ($stdout) { Write-Host $stdout.TrimEnd() }
  if ($stderr) { Write-Host $stderr.TrimEnd() -ForegroundColor Yellow }
  return @{
    Code = $proc.ExitCode
    Output = "$stdout`n$stderr"
  }
}

function PullImage($image, $helpText) {
  $lastOutput = ""
  try {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
      if ($attempt -gt 1) {
        Write-Host "Retrying pull ($attempt/3): $image" -ForegroundColor Yellow
        Start-Sleep -Seconds 3
      }
      $result = InvokeDockerPull $image
      $lastOutput = $result.Output
      if ($result.Code -eq 0) { return }

      if ($lastOutput -match "(?i)\b(denied|unauthorized|authentication required)\b" -and $image -like "ghcr.io/*") {
        Write-Host ""
        Write-Host "GHCR returned an auth error. This public image does not need login; clearing stale GHCR credentials and retrying..." -ForegroundColor Yellow
        & $DockerCmd logout ghcr.io 2>$null | Out-Null
        $result = InvokeDockerPull $image
        $lastOutput = $result.Output
        if ($result.Code -eq 0) { return }
      }

      if ($lastOutput -notmatch "(?i)(timeout|timed out|EOF|connection|TLS handshake|request canceled|network)") {
        break
      }
    }

    if ($lastOutput -match "(?i)(timeout|timed out|EOF|connection|TLS handshake|request canceled|network)") {
      Write-Host ""
      Write-Host "Network timeout while pulling image. If you are in a restricted network, configure Docker Desktop proxy and retry." -ForegroundColor Yellow
    }
    Fail $helpText
  } finally {
  }
}

function SaveUtf8NoBom($path, $lines) {
  $encoding = New-Object System.Text.UTF8Encoding($false)
  $fullPath = Join-Path (Get-Location) $path
  [System.IO.File]::WriteAllLines($fullPath, [string[]]$lines, $encoding)
}

function RepairEnvFile($path) {
  if (!(Test-Path $path)) { return }
  $raw = [System.IO.File]::ReadAllText((Join-Path (Get-Location) $path))
  $raw = $raw.TrimStart([char]0xFEFF)
  $raw = $raw -replace "`r`n", "`n"
  $lines = $raw -split "`n"
  $clean = @()
  foreach ($line in $lines) {
    if ($line -ne $null -and $line.Trim() -ne "") {
      $clean += $line.TrimEnd("`r")
    }
  }
  SaveUtf8NoBom $path $clean
}

Write-Host ""
Write-Host "Hermiss single-user one-click deploy" -ForegroundColor Magenta
Write-Host "Docker Desktop is required. Images will be pulled automatically."

$PanelImage = "ghcr.io/linmumupro/hermiss-panel:single"
$RuntimeImage = "ghcr.io/linmumupro/hermiss:single"

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
  $envLines = @(
    "PANEL_HOST=127.0.0.1"
    "PANEL_PORT=8788"
    "PANEL_USERNAME=hermiss"
    "PANEL_PASSWORD=hermiss"
    "SECRET_KEY=$secretKey"
    "HERMISS_CONTAINER=hermiss-single"
    "HERMISS_CONTAINER_PORT=8770"
    "DOCKER_IMAGE=$RuntimeImage"
  )
  SaveUtf8NoBom ".env" $envLines
} else {
  $envText = Get-Content -LiteralPath ".env" -Raw -ErrorAction SilentlyContinue
  $lines = Get-Content -LiteralPath ".env" -ErrorAction SilentlyContinue
  if ($envText -match "ghcr\.io/mumupro/" -or $envText -match "DOCKER_IMAGE=.*:latest") {
    Write-Host "Found old image config in .env, updating to $RuntimeImage" -ForegroundColor Yellow
    $updated = $false
    $lines = foreach ($line in $lines) {
      if ($line -match "^DOCKER_IMAGE=") {
        $updated = $true
        "DOCKER_IMAGE=$RuntimeImage"
      } else {
        $line
      }
    }
    if (-not $updated) { $lines += "DOCKER_IMAGE=$RuntimeImage" }
  }
  SaveUtf8NoBom ".env" $lines
}
RepairEnvFile ".env"

if ($env:HERMISS_DEPLOY_DRY_RUN -eq "1") {
  Write-Host "Dry run passed." -ForegroundColor Green
  exit 0
}

Step "Pulling Hermiss panel image"
PullImage $PanelImage "failed to pull $PanelImage. Please download the latest Hermiss package from https://github.com/LinMuMuPro/hermiss and try again."

Step "Pulling Hermiss runtime image"
PullImage $RuntimeImage "failed to pull $RuntimeImage. Please check whether the GitHub package is public."

Step "Pulling Milvus image"
PullImage "milvusdb/milvus:v2.4.0" "failed to pull milvusdb/milvus:v2.4.0."

Step "Starting Hermiss panel"
RepairEnvFile ".env"
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

$ErrorActionPreference = "Stop"

function Write-Step($text) {
  Write-Host ""
  Write-Host "==> $text" -ForegroundColor Cyan
}

function Fail($text) {
  Write-Host ""
  Write-Host "部署失败：$text" -ForegroundColor Red
  Write-Host ""
  Write-Host "请确认 Docker Desktop 已安装并启动，然后重新运行 一键部署.bat"
  exit 1
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host ""
Write-Host "Hermiss 单用户版一键部署" -ForegroundColor Magenta
Write-Host "只需要本机已安装并启动 Docker Desktop，脚本会自动拉取所需镜像。"

Write-Step "检查 Docker"
$DockerCmd = $null
$dockerInPath = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerInPath) {
  $DockerCmd = $dockerInPath.Source
}
if (-not $DockerCmd) {
  $dockerDesktopPath = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
  if (Test-Path $dockerDesktopPath) {
    $DockerCmd = $dockerDesktopPath
  }
}
if (-not $DockerCmd) {
  Fail "没有检测到 docker 命令。请先安装 Docker Desktop。"
}

try {
  & $DockerCmd version | Out-Null
} catch {
  Fail "Docker 没有启动，或者当前用户无法访问 Docker。"
}

Write-Step "准备配置文件"
if (!(Test-Path ".env")) {
  @(
    "PANEL_HOST=127.0.0.1"
    "PANEL_PORT=8788"
    "PANEL_USERNAME=hermiss"
    "PANEL_PASSWORD=hermiss"
    "SECRET_KEY=change-me-hermiss-single-user"
    "HERMISS_CONTAINER=hermiss-single"
    "HERMISS_CONTAINER_PORT=8770"
    "DOCKER_IMAGE=ghcr.io/linmumupro/hermiss:single"
  ) | Set-Content -Path ".env" -Encoding UTF8
}

Write-Step "拉取 Hermiss 和 Milvus 镜像"
& $DockerCmd pull ghcr.io/linmumupro/hermiss:single
& $DockerCmd pull milvusdb/milvus:v2.4.0

Write-Step "启动 Hermiss 单用户版"
& $DockerCmd compose up -d --build

Write-Step "等待面板启动"
$url = "http://127.0.0.1:8788"
$ok = $false
for ($i = 1; $i -le 45; $i++) {
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 2
    if ($resp.Content -match '"ok"') {
      $ok = $true
      break
    }
  } catch {
    Start-Sleep -Seconds 2
  }
}

if ($ok) {
  Write-Host ""
  Write-Host "部署完成！" -ForegroundColor Green
  Write-Host "访问地址：$url"
  Write-Host "账号：hermiss"
  Write-Host "密码：hermiss"
  Start-Process $url
}

if (-not $ok) {
  Write-Host ""
  Write-Host "面板可能还在启动中。你可以稍等后手动打开：$url" -ForegroundColor Yellow
  & $DockerCmd compose ps
}

Write-Host ""
Write-Host "常用命令："
Write-Host "查看状态：docker compose ps"
Write-Host "停止服务：docker compose down"

@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo ==========================================
echo   Hermiss 单用户版 一键部署
echo ==========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0一键部署.ps1"

echo.
echo 如果窗口没有自动关闭，说明部署流程已经结束。
pause

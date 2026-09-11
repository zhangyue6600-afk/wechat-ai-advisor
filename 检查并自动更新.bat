@echo off
chcp 65001 >nul
title 微信群 AI 军师 · 全自动云端热更新
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "updater.ps1"

echo.
echo 👉 更新流程已结束，按任意键关闭窗口...
pause >nul

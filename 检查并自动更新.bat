@echo off
chcp 65001 >nul
title 微信群 AI 军师 · 检查更新
cd /d "%~dp0"
if exist "_internal\python.exe" (
    "_internal\python.exe" "updater.py"
) else (
    python "updater.py"
)
pause

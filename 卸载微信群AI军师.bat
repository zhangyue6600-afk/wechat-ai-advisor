@echo off
chcp 65001 >nul
title 微信群 AI 军师 · 一键卸载与清理
echo ==================================================================
echo 🗑️  正在准备卸载并清理 微信群 AI 军师...
echo ==================================================================
echo.
echo 1. 正在停止所有后台监听与 Web 服务进程...
taskkill /f /im "微信群AI军师.exe" 2>nul
taskkill /f /im "pythonw.exe" 2>nul
echo.
echo 2. 清理运行时临时缓存与解密碎片...
if exist "data\cache" rd /s /q "data\cache"
echo.
echo 3. 正在移除桌面临时快捷方式 (若存在)...
if exist "%USERPROFILE%\Desktop\启动微信群AI军师.bat" del /q "%USERPROFILE%\Desktop\启动微信群AI军师.bat" 2>nul
echo.
echo ==================================================================
echo ✅ 卸载清理已顺利完成！
echo 💡 说明: 程序后台服务已全部彻底关闭，不会占用任何后台资源。
echo    如无需保留生成过的群聊知识库，您可直接将本软件文件夹彻底删除。
echo ==================================================================
pause

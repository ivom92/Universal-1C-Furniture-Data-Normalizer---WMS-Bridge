cd /d "%~dp0"
@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8501 ^| findstr LISTENING') do (
    taskkill /f /pid %%a >nul 2>&1
    set "FOUND=1"
)

if "%FOUND%"=="1" (
    echo [УСПЕХ] Сервер WMS Ассистента остановлен. Папка разблокирована.
) else (
    echo [ИНФО] Сервер WMS не запущен ^(порт 8501 свободен^).
)

timeout /t 3 >nul

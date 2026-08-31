cd /d "%~dp0"
@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=1
set PYTHONUTF8=1

if not exist "venv\Scripts\python.exe" (
    echo [ОШИБКА] venv не найден. Сначала запустите 1_УСТАНОВКА.bat
    pause
    exit /b 1
)

set "PY=venv\Scripts\python.exe"

netstat -aon | findstr :8501 | findstr LISTENING >nul 2>&1
if %ERRORLEVEL%==0 (
    echo ============================================================
    echo   WMS Parser уже запущен на http://localhost:8501
    echo   Открываем браузер...
    echo ============================================================
    start "" http://localhost:8501
    exit /b 0
)

echo ============================================================
echo   WMS Parser (Региональный склад Челябинск)
echo   Сервер запущен: http://localhost:8501
echo   Пожалуйста, не закрывайте это окно во время работы.
echo   Для остановки: 3_ОСТАНОВИТЬ.bat или кнопка в интерфейсе.
echo ============================================================
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8501"
"%PY%" -m streamlit run app_ui.py --server.port 8501 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%

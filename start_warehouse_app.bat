@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

echo === Warehouse WMS Bridge — production launcher ===
python scripts\check_system_health.py
if errorlevel 1 (
    echo.
    echo Health check failed. Streamlit will not start.
    pause
    exit /b 1
)

echo.
echo Starting Streamlit on http://localhost:8501
python -m streamlit run app_ui.py --server.port 8501 --server.fileWatcherType none --browser.gatherUsageStats false
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%

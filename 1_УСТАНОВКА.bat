cd /d "%~dp0"
@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=1
set PYTHONUTF8=1

echo === Установка WMS Parser (склад Челябинск) ===
echo.

REM Включение длинных путей Windows (MAX_PATH fix, WinError 206)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1

set "INSTALL_DIR=%~dp0"
set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
set "PATH_LEN=0"
for /l %%i in (0,1,500) do (
    if not "!INSTALL_DIR:~%%i,1!"=="" set /a PATH_LEN+=1
)
if %PATH_LEN% GTR 100 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Путь установки длинный ^(%PATH_LEN% символов^): %INSTALL_DIR%
    echo Рекомендуется перенести папку ближе к корню диска, например C:\WMS\
    echo.
)
echo %INSTALL_DIR% | findstr /i "OneDrive" >nul 2>&1
if not errorlevel 1 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Установка в папке OneDrive может вызывать ошибки pip ^(WinError 206^).
    echo Рекомендуется распаковать архив в C:\WMS\ или D:\WMS\
    echo.
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH. Нужен Python 3.11 или новее.
    pause
    exit /b 1
)

python --version
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [ОШИБКА] Требуется Python 3.11+. Текущая версия не подходит.
    pause
    exit /b 1
)

echo.
echo Создание виртуального окружения venv...
python -m venv venv
if errorlevel 1 (
    echo [ОШИБКА] Не удалось создать venv.
    pause
    exit /b 1
)

set "PY=venv\Scripts\python.exe"

echo Обновление pip и установка зависимостей...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 (
    echo [ОШИБКА] Установка пакетов не удалась.
    pause
    exit /b 1
)

echo.
echo Прогрев кэша FAISS и проверка системы...
"%PY%" scripts\check_system_health.py --warm
if errorlevel 1 (
    echo.
    echo Установка завершена с замечаниями health-check. Проверьте сеть и .env.
    pause
    exit /b 1
)

echo.
echo Установка успешно завершена. Дальше запустите 2_ЗАПУСК.bat
pause
exit /b 0

@echo off
chcp 65001 >nul 2>&1
title U-Hermes - 一键诊断

:: ============================================================================
:: The same thing as Windows-Menu.bat -> [6], with a name someone in trouble
:: can find. It was reachable only from the sixth entry of a menu that a
:: stuck user has no reason to open, so every failure exit in the launcher
:: now points here by name.
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "HERMES_HOME=%SCRIPT_DIR%\data"
set "HERMES_GATEWAY_LOCK_DIR=%SCRIPT_DIR%\data\gateway-locks"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%PATH%"

if not exist "%VENV_PYTHON%" (
    echo.
    echo   ============================================
    echo     U-Hermes - 一键诊断
    echo   ============================================
    echo.
    echo   [X] 连 Python 运行时都还没装上，诊断跑不起来。
    echo.
    echo       这本身就是结论：安装没有完成。
    echo       请先双击 Windows-Start.bat，让它把依赖装完；
    echo       如果它报错，那几行红字就是要解决的问题。
    echo.
    echo       缺的文件：%VENV_PYTHON%
    echo.
    pause
    exit /b 1
)

"%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\diagnose.py"

echo.
echo   （更深入的技术诊断：Windows-Start.bat doctor）
echo.
pause

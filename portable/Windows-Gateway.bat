@echo off
chcp 65001 >nul 2>&1
title U-Hermes - Messaging Gateway

:: ============================================================================
:: U-Hermes Gateway Launcher
:: Starts the messaging gateway (Telegram/QQ/WeChat/Discord/etc.)
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo.
    echo   [X] Hermes 未安装，请先运行 setup.ps1。
    pause
    exit /b 1
)

:: Environment
set "HERMES_HOME=%SCRIPT_DIR%\data"
:: Gateway singleton record stays on the stick; see Windows-Start.bat.
set "HERMES_GATEWAY_LOCK_DIR=%SCRIPT_DIR%\data\gateway-locks"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%SCRIPT_DIR%\runtime\node-win-x64;%PATH%"
set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "UV_CACHE_DIR=%SCRIPT_DIR%\.uv-cache"

echo.
echo   ============================================
echo     U-Hermes - 消息网关
echo   ============================================
echo.
echo   支持的平台:
echo     - Telegram
echo     - QQ 机器人
echo     - Discord
echo     - 微信
echo     - 企业微信
echo     - 钉钉
echo     - 飞书
echo     - WhatsApp
echo     - Signal
echo     - Slack
echo.
echo   在 data/config.yaml 中配置平台
echo   或运行: hermes gateway setup
echo.
echo   按 Ctrl+C 停止网关。
echo.

"%VENV_PYTHON%" -m hermes_cli.main gateway start

if errorlevel 1 (
    echo.
    echo   [!] 网关异常退出。
    echo       请确保已配置至少一个消息平台。
    echo       运行: hermes gateway setup
    echo.
    pause
)

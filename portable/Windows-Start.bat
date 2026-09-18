@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title U-Hermes - AI Agent

:: ============================================================================
:: U-Hermes Windows Launcher
:: Starts Hermes Gateway + Web UI from portable directory
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "RUNTIME_DIR=%SCRIPT_DIR%\runtime"
set "HERMES_DIR=%SCRIPT_DIR%\hermes"
set "DATA_DIR=%SCRIPT_DIR%\data"

:: Python path
set "VENV_DIR=%HERMES_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

:: Node.js path
set "NODE_DIR=%RUNTIME_DIR%\node-win-x64"
set "NODE_EXE=%NODE_DIR%\node.exe"

:: Web UI entry
set "WEBUI_SERVER=%NODE_DIR%\node_modules\hermes-web-ui\dist\server\index.js"

:: ============================================================================
:: Pre-flight checks
:: ============================================================================

if not exist "%VENV_PYTHON%" (
    echo.
    echo   [!] 首次使用，正在安装依赖...
    echo.
    powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\setup.ps1"
    if errorlevel 1 (
        echo.
        echo   [X] 安装失败，请检查上方错误信息。
        pause
        exit /b 1
    )
)

if not exist "%VENV_PYTHON%" (
    echo.
    echo   [X] Python 虚拟环境未找到。
    echo       请先运行 setup.ps1 安装。
    echo.
    pause
    exit /b 1
)

:: ============================================================================
:: Fix stale paths in portable venv (first run on new machine)
:: ============================================================================

set "UV_EXE=%RUNTIME_DIR%\uv\uv.exe"
set "AGENT_DIR=%HERMES_DIR%\hermes-agent"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\scripts\fix-portable-paths.ps1" -VenvDir "%VENV_DIR%" -RuntimeDir "%RUNTIME_DIR%" -AgentDir "%AGENT_DIR%" -UvExe "%UV_EXE%"

:: ============================================================================
:: Set environment variables
:: ============================================================================

set "HERMES_HOME=%DATA_DIR%"
set "HERMES_CONFIG=%DATA_DIR%\config.yaml"
set "HERMES_MEMORY_DIR=%DATA_DIR%\memory"
set "HERMES_SKILLS_DIR=%DATA_DIR%\skills"
set "HERMES_SESSIONS_DIR=%DATA_DIR%\sessions"

set "PATH=%VENV_DIR%\Scripts;%NODE_DIR%;%PATH%"
set "PYTHONPATH=%HERMES_DIR%\hermes-agent;%PYTHONPATH%"

:: Force UTF-8
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

:: China PyPI mirror
set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

:: Hermes Web UI settings (AUTH_DISABLED is not a real knob -- it appears
:: nowhere in the web-ui bundle; auth uses a token under HERMES_WEB_UI_HOME)
set "PORT=8648"
set "HERMES_WEB_UI_HOME=%DATA_DIR%\webui"
:: hermes.cmd resolves python.exe relative to itself, so it survives a drive
:: letter change. The .exe carries an absolute path baked in at build time,
:: and newer uv trampolines cannot be rewritten in place.
set "HERMES_BIN=%VENV_DIR%\Scripts\hermes.cmd"
set "HERMES_AGENT_BRIDGE_PYTHON=%VENV_PYTHON%"
set "HERMES_AGENT_ROOT=%HERMES_DIR%\hermes-agent"
set "HERMES_WEB_UI_STOP_GATEWAYS_ON_SHUTDOWN=0"

:: Gateway API Server settings
set "API_SERVER_ENABLED=true"
set "API_SERVER_PORT=8642"
set "API_SERVER_CORS_ORIGINS=http://localhost:8648,http://127.0.0.1:8648"
set "GATEWAY_ALLOW_ALL_USERS=true"

:: Load data/.env (may override above defaults)
if exist "%DATA_DIR%\.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%DATA_DIR%\.env") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
)

:: ============================================================================
:: Protect config from Web UI overwrites
:: ============================================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\scripts\protect-config.ps1" -ConfigFile "%DATA_DIR%\config.yaml" -InstallDir "%SCRIPT_DIR%"

:: ============================================================================
:: Check if config exists and has a model set
:: ============================================================================

if not exist "%DATA_DIR%\config.yaml" (
    echo.
    echo   [i] 首次启动，正在创建默认配置...
    echo.
    mkdir "%DATA_DIR%" 2>nul
    (
        echo model:
        echo   provider: ""
        echo   model: ""
        echo database:
        echo   journal_mode: "delete"
        echo platforms:
        echo   api_server:
        echo     enabled: true
        echo     extra:
        echo       port: 8642
        echo       host: 127.0.0.1
        echo skills:
        echo   external_dirs:
        echo     - "../skills-cn"
        echo memory:
        echo   enabled: true
        echo cron:
        echo   enabled: true
    ) > "%DATA_DIR%\config.yaml"
)
:: Only the model section at the top of the file decides this. Other sections
:: (delegation, memory, auxiliary...) carry their own empty provider: "" keys,
:: and matching those would send a configured user back to the setup page.
set "MODEL_CONFIGURED=1"
set /a _CFGLINE=0
for /f "usebackq delims=" %%L in ("%DATA_DIR%\config.yaml") do (
    set /a _CFGLINE+=1
    if !_CFGLINE! leq 5 (
        echo %%L | findstr /C:"provider: \"\"" >nul 2>&1 && set "MODEL_CONFIGURED=0"
    )
)
if "%MODEL_CONFIGURED%"=="0" (
    echo.
    echo   [i] 尚未配置 AI 模型。
    echo       正在启动配置服务...
    echo.
    start "" "%VENV_DIR%\Scripts\pythonw.exe" "%SCRIPT_DIR%\scripts\config-server.py"
    timeout /t 1 /nobreak >nul
    echo       正在打开配置页面...
    echo.
    start "" "%SCRIPT_DIR%\Config.html"
    echo   请在浏览器中完成配置，然后重新运行此脚本。
    echo.
    pause
    rem Kill config server if still running
    for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":18790.*LISTENING"') do (
        taskkill /F /PID %%P >nul 2>&1
    )
    exit /b 0
)

:: ============================================================================
:: Install hermes-web-ui if missing
:: ============================================================================

if not exist "%WEBUI_SERVER%" (
    echo.
    echo   [i] 正在安装 Hermes Web UI...
    echo.
    "%NODE_DIR%\npm.cmd" install -g hermes-web-ui --prefix "%NODE_DIR%" 2>nul
    if not exist "%WEBUI_SERVER%" (
        echo   [X] Web UI 安装失败。
        echo.
        pause
        exit /b 1
    )
    echo   [OK] Web UI 安装完成。
    echo.
)

:: ============================================================================
:: Launch
:: ============================================================================

echo.
echo   ============================================
echo     U-Hermes - AI 智能体
echo   ============================================
echo.

:: --gateway is kept for Windows-Menu.bat option 2. The Web UI starts the
:: gateway through GatewayManager, so continue the normal Web UI path.
if /I "%~1"=="--gateway" shift

:: If other arguments are passed, run hermes CLI directly.
if not "%~1"=="" (
    "%VENV_PYTHON%" -m hermes_cli.main %*
    goto :check_exit
)

:: --- Pre-launch: mirror the config into ~/.hermes ---
:: HERMES_HOME IS honoured by both the Web UI and the gateway (verified), so
:: this is a fallback, not a workaround for an override: it keeps a working
:: config in the default location for any component started without our env.
set "USER_HERMES_DIR=%USERPROFILE%\.hermes"
if not exist "%USER_HERMES_DIR%" mkdir "%USER_HERMES_DIR%" 2>nul
:: Copy full data/config.yaml (model, custom_providers, skills, etc.)
copy /Y "%DATA_DIR%\config.yaml" "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
:: Append platforms section once (GatewayManager reads port from here)
findstr /R /C:"^platforms:" "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
if errorlevel 1 (
    (
        echo.
        echo platforms:
        echo   api_server:
        echo     extra:
        echo       port: 8642
        echo       host: 127.0.0.1
        echo     enabled: true
        echo     key: ''
        echo     cors_origins: '*'
    ) >> "%USER_HERMES_DIR%\config.yaml"
)
:: Copy .env (API keys) if present
if exist "%DATA_DIR%\.env" copy /Y "%DATA_DIR%\.env" "%USER_HERMES_DIR%\.env" >nul 2>&1

:: --- Step 1: Kill leftover gateway processes ---
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":8642.*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)

:: Gateway is started automatically by the Web UI's GatewayManager.
:: Do NOT start it here — dual gateways cause port conflicts.

echo   [1/2] 正在启动 Web 界面（含 AI 引擎）...

set "WEBUI_TOKEN_FILE=%NODE_DIR%\node_modules\hermes-web-ui\dist\server\data\.token"
set "AUTH_TOKEN="
if exist "%WEBUI_TOKEN_FILE%" (
    set /p AUTH_TOKEN=<"%WEBUI_TOKEN_FILE%"
)

echo   [2/2] 即将打开浏览器...
echo.
echo   -----------------------------------------------
echo     浏览器地址: http://127.0.0.1:8648
echo     按 Ctrl+C 停止服务
echo   -----------------------------------------------
echo.

:: Auto-open browser after 3s (Web UI starts fast since it's Node)
if defined AUTH_TOKEN (
    start /B "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8648/?token=!AUTH_TOKEN!"
) else (
    start /B "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8648"
)

:: Run Web UI server in foreground (blocks until Ctrl+C or close)
"%NODE_EXE%" "%WEBUI_SERVER%"

:: --- Cleanup: kill gateway when Web UI exits ---
echo.
echo   正在停止 AI 引擎...
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":8642.*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)

:check_exit
echo.
echo   Hermes 已停止。
echo.
pause

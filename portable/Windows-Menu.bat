@echo off
chcp 65001 >nul 2>&1
title U-Hermes - Menu

:: ============================================================================
:: U-Hermes Windows Menu
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:MENU
cls
echo.
echo   ============================================
echo     U-Hermes - AI 智能体
echo     基于 Hermes Agent (Nous Research) 驱动
echo   ============================================
echo.
echo   [1] 启动智能体 (命令行)
echo   [2] 启动智能体 + 消息网关
echo   [3] 仅启动消息网关
echo   [4] 打开配置页面
echo   [5] 模型设置 (hermes model)
echo   [6] 一键诊断
echo   [7] 更新 Hermes
echo   [8] 退出
echo.
set /p choice="  请选择 [1-8]: "

if "%choice%"=="1" goto START_CLI
if "%choice%"=="2" goto START_ALL
if "%choice%"=="3" goto START_GATEWAY
if "%choice%"=="4" goto CONFIG
if "%choice%"=="5" goto MODEL
if "%choice%"=="6" goto DOCTOR
if "%choice%"=="7" goto UPDATE
if "%choice%"=="8" goto EXIT

echo   无效选择。
timeout /t 2 >nul
goto MENU

:START_CLI
call "%SCRIPT_DIR%\Windows-Start.bat"
goto MENU

:START_ALL
call "%SCRIPT_DIR%\Windows-Start.bat" --gateway
goto MENU

:START_GATEWAY
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "HERMES_HOME=%SCRIPT_DIR%\data"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%PATH%"
echo.
echo   正在启动消息网关...
echo   按 Ctrl+C 停止。
echo.
"%VENV_PYTHON%" -m hermes_cli.main gateway start
goto MENU

:CONFIG
set "VENV_PYTHONW=%SCRIPT_DIR%\hermes\.venv\Scripts\pythonw.exe"
if not exist "%VENV_PYTHONW%" (
    echo.
    echo   [X] Python 运行时缺失，请先运行 Windows-Start.bat 完成安装。
    pause
    goto MENU
)
rem 先清理旧的配置服务，再启动新的（供"测试连接"按钮使用）
rem pythonw 无窗口运行，不挂靠本控制台，退出菜单不会卡死
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":18790.*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)
start "" "%VENV_PYTHONW%" "%SCRIPT_DIR%\scripts\config-server.py"
timeout /t 1 /nobreak >nul
start "" "%SCRIPT_DIR%\Config.html"
goto MENU

:MODEL
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "HERMES_HOME=%SCRIPT_DIR%\data"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%PATH%"
"%VENV_PYTHON%" -m hermes_cli.main model
goto MENU

:DOCTOR
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "HERMES_HOME=%SCRIPT_DIR%\data"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%PATH%"
"%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\diagnose.py"
echo   （如需更深入的技术诊断，可另行运行: Windows-Start.bat doctor）
pause
goto MENU

:UPDATE
echo.
echo   正在更新 U-Hermes 组件...
set "UV_EXE=%SCRIPT_DIR%\runtime\uv\uv.exe"
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "NODE_DIR=%SCRIPT_DIR%\runtime\node-win-x64"
set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

echo.
echo   [1/2] 更新 Web 界面 (hermes-web-ui)...
call "%NODE_DIR%\npm.cmd" install -g hermes-web-ui@latest --prefix "%NODE_DIR%"

echo.
echo   [2/2] 修复 Hermes Agent 安装...
:: 非可编辑安装（-e 会把绝对路径写进 venv，换盘符后损坏便携性）
:: HERMES_NIX_BUILD=1 是上游给打包场景留的开关，2026年7月起不设它会拒绝构建
set "HERMES_NIX_BUILD=1"
"%UV_EXE%" pip install "%SCRIPT_DIR%\hermes\hermes-agent[pty,mcp,cron,messaging]" --python "%VENV_PYTHON%" --reinstall-package hermes-agent

:: 修补 exe 启动器中的 Python 路径
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\scripts\fix-portable-paths.ps1" -VenvDir "%SCRIPT_DIR%\hermes\.venv" -RuntimeDir "%SCRIPT_DIR%\runtime" -AgentDir "%SCRIPT_DIR%\hermes\hermes-agent" -UvExe "%UV_EXE%"

echo.
echo   更新完成。
echo.
echo   [i] 如需更新 AI 引擎核心版本，请从 GitHub Releases 下载最新压缩包，
echo       解压后把旧的 data 文件夹复制过去即可保留全部配置和记忆：
echo       https://github.com/FUDAHA99/U-hermes/releases
pause
goto MENU

:EXIT
exit /b 0

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
echo   [1] 启动智能体 (网页界面，推荐)
echo   [2] 启动智能体 (命令行对话)
echo   [3] 仅启动消息网关
echo   [4] 打开配置页面
echo   [5] 模型设置 (hermes model)
echo   [6] 一键诊断
echo   [7] 清理聊天记录（释放 U 盘空间）
echo   [8] 清理本机残留（在别人电脑上用完后执行）
echo   [9] 更新 Hermes
echo   [0] 退出
echo.
:: set /p leaves the variable alone when the user just presses Enter, so
:: without clearing it first an empty line silently re-ran whatever they
:: chose last -- including [7] 更新.
set "choice="
set /p choice="  请选择 [0-9]: "

if "%choice%"=="1" goto START_CLI
if "%choice%"=="2" goto START_ALL
if "%choice%"=="3" goto START_GATEWAY
if "%choice%"=="4" goto CONFIG
if "%choice%"=="5" goto MODEL
if "%choice%"=="6" goto DOCTOR
if "%choice%"=="7" goto PRUNE
if "%choice%"=="8" goto CLEANUP
if "%choice%"=="9" goto UPDATE
if "%choice%"=="0" goto EXIT

echo   无效选择。
timeout /t 2 >nul
goto MENU

:START_CLI
call "%SCRIPT_DIR%\Windows-Start.bat"
goto MENU

:START_ALL
:: Was `--gateway`, which Windows-Start.bat shifts off before continuing down
:: the Web UI path -- so this option did exactly what [1] did, under a
:: different name. `chat` reaches the CLI branch and is genuinely a different
:: way to use the agent.
call "%SCRIPT_DIR%\Windows-Start.bat" chat
goto MENU

:START_GATEWAY
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "HERMES_HOME=%SCRIPT_DIR%\data"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%PATH%"
echo.
echo   正在启动消息网关，按 Ctrl+C 停止。
echo.
:: `gateway start` detaches the process (CREATE_NEW_PROCESS_GROUP +
:: DETACHED_PROCESS), so it ignores Ctrl+C, outlives this window and keeps
:: holding port 8642 with no way to stop it from here. `gateway run` stays
:: in the foreground, which is what the instruction above promises.
"%VENV_PYTHON%" -m hermes_cli.main gateway run
echo.
pause
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
:: The service serves the page and opens it itself, at an address carrying a
:: one-run token. Do not open Config.html from disk -- the service refuses
:: file:// pages, because a sandboxed iframe on any website looks identical.
start "" "%VENV_PYTHONW%" "%SCRIPT_DIR%\scripts\config-server.py"
"%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe" "%SCRIPT_DIR%\scripts\wait-for.py" http://127.0.0.1:18790/ping 15
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

:PRUNE
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "HERMES_HOME=%SCRIPT_DIR%\data"
set "HERMES_CONFIG=%SCRIPT_DIR%\data\config.yaml"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%SCRIPT_DIR%\hermes\.venv\Scripts;%SCRIPT_DIR%\runtime\python-win-x64;%PATH%"
echo.
echo   删除超过 90 天的旧对话，并把数据库压缩到实际大小。
echo   删掉的对话找不回来；配置、密钥、记忆和技能都不受影响。
echo.
"%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\db-maintenance.py" "%SCRIPT_DIR%\data" --days 90
if errorlevel 2 (
    echo.
    echo   [i] 关掉所有 U-Hermes 窗口之后，再回到这里选 [7]。
)
echo.
pause
goto MENU

:UPDATE
echo.
echo   正在更新 U-Hermes 组件...
set "UV_EXE=%SCRIPT_DIR%\runtime\uv\uv.exe"
set "VENV_PYTHON=%SCRIPT_DIR%\hermes\.venv\Scripts\python.exe"
set "NODE_DIR=%SCRIPT_DIR%\runtime\node-win-x64"
set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

:: Pinned, not @latest. Upstream ships 2-4 releases a week, so "update" used
:: to mean "replace a tested component with whatever landed this morning" --
:: which is how the build drifted for two months without anyone noticing.
set "WEBUI_SPEC=hermes-web-ui"
if exist "%SCRIPT_DIR%\versions.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%SCRIPT_DIR%\versions.env") do (
        if /I "%%A"=="HERMES_WEB_UI_VERSION" set "WEBUI_SPEC=hermes-web-ui@%%B"
    )
)

echo.
echo   [1/2] 更新 Web 界面 (%WEBUI_SPEC%)...
call "%NODE_DIR%\npm.cmd" install -g %WEBUI_SPEC% --prefix "%NODE_DIR%"
if errorlevel 1 (
    echo   [i] 直连 npm 没成功，改用国内镜像重试...
    call "%NODE_DIR%\npm.cmd" install -g %WEBUI_SPEC% --prefix "%NODE_DIR%" --registry=https://registry.npmmirror.com
)

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
echo   [i] 如需更新 AI 引擎核心版本，要下载新的压缩包：
echo       https://github.com/FUDAHA99/U-hermes/releases
echo.
echo       升级步骤：
echo         1. 先关掉正在运行的 U-Hermes；
echo         2. 把新压缩包直接解压到当前目录，选"替换目标中的文件"。
echo       配置、密钥和聊天记录都在 data 里，压缩包不会覆盖它们。
echo       你的工作区在安装目录外面，同样不受影响。
pause
goto MENU

:CLEANUP
echo.
echo   正在清理本机 %USERPROFILE%\.hermes 下的配置副本...
set "USER_HERMES_DIR=%USERPROFILE%\.hermes"
set "MIRROR_MARK=%USER_HERMES_DIR%\.u-hermes-mirror"
if not exist "%MIRROR_MARK%" (
    echo   [OK] 本机上没有 U-Hermes 留下的副本。
    echo.
    pause
    goto MENU
)
del /Q "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
del /Q "%USER_HERMES_DIR%\.env" >nul 2>&1
if exist "%USER_HERMES_DIR%\config.yaml.before-u-hermes" move /Y "%USER_HERMES_DIR%\config.yaml.before-u-hermes" "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
if exist "%USER_HERMES_DIR%\.env.before-u-hermes" move /Y "%USER_HERMES_DIR%\.env.before-u-hermes" "%USER_HERMES_DIR%\.env" >nul 2>&1
del /Q "%MIRROR_MARK%" >nul 2>&1
echo   [OK] 已删除 U-Hermes 放在本机的 config.yaml 和 .env。
echo.
:: Only those two files are ours. Anything else in that folder belongs to a
:: Hermes this machine had of its own -- deleting it would be destroying
:: someone else's data -- so report it instead of guessing.
set "_LEFT=0"
for /f %%N in ('dir /b /a "%USER_HERMES_DIR%" 2^>nul ^| find /c /v ""') do set "_LEFT=%%N"
if not "%_LEFT%"=="0" (
    echo   [i] 该文件夹里还剩 %_LEFT% 项，不是 U-Hermes 放的，没有动：
    echo       %USER_HERMES_DIR%
    echo       如果这台电脑自己装过 Hermes，那些是它的数据；
    echo       确认不需要的话可以手动删掉整个文件夹。
)
echo.
pause
goto MENU

:EXIT
exit /b 0

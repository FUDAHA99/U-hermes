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
:: Loopback only. The Web UI defaults to BIND_HOST || "0.0.0.0", and its
:: login page prints the default credentials to every unauthenticated
:: visitor, in their own language ("默认登录名：admin，默认密码：123456").
:: On a cafe, office or hotel network that is an agent with shell access on
:: this machine, handed to anyone who can reach port 8648.
set "BIND_HOST=127.0.0.1"
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
:: Make sure there is a config to repair, then repair it
:: ============================================================================

:: Creating the default has to come first. protect-config.ps1 returns
:: immediately when the file is missing, so running it before this left a
:: brand-new install with no gateway key until the second launch.
if not exist "%DATA_DIR%\config.yaml" (
    echo.
    echo   [i] 首次启动，正在创建默认配置...
    echo.
    mkdir "%DATA_DIR%" 2>nul
    :: journal_mode is NOT read by the pinned engine (v2026.9.14) -- it has no
    :: such config key; hermes_state hardcodes PRAGMA journal_mode=WAL and
    :: falls back to DELETE only when SQLite itself raises, which exFAT does
    :: not. Verified against the packaged engine. Left in place because a
    :: later version may read it, and because synchronous=FULL makes a
    :: committed message durable either way -- but do not tell users the
    :: database is out of WAL mode, because it is not.
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

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\scripts\protect-config.ps1" -ConfigFile "%DATA_DIR%\config.yaml" -InstallDir "%SCRIPT_DIR%"

:: ============================================================================
:: Can this install actually answer a message?
:: ============================================================================

:: This used to be a scan of the first five lines for provider: "". It missed
:: every other way a config can be unusable -- a provider id the engine does
:: not know, a key that never reached .env -- and those all look like a
:: working install until the user types something and nothing comes back.
set /a _CFGTRY=0

:check_config
"%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\preflight.py" "%DATA_DIR%"
:: Exactly 10 means "the user has to configure something". Anything else,
:: including 9009 from a python that will not start, must not send them
:: round the config page three times for a problem the page cannot fix.
if errorlevel 11 goto :config_ok
if not errorlevel 10 goto :config_ok

set /a _CFGTRY+=1
if %_CFGTRY% gtr 3 (
    echo.
    echo   配置还没完成，先退出了。配置好以后重新双击本文件即可。
    echo.
    pause
    exit /b 0
)

echo.
echo       正在打开配置页面...
echo.
:: The service serves the page and opens it itself, at an address carrying a
:: token only it knows. Opening Config.html from disk instead would give the
:: page the same "null" origin any sandboxed iframe has, and the service
:: would have no way to tell them apart.
start "" "%VENV_DIR%\Scripts\pythonw.exe" "%SCRIPT_DIR%\scripts\config-server.py"
"%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\wait-for.py" http://127.0.0.1:18790/ping 15
echo   请在浏览器里选好模型、填上密钥，点「测试连接」通过后再点「保存配置」。
echo   保存完成后回到这个窗口，按任意键继续启动。
echo.
pause
rem Kill config server if still running
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":18790.*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)
goto :check_config

:config_ok

:: ============================================================================
:: Command line mode
:: ============================================================================

:: --gateway is accepted for shortcuts that still pass it. The Web UI starts
:: the gateway through GatewayManager, so it means the same thing as no
:: argument at all; the menu passes `chat` when it wants the CLI.
if /I "%~1"=="--gateway" shift

:: This has to come BEFORE the Web UI install below. The CLI needs nothing
:: from Node, and putting it after meant a failed npm install exited the
:: script -- so on exactly the broken-network machine where someone reaches
:: for the command line as a fallback, the fallback was unreachable too.
if not "%~1"=="" (
    :: Start in the workspace the user chose. The CLI overwrites
    :: terminal.cwd with its own working directory whenever the backend is
    :: local, so without this the config page's workspace box would apply to
    :: the Web UI and not to `hermes chat`, and the CLI agent would write
    :: into the program files instead.
    for /f "usebackq delims=" %%W in (`""%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\preflight.py" --print-workspace "%DATA_DIR%""`) do set "AGENT_CWD=%%W"
    if defined AGENT_CWD if exist "!AGENT_CWD!\" cd /d "!AGENT_CWD!"
    "%VENV_PYTHON%" -m hermes_cli.main %*
    goto :check_exit
)

:: ============================================================================
:: Install hermes-web-ui if missing
:: ============================================================================

if exist "%WEBUI_SERVER%" goto :webui_ready

:: Pin to the version this package was built and tested against. Upstream
:: ships 2-4 releases a week, so installing @latest here would make every
:: repair a different product from the one in the zip.
set "WEBUI_SPEC=hermes-web-ui"
if exist "%SCRIPT_DIR%\versions.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%SCRIPT_DIR%\versions.env") do (
        if /I "%%A"=="HERMES_WEB_UI_VERSION" set "WEBUI_SPEC=hermes-web-ui@%%B"
    )
)

echo.
echo   [i] 正在安装 Web 界面 (!WEBUI_SPEC!)...
echo.
call "%NODE_DIR%\npm.cmd" install -g !WEBUI_SPEC! --prefix "%NODE_DIR%"
if exist "%WEBUI_SERVER%" goto :webui_ok

:: registry.npmjs.org is frequently unreachable from mainland China, which is
:: where this product's users are. Retrying on the mirror turns the most
:: common install failure into a non-event.
echo.
echo   [i] 直连 npm 没成功，改用国内镜像重试...
echo.
call "%NODE_DIR%\npm.cmd" install -g !WEBUI_SPEC! --prefix "%NODE_DIR%" --registry=https://registry.npmmirror.com
if exist "%WEBUI_SERVER%" goto :webui_ok

:: The error used to be swallowed by 2>nul, leaving the user with four
:: characters of diagnosis. Leave npm's own output on screen.
echo.
echo   [X] Web 界面装不上，上面几行是 npm 的原始报错。
echo       多数情况是网络问题：换个网络或挂上代理，再重新双击本文件。
echo.
pause
exit /b 1

:webui_ok
echo   [OK] Web 界面安装完成。
echo.

:webui_ready

:: ============================================================================
:: Launch
:: ============================================================================

echo.
echo   ============================================
echo     U-Hermes - AI 智能体
echo   ============================================
echo.

:: --- Pre-launch: mirror the config into ~/.hermes ---
:: HERMES_HOME IS honoured by both the Web UI and the gateway (verified), so
:: this is a fallback, not a workaround for an override: it keeps a working
:: config in the default location for any component started without our env.
set "USER_HERMES_DIR=%USERPROFILE%\.hermes"
set "MIRROR_MARK=%USER_HERMES_DIR%\.u-hermes-mirror"
if not exist "%USER_HERMES_DIR%" mkdir "%USER_HERMES_DIR%" 2>nul

:: A previous run that was Ctrl+C'd or had its window closed never reached
:: its own cleanup, so its copy of the keys is still sitting here. Clear it
:: before doing anything else, so at worst the keys survive until the next
:: launch on this machine rather than indefinitely.
call :restore_mirror quiet

:: If this machine already has its own Hermes, move its files aside rather
:: than overwriting them, and put them back on the way out. The marker says
:: "the config.yaml and .env in here are ours" -- it survives a hard kill, so
:: a second run will not stash a mirror on top of a mirror.
if exist "%MIRROR_MARK%" goto :mirror_ready

:: A copy that is byte-identical to ours is a mirror an older version of this
:: launcher left behind, from before the marker existed. Preserving it would
:: mean restoring our own keys onto this machine on the way out.
if not exist "%USER_HERMES_DIR%\config.yaml" goto :mirror_stashed
fc /B "%USER_HERMES_DIR%\config.yaml" "%DATA_DIR%\config.yaml" >nul 2>&1
if not errorlevel 1 goto :mirror_stashed
move /Y "%USER_HERMES_DIR%\config.yaml" "%USER_HERMES_DIR%\config.yaml.before-u-hermes" >nul 2>&1
if exist "%USER_HERMES_DIR%\.env" move /Y "%USER_HERMES_DIR%\.env" "%USER_HERMES_DIR%\.env.before-u-hermes" >nul 2>&1

:mirror_stashed
echo u-hermes> "%MIRROR_MARK%"

:mirror_ready

:: The platforms block used to be appended here with key: '' when missing,
:: which handed the gateway an empty key and stopped it starting. It is not
:: needed any more: protect-config.ps1 has already put a real one in the
:: source config above, so a byte copy is correct.
copy /Y "%DATA_DIR%\config.yaml" "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
if exist "%DATA_DIR%\.env" copy /Y "%DATA_DIR%\.env" "%USER_HERMES_DIR%\.env" >nul 2>&1

:: --- Step 1: Kill leftover gateway processes ---
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":8642.*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)

:: Gateway is started automatically by the Web UI's GatewayManager.
:: Do NOT start it here — dual gateways cause port conflicts.

echo   [1/2] 正在启动 Web 界面（含 AI 引擎）...
echo   [2/2] 界面就绪后会自动打开浏览器...
echo.
echo   -----------------------------------------------
echo     浏览器地址: http://127.0.0.1:8648
echo     停止服务: 按 Ctrl+C，看到提示时选 Y
echo     （直接点窗口右上角的 X 关闭，本机上的配置副本
echo       来不及清理，下次启动或用菜单 [8] 可以清掉）
echo   -----------------------------------------------
echo.

:: Wait for the Web UI to actually answer, claim its account before anyone
:: else can, then open the browser. A fixed delay was wrong on anything
:: slower than the machine it was written on -- and the token query string
:: it used to append never worked: that file does not exist at that path,
:: and the API rejects the token either way.
::
:: The account matters because upstream ships it unclaimed: the first caller
:: to post admin/123456 becomes super admin, and the login page prints those
:: credentials to every visitor. Loopback-only (BIND_HOST above) keeps that
:: off the network; this keeps it off a shared Windows PC too.
start /B "" "%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\first-login.py" http://127.0.0.1:8648 "%DATA_DIR%\webui" 90

:: Run Web UI server in foreground (blocks until Ctrl+C or close)
::
:: From the install directory, deliberately. The Web UI resolves its account
:: database as cwd + "packages/server/data" unless NODE_ENV=production, so
:: without this the login store lands wherever the user happened to launch
:: from -- a different database on every machine, and on C: rather than the
:: stick when the shortcut starts elsewhere. setlocal at the top of this
:: script restores the caller's directory, so Windows-Menu.bat is unaffected.
cd /d "%SCRIPT_DIR%"
"%NODE_EXE%" "%WEBUI_SERVER%"

:: --- Cleanup: kill gateway when Web UI exits ---
echo.
echo   正在停止 AI 引擎...
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr ":8642.*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)

call :restore_mirror

:check_exit
echo.
echo   Hermes 已停止。
echo.
pause
exit /b 0

:: --- Take the copy of the user's keys off this machine ---------------------
:: "数据不出U盘" has to survive running on someone else's computer, and the
:: mirror contains their API keys in plain text. Only files we put there are
:: removed; anything this machine had is moved back.
::
:: Called both on the way out and at the start of the next run, because
:: neither Ctrl+C nor closing the window with [X] reaches the exit path.
:restore_mirror
if not exist "%MIRROR_MARK%" goto :eof
del /Q "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
del /Q "%USER_HERMES_DIR%\.env" >nul 2>&1
if exist "%USER_HERMES_DIR%\config.yaml.before-u-hermes" move /Y "%USER_HERMES_DIR%\config.yaml.before-u-hermes" "%USER_HERMES_DIR%\config.yaml" >nul 2>&1
if exist "%USER_HERMES_DIR%\.env.before-u-hermes" move /Y "%USER_HERMES_DIR%\.env.before-u-hermes" "%USER_HERMES_DIR%\.env" >nul 2>&1
del /Q "%MIRROR_MARK%" >nul 2>&1
if not "%~1"=="quiet" echo   已清理本机上的配置副本。
goto :eof

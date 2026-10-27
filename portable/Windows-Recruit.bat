@echo off
chcp 65001 >nul 2>&1
setlocal
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "DATA_DIR=%SCRIPT_DIR%\data"
set "RUNTIME_DIR=%SCRIPT_DIR%\runtime"
set "HERMES_DIR=%SCRIPT_DIR%\hermes"
set "VENV_DIR=%HERMES_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "UV_EXE=%RUNTIME_DIR%\uv\uv.exe"
set "UV_CACHE_DIR=%SCRIPT_DIR%\.uv-cache"
set "PYTHONNOUSERSITE=1"

rem Cleanup runs before setup or any engine import, just like the general launcher.
if exist "%RUNTIME_DIR%\python-win-x64\python.exe" "%RUNTIME_DIR%\python-win-x64\python.exe" "%SCRIPT_DIR%\scripts\remove-old-host-copy.py" "%DATA_DIR%"
if exist "%RUNTIME_DIR%\python-win-x64\python.exe" "%RUNTIME_DIR%\python-win-x64\python.exe" "%SCRIPT_DIR%\scripts\prune-stale-files.py" "%SCRIPT_DIR%\."
if not exist "%VENV_PYTHON%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\setup.ps1"
    if errorlevel 1 goto FAIL
)
if not exist "%VENV_PYTHON%" goto FAIL
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\scripts\fix-portable-paths.ps1" -VenvDir "%VENV_DIR%" -RuntimeDir "%RUNTIME_DIR%" -AgentDir "%HERMES_DIR%\hermes-agent" -UvExe "%UV_EXE%"
if errorlevel 1 goto FAIL
"%VENV_PYTHON%" -c "import pypdf; assert pypdf.__version__ == '6.21.2'" >nul 2>&1
if errorlevel 1 (
    "%UV_EXE%" pip install -r "%SCRIPT_DIR%\recruit\requirements.txt" --python "%VENV_PYTHON%"
    if errorlevel 1 goto FAIL
)
set "HERMES_HOME=%DATA_DIR%\recruit\hermes-home"
set "HERMES_GATEWAY_LOCK_DIR=%DATA_DIR%\recruit\hermes-home\gateway-locks"
"%VENV_PYTHON%" "%SCRIPT_DIR%\scripts\recruit-server.py" %*
if errorlevel 1 goto FAIL
exit /b 0

:FAIL
echo.
echo 招聘版未能启动，请查看上方提示；安装依赖需要联网。
echo 若端口已被占用，请先关闭通用版。使用说明在 recruit\RECRUIT.md。
pause
exit /b 1

@echo off
chcp 65001 >nul 2>&1
echo ========== U-Hermes Debug ==========
echo.

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "DATA_DIR=%SCRIPT_DIR%\data"
:: Point the engine at the stick. Without this the dashboard below read the
:: PC's own ~/.hermes, not the config printed above it.
set "HERMES_HOME=%DATA_DIR%"
set "HERMES_GATEWAY_LOCK_DIR=%DATA_DIR%\gateway-locks"
set "HERMES_DIR=%SCRIPT_DIR%\hermes"
set "VENV_PYTHON=%HERMES_DIR%\.venv\Scripts\python.exe"
set "NODE_EXE=%SCRIPT_DIR%\runtime\node-win-x64\node.exe"
set "PYTHONPATH=%HERMES_DIR%\hermes-agent;%PYTHONPATH%"

echo [1] Script dir: %SCRIPT_DIR%
echo [2] Python: %VENV_PYTHON%
if exist "%VENV_PYTHON%" (echo     OK - exists) else (echo     MISSING!)
echo [3] Node: %NODE_EXE%
if exist "%NODE_EXE%" (echo     OK - exists) else (echo     MISSING!)
echo [4] Config: %DATA_DIR%\config.yaml
if exist "%DATA_DIR%\config.yaml" (echo     OK - exists) else (echo     MISSING!)
echo [5] .env: %DATA_DIR%\.env
if exist "%DATA_DIR%\.env" (echo     OK - exists) else (echo     not found - ok)
echo.

echo --- config.yaml content ---
if exist "%DATA_DIR%\config.yaml" type "%DATA_DIR%\config.yaml"
echo.
echo --- end config ---
echo.

echo [6] Testing Python...
"%VENV_PYTHON%" -c "print('Python OK')" 2>&1
echo.

echo [7] Testing hermes import...
"%VENV_PYTHON%" -c "import hermes_cli; print('hermes_cli OK')" 2>&1
echo.

echo [8] Testing dashboard command...
"%VENV_PYTHON%" -m hermes_cli.main dashboard --port 9119 2>&1

echo.
echo ========== Done ==========
pause

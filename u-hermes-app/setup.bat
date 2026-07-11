@echo off
chcp 65001 >nul 2>&1
echo.
echo   U-Hermes Desktop App - Development Setup
echo   ==========================================
echo.

:: Check Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo   [X] Node.js not found. Please install Node.js 20+ first.
    echo       https://npmmirror.com/mirrors/node/
    pause
    exit /b 1
)

:: Use China npm mirror
set "NPM_CONFIG_REGISTRY=https://registry.npmmirror.com"
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"

echo   [1/3] Installing npm dependencies...
npm install

echo.
echo   [2/3] Preparing runtime resources...
:: Copy portable resources for dev mode
if not exist "resources\hermes" (
    echo   Copying hermes from portable...
    if exist "..\portable\hermes" (
        xcopy /E /I /Q "..\portable\hermes" "resources\hermes" >nul
    ) else (
        echo   [!] Run portable\setup.ps1 first to download Hermes Agent.
    )
)

if not exist "resources\cloud" (
    xcopy /E /I /Q "..\portable\cloud" "resources\cloud" >nul 2>&1
)

if not exist "resources\skills-cn" (
    xcopy /E /I /Q "..\portable\skills-cn" "resources\skills-cn" >nul 2>&1
)

if not exist "resources\Config.html" (
    copy "..\portable\Config.html" "resources\Config.html" >nul 2>&1
)

echo.
echo   [3/3] Done!
echo.
echo   Commands:
echo     npm run dev          - Run in dev mode
echo     npm run build:win    - Build Windows installer
echo     npm run build:mac-arm64 - Build Mac ARM64
echo.
pause

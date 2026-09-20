# ============================================================================
# U-Hermes Portable Setup Script (Windows)
# Downloads: Embedded Python 3.11 + uv + Hermes Agent + dependencies
# All downloads use China mirrors where possible.
# ============================================================================

param(
    [switch]$SkipHermes,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $scriptDir "runtime"
$hermesDir = Join-Path $scriptDir "hermes"
$dataDir = Join-Path $scriptDir "data"
$uvCacheDir = Join-Path $scriptDir ".uv-cache"

# Fix cross-drive cache issue (uv defaults to %LOCALAPPDATA% which may be on C:)
$env:UV_CACHE_DIR = $uvCacheDir

# Versions -- read from versions.env, the single source of truth that
# release.yml and setup.sh already use.
#
# This script used to carry its own copies (Python 3.11.9, Node v22.22.1,
# uv 0.7.12) which had drifted away from the pins, so a Windows developer
# running setup.ps1 built a toolchain no release has ever shipped. It also
# cloned hermes-agent from main rather than HERMES_AGENT_REF, which is how
# the local engine ended up four months behind the released one -- and how
# a day of conclusions came to be checked against the wrong source.
$versionsFile = Join-Path $scriptDir "versions.env"
$V = @{}
if (Test-Path $versionsFile) {
    foreach ($line in Get-Content $versionsFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') { $V[$Matches[1]] = $Matches[2] }
    }
} else {
    Write-Host "  [!] versions.env not found beside this script; using fallbacks." -ForegroundColor Yellow
}
function Pin([string]$name, [string]$fallback) {
    if ($V.ContainsKey($name) -and $V[$name]) { return $V[$name] }
    return $fallback
}

$pythonVersion = Pin "PYTHON_EMBED_VERSION" "3.13.15"
$nodeVersion   = Pin "NODE_VERSION"         "v24.21.0"
$uvVersion     = Pin "UV_VERSION"           "0.12.16"
$agentRef      = Pin "HERMES_AGENT_REF"     ""
if ($env:CANARY -eq "true") {
    $agentRef = ""
    Write-Host "  [i] CANARY: building against hermes-agent HEAD" -ForegroundColor Cyan
}

# Mirrors (China-friendly)
$pypiMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
$nodeMirror = "https://npmmirror.com/mirrors/node"
$pythonMirror = "https://npmmirror.com/mirrors/python"
$uvMirror = "https://github.com/astral-sh/uv/releases/download"

# ============================================================================
# Helpers
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Magenta
    Write-Host "    U-Hermes Portable Setup" -ForegroundColor Magenta
    Write-Host "    USB AI Agent - Powered by Hermes Agent" -ForegroundColor Magenta
    Write-Host "  ============================================" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Step {
    param([string]$Icon, [string]$Msg, [string]$Color = "Cyan")
    Write-Host "  $Icon $Msg" -ForegroundColor $Color
}

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Download-File {
    param([string]$Url, [string]$Dest, [string]$Desc = "")
    if ($Desc) { Write-Step "->" "Downloading $Desc..." "Cyan" }
    try {
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
        $ProgressPreference = 'Continue'
    } catch {
        Write-Step "X" "Download failed: $Url" "Red"
        Write-Step "  " "Error: $_" "Red"
        throw
    }
}

# ============================================================================
# Step 1: Embedded Python
# ============================================================================

function Install-Python {
    $pythonDir = Join-Path $runtimeDir "python-win-x64"
    $pythonExe = Join-Path $pythonDir "python.exe"

    if ((Test-Path $pythonExe) -and -not $Force) {
        Write-Step "OK" "Python $pythonVersion already installed." "Green"
        return $pythonDir
    }

    Write-Step "1/5" "Installing embedded Python $pythonVersion..." "Yellow"
    Ensure-Dir $pythonDir

    $zipName = "python-$pythonVersion-embed-amd64.zip"
    $zipPath = Join-Path $runtimeDir $zipName

    # Try China mirror first, fallback to python.org
    $urls = @(
        "$pythonMirror/$pythonVersion/$zipName",
        "https://www.python.org/ftp/python/$pythonVersion/$zipName"
    )

    $downloaded = $false
    foreach ($url in $urls) {
        try {
            Download-File -Url $url -Dest $zipPath -Desc "Python $pythonVersion"
            $downloaded = $true
            break
        } catch {
            Write-Step "  " "Mirror failed, trying next..." "DarkGray"
        }
    }
    if (-not $downloaded) { throw "Failed to download Python from all mirrors" }

    # Extract
    Write-Step "  " "Extracting..." "DarkGray"
    Expand-Archive -Path $zipPath -DestinationPath $pythonDir -Force
    Remove-Item $zipPath -Force

    # Enable pip/site-packages: uncomment import site in python311._pth
    $pthFile = Get-ChildItem -Path $pythonDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $content = Get-Content $pthFile.FullName
        $content = $content -replace "^#import site", "import site"
        Set-Content -Path $pthFile.FullName -Value $content -Encoding utf8
    }

    # Install pip via get-pip.py
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPipPath = Join-Path $pythonDir "get-pip.py"
    Download-File -Url $getPipUrl -Dest $getPipPath -Desc "pip installer"
    & $pythonExe $getPipPath --quiet 2>$null
    Remove-Item $getPipPath -Force -ErrorAction SilentlyContinue

    Write-Step "OK" "Python $pythonVersion installed." "Green"
    return $pythonDir
}

# ============================================================================
# Step 2: uv package manager
# ============================================================================

function Install-Uv {
    $uvDir = Join-Path $runtimeDir "uv"
    $uvExe = Join-Path $uvDir "uv.exe"

    if ((Test-Path $uvExe) -and -not $Force) {
        Write-Step "OK" "uv already installed." "Green"
        return $uvExe
    }

    Write-Step "2/5" "Installing uv $uvVersion..." "Yellow"
    Ensure-Dir $uvDir

    $zipName = "uv-x86_64-pc-windows-msvc.zip"
    $zipPath = Join-Path $runtimeDir "uv.zip"
    $url = "$uvMirror/$uvVersion/$zipName"

    Download-File -Url $url -Dest $zipPath -Desc "uv $uvVersion"

    # Extract
    Expand-Archive -Path $zipPath -DestinationPath $uvDir -Force
    Remove-Item $zipPath -Force

    # uv may be nested in a subfolder
    $nestedUv = Get-ChildItem -Path $uvDir -Recurse -Filter "uv.exe" | Select-Object -First 1
    if ($nestedUv -and ($nestedUv.DirectoryName -ne $uvDir)) {
        Move-Item -Path (Join-Path $nestedUv.DirectoryName "*") -Destination $uvDir -Force
    }

    if (-not (Test-Path $uvExe)) {
        throw "uv.exe not found after extraction"
    }

    Write-Step "OK" "uv $uvVersion installed." "Green"
    return $uvExe
}

# ============================================================================
# Step 3: Node.js (for browser tools)
# ============================================================================

function Install-Node {
    $nodeDir = Join-Path $runtimeDir "node-win-x64"
    $nodeExe = Join-Path $nodeDir "node.exe"

    if ((Test-Path $nodeExe) -and -not $Force) {
        Write-Step "OK" "Node.js already installed." "Green"
        return $nodeDir
    }

    Write-Step "3/5" "Installing Node.js $nodeVersion..." "Yellow"
    Ensure-Dir $nodeDir

    $zipName = "node-$nodeVersion-win-x64.zip"
    $zipPath = Join-Path $runtimeDir $zipName
    $url = "$nodeMirror/$nodeVersion/$zipName"

    Download-File -Url $url -Dest $zipPath -Desc "Node.js $nodeVersion"

    # Extract to temp then move contents
    $tempDir = Join-Path $runtimeDir "node-temp"
    Expand-Archive -Path $zipPath -DestinationPath $tempDir -Force
    $innerDir = Get-ChildItem -Path $tempDir -Directory | Select-Object -First 1
    if ($innerDir) {
        Copy-Item -Path (Join-Path $innerDir.FullName "*") -Destination $nodeDir -Recurse -Force
    }
    Remove-Item $tempDir -Recurse -Force
    Remove-Item $zipPath -Force

    Write-Step "OK" "Node.js $nodeVersion installed." "Green"
    return $nodeDir
}

# ============================================================================
# Step 4: Hermes Agent source
# ============================================================================

function Install-HermesSource {
    $agentDir = Join-Path $hermesDir "hermes-agent"

    if ((Test-Path (Join-Path $agentDir "pyproject.toml")) -and -not $Force) {
        Write-Step "OK" "Hermes Agent source already present." "Green"
        return $agentDir
    }

    Write-Step "4/5" "Downloading Hermes Agent..." "Yellow"
    Ensure-Dir $hermesDir

    # Clone via git if available, otherwise download zip
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        if (Test-Path $agentDir) { Remove-Item $agentDir -Recurse -Force }
        if ($agentRef) {
            & git clone --depth 1 --branch $agentRef https://github.com/NousResearch/hermes-agent.git $agentDir 2>&1 | Out-Null
        } else {
            & git clone --depth 1 https://github.com/NousResearch/hermes-agent.git $agentDir 2>&1 | Out-Null
        }
    } else {
        # Download as zip
        $zipRef = if ($agentRef) { $agentRef } else { "main" }
        $zipUrl = "https://github.com/NousResearch/hermes-agent/archive/refs/$(if ($agentRef) { 'tags' } else { 'heads' })/$zipRef.zip"
        $zipPath = Join-Path $hermesDir "hermes-agent.zip"
        Download-File -Url $zipUrl -Dest $zipPath -Desc "Hermes Agent (zip)"
        Expand-Archive -Path $zipPath -DestinationPath $hermesDir -Force
        # Rename extracted folder
        $extracted = Get-ChildItem -Path $hermesDir -Directory -Filter "hermes-agent-*" | Select-Object -First 1
        if ($extracted) {
            if (Test-Path $agentDir) { Remove-Item $agentDir -Recurse -Force }
            Rename-Item $extracted.FullName "hermes-agent"
        }
        Remove-Item $zipPath -Force
    }

    Write-Step "OK" "Hermes Agent source ready." "Green"
    return $agentDir
}

# ============================================================================
# Step 5: Python virtual environment + dependencies
# ============================================================================

function Install-Dependencies {
    param([string]$UvExe, [string]$PythonDir, [string]$AgentDir)

    $venvDir = Join-Path $hermesDir ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"

    if ((Test-Path $venvPython) -and -not $Force) {
        Write-Step "OK" "Virtual environment already exists." "Green"
        return
    }

    Write-Step "5/5" "Installing dependencies (this may take a few minutes)..." "Yellow"

    $pythonExe = Join-Path $PythonDir "python.exe"

    # Create venv using uv
    Write-Step "  " "Creating virtual environment..." "DarkGray"
    & $UvExe venv $venvDir --python $pythonExe 2>&1 | Out-Null

    # Install Hermes with core extras using China PyPI mirror
    Write-Step "  " "Installing Hermes Agent packages (China mirror)..." "DarkGray"
    $env:UV_INDEX_URL = $pypiMirror

    # Install hermes-agent. Non-editable: an editable install bakes an absolute
    # path into the venv, which breaks as soon as the drive letter changes.
    # HERMES_NIX_BUILD=1 is upstream's escape hatch for packaging contexts --
    # since Jul 2026 their setup.py refuses to build a wheel without it.
    # Extras: `cli` was removed upstream; pty/cron are no-op aliases now.
    $env:HERMES_NIX_BUILD = "1"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $UvExe pip install "$AgentDir[pty,mcp,cron,messaging]" --python $venvPython 2>&1 | ForEach-Object {
        if ($_ -match "error|Error|ERROR") { Write-Host "    $_" -ForegroundColor Red }
    }
    $ErrorActionPreference = $prevEAP

    # Verify installation
    if (Test-Path $venvPython) {
        $hermesCheck = & $venvPython -c "import agent; print('ok')" 2>$null
        if ($hermesCheck -eq "ok") {
            Write-Step "OK" "All dependencies installed successfully." "Green"
        } else {
            Write-Step "!!" "Dependencies installed but import check failed. May still work." "Yellow"
        }
    } else {
        Write-Step "X" "Virtual environment creation failed." "Red"
    }

    $env:UV_INDEX_URL = $null
}

# ============================================================================
# Step 6: Initialize data directory
# ============================================================================

function Initialize-Data {
    Ensure-Dir $dataDir
    Ensure-Dir (Join-Path $dataDir "memory")
    Ensure-Dir (Join-Path $dataDir "skills")
    Ensure-Dir (Join-Path $dataDir "sessions")
    Ensure-Dir (Join-Path $dataDir "cron")

    $configFile = Join-Path $dataDir "config.yaml"
    if (-not (Test-Path $configFile)) {
        # Write default config with Chinese-friendly defaults
        $defaultConfig = @"
# U-Hermes Configuration
# Docs: https://hermes-agent.nousresearch.com/docs/user-guide/configuration

model:
  provider: ""
  model: ""
  # Uncomment and fill in your preferred provider:
  # provider: "deepseek"
  # model: "deepseek-chat"

providers:
  deepseek:
    api_key: ""
    base_url: "https://api.deepseek.com/v1"
  kimi:
    api_key: ""
    base_url: "https://api.moonshot.cn/v1"
  qwen:
    api_key: ""
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  glm:
    api_key: ""
    base_url: "https://open.bigmodel.cn/api/paas/v4"
  minimax:
    api_key: ""
    base_url: "https://api.minimax.chat/v1"
  doubao:
    api_key: ""
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
api_server:
  extra:
    port: 8642

gateway:
  platforms: []
  # Example:
  # platforms:
  #   - type: telegram
  #     token: "YOUR_BOT_TOKEN"
  #   - type: qqbot
  #     app_id: "YOUR_APP_ID"
  #     app_secret: "YOUR_SECRET"

skills:
  extra_dirs:
    - "../skills-cn"

memory:
  enabled: true

cron:
  enabled: true
"@
        Set-Content -Path $configFile -Value $defaultConfig -Encoding utf8
    }

    Write-Step "OK" "Data directory initialized." "Green"
}

# ============================================================================
# Main
# ============================================================================

Write-Banner

$startTime = Get-Date

Ensure-Dir $runtimeDir

# Step 1-3: Runtime components
$pythonDir = Install-Python
$uvExe = Install-Uv
$nodeDir = Install-Node

# Step 4-5: Hermes Agent
if (-not $SkipHermes) {
    $agentDir = Install-HermesSource
    Install-Dependencies -UvExe $uvExe -PythonDir $pythonDir -AgentDir $agentDir
}

# Step 6: Hermes Web UI (npm package)
$webuiServer = Join-Path $nodeDir "node_modules\hermes-web-ui\dist\server\index.js"
if (-not (Test-Path $webuiServer)) {
    Write-Step "6/7" "Installing Hermes Web UI..." "Yellow"
    $npmCmd = Join-Path $nodeDir "npm.cmd"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $npmCmd install -g hermes-web-ui --prefix $nodeDir 2>&1 | ForEach-Object {
        if ($_ -match "error|Error|ERROR") { Write-Host "    $_" -ForegroundColor Red }
    }
    $ErrorActionPreference = $prevEAP
    if (Test-Path $webuiServer) {
        Write-Step "OK" "Hermes Web UI installed." "Green"
    } else {
        Write-Step "WARN" "Hermes Web UI install failed (will retry on first launch)." "Yellow"
    }
} else {
    Write-Step "OK" "Hermes Web UI already installed." "Green"
}

# Step 7: Data directory
Initialize-Data

$elapsed = ((Get-Date) - $startTime).TotalSeconds
Write-Host ""
Write-Step "OK" "Setup complete! ($([math]::Round($elapsed))s)" "Green"
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Open Config.html to configure your AI model" -ForegroundColor Gray
Write-Host "    2. Run Windows-Start.bat to launch U-Hermes" -ForegroundColor Gray
Write-Host ""

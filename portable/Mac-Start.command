#!/bin/bash
# ============================================================================
# U-Hermes Mac/Linux Launcher
# Starts Hermes Gateway + Web UI from portable directory
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$SCRIPT_DIR/runtime"
HERMES_DIR="$SCRIPT_DIR/hermes"
DATA_DIR="$SCRIPT_DIR/data"
SKILLS_CN_DIR="$SCRIPT_DIR/skills-cn"

# Detect architecture
ARCH="$(uname -m)"
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then
        NODE_DIR="$RUNTIME_DIR/node-mac-arm64"
    else
        NODE_DIR="$RUNTIME_DIR/node-mac-x64"
    fi
else
    NODE_DIR="$RUNTIME_DIR/node-linux-x64"
fi

VENV_DIR="$HERMES_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
NODE_EXE="$NODE_DIR/bin/node"
WEBUI_SERVER="$NODE_DIR/lib/node_modules/hermes-web-ui/dist/server/index.js"

# ============================================================================
# Pre-flight checks
# ============================================================================

# "Present" is not the same as "works", and the difference is the whole
# macOS problem. The v0.4.0/v0.4.1 zips were built without `zip -y`, so the
# venv's interpreter was stored as a COPY of the build machine's python.org
# framework stub -- a regular file that passes `[ -f ]` and then dies at
# dyld time on a Mac that has no such framework. Gating the rebuild on the
# file's existence meant the launcher never even tried to recover.
venv_python_works() {
    [ -x "$VENV_PYTHON" ] && "$VENV_PYTHON" -c "import sys" >/dev/null 2>&1
}

if ! venv_python_works; then
    echo ""
    if [ -f "$VENV_PYTHON" ]; then
        echo "  [!] The bundled Python cannot run on this Mac — rebuilding..."
    else
        echo "  [!] First use — installing dependencies..."
    fi
    echo ""
    if ! bash "$SCRIPT_DIR/setup.sh"; then
        echo ""
        echo "  [X] Setup failed. Check errors above."
        read -p "  Press Enter to exit..."
        exit 1
    fi
fi

if ! venv_python_works; then
    echo ""
    echo "  [X] No usable Python in hermes/.venv."
    if [ -f "$VENV_PYTHON" ]; then
        echo "      The file is there but will not start:"
        "$VENV_PYTHON" -c "import sys" 2>&1 | sed "s/^/      /"
    fi
    echo "      Run setup.sh by hand and read what it says."
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

# ============================================================================
# Fix stale paths in portable venv (first run on new machine)
# ============================================================================

bash "$SCRIPT_DIR/scripts/fix-portable-paths.sh" "$VENV_DIR" "$RUNTIME_DIR" "$HERMES_DIR/hermes-agent" "$RUNTIME_DIR/uv/uv"

# ============================================================================
# Set environment variables
# ============================================================================

export HERMES_HOME="$DATA_DIR"
# One gateway per OS user since hermes-agent 0.21.4; keep that rendezvous on
# the stick, not in ~/.local/state (see Windows-Start.bat for why).
export HERMES_GATEWAY_LOCK_DIR="$DATA_DIR/gateway-locks"
export HERMES_CONFIG="$DATA_DIR/config.yaml"
export HERMES_MEMORY_DIR="$DATA_DIR/memory"
export HERMES_SKILLS_DIR="$DATA_DIR/skills"
export HERMES_SESSIONS_DIR="$DATA_DIR/sessions"

export PATH="$VENV_DIR/bin:$NODE_DIR/bin:$PATH"
export PYTHONPATH="$HERMES_DIR/hermes-agent:${PYTHONPATH:-}"

# Force UTF-8
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# China PyPI mirror
export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"

# Hermes Web UI settings
export AUTH_DISABLED=1
export PORT=8648
# Loopback only. The Web UI defaults to BIND_HOST || "0.0.0.0", and its
# login page prints the default credentials to every unauthenticated
# visitor. On a shared network that hands an agent with shell access on
# this machine to anyone who can reach port 8648.
export BIND_HOST=127.0.0.1
export HERMES_WEB_UI_HOME="$DATA_DIR/webui"
export HERMES_BIN="$VENV_DIR/bin/hermes"
export HERMES_AGENT_BRIDGE_PYTHON="$VENV_PYTHON"
export HERMES_AGENT_ROOT="$HERMES_DIR/hermes-agent"
# Let the Web UI stop the gateway during its own shutdown; otherwise
# closing the terminal leaves it running and holding the port.
export HERMES_WEB_UI_STOP_GATEWAYS_ON_SHUTDOWN=1
# Paired phone App: no reply text in push notifications (Windows-Start.bat).
export STUDIO_PUSH_CONTENT_PREVIEW=0

# Gateway API Server settings
export API_SERVER_ENABLED=true
export API_SERVER_PORT=8642
export API_SERVER_CORS_ORIGINS="http://localhost:8648,http://127.0.0.1:8648"
export GATEWAY_ALLOW_ALL_USERS=true

# Load data/.env (API keys, may override above defaults)
if [ -f "$DATA_DIR/.env" ]; then
    set -a
    source "$DATA_DIR/.env"
    set +a
fi

# Remove quarantine flags (macOS Gatekeeper)
if [ "$OS" = "Darwin" ]; then
    xattr -rd com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null
fi

# ============================================================================
# Check if config has model set
# ============================================================================

# The zip ships config.yaml.default so that extracting a new build over an
# existing install cannot destroy the user's configuration.
if [ ! -f "$DATA_DIR/config.yaml" ] && [ -f "$DATA_DIR/config.yaml.default" ]; then
    mkdir -p "$DATA_DIR"
    cp "$DATA_DIR/config.yaml.default" "$DATA_DIR/config.yaml"
fi

if [ ! -f "$DATA_DIR/config.yaml" ]; then
    echo ""
    echo "  [i] First launch - creating default config..."
    echo ""
    mkdir -p "$DATA_DIR"
    cat > "$DATA_DIR/config.yaml" << 'CFGEOF'
model:
  provider: ""
  model: ""
database:
  journal_mode: "delete"
platforms:
  api_server:
    enabled: true
    extra:
      port: 8642
      host: 127.0.0.1
skills:
  external_dirs:
    - "../skills-cn"
memory:
  enabled: true
cron:
  enabled: true
CFGEOF
fi

if grep -q 'provider: ""' "$DATA_DIR/config.yaml" 2>/dev/null; then
    echo ""
    echo "  [i] No AI model configured yet."
    echo "      Starting config server..."
    echo ""
    "$VENV_PYTHON" "$SCRIPT_DIR/scripts/config-server.py" &
    CONFIG_SERVER_PID=$!
    sleep 1
    echo "      Opening configuration page..."
    echo ""
    if [ "$OS" = "Darwin" ]; then
        open "$SCRIPT_DIR/Config.html" 2>/dev/null
    else
        xdg-open "$SCRIPT_DIR/Config.html" 2>/dev/null
    fi
    echo "  Complete the configuration in browser, then run this script again."
    echo ""
    read -p "  Press Enter to exit..."
    kill $CONFIG_SERVER_PID 2>/dev/null
    exit 0
fi

# ============================================================================
# Install hermes-web-ui if missing
# ============================================================================

if [ ! -f "$WEBUI_SERVER" ]; then
    echo ""
    echo "  [i] Installing Hermes Web UI..."
    echo ""
    # Pinned to the version this package was built against, with npm's own
    # errors left on screen and its cache kept on the stick. All three were
    # wrong here: unpinned, 2>/dev/null, and a cache under the user's home.
    WEBUI_SPEC="hermes-web-ui"
    if [ -f "$SCRIPT_DIR/versions.env" ]; then
        _v=$(grep -E "^HERMES_WEB_UI_VERSION=" "$SCRIPT_DIR/versions.env" | cut -d= -f2)
        [ -n "$_v" ] && WEBUI_SPEC="hermes-web-ui@${_v}"
    fi
    npm_config_cache="$RUNTIME_DIR/.npm-cache"         npm install -g "$WEBUI_SPEC" --prefix "$NODE_DIR"
    rm -rf "$RUNTIME_DIR/.npm-cache"
    if [ ! -f "$WEBUI_SERVER" ]; then
        echo "  [X] Web UI installation failed (npm's own output is above)."
        echo ""
        read -p "  Press Enter to exit..."
        exit 1
    fi
    echo "  [OK] Web UI installed."
    echo ""
fi

# ============================================================================
# Launch
# ============================================================================

echo ""
echo "  ============================================"
# Written into the package by the release workflow; absent in a clone.
UH_VERSION=""
[ -f "$SCRIPT_DIR/VERSION" ] && UH_VERSION=$(head -n 1 "$SCRIPT_DIR/VERSION" | tr -d "\r")
if [ -n "$UH_VERSION" ]; then
    echo "    U-Hermes $UH_VERSION - AI 智能体"
else
    echo "    U-Hermes - AI 智能体"
fi
echo "  ============================================"
echo ""

# If arguments passed, run hermes CLI directly
if [ $# -gt 0 ]; then
    "$VENV_PYTHON" -m hermes_cli.main "$@"
    exit $?
fi

# --- Pre-launch: Sync config to ~/.hermes/ for GatewayManager ---
# The Web UI's GatewayManager overrides HERMES_HOME to ~/.hermes
# so the gateway reads ~/.hermes/config.yaml instead of data/config.yaml.
# We must copy model+provider config AND set port=8642 before launching.
USER_HERMES_DIR="$HOME/.hermes"
mkdir -p "$USER_HERMES_DIR"
# Copy full data/config.yaml (model, custom_providers, skills, etc.)
cp -f "$DATA_DIR/config.yaml" "$USER_HERMES_DIR/config.yaml" 2>/dev/null
# Append platforms section (GatewayManager reads port from here)
cat >> "$USER_HERMES_DIR/config.yaml" << 'HCEOF'

platforms:
  api_server:
    extra:
      port: 8642
      host: 127.0.0.1
    enabled: true
    key: ''
    cors_origins: '*'
HCEOF
# Copy .env (API keys) if present
[ -f "$DATA_DIR/.env" ] && cp -f "$DATA_DIR/.env" "$USER_HERMES_DIR/.env" 2>/dev/null

# --- Step 1: Kill leftover gateway processes ---
lsof -ti:8642 2>/dev/null | xargs kill -9 2>/dev/null

# Gateway is started automatically by the Web UI's GatewayManager.
# Do NOT start it here — dual gateways cause port conflicts.

echo "  [1/2] Starting Web UI (includes AI engine)..."

# Read auth token
WEBUI_TOKEN_FILE="$NODE_DIR/lib/node_modules/hermes-web-ui/dist/server/data/.token"
AUTH_TOKEN=""
if [ -f "$WEBUI_TOKEN_FILE" ]; then
    AUTH_TOKEN="$(cat "$WEBUI_TOKEN_FILE" | tr -d '[:space:]')"
fi

echo "  [2/2] Opening browser..."
echo ""
echo "  -----------------------------------------------"
echo "    Browser: http://127.0.0.1:8648"
echo "    Press Ctrl+C to stop"
echo "  -----------------------------------------------"
echo ""

# Auto-open browser after 3s
if [ -n "$AUTH_TOKEN" ]; then
    OPEN_URL="http://127.0.0.1:8648/?token=$AUTH_TOKEN"
else
    OPEN_URL="http://127.0.0.1:8648"
fi
(sleep 3 && {
    if [ "$OS" = "Darwin" ]; then
        open "$OPEN_URL" 2>/dev/null
    else
        xdg-open "$OPEN_URL" 2>/dev/null
    fi
}) &

# Cleanup function
cleanup() {
    echo ""
    echo "  Stopping AI engine..."
    lsof -ti:8642 2>/dev/null | xargs kill -9 2>/dev/null
    echo "  Hermes stopped."
}
trap cleanup EXIT

# Run Web UI in foreground
"$NODE_EXE" "$WEBUI_SERVER"

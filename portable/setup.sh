#!/bin/bash
# ============================================================================
# U-Hermes Portable Setup Script (Mac / Linux)
# Downloads: Python 3.11 + uv + Hermes Agent + dependencies
# All downloads use China mirrors where possible.
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$SCRIPT_DIR/runtime"
HERMES_DIR="$SCRIPT_DIR/hermes"
DATA_DIR="$SCRIPT_DIR/data"

# Versions (single source of truth, shared with .github/workflows/release.yml)
# shellcheck source=versions.env
. "$SCRIPT_DIR/versions.env"

# Mirrors (overridable via environment variables for CI)
PYPI_MIRROR="${PYPI_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
NODE_MIRROR="${NODE_MIRROR:-https://npmmirror.com/mirrors/node}"
UV_MIRROR="${UV_MIRROR:-https://github.com/astral-sh/uv/releases/download}"

# Detect platform
OS="$(uname -s)"
ARCH="$(uname -m)"

# ============================================================================
# Helpers
# ============================================================================

info()  { echo "  -> $1"; }
ok()    { echo "  ✓ $1"; }
warn()  { echo "  ⚠ $1"; }
err()   { echo "  ✗ $1"; }

ensure_dir() { mkdir -p "$1"; }

download() {
    local url="$1" dest="$2" desc="$3"
    [ -n "$desc" ] && info "Downloading $desc..."
    if command -v curl &>/dev/null; then
        curl -fsSL "$url" -o "$dest"
    elif command -v wget &>/dev/null; then
        wget -q "$url" -O "$dest"
    else
        err "Neither curl nor wget found"; exit 1
    fi
}

# ============================================================================
# Banner
# ============================================================================

echo ""
echo "  ============================================"
echo "    U-Hermes Portable Setup"
echo "    USB AI Agent - Powered by Hermes Agent"
echo "  ============================================"
echo ""

# ============================================================================
# Step 1: uv (manages Python + venv + packages)
# ============================================================================

UV_EXE=""  # global result

install_uv() {
    local uv_dir="$RUNTIME_DIR/uv"
    local uv_exe="$uv_dir/uv"
    UV_EXE="$uv_exe"

    if [ -f "$uv_exe" ] && [ -z "$FORCE" ]; then
        ok "uv already installed."
        return
    fi

    info "[1/4] Installing uv $UV_VERSION..."
    ensure_dir "$uv_dir"

    local platform_suffix
    if [ "$OS" = "Darwin" ]; then
        if [ "$ARCH" = "arm64" ]; then
            platform_suffix="aarch64-apple-darwin"
        else
            platform_suffix="x86_64-apple-darwin"
        fi
    else
        if [ "$ARCH" = "aarch64" ]; then
            platform_suffix="aarch64-unknown-linux-gnu"
        else
            platform_suffix="x86_64-unknown-linux-gnu"
        fi
    fi

    local tarball="uv-${platform_suffix}.tar.gz"
    local url="$UV_MIRROR/$UV_VERSION/$tarball"
    local tar_path="$RUNTIME_DIR/uv.tar.gz"

    download "$url" "$tar_path" "uv $UV_VERSION"
    # Extract to temp dir first, then move binaries to uv_dir
    local tmp_dir="$RUNTIME_DIR/uv-tmp"
    rm -rf "$tmp_dir"
    mkdir -p "$tmp_dir"
    tar -xzf "$tar_path" -C "$tmp_dir"
    rm -f "$tar_path"

    # Debug: show what was extracted
    echo "  -> uv archive contents:"
    find "$tmp_dir" -type f | head -20

    # Find the uv binary wherever it ended up (may be named uv or uv-*)
    local found=$(find "$tmp_dir" -type f -name "uv" | head -1)
    if [ -z "$found" ]; then
        # Some releases put it as the only executable
        found=$(find "$tmp_dir" -type f -perm +111 | grep -v uvx | head -1)
    fi
    if [ -n "$found" ]; then
        cp "$found" "$uv_exe"
        chmod +x "$uv_exe"
        # Also copy uvx if present
        local found_uvx=$(find "$tmp_dir" -type f -name "uvx" | head -1)
        [ -n "$found_uvx" ] && cp "$found_uvx" "$uv_dir/uvx" && chmod +x "$uv_dir/uvx"
    else
        err "Could not find uv binary in archive"
        find "$tmp_dir" -type f
    fi
    rm -rf "$tmp_dir"

    ok "uv $UV_VERSION installed."
}

# ============================================================================
# Step 2: Node.js (for browser tools)
# ============================================================================

install_node() {
    local node_dir
    if [ "$OS" = "Darwin" ]; then
        if [ "$ARCH" = "arm64" ]; then
            node_dir="$RUNTIME_DIR/node-mac-arm64"
        else
            node_dir="$RUNTIME_DIR/node-mac-x64"
        fi
    else
        node_dir="$RUNTIME_DIR/node-linux-x64"
    fi

    if [ -f "$node_dir/bin/node" ] && [ -z "$FORCE" ]; then
        ok "Node.js already installed."
        return
    fi

    info "[2/4] Installing Node.js $NODE_VERSION..."
    ensure_dir "$node_dir"

    local platform_name
    if [ "$OS" = "Darwin" ]; then
        if [ "$ARCH" = "arm64" ]; then
            platform_name="darwin-arm64"
        else
            platform_name="darwin-x64"
        fi
    else
        platform_name="linux-x64"
    fi

    local tarball="node-$NODE_VERSION-$platform_name.tar.xz"
    local url="$NODE_MIRROR/$NODE_VERSION/$tarball"
    local tar_path="$RUNTIME_DIR/node.tar.xz"

    download "$url" "$tar_path" "Node.js $NODE_VERSION"
    tar -xJf "$tar_path" -C "$node_dir" --strip-components=1
    rm -f "$tar_path"

    ok "Node.js $NODE_VERSION installed."
}

# ============================================================================
# Step 3: Hermes Agent source
# ============================================================================

AGENT_DIR=""  # global result

install_hermes_source() {
    local agent_dir="$HERMES_DIR/hermes-agent"
    AGENT_DIR="$agent_dir"

    if [ -f "$agent_dir/pyproject.toml" ] && [ -z "$FORCE" ]; then
        ok "Hermes Agent source already present."
        return
    fi

    info "[3/4] Downloading Hermes Agent..."
    ensure_dir "$HERMES_DIR"

    if command -v git &>/dev/null; then
        rm -rf "$agent_dir"
        git clone --depth 1 https://github.com/NousResearch/hermes-agent.git "$agent_dir" 2>/dev/null
    else
        local zip_url="https://github.com/NousResearch/hermes-agent/archive/refs/heads/main.zip"
        local zip_path="$HERMES_DIR/hermes-agent.zip"
        download "$zip_url" "$zip_path" "Hermes Agent (zip)"
        unzip -qo "$zip_path" -d "$HERMES_DIR"
        rm -rf "$agent_dir"
        mv "$HERMES_DIR"/hermes-agent-* "$agent_dir"
        rm -f "$zip_path"
    fi

    ok "Hermes Agent source ready."
}

# ============================================================================
# Step 4: Python venv + dependencies
# ============================================================================

install_dependencies() {
    local uv_exe="$1"
    local agent_dir="$2"
    local venv_dir="$HERMES_DIR/.venv"
    local venv_python="$venv_dir/bin/python"

    if [ -f "$venv_python" ] && [ -z "$FORCE" ]; then
        ok "Virtual environment already exists."
        return
    fi

    info "[4/4] Installing dependencies (may take a few minutes)..."

    # Create venv with Python 3.11 (uv will download if needed)
    info "Creating virtual environment..."
    "$uv_exe" venv "$venv_dir" --python 3.11 2>/dev/null || "$uv_exe" venv "$venv_dir"

    # Install with China mirror
    info "Installing Hermes Agent packages (China mirror)..."
    export UV_INDEX_URL="$PYPI_MIRROR"
    # Non-editable: an editable install bakes an absolute path into the venv,
    # which breaks as soon as the drive letter changes.
    # HERMES_NIX_BUILD=1 is upstream's escape hatch for packaging contexts --
    # since Jul 2026 their setup.py refuses to build a wheel without it.
    # Extras: `cli` was removed upstream; pty/cron are no-op aliases now.
    export HERMES_NIX_BUILD=1
    "$uv_exe" pip install "$agent_dir[pty,mcp,cron,messaging]" --python "$venv_python" 2>&1 | tail -5

    # Verify
    if "$venv_python" -c "import agent; print('ok')" 2>/dev/null | grep -q "ok"; then
        ok "All dependencies installed successfully."
    else
        warn "Dependencies installed but import check failed. May still work."
    fi

    unset UV_INDEX_URL
}

# ============================================================================
# Step 5: Initialize data directory
# ============================================================================

initialize_data() {
    ensure_dir "$DATA_DIR"
    ensure_dir "$DATA_DIR/memory"
    ensure_dir "$DATA_DIR/skills"
    ensure_dir "$DATA_DIR/sessions"
    ensure_dir "$DATA_DIR/cron"

    local config_file="$DATA_DIR/config.yaml"
    if [ ! -f "$config_file" ]; then
        cat > "$config_file" << 'EOF'
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

skills:
  extra_dirs:
    - "../skills-cn"

memory:
  enabled: true

cron:
  enabled: true
EOF
    fi

    ok "Data directory initialized."
}

# ============================================================================
# Main
# ============================================================================

START_TIME=$(date +%s)

ensure_dir "$RUNTIME_DIR"

install_uv
install_node
install_hermes_source
install_dependencies "$UV_EXE" "$AGENT_DIR"

# Install Hermes Web UI (npm package)
WEBUI_SERVER="$NODE_DIR/lib/node_modules/hermes-web-ui/dist/server/index.js"
if [ ! -f "$WEBUI_SERVER" ]; then
    info "Installing Hermes Web UI..."
    NPM_CMD="$NODE_DIR/bin/npm"
    if [ -x "$NPM_CMD" ]; then
        "$NPM_CMD" install -g hermes-web-ui --prefix "$NODE_DIR" 2>/dev/null
        if [ -f "$WEBUI_SERVER" ]; then
            ok "Hermes Web UI installed."
        else
            warn "Hermes Web UI install failed (will retry on first launch)."
        fi
    else
        warn "npm not found, skipping Web UI install."
    fi
else
    ok "Hermes Web UI already installed."
fi

initialize_data

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
ok "Setup complete! (${ELAPSED}s)"
echo ""
echo "  Next steps:"
echo "    1. Open Config.html to configure your AI model"
echo "    2. Run: bash Mac-Start.command"
echo ""

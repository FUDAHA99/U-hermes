#!/bin/bash
# fix-portable-paths.sh — Fix stale paths in portable venv after moving to a new machine
# The CI build creates the venv on a different path than where the user runs from.
# This script fixes: pyvenv.cfg + shebang lines in bin/ scripts.

VENV_DIR="$1"
RUNTIME_DIR="$2"
AGENT_DIR="$3"
UV_EXE="$4"

[ -z "$VENV_DIR" ] && exit 0

BIN_DIR="$VENV_DIR/bin"
CORRECT_PYTHON="$BIN_DIR/python"
CFG_FILE="$VENV_DIR/pyvenv.cfg"

[ -f "$CORRECT_PYTHON" ] || exit 0
[ -f "$CFG_FILE" ] || exit 0

# Detect correct Python home
OS="$(uname -s)"
ARCH="$(uname -m)"
if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then
        PYTHON_HOME="$RUNTIME_DIR/python-mac-arm64"
    else
        PYTHON_HOME="$RUNTIME_DIR/python-mac-x64"
    fi
else
    PYTHON_HOME="$RUNTIME_DIR/python-linux-x64"
fi

# Check if pyvenv.cfg home is already correct
CURRENT_HOME=$(grep '^home = ' "$CFG_FILE" | sed 's/^home = //')
if [ "$CURRENT_HOME" = "$PYTHON_HOME" ]; then
    # Already correct — no fix needed
    exit 0
fi

echo "  [i] Fixing portable paths (first run on this machine)..."

# Fix pyvenv.cfg
sed -i.bak "s|^home = .*|home = $PYTHON_HOME|" "$CFG_FILE"
rm -f "${CFG_FILE}.bak"
echo "  [OK] Fixed venv config."

# Fix shebangs in bin/ scripts
CORRECT_SHEBANG="#!$CORRECT_PYTHON"
FIXED=0
for script in "$BIN_DIR"/*; do
    [ -f "$script" ] || continue
    [ -x "$script" ] || continue
    case "$(basename "$script")" in
        python*|activate*) continue ;;
    esac
    head -1 "$script" 2>/dev/null | grep -q '^#!.*python' || continue
    OLD_SHEBANG=$(head -1 "$script")
    if [ "$OLD_SHEBANG" = "$CORRECT_SHEBANG" ]; then continue; fi
    sed -i.bak "1s|^#!.*|$CORRECT_SHEBANG|" "$script"
    rm -f "${script}.bak"
    FIXED=$((FIXED + 1))
done

if [ "$FIXED" -gt 0 ]; then
    echo "  [OK] Fixed shebangs in $FIXED script(s)."
fi

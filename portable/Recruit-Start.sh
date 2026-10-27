#!/bin/bash
set -e
RECRUIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
RECRUIT_PYTHON="${RECRUIT_PYTHON:-$RECRUIT_ROOT/hermes/.venv/bin/python}"
if [ ! -x "$RECRUIT_PYTHON" ]; then
    echo "请先安装 Python 3.13 与锁定版 Hermes 依赖，参见 recruit/RECRUIT.md。"
    exit 1
fi
export PYTHONNOUSERSITE=1
"$RECRUIT_PYTHON" "$RECRUIT_ROOT/scripts/remove-old-host-copy.py" "$RECRUIT_ROOT/data"
"$RECRUIT_PYTHON" "$RECRUIT_ROOT/scripts/prune-stale-files.py" "$RECRUIT_ROOT"
if ! "$RECRUIT_PYTHON" -c "import pypdf; assert pypdf.__version__ == '6.21.2'"; then
    echo "请在该隔离环境安装 recruit/requirements.txt 后再启动。"
    exit 1
fi
exec "$RECRUIT_PYTHON" "$RECRUIT_ROOT/scripts/recruit-server.py" "$@"

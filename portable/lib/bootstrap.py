"""
U-Hermes Bootstrap Module
Handles portable path resolution and environment setup.
Called before hermes_cli.main to configure HERMES_HOME etc.
"""

import os
import sys
from pathlib import Path


def get_portable_root() -> Path:
    """Find the portable root directory (where Windows-Start.bat lives)."""
    # Try environment variable first
    if os.environ.get("UHERMES_ROOT"):
        return Path(os.environ["UHERMES_ROOT"])

    # Walk up from this file: lib/bootstrap.py -> portable/
    here = Path(__file__).resolve().parent
    root = here.parent

    # Verify by checking for known files
    if (root / "Windows-Start.bat").exists() or (root / "Mac-Start.command").exists():
        return root

    # Fallback: current working directory
    return Path.cwd()


def get_data_dir() -> Path:
    return get_portable_root() / "data"


def get_hermes_dir() -> Path:
    return get_portable_root() / "hermes"


def get_runtime_dir() -> Path:
    return get_portable_root() / "runtime"


def setup_environment():
    """Set up environment variables for portable Hermes operation."""
    root = get_portable_root()
    data = get_data_dir()

    # Core Hermes paths
    os.environ.setdefault("HERMES_HOME", str(data))
    os.environ.setdefault("HERMES_CONFIG", str(data / "config.yaml"))
    os.environ.setdefault("HERMES_MEMORY_DIR", str(data / "memory"))
    os.environ.setdefault("HERMES_SKILLS_DIR", str(data / "skills"))
    os.environ.setdefault("HERMES_SESSIONS_DIR", str(data / "sessions"))

    # Skills search path - include Chinese skills
    skills_cn = root / "skills-cn"
    if skills_cn.exists():
        existing = os.environ.get("HERMES_EXTRA_SKILLS_DIRS", "")
        if str(skills_cn) not in existing:
            dirs = [d for d in existing.split(os.pathsep) if d]
            dirs.append(str(skills_cn))
            os.environ["HERMES_EXTRA_SKILLS_DIRS"] = os.pathsep.join(dirs)

    # China mirrors for lazy-installed packages
    os.environ.setdefault("UV_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")
    os.environ.setdefault("PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")

    # Node.js path for browser tools
    if sys.platform == "win32":
        node_dir = root / "runtime" / "node-win-x64"
    elif sys.platform == "darwin":
        import platform as plat
        arch = plat.machine()
        if arch == "arm64":
            node_dir = root / "runtime" / "node-mac-arm64"
        else:
            node_dir = root / "runtime" / "node-mac-x64"
    else:
        node_dir = root / "runtime" / "node-linux-x64"

    if node_dir.exists():
        node_bin = node_dir / "bin" if sys.platform != "win32" else node_dir
        path = os.environ.get("PATH", "")
        if str(node_bin) not in path:
            os.environ["PATH"] = str(node_bin) + os.pathsep + path


def find_free_port(start=18789, end=18799) -> int:
    """Find a free port in the given range."""
    import socket
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


if __name__ == "__main__":
    setup_environment()
    print(f"HERMES_HOME={os.environ.get('HERMES_HOME')}")
    print(f"HERMES_CONFIG={os.environ.get('HERMES_CONFIG')}")
    print(f"Root: {get_portable_root()}")

"""
China mirror configuration for U-Hermes.
Ensures all package downloads use domestic mirrors for speed in mainland China.
"""

import os
import sys
from pathlib import Path


# PyPI mirrors (in priority order)
PYPI_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",      # Tsinghua
    "https://mirrors.aliyun.com/pypi/simple",         # Alibaba
    "https://pypi.mirrors.ustc.edu.cn/simple",        # USTC
]

# npm mirrors
NPM_MIRROR = "https://registry.npmmirror.com"
NODE_MIRROR = "https://npmmirror.com/mirrors/node"

# GitHub proxy (for releases / clone)
GITHUB_PROXIES = [
    "https://ghfast.top",
    "https://mirror.ghproxy.com",
]


def configure_pip_mirror():
    """Set pip/uv to use China PyPI mirror."""
    mirror = PYPI_MIRRORS[0]
    os.environ.setdefault("PIP_INDEX_URL", mirror)
    os.environ.setdefault("PIP_TRUSTED_HOST", "pypi.tuna.tsinghua.edu.cn")
    os.environ.setdefault("UV_INDEX_URL", mirror)


def configure_npm_mirror():
    """Set npm to use China mirror."""
    os.environ.setdefault("NPM_CONFIG_REGISTRY", NPM_MIRROR)


def configure_all():
    """Apply all China mirror configurations."""
    configure_pip_mirror()
    configure_npm_mirror()


def write_pip_conf(target_dir: Path):
    """Write pip.conf for the virtual environment."""
    pip_dir = target_dir / "pip"
    pip_dir.mkdir(parents=True, exist_ok=True)

    conf_file = pip_dir / ("pip.ini" if sys.platform == "win32" else "pip.conf")
    if not conf_file.exists():
        conf_file.write_text(
            f"[global]\n"
            f"index-url = {PYPI_MIRRORS[0]}\n"
            f"trusted-host = pypi.tuna.tsinghua.edu.cn\n",
            encoding="utf-8"
        )


if __name__ == "__main__":
    configure_all()
    print(f"PIP_INDEX_URL={os.environ.get('PIP_INDEX_URL')}")
    print(f"UV_INDEX_URL={os.environ.get('UV_INDEX_URL')}")
    print(f"NPM_CONFIG_REGISTRY={os.environ.get('NPM_CONFIG_REGISTRY')}")

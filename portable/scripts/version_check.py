# -*- coding: utf-8 -*-
"""Is this install the one versions.env pins? Answer for every component.

There was an engine-only version of this check, and it was right to exist:
setup.ps1 cloned hermes-agent from main while release.yml cloned the pinned
tag, so a developer's engine sat four months behind the shipped one and every
conclusion drawn by reading it described a product nobody receives.

Checking one component turned out to be checking a fifth of the problem. On
the machine this was written on, all five had drifted -- engine, web UI, Node,
Python and uv -- and nothing anywhere said so. Three separate bugs in this
project were caused by verifying against a locally installed version that
nobody ships:

  * a day of engine conclusions drawn from 0.14.0 while 0.21.3 ships;
  * the Web UI account claim, which was a no-op on the pinned 0.7.22 because
    the check it used only made sense on the 0.6.5 installed locally;
  * the test mock for that feature, written to match 0.6.5, which is why the
    suite stayed green while the feature did nothing.

Warns; never blocks. An end user unpacking a release has matching versions by
construction, and a component this cannot read is reported as unknown rather
than guessed at.

Usage:  version_check.py [--quiet]
Exit 0  everything matched, or nothing could be determined
Exit 3  at least one component differs from the pin
"""
import io
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(HERE)

UNKNOWN = None


def read_pins(portable=PORTABLE):
    pins = {}
    try:
        with io.open(os.path.join(portable, "versions.env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                pins[name.strip()] = value.strip()
    except OSError:
        pass
    return pins


def _run(args, timeout=15):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    return (out.stdout or "").strip()


def _same(pinned, installed):
    """Compare leniently on the v prefix, strictly on everything else.

    `NODE_VERSION=v24.21.0` and `node --version` -> `v24.21.0`; the engine pin
    is a git tag and `git describe` adds `-N-gSHA` when the checkout is not
    exactly on it, which is itself a mismatch worth reporting.
    """
    if not pinned or installed is UNKNOWN:
        return UNKNOWN
    return pinned.lstrip("vV") == str(installed).lstrip("vV")


def engine_version(portable=PORTABLE):
    """The tag the bundled engine checkout is on, if git can say."""
    agent = os.path.join(portable, "hermes", "hermes-agent")
    if not os.path.isdir(os.path.join(agent, ".git")):
        return UNKNOWN  # a release zip may not carry .git
    tag = _run(["git", "-C", agent, "describe", "--tags", "--always"])
    if not tag:
        return UNKNOWN
    # `--always` falls back to a bare SHA when the clone carries no tags at
    # all, which is what a shallow CI clone can look like. That is "cannot
    # tell", not "wrong version" -- reporting it as drift would fail the
    # build for the one case where the pin is guaranteed correct.
    bare_sha = (7 <= len(tag) <= 40
                and all(c in "0123456789abcdef" for c in tag.lower()))
    return UNKNOWN if bare_sha else tag


def web_ui_version(portable=PORTABLE):
    for node_dir in ("node-win-x64", "node-mac-arm64", "node-mac-x64",
                     "node-linux-x64"):
        for depth in (("node_modules",), ("lib", "node_modules")):
            pkg = os.path.join(portable, "runtime", node_dir, *depth,
                               "hermes-web-ui", "package.json")
            if os.path.exists(pkg):
                try:
                    with io.open(pkg, encoding="utf-8") as f:
                        return json.load(f).get("version") or UNKNOWN
                except (OSError, ValueError):
                    return UNKNOWN
    return UNKNOWN


def node_version(portable=PORTABLE):
    for node_dir, exe in (("node-win-x64", "node.exe"),
                          ("node-mac-arm64", os.path.join("bin", "node")),
                          ("node-mac-x64", os.path.join("bin", "node")),
                          ("node-linux-x64", os.path.join("bin", "node"))):
        path = os.path.join(portable, "runtime", node_dir, exe)
        if os.path.exists(path):
            return _run([path, "--version"]) or UNKNOWN
    return UNKNOWN


def python_version(portable=PORTABLE):
    """The venv's interpreter -- but only where the pin governs it.

    PYTHON_EMBED_VERSION is, by its own comment in versions.env, the Windows
    *embeddable* interpreter. setup.sh downloads no Python at all: it runs
    `uv venv --python 3.11` and takes whatever the machine or uv provides.
    Comparing that against this pin reports drift the pin never claimed to
    govern, and a check that fails for a reason nobody intends to fix is a
    check that gets deleted. Where there is no bundled interpreter, say so
    and compare nothing.

    (The mac build genuinely has no pinned Python. That is tracked with the
    rest of the macOS packaging problem, which already blocks its release.)
    """
    bundled = any(
        os.path.isdir(os.path.join(portable, "runtime", d))
        for d in ("python-win-x64", "python-mac-arm64", "python-mac-x64",
                  "python-linux-x64"))
    if not bundled:
        return UNKNOWN
    for exe in (os.path.join("Scripts", "python.exe"),
                os.path.join("bin", "python")):
        path = os.path.join(portable, "hermes", ".venv", exe)
        if os.path.exists(path):
            out = _run([path, "-c",
                        "import sys;print('.'.join(map(str,sys.version_info[:3])))"])
            return out or UNKNOWN
    return UNKNOWN


def uv_version(portable=PORTABLE):
    for exe in ("uv.exe", "uv"):
        path = os.path.join(portable, "runtime", "uv", exe)
        if os.path.exists(path):
            out = _run([path, "--version"])
            # "uv 0.12.16 (abcdef 2026-01-01)" -> 0.12.16
            parts = out.split()
            return parts[1] if len(parts) > 1 else (out or UNKNOWN)
    return UNKNOWN


# (label shown to a human, versions.env key, how to read what is installed)
COMPONENTS = (
    ("AI 引擎", "HERMES_AGENT_REF", engine_version),
    ("网页界面", "HERMES_WEB_UI_VERSION", web_ui_version),
    ("Node.js", "NODE_VERSION", node_version),
    ("Python", "PYTHON_EMBED_VERSION", python_version),
    ("uv", "UV_VERSION", uv_version),
)


def survey(portable=PORTABLE):
    """[(label, key, pinned, installed, verdict)] -- verdict may be None."""
    pins = read_pins(portable)
    rows = []
    for label, key, reader in COMPONENTS:
        pinned = pins.get(key, "")
        installed = reader(portable)
        rows.append((label, key, pinned, installed, _same(pinned, installed)))
    return rows


def drifted(rows):
    return [r for r in rows if r[4] is False]


def report(rows, say=None):
    """Print the mismatches. Returns the number of them."""
    emit = say or (lambda *lines: [print("   " + l) for l in lines])
    bad = drifted(rows)
    if not bad:
        return 0
    emit("[!] 这份安装和 versions.env 钉住的版本对不上：")
    for label, _key, pinned, installed, _ in bad:
        emit("    %s：装的是 %s，钉的是 %s" % (label, installed, pinned))
    emit("    在这上面测出来的结论不代表用户拿到的版本。",
         "    网页界面：Windows-Menu.bat 选 [9] 更新。",
         "    其余组件：portable\\setup.ps1 -Force（会重新下载）。")
    return len(bad)


def main(argv):
    rows = survey()
    if "--quiet" not in argv:
        print()
        print("  %-10s %-18s %-18s" % ("组件", "钉住", "已安装"))
        print("  " + "-" * 50)
        for label, _key, pinned, installed, ok in rows:
            mark = "OK" if ok else ("?" if ok is UNKNOWN else "不一致")
            print("  %-10s %-18s %-18s %s"
                  % (label, pinned or "?",
                     installed if installed is not UNKNOWN else "读不到", mark))
        print()
    return 3 if drifted(rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

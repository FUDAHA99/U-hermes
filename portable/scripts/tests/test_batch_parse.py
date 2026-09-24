"""Feed every ( ) block of every launcher to the real cmd.exe.

From v0.4.2 to v0.4.5, double-clicking Windows-Start.bat printed
"reads was unexpected at this time." and closed. The cause was a comment:

    if not exist "%DATA_DIR%\\config.yaml" (
        ...
        :: journal_mode IS read by the pinned engine: ...
        :: resolve_journal_mode() reads database.journal_mode before ...

cmd reads a :: line as a label. Inside ( ) the line after a label is parsed
as a command, so the "()" on the second line closed the block and "reads"
was a stray token. cmd parses a whole block before deciding whether to run
it, so this fired on every start, not only on first run. Nothing caught it:
every other check ran the engine and the Web UI directly, never the file a
user actually double-clicks.

cmd has no parse-only switch, and it only parses a block when execution
reaches it. So each top-level block is cut out and wrapped in `if 1==0 ( )`:
cmd parses all of it and runs none of it.

Run:  python portable/scripts/tests/test_batch_parse.py
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(os.path.dirname(HERE))
MARK = "BLOCK-PARSED-OK"

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def launchers():
    """The batch files this project writes: the package root and scripts/."""
    found = glob.glob(os.path.join(PORTABLE, "*.bat"))
    for ext in ("*.bat", "*.cmd"):
        found += glob.glob(os.path.join(PORTABLE, "scripts", ext))
    return sorted(found)


def is_comment(stripped):
    low = stripped.lower()
    return stripped.startswith("::") or low == "rem" or low.startswith("rem ")


def top_level_blocks(lines):
    """(first, last) line indexes of each top-level ( ) statement.

    Follows the layout these launchers use throughout: a block opens on a
    line ending in "(" and closes on a line starting with ")", and
    ") else (" continues the same statement.
    """
    depth, start = 0, None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or is_comment(s):
            continue
        if s.startswith(")"):
            depth -= 1
            if s.endswith("("):
                depth += 1
            elif depth == 0 and start is not None:
                yield start, i
                start = None
        elif s.endswith("("):
            if depth == 0:
                start = i
            depth += 1


def labels_inside_blocks(lines):
    """Line numbers of :: comments that sit inside a ( ) block."""
    bad = []
    for first, last in top_level_blocks(lines):
        for i in range(first + 1, last):
            if lines[i].strip().startswith("::"):
                bad.append(i + 1)
    return bad


def cmd_parses(block_lines, delayed):
    """True if cmd.exe accepts these lines as the body of a block.

    %VAR% is expanded while the block is read, before it is parsed. In the
    launcher every one of them has a value by then; left unset here,
    `if %_CFGTRY% gtr 3 (` would read as `if  gtr 3 (` and fail for a reason
    the real file never meets. Each one gets a plain placeholder instead.
    """
    head = ["@echo off", "chcp 65001 >nul 2>&1", "setlocal"]
    if delayed:
        head.append("setlocal enabledelayedexpansion")
    names = sorted(set(re.findall(r"(?<!%)%([A-Za-z_][A-Za-z0-9_]*)%",
                                  "\n".join(block_lines))))
    head += ['set "%s=x"' % n for n in names]
    body = head + ["if 1==0 ("] + block_lines + [")", "echo " + MARK]
    fd, path = tempfile.mkstemp(suffix=".bat")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(("\r\n".join(body) + "\r\n").encode("utf-8"))
        r = subprocess.run(["cmd", "/d", "/c", path], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        return MARK in out and r.returncode == 0, out.strip()
    finally:
        os.remove(path)


def test_the_harness_catches_the_v042_bug():
    """Pin the harness on the exact lines that shipped broken."""
    broken = [
        'if not exist "%DATA_DIR%\\config.yaml" (',
        "    echo.",
        "    :: journal_mode IS read by the pinned engine: hermes_state_wal's",
        "    :: resolve_journal_mode() reads database.journal_mode before any pragma",
        '    mkdir "%DATA_DIR%" 2>nul',
        ")",
    ]
    fixed = [
        'if not exist "%DATA_DIR%\\config.yaml" (',
        "    echo.",
        '    mkdir "%DATA_DIR%" 2>nul',
        ")",
    ]
    check(labels_inside_blocks(broken) == [3, 4],
          "the static rule flags :: lines inside a block")
    check(labels_inside_blocks(fixed) == [], "...and nothing in the fixed layout")
    blocks = list(top_level_blocks(["if a (", "  x", ") else (", "  y", ")", "echo z"]))
    check(blocks == [(0, 4)], ") else ( stays one statement (got %s)" % blocks)
    if os.name != "nt":
        check(True, "skip: cmd.exe checks need Windows")
        return
    ok, out = cmd_parses(broken, delayed=True)
    check(not ok, "cmd.exe rejects the v0.4.2-v0.4.5 lines (%s)" % out[-80:])
    ok, out = cmd_parses(fixed, delayed=True)
    check(ok, "cmd.exe accepts the fixed lines (%s)" % out[-80:])


def test_every_launcher_block_parses():
    files = launchers()
    names = {os.path.basename(f) for f in files}
    check({"Windows-Start.bat", "Windows-Menu.bat"} <= names,
          "found the launchers (%d batch files)" % len(files))
    total = 0
    for path in files:
        name = os.path.relpath(path, PORTABLE)
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
        lines = text.splitlines()
        bad = labels_inside_blocks(lines)
        check(not bad, "%s: no :: comment inside a ( ) block%s" % (
            name, "" if not bad else " (lines %s -- use rem, or move it above)" % bad))
        if os.name != "nt":
            continue
        delayed = "enabledelayedexpansion" in text.lower()
        for first, last in top_level_blocks(lines):
            total += 1
            ok, out = cmd_parses(lines[first:last + 1], delayed)
            if not ok:
                check(False, "%s: cmd.exe rejects the block at lines %d-%d: %s"
                      % (name, first + 1, last + 1, out[-120:]))
    if os.name == "nt":
        check(total > 20, "cmd.exe parsed %d blocks across the launchers" % total)
    else:
        check(True, "skip: cmd.exe checks need Windows")


if __name__ == "__main__":
    for fn in (test_the_harness_catches_the_v042_bug,
               test_every_launcher_block_parses):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("batch parse: all checks passed")

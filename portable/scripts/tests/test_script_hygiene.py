"""Catch the file-level corruption that every syntax checker waves through.

Twice while this project was being worked on, a shell layer ate one level of
backslash escaping and turned an intended escape into something else:

  * `%SCRIPT_DIR%\\runtime\\...` in a .bat became `%SCRIPT_DIR%<CR>untime\\...`.
    A terminal renders a carriage return by returning to the start of the
    line, so `grep` appeared to show `%SCRIPT_DIR%untime` and it read as a
    missing letter. The launcher just quietly ran with a directory missing
    from its PATH.
  * a line continuation in setup.sh became the literal two characters `\\n`.
    Unquoted, bash reads that as the single character `n`, so the command
    word became `n`, npm was never invoked, and `set -e` killed the installer
    before it created the data directory.

Neither is a syntax error. `bash -n`, `py_compile` and a YAML parse all pass,
which is exactly why this file exists.

Note what is NOT checked here: whether a .bat in the working tree is CRLF.
`.gitattributes` decides that at checkout time, so the bytes on one
developer's disk say nothing about the bytes in the release. What is checked
is that `.gitattributes` still covers every extension that needs it.

Run:  python portable/scripts/tests/test_script_hygiene.py
"""
import io
import os
import re
import sys

# CI pipes this suite's stdout, and Python then encodes it with the machine's
# ANSI codepage rather than UTF-8. On GitHub's en-US Windows runner that is
# cp1252, which cannot encode a single Chinese character, so the first label
# containing one killed the whole release job with a UnicodeEncodeError.
# Unreproducible on a Chinese Windows box, where the codepage is GBK.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(PORTABLE)

CR = b"\r"
CRLF = b"\r\n"
BOM = b"\xef\xbb\xbf"
B = chr(92)
BS_ESCAPE = chr(92)

# Skipped by PATH, not by bare directory name. Matching on the name pruned
# portable/lib, portable/cloud, portable/config-server and portable/skills-cn
# -- 18 tracked files that are packaged verbatim -- and would have gone on
# pruning any future directory that happened to share one of those names,
# while the suite kept printing a reassuring count.
SKIP_PATHS = {
    "portable/runtime",      # downloaded Node/Python/uv
    "portable/hermes",       # the engine checkout and its venv
    "portable/data",         # the user's own config, keys and chat history
    "portable/backups",      # old copies of this tree
    "portable/.uv-cache",
}
# ...except this one, which the release job asserts is inside the zip.
SKIP_EXCEPTIONS = {"portable/data/config.yaml.default"}
SKIP_NAMES = {"__pycache__", ".git", "node_modules"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".ico", ".zip", ".db", ".pyc", ".exe", ".svg"}

# What .gitattributes has to keep pinning, and to what.
EOL_RULES = {
    "*.bat": "crlf", "*.cmd": "crlf", "*.ps1": "crlf",
    "*.sh": "lf", "*.command": "lf", "*.env": "lf",
}

# PowerShell 5.1 reads a BOM-less UTF-8 script as the system ANSI codepage,
# which turns every Chinese string in it into mojibake. The quick-start is
# opened in Notepad, which wants the same hint.
MUST_HAVE_BOM = {
    "portable/setup.ps1",
    "portable/scripts/protect-config.ps1",
    "portable/scripts/fix-portable-paths.ps1",
    "portable/scripts/tests/test_protect_config.ps1",
    "portable/0-先看我-使用说明.txt",
}

# Only shell scripts. In a .bat the backslash is the path separator, so
# `%RUNTIME_DIR%\node-win-x64` legitimately contains `\n` -- a survey that
# missed this was itself mangled by the same escaping bug it was looking for.
SHELL_EXT = {".sh", ".command"}

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def shipped_files():
    """Every text file that goes out in the package, plus the site and CI."""
    roots = [PORTABLE, os.path.join(REPO, "docs"), os.path.join(REPO, ".github")]
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            keep = []
            for d in dirnames:
                rel = os.path.relpath(
                    os.path.join(dirpath, d), REPO).replace(os.sep, "/")
                if d in SKIP_NAMES or rel in SKIP_PATHS:
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in filenames:
                if os.path.splitext(name)[1].lower() in SKIP_EXT:
                    continue
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, REPO).replace(os.sep, "/"), full


def unquoted_escapes(line):
    """Backslash-n/r/t occurrences that are NOT inside quotes.

    `tr -d "\r"` is fine -- the escape is data inside a quoted argument and
    tr is the thing that interprets it. An unquoted one is the corruption:
    bash collapses it to the bare letter before any command ever sees it.

    A flat quote toggle is not enough. Bash re-parses the inside of
    `$( ... )` as a fresh context, so the quotes in
    `V="$(head -n 1 "$F" | tr -d "\r")"` are balanced per level but not in a
    straight left-to-right count -- a flat scanner shifts parity there and
    reports the `\r` as unquoted. Hence the state stack.
    """
    found = []
    stack = []          # quote state saved on entering each $( ... )
    quote = None
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        nxt = line[i + 1:i + 2]
        if quote == "'":
            # Nothing is special inside single quotes, not even a backslash.
            quote = None if c == "'" else quote
            i += 1
        elif quote == '"':
            if c == B:
                i += 2                      # escapes the next character
            elif c == '"':
                quote = None
                i += 1
            elif c == "$" and nxt == "(":
                stack.append(quote)         # bash re-parses from scratch here
                quote = None
                i += 2
            else:
                i += 1
        elif c == B:
            if nxt in "nrt":
                found.append(B + nxt)
            i += 2                          # escapes whatever follows
        elif c == "$" and nxt == "(":
            stack.append(quote)
            quote = None
            i += 2
        elif c == ")" and stack:
            quote = stack.pop()
            i += 1
        elif c in ("'", '"'):
            quote = c
            i += 1
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            break                           # rest of the line is a comment
        else:
            i += 1
    return found


def test_the_walk_reaches_what_actually_ships():
    """The count is only reassuring if it covers the packaged tree.

    An earlier version pruned by bare directory name, which quietly dropped
    portable/lib, portable/cloud, portable/config-server and
    portable/skills-cn -- 18 tracked files that go into the zip verbatim --
    while still printing a count that read like full coverage.
    """
    import subprocess
    out = subprocess.run(["git", "-C", REPO, "ls-files", "portable"],
                         capture_output=True, text=True).stdout
    tracked = set()
    for line in out.splitlines():
        line = line.strip().strip('"')
        if not line or os.path.splitext(line)[1].lower() in SKIP_EXT:
            continue
        tracked.add(line)
    if not tracked:
        check(True, "skip: git not available to cross-check the walk")
        return
    walked = {rel for rel, _ in shipped_files()}
    missed = sorted(
        t for t in tracked
        if t not in walked
        and not any(t.startswith(pref + "/") for pref in SKIP_PATHS)
        and BS_ESCAPE not in t)          # git escapes non-ASCII names
    check(not missed, "every tracked, packaged file is scanned%s" % (
        "" if not missed else " (missed: " + ", ".join(missed[:8]) + ")"))
    for d in ("portable/lib", "portable/cloud", "portable/config-server",
              "portable/skills-cn"):
        check(any(r.startswith(d + "/") for r in walked),
              "%s is walked, not pruned by a bare-name match" % d)


def test_no_lone_carriage_returns():
    n = 0
    for rel, full in shipped_files():
        raw = io.open(full, "rb").read()
        lone = raw.count(CR) - raw.count(CRLF)
        if lone:
            check(False, "%s has %d lone CR(s)" % (rel, lone))
        n += 1
    check(True, "scanned %d shipped files, no stray carriage return" % n)


def test_gitattributes_still_pins_every_script_type():
    """The release bytes come from checkout, so this file is the real control."""
    path = os.path.join(REPO, ".gitattributes")
    if not os.path.exists(path):
        check(False, ".gitattributes exists (it is what makes .bat files CRLF)")
        return
    text = io.open(path, encoding="utf-8").read()
    for pattern, want in sorted(EOL_RULES.items()):
        rx = re.compile(
            r"^\s*" + re.escape(pattern) + r"\s+.*\beol=" + want + r"\b", re.M)
        check(rx.search(text) is not None,
              "%s is pinned to eol=%s" % (pattern, want))


def test_no_eaten_backslash_escapes_in_shell_scripts():
    for rel, full in shipped_files():
        if os.path.splitext(rel)[1].lower() not in SHELL_EXT:
            continue
        text = io.open(full, encoding="utf-8", errors="replace").read()
        bad = []
        for n, line in enumerate(text.splitlines(), 1):
            for esc in unquoted_escapes(line):
                bad.append("line %d: %s" % (n, esc))
        check(not bad, "%s has no unquoted backslash escape%s" % (
            rel, "" if not bad else " -- " + "; ".join(bad)))


def test_the_escape_scanner_actually_works():
    """Pin the scanner itself: it is the only thing standing between this
    corruption and a release, and it has one job."""
    caught = B + "n"
    cases = [
        ('npm_config_cache="$X/.c" ' + B + 'n    "$NPM" install', [caught], "the real setup.sh bug"),
        ('[ -f x ] && V=$(head -n 1 f | tr -d "' + B + 'r")', [], "tr -d inside double quotes"),
        ("printf '%s" + B + "n' \"$V\" > f", [], "printf inside single quotes"),
        ('echo hello ' + B + '\n', [], "a genuine line continuation"),
        ('# a comment mentioning ' + B + 'n', [], "inside a comment"),
        ('grep ' + B + 'n file', [caught], "unquoted, mid-command"),
        ('echo "a ' + B + B + B + 'n b"', [], "an escaped backslash inside quotes"),
        # The shape shellcheck asks for. A flat quote toggle shifts parity
        # across the nested substitution and flags this correct line.
        ('V="$(head -n 1 "$F" | tr -d "' + B + 'r")"', [],
         "a nested command substitution, fully quoted"),
        ('V=$(head -n 1 "$F" | tr -d "' + B + 'r")', [],
         "...and the unquoted-assignment form of the same"),
        ('echo "$(printf ' + B + 'n)"', [caught],
         "a real unquoted escape INSIDE a substitution is still caught"),
        ("echo it" + B + "'s fine", [], "an escaped quote does not open a region"),
        # Pins the ")" pop specifically: without it the quote state stays
        # None after the substitution, the closing " opens a phantom region,
        # and a real unquoted escape after it is silently skipped.
        ('V="$(cmd)" && echo ' + B + 'n', [caught],
         "quote state is restored when a substitution closes"),
    ]
    for line, want, label in cases:
        got = unquoted_escapes(line)
        check(got == want, "scanner: %s (wanted %s, got %s)" % (label, want, got))


def test_byte_order_marks():
    seen = set()
    for rel, full in shipped_files():
        has = io.open(full, "rb").read(3) == BOM
        ext = os.path.splitext(rel)[1].lower()
        if rel in MUST_HAVE_BOM:
            seen.add(rel)
            check(has, "%s keeps its UTF-8 BOM" % rel)
        elif ext in SHELL_EXT:
            check(not has, "%s has no BOM -- it would break the shebang" % rel)
    missing = MUST_HAVE_BOM - seen
    check(not missing, "every file on the BOM list still exists%s" % (
        "" if not missing else " (not found: " + ", ".join(sorted(missing)) + ")"))


if __name__ == "__main__":
    for fn in (test_the_walk_reaches_what_actually_ships,
               test_no_lone_carriage_returns,
               test_gitattributes_still_pins_every_script_type,
               test_no_eaten_backslash_escapes_in_shell_scripts,
               test_the_escape_scanner_actually_works,
               test_byte_order_marks):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("script hygiene: all checks passed")

"""Every launcher keeps the engine's state on the stick, not on the host PC.

hermes-agent 0.21.4 (v2026.9.21) made the gateway a per-OS-user singleton:
every `gateway run` claims a host role and publishes a record in a
"rendezvous" directory, by default %USERPROFILE%\\.local\\state\\hermes\\
gateway-locks. Left there, two things went wrong on someone else's PC:

  * the Web UI starts our gateway with `gateway run --replace`, and --replace
    now targets whoever holds the HOST role -- so if that user runs their own
    Hermes (a Telegram bot, say), starting U-Hermes killed it; if theirs
    served another profile, ours refused to start and the chat had no engine;
  * the record, naming the stick's path, stayed on the PC after every launch
    (the launcher's taskkill /F skips the engine's atexit cleanup).

HERMES_GATEWAY_LOCK_DIR moves that directory. Pointed into data\\, the
singleton is scoped to this stick and nothing is left on the host. The
engine has read the variable since before v2026.9.14, so it is safe on both.

The launchers set HERMES_HOME per block (Windows-Menu.bat does it once per
menu item), so the check is per assignment, not per file.

Run:  python portable/scripts/tests/test_launcher_env.py
"""
import os
import re
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(os.path.dirname(HERE))

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def launchers():
    for name in sorted(os.listdir(PORTABLE)):
        if name.endswith((".bat", ".command")):
            yield name


def read_lines(name):
    with open(os.path.join(PORTABLE, name), "rb") as f:
        return f.read().decode("utf-8", errors="replace").splitlines()


# set "HERMES_HOME=%DATA_DIR%"   /   export HERMES_HOME="$DATA_DIR"
def assignment(line, var):
    m = re.match(r'\s*(?:set\s+"%s=([^"]*)"|export\s+%s="?([^"\s]*)"?)\s*$' % (var, var), line)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


# What makes a launcher start the engine at all.
RUNS_ENGINE = re.compile(r"hermes_cli|hermes\.cmd|hermes\.exe|HERMES_BIN|hermes-web-ui", re.I)


def test_each_home_on_the_stick_keeps_its_locks_there_too():
    print("wherever HERMES_HOME points at the stick, the gateway lock dir follows it")
    seen = 0
    for name in launchers():
        lines = read_lines(name)
        for i, line in enumerate(lines):
            home = assignment(line, "HERMES_HOME")
            if home is None:
                continue
            seen += 1
            lock = None
            # Same block: stop at the next label or the next HERMES_HOME.
            for nxt in lines[i + 1:i + 12]:
                if nxt.startswith(":") and not nxt.startswith("::") or assignment(nxt, "HERMES_HOME") is not None:
                    break
                lock = assignment(nxt, "HERMES_GATEWAY_LOCK_DIR")
                if lock is not None:
                    break
            sep = "\\" if name.endswith(".bat") else "/"
            check(lock == home + sep + "gateway-locks",
                  "%s:%d HERMES_HOME=%s -> lock dir %s" % (name, i + 1, home, lock))
    check(seen >= 8, "found the HERMES_HOME assignments at all (%d; 8 expected)" % seen)


def test_every_launcher_that_runs_the_engine_sets_a_home():
    print("a launcher that runs the engine without HERMES_HOME uses the host's ~/.hermes")
    for name in launchers():
        text = "\n".join(read_lines(name))
        if not RUNS_ENGINE.search(text):
            continue
        has_home = any(assignment(l, "HERMES_HOME") is not None for l in text.splitlines())
        check(has_home, "%s runs the engine and sets HERMES_HOME" % name)


# Loads or reinstalls the engine: a stale module there is imported, or built
# into the new install's RECORD, where prune-stale-files.py may not touch it.
ENGINE_CALL = re.compile(r"hermes_cli|pip install .*hermes-agent|%HERMES_BIN%|hermes\.cmd", re.I)
PRUNE_CALL = re.compile(r"prune-stale-files\.py|call :PRUNE_STALE", re.I)


def test_every_way_into_the_engine_prunes_first():
    print("stale modules from an older release are removed before the engine loads")
    for name in launchers():
        if not name.endswith(".bat"):
            continue  # macOS is not shipped; its launcher gets this when it is
        lines = read_lines(name)
        if name == "Windows-Menu.bat":
            # One block per menu item: each must prune before it runs the engine.
            blocks, label, start = [], None, 0
            for i, line in enumerate(lines + [":END"]):
                if line.startswith(":") and not line.startswith("::"):
                    blocks.append((label, lines[start:i]))
                    label, start = line, i + 1
            for label, body in blocks:
                if label == ":PRUNE_STALE":
                    continue
                calls = [i for i, l in enumerate(body) if ENGINE_CALL.search(l) and not l.lstrip().startswith("::")]
                if not calls:
                    continue
                prunes = [i for i, l in enumerate(body) if PRUNE_CALL.search(l)]
                check(bool(prunes) and prunes[0] < calls[0], "%s %s prunes before it runs the engine" % (name, label))
            sub = dict(blocks).get(":PRUNE_STALE", [])
            check(any('prune-stale-files.py" "%SCRIPT_DIR%\\."' in l for l in sub),
                  "%s :PRUNE_STALE runs the script on \"%%SCRIPT_DIR%%\\.\"" % name)
            continue
        calls = [i for i, l in enumerate(lines) if ENGINE_CALL.search(l) and not l.lstrip().startswith("::")]
        if not calls:
            continue
        prunes = [i for i, l in enumerate(lines) if PRUNE_CALL.search(l) and not l.lstrip().startswith("::")]
        check(bool(prunes) and prunes[0] < calls[0],
              "%s prunes (line %s) before the engine first loads (line %d)"
              % (name, prunes[0] + 1 if prunes else "none", calls[0] + 1))


def test_the_engine_reads_that_variable():
    print("the variable name is the engine's, not a guess")
    try:
        import gateway.status as status
    except ImportError:
        print("  skip  no engine on sys.path (CI runs this with the packaged venv)")
        return
    target = os.path.join(tempfile.gettempdir(), "u-hermes-lock-probe", "gateway-locks")
    saved = os.environ.get("HERMES_GATEWAY_LOCK_DIR")
    os.environ["HERMES_GATEWAY_LOCK_DIR"] = target
    try:
        check(os.path.normcase(str(status._get_lock_dir())) == os.path.normcase(target),
              "gateway.status._get_lock_dir() follows HERMES_GATEWAY_LOCK_DIR")
        try:
            import gateway.host_rendezvous as hr
        except ImportError:
            print("  skip  no host_rendezvous (engine older than v2026.9.21)")
        else:
            check(os.path.normcase(str(hr.host_state_dir())) == os.path.normcase(target),
                  "the host-gateway record goes there too")
    finally:
        if saved is None:
            os.environ.pop("HERMES_GATEWAY_LOCK_DIR", None)
        else:
            os.environ["HERMES_GATEWAY_LOCK_DIR"] = saved


# --- nothing of ours in the host's .hermes ----------------------------------
# Up to v0.4.7 the launchers copied data\config.yaml and data\.env -- the
# user's API keys -- into %USERPROFILE%\.hermes (Mac: ~/.hermes) for every
# run, "for any component started without our env". There is no such
# component: on Windows the engine falls back to %LOCALAPPDATA%\hermes, and
# the Web UI hands HERMES_HOME to every Hermes process it starts. The only
# reader was a Hermes the machine's owner installed, which then ran on the
# stick owner's keys; and [X] or a pulled stick left the keys behind.
HOST_HERMES = r'(%USER_HERMES_DIR%|%USERPROFILE%\\\.hermes|\$USER_HERMES_DIR|\$HOME/\.hermes|~/\.hermes)'
PUTS_THERE = [
    re.compile(r'\b(?:copy|xcopy|robocopy|cp)\s+.*' + HOST_HERMES, re.I),
    re.compile(r'>>?\s*"?' + HOST_HERMES, re.I),
    re.compile(r'>>?\s*"?%MIRROR_MARK%', re.I),
    re.compile(r'\bmkdir\b.*' + HOST_HERMES, re.I),
]


def test_nothing_of_ours_goes_into_the_hosts_hermes():
    print("no launcher copies config or keys into the host's .hermes")
    names = list(launchers())
    hits = []
    for name in names:
        for i, line in enumerate(read_lines(name)):
            if line.lstrip().startswith(("::", "#", "rem ", "REM ")):
                continue
            # Moving the machine's own *.before-u-hermes back is restoring
            # its files, not putting ours there.
            if "before-u-hermes" in line:
                continue
            if any(r.search(line) for r in PUTS_THERE):
                hits.append("%s:%d %s" % (name, i + 1, line.strip()))
    check(not hits, "none of %d launchers writes into the host's .hermes%s"
          % (len(names), "".join("\n         " + h for h in hits)))
    check(len(names) >= 5, "(found the launchers)")
    # The rule itself, on the lines v0.4.7 shipped.
    shipped = ['copy /Y "%DATA_DIR%\\config.yaml" "%USER_HERMES_DIR%\\config.yaml" >nul 2>&1',
               'if exist "%DATA_DIR%\\.env" copy /Y "%DATA_DIR%\\.env" "%USER_HERMES_DIR%\\.env" >nul 2>&1',
               'echo u-hermes> "%MIRROR_MARK%"',
               'if not exist "%USER_HERMES_DIR%" mkdir "%USER_HERMES_DIR%" 2>nul',
               'cp -f "$DATA_DIR/config.yaml" "$USER_HERMES_DIR/config.yaml" 2>/dev/null',
               '[ -f "$DATA_DIR/.env" ] && cp -f "$DATA_DIR/.env" "$USER_HERMES_DIR/.env" 2>/dev/null']
    check(all(any(r.search(l) for r in PUTS_THERE) for l in shipped),
          "(the rule catches every line v0.4.7 used to make the copy)")


def _routine(name, first, last):
    """The lines of a batch routine, from the line starting with `first`
    through the first line equal to `last`."""
    lines = read_lines(name)
    a = next(i for i, l in enumerate(lines) if l.startswith(first))
    z = next(i for i in range(a, len(lines)) if lines[i].strip() == last)
    return lines[a:z + 1]


def _core(lines, data):
    """What the cleanup does, with the data dir spelled one way."""
    out = []
    for l in lines:
        s = l.strip().replace(data, "<DATA>")
        if s.startswith(("del ", "move ", "fc ", "rd ", "if exist", "if not errorlevel", "if errorlevel")):
            out.append(re.sub(r"goto :?\S+", "goto <label>", s))
    return out


def test_the_menu_cleans_up_by_the_same_rules():
    print("Windows-Menu.bat [8] and the launcher take away the same old copies")
    start = _routine("Windows-Start.bat", ":remove_old_copy", "goto :eof")
    menu = _routine("Windows-Menu.bat", ":CLEANUP", 'rd "%USER_HERMES_DIR%" >nul 2>&1')
    a = _core(start, "%DATA_DIR%")
    b = _core(menu, "%SCRIPT_DIR%\\data")
    check(len(a) >= 10 and a == b, "same steps in the same order (%d vs %d)" % (len(a), len(b)))
    if a != b:
        for x, y in zip(a, b):
            if x != y:
                print("       start: %s\n       menu:  %s" % (x, y))


def test_old_copies_are_taken_away():
    """Run the launcher's own :remove_old_copy against a fake home."""
    print("an old version's copy is taken away; the machine's own files are not")
    if os.name != "nt":
        check(True, "skip: needs cmd.exe")
        return
    import shutil
    import subprocess
    routine = _routine("Windows-Start.bat", ":: --- Take away a copy", "goto :eof")

    def run(files, data_files):
        root = tempfile.mkdtemp(prefix="uh-oldcopy-")
        try:
            home, data = os.path.join(root, "home"), os.path.join(root, "data")
            os.makedirs(data)
            for rel, text in data_files.items():
                with open(os.path.join(data, rel), "w", encoding="utf-8") as f:
                    f.write(text)
            for rel, text in files.items():
                path = os.path.join(home, ".hermes", rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            bat = os.path.join(root, "harness.bat")
            body = ["@echo off", "chcp 65001 >nul", "setlocal enabledelayedexpansion",
                    'set "USERPROFILE=%s"' % home, 'set "DATA_DIR=%s"' % data,
                    'set "USER_HERMES_DIR=%USERPROFILE%\\.hermes"',
                    'set "MIRROR_MARK=%USER_HERMES_DIR%\\.u-hermes-mirror"',
                    "call :remove_old_copy", "echo HARNESS-DONE", "exit /b 0", ""] + routine
            with open(bat, "wb") as f:
                f.write(("\r\n".join(body) + "\r\n").encode("utf-8"))
            r = subprocess.run(["cmd", "/d", "/c", bat], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=60)
            out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
            left = {}
            hdir = os.path.join(home, ".hermes")
            if os.path.isdir(hdir):
                for dp, _dn, fn in os.walk(hdir):
                    for n in fn:
                        p = os.path.join(dp, n)
                        with open(p, encoding="utf-8") as f:
                            left[os.path.relpath(p, hdir).replace("\\", "/")] = f.read()
            return out, os.path.isdir(hdir), left
        finally:
            shutil.rmtree(root, ignore_errors=True)

    data = {"config.yaml": "model: current\n", ".env": "CURRENT_API_KEY=sk-current\n"}

    out, exists, left = run({".u-hermes-mirror": "u-hermes\n", "config.yaml": "model: old-copy\n",
                             ".env": "OLD_API_KEY=sk-old-copy\n",
                             "config.yaml.before-u-hermes": "model: machine-own\n",
                             ".env.before-u-hermes": "MACHINE_API_KEY=sk-machine\n",
                             "sessions/keep.json": "{}"}, data)
    check("HARNESS-DONE" in out, "the routine runs to the end (%s)" % out.strip()[-120:])
    check(left == {"config.yaml": "model: machine-own\n", ".env": "MACHINE_API_KEY=sk-machine\n",
                   "sessions/keep.json": "{}"},
          "a marked copy (v0.4.2-v0.4.7) goes; the machine's own files come back (%s)" % sorted(left))
    check("sk-old-copy" not in "".join(left.values()), "...and no trace of the old copy's key")
    check("已删掉旧版本" in out, "...and the user is told")

    out, exists, left = run({".u-hermes-mirror": "u-hermes\n", "config.yaml": "model: old-copy\n",
                             ".env": "OLD_API_KEY=sk-old-copy\n"}, data)
    check(not exists, "a marked copy on a machine with no Hermes of its own leaves no folder behind")

    out, exists, left = run({"config.yaml": data["config.yaml"], ".env": data[".env"]}, data)
    check(not exists, "an unmarked copy byte-identical to ours (v0.3.5-v0.4.1) goes too")
    check("已删掉旧版本" in out, "...and the user is told")

    own = {"config.yaml": "model: machine-own\n", ".env": "MACHINE_API_KEY=sk-machine\n"}
    out, exists, left = run(dict(own), data)
    check(left == own, "a Hermes this machine has of its own is left alone (%s)" % sorted(left))
    check("已删掉旧版本" not in out, "...and nothing is claimed")

    out, exists, left = run({}, data)
    check(not exists and "HARNESS-DONE" in out, "a machine that never had one does not get a .hermes folder")


if __name__ == "__main__":
    test_each_home_on_the_stick_keeps_its_locks_there_too()
    test_every_launcher_that_runs_the_engine_sets_a_home()
    test_every_way_into_the_engine_prunes_first()
    test_the_engine_reads_that_variable()
    test_nothing_of_ours_goes_into_the_hosts_hermes()
    test_the_menu_cleans_up_by_the_same_rules()
    test_old_copies_are_taken_away()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")

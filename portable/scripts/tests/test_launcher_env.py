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
HOST_HERMES = (r'(%USER_HERMES_DIR%|!USER_HERMES_DIR!|%USERPROFILE%\\\.hermes|!USERPROFILE!\\\.hermes'
               r'|\$\{?USER_HERMES_DIR\}?|\$\{?HOME\}?/\.hermes|~/\.hermes)')
PUTS_THERE = [
    re.compile(r'\b(?:copy|xcopy|robocopy|cp)\s+.*' + HOST_HERMES, re.I),
    re.compile(r'>>?\s*"?' + HOST_HERMES, re.I),
    re.compile(r'>>?\s*"?%MIRROR_MARK%', re.I),
    re.compile(r'\b(?:mkdir|md)\b.*' + HOST_HERMES, re.I),
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
               '[ -f "$DATA_DIR/.env" ] && cp -f "$DATA_DIR/.env" "$USER_HERMES_DIR/.env" 2>/dev/null',
               # and spellings a later change might use
               'md "%USER_HERMES_DIR%"',
               'copy /Y "%DATA_DIR%\\.env" "!USER_HERMES_DIR!\\.env"',
               'cp "$DATA_DIR/.env" "${HOME}/.hermes/.env"']
    check(all(any(r.search(l) for r in PUTS_THERE) for l in shipped),
          "(the rule catches every line v0.4.7 used to make the copy)")


def _is_comment(line):
    s = line.lstrip().lower().lstrip("@")
    return s.startswith(("::", "#")) or s == "rem" or s.startswith("rem ")


def _block_depth(lines, idx):
    """How many ( ) blocks are open at line `idx`, by the layout these
    launchers use: a block opens on a line ending in "(" and closes on a line
    starting with ")" (test_batch_parse reads them the same way)."""
    depth = 0
    for line in lines[:idx]:
        s = line.strip()
        if not s or _is_comment(line):
            continue
        if s.startswith(")"):
            depth -= 1
        if s.endswith("("):
            depth += 1
    return depth


def _first(lines, needle, start=0):
    """First line at or after `start` that mentions `needle` outside a comment."""
    return next((i for i in range(start, len(lines))
                 if needle in lines[i] and not _is_comment(lines[i])), None)


def test_old_copies_are_taken_away_first():
    """Every launcher runs scripts/remove-old-host-copy.py, and the main one
    runs it before anything can skip it or change what it compares against.

    A first, batch-file version of the cleanup sat after the CLI branch
    (menu [2] never reached it) and after protect-config.ps1, which rewrites
    data\\config.yaml -- so an old copy of it no longer matched and stayed.
    """
    print("each launcher takes away what older versions left, early enough")
    script = "remove-old-host-copy.py"
    start = read_lines("Windows-Start.bat")
    call = _first(start, script)
    check(call is not None and not start[call].lstrip().startswith("::"),
          "Windows-Start.bat runs %s" % script)
    if call is not None:
        for needle, what in (("protect-config.ps1", "protect-config.ps1 rewrites data\\config.yaml"),
                             ('if not "%~1"==""', "the CLI branch (menu [2], shortcuts with arguments)"),
                             ("setup.ps1", "the first-run install and its early exits"),
                             ("preflight.py", "the pre-flight exits")):
            later = _first(start, needle)
            check(later is not None and call < later, "...before %s" % what)
        check(start[call].lstrip().lower().startswith("if exist") and _block_depth(start, call) == 0,
              "...on one line, not in a ( ) block a ')' in the install path would break")
    menu = read_lines("Windows-Menu.bat")
    at = _first(menu, ":CLEANUP")
    run = _first(menu, script, at or 0)
    check(at is not None and run is not None and "--report" in menu[run],
          "Windows-Menu.bat [8] runs it with --report")
    body = menu[at:_first(menu, "goto MENU", at)] if at is not None else []
    check(not any("%USER_HERMES_DIR%" in l or "%USERPROFILE%" in l for l in body
                  if l.lstrip().lower().startswith("echo")),
          "...and echoes no path itself (a ')' or '&' in the user name broke that)")
    mac = read_lines("Mac-Start.command")
    at = _first(mac, script)
    check(at is not None, "Mac-Start.command runs it")
    if at is not None:
        for needle, what in (("if [ $# -gt 0 ]", "the CLI branch"),
                             ("exit 0", "the 'not configured' exit"),
                             ("setup.sh", "is fine after setup, which it needs")):
            later = _first(mac, needle)
            if what.startswith("is fine"):
                check(later is not None and later < at, "...after setup.sh, whose venv it runs on")
            else:
                check(later is not None and at < later, "...before %s" % what)
    # A bot-only user may never run anything but the gateway launcher.
    for name in ("Windows-Gateway.bat", "debug.bat"):
        lines = read_lines(name)
        at = _first(lines, script)
        engine = next((i for i, l in enumerate(lines) if ENGINE_CALL.search(l) and not _is_comment(l)), None)
        check(at is not None and (engine is None or at < engine),
              "%s runs it before the engine starts" % name)
        check(at is not None and _block_depth(lines, at) == 0, "...outside any ( ) block")
    check(run is not None and _block_depth(menu, run) == 0, "Windows-Menu.bat [8] runs it outside any ( ) block")
    # The rules themselves, on what a later edit might do.
    wrapped = ['if exist "%DATA_DIR%" (', "    echo.", '    "python.exe" "scripts\\remove-old-host-copy.py" "data"', ")"]
    check(_block_depth(wrapped, 2) == 1, "(a call wrapped in a multi-line block is seen as inside one)")
    check(_first(['@rem "python.exe" remove-old-host-copy.py', "REM x remove-old-host-copy.py"], script) is None,
          "(a call behind @rem or REM does not count as a call)")



# --- the Web UI's data, and what it does on its own --------------------------
# Web UI 0.7.x keeps its logins, chat list and the Ekko agent's data under
# HERMES_WEB_UI_HOME only with NODE_ENV=production; otherwise in
# packages\server\data and inside the npm package, which every reinstall
# deletes. scripts/prepare-webui.py moves an older version's data first, and
# the launcher switches only on its word (test_prepare_webui.py covers the
# script). These pin where the launchers call it and set what around it.

def _code(lines):
    """(index, line) of every line that is not a comment."""
    return [(i, l) for i, l in enumerate(lines) if l.strip() and not _is_comment(l)]


def _where(lines, needle, start=0):
    return [i for i, l in _code(lines) if needle in l and i >= start]


def test_production_mode_only_after_the_data_moved():
    print("NODE_ENV=production: after data\\.env, after prepare-webui.py, only on its word")
    lines = read_lines("Windows-Start.bat")
    loader = _where(lines, 'in ("%DATA_DIR%\\.env")')
    node = _where(lines, '"%NODE_EXE%" "%WEBUI_SERVER%"')
    prepare = [i for i in _where(lines, "prepare-webui.py") if "--move-only" not in lines[i]]
    mentions = _where(lines, "NODE_ENV")
    check(len(loader) == 1 and len(node) == 1 and len(prepare) == 1, "(found the .env loader, the node line and one full prepare call)")
    if len(loader) == 1 and len(node) == 1 and len(prepare) == 1:
        loader, node, prepare = loader[0], node[0], prepare[0]
        check('"%HERMES_WEB_UI_HOME%"' in lines[prepare] and "node_modules\\hermes-web-ui" in lines[prepare],
              "prepare-webui.py gets the Web UI home and the package it moves out of")
        # Exactly 0: "if not errorlevel 1" alone is also true for the negative
        # code a Ctrl+C leaves, which would start production mid-move.
        check([lines[i].strip() for i in mentions] == [
                'set "NODE_ENV="',
                'if errorlevel 0 if not errorlevel 1 set "NODE_ENV=production"',
                'if exist "%HERMES_WEB_UI_HOME%\\hermes-web-ui.db" set "NODE_ENV=production"'],
              "NODE_ENV is cleared, then set only on prepare's exit 0 or when the moved database is there")
        check(loader < mentions[0] < prepare < mentions[1] < mentions[2] < node,
              "...in that order, after data\\.env and before node starts")
        check(_block_depth(lines, prepare) == 0 and all(_block_depth(lines, i) == 0 for i in mentions),
              "...outside any ( ) block, so errorlevel is read when the line runs")
        home = [i for i, l in _code(lines) if assignment(l, "HERMES_WEB_UI_HOME") is not None]
        check(bool(home) and all(assignment(lines[i], "HERMES_WEB_UI_HOME") == "%DATA_DIR%\\webui" for i in home)
              and loader < home[-1] < prepare and _block_depth(lines, home[-1]) == 0,
              "HERMES_WEB_UI_HOME is set to data\\webui again after data\\.env: Menu [9] and setup.ps1 move data there")
    check(prep_constants() == (0, 10), "prepare-webui.py's READY/DEFERRED are the 0/10 these lines test for")
    mac = read_lines("Mac-Start.command")
    source = _where(mac, 'source "$DATA_DIR/.env"')
    full = [i for i in _where(mac, "prepare-webui.py") if "--move-only" not in mac[i]]
    order = [_where(mac, n) for n in ("unset NODE_ENV", "export NODE_ENV=production",
                                       'cd "$SCRIPT_DIR"', '"$NODE_EXE" "$WEBUI_SERVER"')]
    check(source and len(full) == 1 and all(order)
          and source[0] < order[0][0] < full[0] < order[1][0] < order[2][-1] < order[3][-1],
          "Mac-Start.command: unset, prepare, export, cd, node -- after data/.env")
    check(len(order[1]) == 1 and mac[order[1][0] - 1].strip()
          == 'if [ "$UH_PREPARED" -eq 0 ] || [ -f "$HERMES_WEB_UI_HOME/hermes-web-ui.db" ]; then',
          "...exported only on exit 0 or when the moved database is there")
    home = _where(mac, 'export HERMES_WEB_UI_HOME="$DATA_DIR/webui"')
    check(source and home and home[-1] > source[0] and home[-1] < full[0] if full else False,
          "...and HERMES_WEB_UI_HOME is data/webui again after data/.env")


def prep_constants():
    with open(os.path.join(PORTABLE, "scripts", "prepare-webui.py"), encoding="utf-8") as f:
        text = f.read()
    got = [re.search(r"^%s = (\d+)" % n, text, re.M) for n in ("READY", "DEFERRED")]
    return tuple(int(m.group(1)) if m else None for m in got)


PRIVACY_DEFAULTS = [("BIND_HOST", "127.0.0.1"), ("STUDIO_PUSH_CONTENT_PREVIEW", "0"),
                    ("HERMES_WEB_UI_DISABLE_UPDATE_CHECK", "1"), ("HERMES_LAN_DISCOVERY_ENABLED", "0"),
                    ("npm_config_include", "dev")]


def test_web_ui_defaults_come_before_data_env():
    print("loopback, no update button, no LAN discovery, devDependencies kept -- overridable in data\\.env")
    for name, loader_needle in (("Windows-Start.bat", 'in ("%DATA_DIR%\\.env")'),
                                ("Mac-Start.command", 'source "$DATA_DIR/.env"')):
        lines = read_lines(name)
        loader = _where(lines, loader_needle)
        for var, value in PRIVACY_DEFAULTS:
            at = [i for i, l in _code(lines) if assignment(l, var) is not None]
            check(bool(at) and bool(loader) and all(assignment(lines[i], var) == value for i in at) and at[0] < loader[0],
                  "%s sets %s=%s before loading data\\.env" % (name, var, value))


def test_every_reinstall_moves_ekko_out_first():
    print("npm replaces node_modules\\hermes-web-ui: every reinstall moves .ekko out before it")
    lines = read_lines("Windows-Start.bat")
    installs = _where(lines, 'npm.cmd" install -g')
    move = [i for i in _where(lines, "prepare-webui.py") if "--move-only" in lines[i]]
    guard = _where(lines, 'if exist "%NODE_DIR%\\node_modules\\hermes-web-ui\\.ekko\\" goto :ekko_stuck')
    check(bool(installs) and len(move) == 1 and len(guard) == 1 and move[0] < guard[0] < installs[0],
          "Windows-Start.bat: move, then stop if .ekko is still there, then npm")
    check(len(move) == 1 and '"%HERMES_WEB_UI_HOME%"' in lines[move[0]]
          and '"%NODE_DIR%\\node_modules\\hermes-web-ui"' in lines[move[0]],
          "...moving to the Web UI home, out of the package npm replaces")
    stuck = _where(lines, ":ekko_stuck")
    check(len(stuck) == 2 and lines[stuck[1]].strip() == ":ekko_stuck"
          and "exit /b 1" in "".join(lines[stuck[1]:stuck[1] + 9]), "...where it explains and exits")

    menu = read_lines("Windows-Menu.bat")
    installs = _where(menu, 'npm.cmd" install -g')
    sub = _where(menu, ":UPDATE_WEBUI")
    label = [i for i in sub if menu[i].strip() == ":UPDATE_WEBUI"]
    check(bool(installs) and label and all(i > label[0] for i in installs),
          "Windows-Menu.bat: npm install only inside :UPDATE_WEBUI")
    update = [i for i, l in enumerate(menu) if l.strip() == ":UPDATE"]
    move = [i for i in _where(menu, "prepare-webui.py") if "--move-only" in menu[i]]
    guard = _where(menu, 'if exist "%NODE_DIR%\\node_modules\\hermes-web-ui\\.ekko\\" (')
    calls = [i for i in sub if "call :UPDATE_WEBUI" in menu[i]]
    check(update and len(move) == 1 and len(guard) == 1 and len(calls) == 1
          and update[0] < move[0] < guard[0] < calls[0] < label[0],
          "...called from [9] only after the move, in the else of the .ekko check")
    check(len(move) == 1 and '"%SCRIPT_DIR%\\data\\webui"' in menu[move[0]]
          and '"%NODE_DIR%\\node_modules\\hermes-web-ui"' in menu[move[0]],
          "...moving to data\\webui (where the launcher keeps it), out of the package")

    with open(os.path.join(PORTABLE, "setup.ps1"), "rb") as f:
        setup = f.read().decode("utf-8-sig").splitlines()
    saves = [i for i, l in enumerate(setup) if l.strip() == "Save-WebUiData -NodeDir $nodeDir"]
    wipe = [i for i, l in enumerate(setup) if "Remove-Item $nodeDir -Recurse -Force" in l]
    npm = [i for i, l in enumerate(setup) if l.strip().startswith("& $npmCmd install -g")]
    check(len(saves) == 2 and wipe and npm and saves[0] < wipe[0] and saves[0] < wipe[0] < saves[1] < npm[0],
          "setup.ps1: Save-WebUiData before Install-Node wipes the Node folder and before npm install")

    for name, npm_needle, move_needle, guard_needle in (
            ("Mac-Start.command", 'npm install -g "$WEBUI_SPEC"', "--move-only",
             'if [ -d "$NODE_DIR/lib/node_modules/hermes-web-ui/.ekko" ]; then'),
            ("setup.sh", 'install -g "$WEBUI_SPEC"', "--move-only",
             'if [ -d "$NODE_DIR/lib/node_modules/hermes-web-ui/.ekko" ]; then')):
        lines = read_lines(name)
        installs, moves, guards = _where(lines, npm_needle), _where(lines, move_needle), _where(lines, guard_needle)
        check(len(installs) == 1 and moves and guards and moves[0] < guards[0] < installs[0],
              "%s: move, then refuse while .ekko is still there, then npm" % name)


def _ps_function(text, name):
    """The source of `function <name> { ... }` in a .ps1, by brace matching."""
    start = text.index("function %s {" % name)
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i + 1]
        i += 1


def test_setup_ps1_save_webui_data_returns_nothing():
    """Install-Node calls Save-WebUiData and returns the Node folder. Anything
    Save-WebUiData lets through to the pipeline joins that return value: the
    first version let prepare-webui.py's messages through, so on every stick
    still holding .ekko, `setup.ps1 -Force` died at step 6 with "Cannot find
    drive" -- after Node had been wiped. Run the function as setup.ps1 has it,
    under Windows PowerShell, on a stick that has an .ekko to move."""
    print("setup.ps1: Save-WebUiData moves .ekko and puts nothing into its caller's return value")
    import shutil
    import subprocess
    ps = shutil.which("powershell.exe") or shutil.which("powershell")
    py = os.path.join(PORTABLE, "runtime", "python-win-x64", "python.exe")
    if not ps or not os.path.isfile(py):
        print("  skip  needs Windows PowerShell and the bundled runtime\\python-win-x64")
        return
    with open(os.path.join(PORTABLE, "setup.ps1"), encoding="utf-8-sig") as f:
        text = f.read()
    stick = tempfile.mkdtemp(prefix="save-webui-")
    try:
        os.makedirs(os.path.join(stick, "scripts"))
        shutil.copy2(os.path.join(PORTABLE, "scripts", "prepare-webui.py"), os.path.join(stick, "scripts"))
        node = os.path.join(stick, "runtime", "node-win-x64")
        ekko = os.path.join(node, "node_modules", "hermes-web-ui", ".ekko")
        os.makedirs(os.path.join(ekko, "skills"))
        with open(os.path.join(ekko, "skills", "mine.md"), "w") as f:
            f.write("x")
        os.makedirs(os.path.join(stick, "data"))
        script = os.path.join(stick, "probe.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write('$ErrorActionPreference = "Stop"\r\n')
            f.write("$scriptDir = '%s'\r\n$dataDir = '%s'\r\n$runtimeDir = '%s'\r\n"
                    % (stick, os.path.join(stick, "data"), os.path.join(PORTABLE, "runtime")))
            f.write(_ps_function(text, "Invoke-Native") + "\r\n")
            f.write(_ps_function(text, "Save-WebUiData") + "\r\n")
            f.write("function Probe { Save-WebUiData -NodeDir '%s'; return 'THE-NODE-DIR' }\r\n" % node)
            f.write("try { $r = Probe } catch { Write-Host 'RESULT:THREW'; exit 0 }\r\n")
            f.write("if ($r -is [string] -and $r -eq 'THE-NODE-DIR') { Write-Host 'RESULT:SINGLE' }"
                    " else { Write-Host ('RESULT:LEAKED ' + @($r).Count) }\r\n")

        def probe():
            out = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                                 capture_output=True, timeout=180)
            return out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")
        text_out = probe()
        check("RESULT:SINGLE" in text_out, "the caller gets back only its own value (%s)"
              % (text_out.strip().splitlines()[-1:] or ["no output"])[0])
        check(not os.path.exists(ekko) and os.path.isfile(os.path.join(stick, "data", "webui", ".ekko", "skills", "mine.md")),
              ".ekko was moved to data\\webui before anything could wipe it")
        # Now one that cannot move: a file in it is open. Install-Node must
        # not go on to Remove-Item the Node folder.
        shutil.rmtree(os.path.join(stick, "data", "webui"))
        os.makedirs(os.path.join(ekko, "skills"))
        with open(os.path.join(ekko, "skills", "mine.md"), "w") as f:
            f.write("x")
        held = open(os.path.join(ekko, "skills", "mine.md"), "rb")
        try:
            text_out = probe()
        finally:
            held.close()
        check("RESULT:THREW" in text_out, "with .ekko held open it throws, so nothing after it runs")
        check(os.path.isfile(os.path.join(ekko, "skills", "mine.md")), "...and .ekko is still whole where it was")
    finally:
        shutil.rmtree(stick, ignore_errors=True)


if __name__ == "__main__":
    test_production_mode_only_after_the_data_moved()
    test_web_ui_defaults_come_before_data_env()
    test_every_reinstall_moves_ekko_out_first()
    test_setup_ps1_save_webui_data_returns_nothing()
    test_each_home_on_the_stick_keeps_its_locks_there_too()
    test_every_launcher_that_runs_the_engine_sets_a_home()
    test_every_way_into_the_engine_prunes_first()
    test_the_engine_reads_that_variable()
    test_nothing_of_ours_goes_into_the_hosts_hermes()
    test_old_copies_are_taken_away_first()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")

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


if __name__ == "__main__":
    test_each_home_on_the_stick_keeps_its_locks_there_too()
    test_every_launcher_that_runs_the_engine_sets_a_home()
    test_the_engine_reads_that_variable()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")

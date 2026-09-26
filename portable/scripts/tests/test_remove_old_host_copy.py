"""The copy of the user's keys that v0.4.7 and earlier left on host PCs.

Up to v0.4.7 every launch copied data\\config.yaml and data\\.env -- the API
keys -- into %USERPROFILE%\\.hermes (Mac: ~/.hermes). v0.4.8 stopped, and
scripts/remove-old-host-copy.py takes away what was left. Every case here is
one a review found in a first, batch-file version of the cleanup, which
compared the host copy with today's data\\config.yaml only:

  * v0.3.5-v0.4.1 appended a platforms block to the copy, so it never
    matched, and the .env with the keys was only looked at if it did;
  * the launcher runs protect-config.ps1, which rewrites data\\config.yaml,
    before the cleanup ran -- so even an exact copy stopped matching;
  * v0.4.2-v0.4.7 stashed such a copy as "the machine's own" and the cleanup
    put it back, while saying it had removed it;
  * rd on a junction removes the link whatever is behind it.

Runs against temp dirs on any OS; the junction case needs Windows.

Run:  python portable/scripts/tests/test_remove_old_host_copy.py
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "remove-old-host-copy.py")
_spec = importlib.util.spec_from_file_location("remove_old_host_copy", SCRIPT)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


CONFIG = "model:\r\n  provider: custom:longcat\r\nterminal:\r\n  cwd: H:\\U-Hermes工作区\r\n".encode("utf-8")
CONFIG_BEFORE_PROTECT = b"model:\r\n  provider: custom:longcat\r\n"
ENV = b"LONGCAT_API_KEY=sk-stick-owner\r\n"
OWN_CONFIG = b"model:\n  provider: openrouter\n"
OWN_ENV = b"OPENROUTER_API_KEY=sk-machine-owner\n"


class Case(object):
    def __init__(self, host_files, data_files=None, backups=None):
        self.root = tempfile.mkdtemp(prefix="uh-hostcopy-")
        self.home = os.path.join(self.root, "home")
        self.data = os.path.join(self.root, "data")
        self.host = os.path.join(self.home, ".hermes")
        os.makedirs(os.path.join(self.data, "backups", "config"))
        os.makedirs(self.home)
        files = {"config.yaml": CONFIG, ".env": ENV}
        files.update(data_files or {})
        for rel, data in files.items():
            if data is not None:
                self._write(os.path.join(self.data, rel), data)
        for rel, data in (backups or {}).items():
            self._write(os.path.join(self.data, "backups", "config", rel), data)
        for rel, data in host_files.items():
            self._write(os.path.join(self.host, rel), data)

    @staticmethod
    def _write(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def run(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = rc.main(["remove-old-host-copy.py", self.data, "--home", self.home] + list(extra))
        return code, out.getvalue()

    def left(self):
        found = {}
        if os.path.isdir(self.host):
            for dp, _dn, fn in os.walk(self.host):
                for n in fn:
                    p = os.path.join(dp, n)
                    with open(p, "rb") as f:
                        found[os.path.relpath(p, self.host).replace("\\", "/")] = f.read()
        return found

    def done(self):
        shutil.rmtree(self.root, ignore_errors=True)


def test_a_marked_copy_goes_and_the_machines_own_comes_back():
    print("v0.4.2-v0.4.7: a marked copy, the machine's own files set aside")
    c = Case({".u-hermes-mirror": b"u-hermes\r\n", "config.yaml": b"model: whatever-it-was\n",
              ".env": b"OLD_API_KEY=sk-old\n", "config.yaml.before-u-hermes": OWN_CONFIG,
              ".env.before-u-hermes": OWN_ENV, "state.db": b"machine data"})
    code, out = c.run()
    check(code == 0, "exits 0")
    check(c.left() == {"config.yaml": OWN_CONFIG, ".env": OWN_ENV, "state.db": b"machine data"},
          "the copy and marker go, the machine's files are back (%s)" % sorted(c.left()))
    check("已删掉旧版本" in out and "放回原处" in out, "and the user is told both")
    c.done()


def test_a_marked_copy_on_a_machine_without_hermes_leaves_nothing():
    print("...and on a machine with no Hermes of its own, no folder is left")
    c = Case({".u-hermes-mirror": b"u-hermes\r\n", "config.yaml": CONFIG, ".env": ENV})
    c.run()
    check(not os.path.exists(c.host), "the folder is gone")
    c.done()


def test_a_v041_copy_with_the_appended_block():
    print("v0.3.5-v0.4.1: copy + appended platforms block, no marker")
    c = Case({"config.yaml": CONFIG + rc.WINDOWS_TAIL, ".env": ENV})
    code, out = c.run()
    check(not os.path.exists(c.host), "config copy with the tail and the .env both go (%s)" % sorted(c.left()))
    check("已删掉旧版本" in out, "and the user is told")
    c.done()


def test_a_copy_of_the_config_before_protect_config_rewrote_it():
    print("the stick's config changed since the copy; its backup still matches")
    c = Case({"config.yaml": CONFIG_BEFORE_PROTECT + rc.WINDOWS_TAIL, ".env": ENV},
             backups={"config.yaml.good.20260918-183711": CONFIG_BEFORE_PROTECT})
    c.run()
    check(not os.path.exists(c.host), "matched through data\\backups (%s)" % sorted(c.left()))
    c.done()


def test_the_env_is_judged_on_its_own():
    print("config no longer provably ours, .env still byte-identical to the stick's")
    c = Case({"config.yaml": "model: gone\nterminal:\n  cwd: D:\\U-Hermes工作区\n".encode("utf-8"),
              ".env": ENV})
    code, out = c.run()
    left = c.left()
    check(".env" not in left, "the keys go even though the config does not match")
    check("config.yaml" in left, "the unprovable config is left")
    check("[!]" in out and "没有自动删" in out, "and reported as probably ours, not deleted")
    c.done()


def test_a_v041_copy_stashed_as_the_machines_own_is_not_put_back():
    print("v0.4.2+ stashed a v0.4.1 copy as the machine's own; it must not come back")
    c = Case({".u-hermes-mirror": b"u-hermes\r\n", "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": CONFIG_BEFORE_PROTECT + rc.WINDOWS_TAIL,
              ".env.before-u-hermes": ENV},
             backups={"config.yaml.good.1": CONFIG_BEFORE_PROTECT})
    code, out = c.run()
    check(not os.path.exists(c.host), "nothing restored, folder gone (%s)" % sorted(c.left()))
    check("放回原处" not in out, "and nothing is claimed as put back")
    c.done()


def test_the_users_own_machine_on_2026_09_26():
    print("the state found on the author's PC: marked copy, stash = a stick backup, 1-byte .env stash")
    c = Case({".u-hermes-mirror": b"u-hermes\r\n", "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": CONFIG_BEFORE_PROTECT, ".env.before-u-hermes": b"\n",
              "memories/MEMORY.md": b"machine memory", "state.db": b"machine db"},
             backups={"config.yaml.good.20260918-183711": CONFIG_BEFORE_PROTECT})
    c.run()
    check(c.left() == {".env": b"\n", "memories/MEMORY.md": b"machine memory", "state.db": b"machine db"},
          "old copies gone, the empty .env and the machine's data stay (%s)" % sorted(c.left()))
    c.done()


def test_whitespace_is_never_ours():
    print("an empty .env on both sides is not a match")
    c = Case({".env": b"\n"}, data_files={".env": b"\n"})
    c.run()
    check(c.left() == {".env": b"\n"}, "left alone")
    c.done()


def test_a_machines_own_hermes_is_left_alone():
    print("a Hermes this machine has of its own")
    c = Case({"config.yaml": OWN_CONFIG, ".env": OWN_ENV})
    code, out = c.run()
    check(c.left() == {"config.yaml": OWN_CONFIG, ".env": OWN_ENV}, "untouched")
    check(out.strip() == "", "and nothing printed at launch")
    code, out = c.run("--report")
    check("没有这个 U 盘留下的副本" in out and "还有 2 项" in out, "[8] reports it as not ours")
    c.done()


def test_nothing_there_means_nothing_created():
    print("a machine that never had one")
    c = Case({})
    code, out = c.run()
    check(not os.path.exists(c.host) and out.strip() == "", "no folder, no output")
    c.done()


def test_the_mac_tail():
    print("Mac: cp -f plus the heredoc block, no marker, no stash")
    c = Case({"config.yaml": CONFIG + rc.MAC_TAIL, ".env": ENV})
    c.run()
    check(not os.path.exists(c.host), "both go")
    c.done()


def test_a_junction_is_never_removed():
    print("~/.hermes as a junction to the machine's own data")
    if os.name != "nt":
        check(True, "skip: junctions are Windows")
        return
    c = Case({})
    target = os.path.join(c.root, "real-hermes")
    os.makedirs(target)
    for rel, data in {".u-hermes-mirror": b"u-hermes\r\n", "config.yaml": CONFIG, ".env": ENV}.items():
        with open(os.path.join(target, rel), "wb") as f:
            f.write(data)
    r = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", c.host, target], capture_output=True)
    if r.returncode != 0:
        check(True, "skip: could not create a junction here")
        c.done()
        return
    c.run()
    check(os.path.isdir(c.host), "the junction is still there")
    check(os.listdir(target) == [], "our files behind it are gone")
    os.rmdir(c.host)
    c.done()


def test_it_never_prints_a_key():
    print("no file content in the output")
    c = Case({".u-hermes-mirror": b"u-hermes\r\n", "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": "x: y\nterminal:\n  cwd: U-Hermes\n".encode("utf-8")})
    code, out = c.run("--report")
    check("sk-stick-owner" not in out and "longcat" not in out, "keys and config text stay out of it")
    c.done()


def test_a_failure_does_not_stop_the_launch():
    print("an unexpected error exits 0")
    r = subprocess.run([sys.executable, SCRIPT, os.path.join(tempfile.gettempdir(), "no-such-data"),
                        "--home"], capture_output=True)
    check(r.returncode == 0, "exit %d (%s)" % (r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()[-80:]))


if __name__ == "__main__":
    for fn in (test_a_marked_copy_goes_and_the_machines_own_comes_back,
               test_a_marked_copy_on_a_machine_without_hermes_leaves_nothing,
               test_a_v041_copy_with_the_appended_block,
               test_a_copy_of_the_config_before_protect_config_rewrote_it,
               test_the_env_is_judged_on_its_own,
               test_a_v041_copy_stashed_as_the_machines_own_is_not_put_back,
               test_the_users_own_machine_on_2026_09_26,
               test_whitespace_is_never_ours,
               test_a_machines_own_hermes_is_left_alone,
               test_nothing_there_means_nothing_created,
               test_the_mac_tail,
               test_a_junction_is_never_removed,
               test_it_never_prints_a_key,
               test_a_failure_does_not_stop_the_launch):
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("remove old host copy: all checks passed")

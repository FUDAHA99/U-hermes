"""The copy of the user's keys that v0.4.7 and earlier left on host PCs.

Up to v0.4.7 every launch copied data\\config.yaml and data\\.env -- the API
keys -- into %USERPROFILE%\\.hermes (Mac: ~/.hermes). v0.4.8 stopped, and
scripts/remove-old-host-copy.py takes away what was left. Every case here is
one two rounds of review found, reproduced, in an earlier version:

  * v0.3.5-v0.4.1 appended a platforms block to the copy, so it never
    matched, and the .env with the keys was only looked at if it did;
  * protect-config.ps1 rewrites data\\config.yaml before the cleanup ran;
  * v0.4.2-v0.4.7 stashed such a copy as "the machine's own" and the cleanup
    put it back, while saying it had removed it;
  * rd on a junction removes the link whatever is behind it; a junction
    from ~/.hermes to the stick's data made every live file "ours";
  * a read-only copy survived, the marker went, and nothing could ever
    prove the copy ours again;
  * an unprovable .env passed in silence while [8] called the PC clean.

The two tails are written out here from the old launchers' source, not taken
from the script: a fixture built from the script's own constant would pass
whatever the constant said.

Runs against temp dirs on any OS; the junction cases need Windows.

Run:  python portable/scripts/tests/test_remove_old_host_copy.py
"""
import importlib.util
import io
import os
import shutil
import stat
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


# v0.3.5-v0.4.1 Windows-Start.bat, under chcp 65001:
#     (  echo.  echo platforms:  echo   api_server: ... echo     cors_origins: '*'  ) >> copy
V041_TAIL = (b"\r\n"
             b"platforms:\r\n"
             b"  api_server:\r\n"
             b"    extra:\r\n"
             b"      port: 8642\r\n"
             b"      host: 127.0.0.1\r\n"
             b"    enabled: true\r\n"
             b"    key: ''\r\n"
             b"    cors_origins: '*'\r\n")
# Every Mac-Start.command up to v0.4.7: cat >> copy << 'HCEOF' (blank line first).
MAC_TAIL = (b"\n"
            b"platforms:\n"
            b"  api_server:\n"
            b"    extra:\n"
            b"      port: 8642\n"
            b"      host: 127.0.0.1\n"
            b"    enabled: true\n"
            b"    key: ''\n"
            b"    cors_origins: '*'\n")

CONFIG = "model:\r\n  provider: custom:longcat\r\nterminal:\r\n  cwd: H:\\U-Hermes工作区\r\n".encode("utf-8")
CONFIG_BEFORE_PROTECT = b"model:\r\n  provider: custom:longcat\r\n"
ENV = b"LONGCAT_API_KEY=sk-stick-owner\r\n"
OLD_ENV = b"LONGCAT_API_KEY=sk-stick-owner-rotated-out\r\n"
OWN_CONFIG = b"model:\n  provider: openrouter\n"
OWN_ENV = b"OPENROUTER_API_KEY=sk-machine-owner\n"
MARK = b"u-hermes\r\n"


class Case(object):
    def __init__(self, host_files, data_files=None, backups=None, no_env=False):
        self.root = tempfile.mkdtemp(prefix="uh-hostcopy-")
        self.home = os.path.join(self.root, "home")
        self.data = os.path.join(self.root, "data")
        self.host = os.path.join(self.home, ".hermes")
        os.makedirs(os.path.join(self.data, "backups", "config"))
        os.makedirs(self.home)
        files = {"config.yaml": CONFIG} if no_env else {"config.yaml": CONFIG, ".env": ENV}
        files.update(data_files or {})
        for rel, data in files.items():
            self.write(os.path.join(self.data, rel), data)
        for rel, data in (backups or {}).items():
            self.write(os.path.join(self.data, "backups", "config", rel), data)
        for rel, data in host_files.items():
            self.write(os.path.join(self.host, rel), data)

    @staticmethod
    def write(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def run(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = rc.main(["remove-old-host-copy.py", self.data, "--home", self.home] + list(extra))
        return code, out.getvalue()

    def left(self, where=None):
        where = where or self.host
        found = {}
        if os.path.isdir(where):
            for dp, _dn, fn in os.walk(where):
                for n in fn:
                    p = os.path.join(dp, n)
                    with open(p, "rb") as f:
                        found[os.path.relpath(p, where).replace("\\", "/")] = f.read()
        return found

    def done(self):
        for dp, dn, fn in os.walk(self.root):
            for n in fn:
                try:
                    os.chmod(os.path.join(dp, n), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)


def junction(link, target):
    r = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", link, target], capture_output=True)
    return r.returncode == 0


def test_the_tails_are_what_the_old_launchers_wrote():
    print("the script's tails are the bytes the old launchers appended")
    check(rc.WINDOWS_TAIL == V041_TAIL, "Windows v0.3.5-v0.4.1")
    check(rc.MAC_TAIL == MAC_TAIL, "Mac, every version")


def test_a_marked_copy_goes_and_the_machines_own_comes_back():
    print("v0.4.2-v0.4.7: a marked copy, the machine's own files set aside")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": b"model: whatever-it-was\n",
              ".env": b"OLD_API_KEY=sk-old\n", "config.yaml.before-u-hermes": OWN_CONFIG,
              ".env.before-u-hermes": OWN_ENV, "state.db": b"machine data"})
    code, out = c.run()
    check(code == 0, "exits 0")
    check(c.left() == {"config.yaml": OWN_CONFIG, ".env": OWN_ENV, "state.db": b"machine data"},
          "the copy and marker go, the machine's files are back (%s)" % sorted(c.left()))
    check("已删掉旧版本" in out and "放回原处" in out and "[!]" not in out, "and the user is told both, no warning")
    c.done()


def test_a_marked_copy_on_a_machine_without_hermes_leaves_nothing():
    print("...and on a machine with no Hermes of its own, no folder is left")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV})
    c.run()
    check(not os.path.exists(c.host), "the folder is gone")
    c.done()


def test_a_v041_copy_with_the_appended_block():
    print("v0.3.5-v0.4.1: copy + appended platforms block, no marker")
    c = Case({"config.yaml": CONFIG + V041_TAIL, ".env": ENV})
    code, out = c.run()
    check(not os.path.exists(c.host), "config copy with the tail and the .env both go (%s)" % sorted(c.left()))
    check("已删掉旧版本" in out, "and the user is told")
    c.done()


def test_a_copy_of_the_config_before_protect_config_rewrote_it():
    print("the stick's config changed since the copy; its backup still matches")
    c = Case({"config.yaml": CONFIG_BEFORE_PROTECT + V041_TAIL, ".env": ENV},
             backups={"config.yaml.good.20260918-183711": CONFIG_BEFORE_PROTECT})
    c.run()
    check(not os.path.exists(c.host), "matched through data\\backups (%s)" % sorted(c.left()))
    c.done()


def test_the_config_sidecar_backups_count_too():
    print("...and through data\\config.yaml.* beside the live one")
    c = Case({"config.yaml": CONFIG_BEFORE_PROTECT, ".env": ENV},
             data_files={"config.yaml.providers.bak": CONFIG_BEFORE_PROTECT})
    c.run()
    check(not os.path.exists(c.host), "matched through data\\config.yaml.providers.bak")
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


def test_an_unprovable_env_beside_a_proven_config_is_named():
    print("config proven ours, .env rotated out of the stick's history")
    c = Case({"config.yaml": CONFIG + V041_TAIL, ".env": OLD_ENV})
    code, out = c.run("--report")
    check(c.left() == {".env": OLD_ENV}, "the unprovable .env is left")
    check("[!]" in out and ".env" in out.split("[!]", 1)[1], "but named in a warning")
    check("[OK]" not in out, "and the PC is not called clean")
    c.done()


def test_a_v041_copy_stashed_as_the_machines_own_is_not_put_back():
    print("v0.4.2+ stashed a v0.4.1 copy as the machine's own; it must not come back")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": CONFIG_BEFORE_PROTECT + V041_TAIL,
              ".env.before-u-hermes": ENV},
             backups={"config.yaml.good.1": CONFIG_BEFORE_PROTECT})
    code, out = c.run()
    check(not os.path.exists(c.host), "nothing restored, folder gone (%s)" % sorted(c.left()))
    check("放回原处" not in out, "and nothing is claimed as put back")
    c.done()


def test_an_unprovable_fingerprinted_stash_stays_put():
    print("...and one that only looks like ours is not put back either")
    stash = "model: x\nterminal:\n  cwd: E:\\U-Hermes工作区\n".encode("utf-8")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": stash, ".env.before-u-hermes": OLD_ENV})
    code, out = c.run()
    check(c.left() == {"config.yaml.before-u-hermes": stash, ".env.before-u-hermes": OLD_ENV},
          "both stashes stay as they are, nothing live (%s)" % sorted(c.left()))
    check("放回原处" not in out and "config.yaml.before-u-hermes" in out and ".env.before-u-hermes" in out,
          "and the pair is reported, not restored")
    c.write(os.path.join(c.host, "config.yaml"), OWN_CONFIG)
    c.run()
    check(c.left().get("config.yaml") == OWN_CONFIG,
          "the marker is gone, so a config the owner puts back later is not deleted")
    c.done()


def test_a_copy_that_cannot_be_removed_keeps_its_marker():
    print("a read-only marked copy: keep the marker, say so, try again next time")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": b"model: rotated-away\n",
              ".env": OLD_ENV, "config.yaml.before-u-hermes": OWN_CONFIG})
    ro = os.path.join(c.host, ".env")
    os.chmod(ro, stat.S_IREAD)
    code, out = c.run()
    can_block = os.path.exists(ro)
    if not can_block:
        check(True, "skip: this OS deletes read-only files anyway")
        c.done()
        return
    check(os.path.exists(os.path.join(c.host, ".u-hermes-mirror")), "the marker stays")
    check("删不掉" in out, "and the user is told which file")
    os.chmod(ro, stat.S_IWRITE | stat.S_IREAD)
    c.run()
    check(c.left() == {"config.yaml": OWN_CONFIG}, "the next run finishes the job (%s)" % sorted(c.left()))
    c.done()


def test_a_marker_does_not_take_a_env_the_stick_never_had():
    print("marker, but the stick has never had a .env: the machine's .env stays")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": OWN_ENV}, no_env=True)
    code, out = c.run()
    check(c.left() == {".env": OWN_ENV}, "only the config goes (%s)" % sorted(c.left()))
    check("[!]" not in out, "and the machine's .env is not called ours")
    c.done()


def test_a_stash_without_a_marker_is_not_restored():
    print("a *.before-u-hermes with no marker is left where it is")
    c = Case({"config.yaml.before-u-hermes": OWN_CONFIG})
    c.run()
    check(c.left() == {"config.yaml.before-u-hermes": OWN_CONFIG}, "untouched")
    c.done()


def test_the_users_own_machine_on_2026_09_26():
    print("the state found on the author's PC: marked copy, stash = a stick backup, 1-byte .env stash")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": CONFIG_BEFORE_PROTECT, ".env.before-u-hermes": b"\n",
              "memories/MEMORY.md": b"machine memory", "state.db": b"machine db"},
             backups={"config.yaml.good.20260918-183711": CONFIG_BEFORE_PROTECT})
    code, out = c.run()
    check(c.left() == {".env": b"\n", "memories/MEMORY.md": b"machine memory", "state.db": b"machine db"},
          "old copies gone, the empty .env and the machine's data stay (%s)" % sorted(c.left()))
    check("[!]" not in out, "no false alarm about the empty .env")
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
    check("[OK]" in out and "其余 2 项" in out, "[8] says it found nothing it recognises (%s)" % out.strip()[:80])
    c.done()


def test_an_empty_folder_the_owner_made_survives():
    print("an empty ~/.hermes with nothing of ours in it")
    c = Case({})
    os.makedirs(c.host)
    c.run()
    check(os.path.isdir(c.host), "still there")
    c.done()


def test_nothing_there_means_nothing_created():
    print("a machine that never had one")
    c = Case({})
    code, out = c.run()
    check(not os.path.exists(c.host) and out.strip() == "", "no folder, no output")
    c.done()


def test_the_mac_tail():
    print("Mac: cp -f plus the heredoc block, no marker, no stash")
    c = Case({"config.yaml": CONFIG + MAC_TAIL, ".env": ENV})
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
    for rel, data in {".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV}.items():
        c.write(os.path.join(target, rel), data)
    if not junction(c.host, target):
        check(True, "skip: could not create a junction here")
        c.done()
        return
    c.run()
    check(os.path.isdir(c.host), "the junction is still there")
    check(os.listdir(target) == [], "our files behind it are gone")
    os.rmdir(c.host)
    c.done()


def test_a_junction_to_the_sticks_own_data_is_left_alone():
    print("~/.hermes linked to the stick's data folder: those are the live files")
    c = Case({})
    if os.name == "nt":
        made = junction(c.host, c.data)
    else:
        os.symlink(c.data, c.host)
        made = True
    if not made:
        check(True, "skip: could not create a link here")
        c.done()
        return
    c.write(os.path.join(c.data, ".u-hermes-mirror"), MARK)
    code, out = c.run("--report")
    check(c.left(c.data).get("config.yaml") == CONFIG and c.left(c.data).get(".env") == ENV,
          "the stick's config and keys are untouched")
    check("就是这个 U 盘自己的数据" in out, "and [8] says why")
    if os.name == "nt":
        os.rmdir(c.host)
    else:
        os.unlink(c.host)
    c.done()


def test_it_never_prints_a_key():
    print("no file content in the output")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": "api_key: sk-secret-in-config\nterminal:\n  cwd: U-Hermes\n".encode("utf-8"),
              ".env.before-u-hermes": b"X_API_KEY=sk-secret-in-env\n"})
    code, out = c.run("--report")
    check("sk-" not in out and "longcat" not in out, "keys and config text stay out of it")
    c.done()


def test_a_failure_does_not_stop_the_launch():
    print("an unexpected error exits 0")
    r = subprocess.run([sys.executable, SCRIPT, os.path.join(tempfile.gettempdir(), "no-such-data"),
                        "--home"], capture_output=True)
    check(r.returncode == 0, "exit %d (%s)" % (r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()[-80:]))


if __name__ == "__main__":
    for fn in (test_the_tails_are_what_the_old_launchers_wrote,
               test_a_marked_copy_goes_and_the_machines_own_comes_back,
               test_a_marked_copy_on_a_machine_without_hermes_leaves_nothing,
               test_a_v041_copy_with_the_appended_block,
               test_a_copy_of_the_config_before_protect_config_rewrote_it,
               test_the_config_sidecar_backups_count_too,
               test_the_env_is_judged_on_its_own,
               test_an_unprovable_env_beside_a_proven_config_is_named,
               test_a_v041_copy_stashed_as_the_machines_own_is_not_put_back,
               test_an_unprovable_fingerprinted_stash_stays_put,
               test_a_copy_that_cannot_be_removed_keeps_its_marker,
               test_a_marker_does_not_take_a_env_the_stick_never_had,
               test_a_stash_without_a_marker_is_not_restored,
               test_the_users_own_machine_on_2026_09_26,
               test_whitespace_is_never_ours,
               test_a_machines_own_hermes_is_left_alone,
               test_an_empty_folder_the_owner_made_survives,
               test_nothing_there_means_nothing_created,
               test_the_mac_tail,
               test_a_junction_is_never_removed,
               test_a_junction_to_the_sticks_own_data_is_left_alone,
               test_it_never_prints_a_key,
               test_a_failure_does_not_stop_the_launch):
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("remove old host copy: all checks passed")

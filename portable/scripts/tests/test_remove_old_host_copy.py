"""The copy of the user's keys that v0.4.7 and earlier left on host PCs.

Up to v0.4.7 every launch copied data\\config.yaml and data\\.env -- the API
keys -- into %USERPROFILE%\\.hermes (Mac: ~/.hermes). v0.4.8 stopped, and
scripts/remove-old-host-copy.py takes away what was left. Every case here is
one three rounds of review found, reproduced, in an earlier version:

  * v0.3.5-v0.4.1 appended a platforms block to the copy when the config had
    no platforms: line, so it often did not match -- and the .env with the
    keys was only looked at if it did;
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

    def left(self, where=None, state=False):
        where = where or self.host
        found = {}
        if os.path.isdir(where):
            for dp, _dn, fn in os.walk(where):
                for n in fn:
                    p = os.path.join(dp, n)
                    with open(p, "rb") as f:
                        found[os.path.relpath(p, where).replace("\\", "/")] = f.read()
        if not state:
            found.pop(rc.PENDING, None)
        return found

    def state(self):
        path = os.path.join(self.host, rc.PENDING)
        return rc.read_pending(path) if os.path.exists(path) else None

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
    st = c.state()
    check(st is not None and st[0] == (".env",), "the state file keeps vouching for the .env, and only it (%s)" % (st,))
    check("删不掉" in out, "and the user is told which file")
    os.chmod(ro, stat.S_IWRITE | stat.S_IREAD)
    c.run()
    check(c.left() == {"config.yaml": OWN_CONFIG}, "the next run finishes the job (%s)" % sorted(c.left()))
    c.done()


def test_a_marker_does_not_take_a_env_the_stick_never_had():
    print("marker, but the stick has never had a .env: the machine's .env stays")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": OWN_ENV}, no_env=True)
    code, out = c.run("--report")
    check(c.left() == {".env": OWN_ENV}, "only the config goes (%s)" % sorted(c.left()))
    check("[!]" in out and ".env" in out.split("[!]", 1)[1] and "[OK]" not in out,
          "but the .env is named: another stick may have left it")
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


def _readonly(path):
    os.chmod(path, stat.S_IREAD)


def _writable(path):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def _ro_blocks_delete(c):
    probe = os.path.join(c.root, "ro-probe")
    c.write(probe, b"x")
    _readonly(probe)
    try:
        os.remove(probe)
        return False
    except OSError:
        _writable(probe)
        return True


def test_the_env_stashed_with_a_proven_config_is_not_put_back():
    print("config stash proven ours, .env stash beside it unprovable")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": CONFIG_BEFORE_PROTECT + V041_TAIL,
              ".env.before-u-hermes": OLD_ENV},
             backups={"config.yaml.good.1": CONFIG_BEFORE_PROTECT})
    code, out = c.run("--report")
    check(c.left() == {".env.before-u-hermes": OLD_ENV}, "the old key is not made live again (%s)" % sorted(c.left()))
    check(".env.before-u-hermes" in out and "放回原处" not in out and "[OK]" not in out, "it is reported instead")
    c.done()


def test_a_read_only_marker_is_still_cleared():
    print("a read-only marker is made writable and removed")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": OWN_CONFIG})
    _readonly(os.path.join(c.host, ".u-hermes-mirror"))
    c.run()
    check(c.left(state=True) == {"config.yaml": OWN_CONFIG}, "copies gone, the machine's config back (%s)"
          % sorted(c.left(state=True)))
    c.done()


def test_nothing_goes_back_while_the_marker_stays():
    print("the marker itself cannot be removed (held open): nothing is put back")
    if os.name != "nt":
        check(True, "skip: an open file blocks deletion only on Windows")
        return
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": OWN_CONFIG})
    with open(os.path.join(c.host, ".u-hermes-mirror"), "rb"):
        code, out = c.run()
    check(c.left() == {".u-hermes-mirror": MARK, "config.yaml.before-u-hermes": OWN_CONFIG},
          "copies gone, machine's config still set aside (%s)" % sorted(c.left()))
    check("放回原处" not in out and "删不掉" in out, "and the user is told")
    st = c.state()
    check(st is not None and st[0] == () and st[2], "the state file vouches for nothing and says a restore is due (%s)" % (st,))
    c.write(os.path.join(c.host, "config.yaml"), b"model: written-by-the-machine\n")
    os.remove(os.path.join(c.host, "config.yaml"))
    c.run()
    check(c.left(state=True) == {"config.yaml": OWN_CONFIG}, "the next run puts it back and clears up (%s)"
          % sorted(c.left(state=True)))
    c.done()


def test_a_marker_that_stays_does_not_vouch_for_new_files():
    print("the old marker cannot be removed; the machine writes a config; the next run")
    if os.name != "nt":
        check(True, "skip: an open file blocks deletion only on Windows")
        return
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV})
    with open(os.path.join(c.host, ".u-hermes-mirror"), "rb"):
        c.run()
        c.write(os.path.join(c.host, "config.yaml"), OWN_CONFIG)
        c.run()
    check(c.left().get("config.yaml") == OWN_CONFIG, "the machine's new config survives (%s)" % sorted(c.left()))
    c.done()


def test_a_restore_that_fails_is_tried_again():
    print("the machine's stash is held open when it should go back")
    if os.name != "nt":
        check(True, "skip: an open file blocks renaming only on Windows")
        return
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": OWN_CONFIG})
    with open(os.path.join(c.host, "config.yaml.before-u-hermes"), "rb"):
        code, out = c.run("--report")
    check("下次启动会再试" in out and "会自动放回" in out and "[OK]" not in out, "a retry is promised")
    c.run()
    check(c.left(state=True) == {"config.yaml": OWN_CONFIG}, "and kept (%s)" % sorted(c.left(state=True)))
    c.done()


def test_nothing_goes_back_while_a_proven_stash_is_stuck():
    print("a proven .env stash cannot be removed: the machine's config waits too")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": OWN_CONFIG, ".env.before-u-hermes": ENV})
    if not _ro_blocks_delete(c):
        check(True, "skip: this OS deletes read-only files anyway")
        c.done()
        return
    _readonly(os.path.join(c.host, ".env.before-u-hermes"))
    code, out = c.run()
    check("config.yaml" not in c.left() and c.state() is not None and c.state()[2],
          "nothing restored, a restore still due (%s)" % sorted(c.left()))
    _writable(os.path.join(c.host, ".env.before-u-hermes"))
    c.run()
    check(c.left() == {"config.yaml": OWN_CONFIG}, "the next run finishes (%s)" % sorted(c.left()))
    c.done()


def test_a_kept_marker_vouches_only_for_what_is_left():
    print("run 1 keeps the marker for a stuck .env; the machine writes a config; run 2")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV})
    if not _ro_blocks_delete(c):
        check(True, "skip: this OS deletes read-only files anyway")
        c.done()
        return
    _readonly(os.path.join(c.host, ".env"))
    c.run()
    c.write(os.path.join(c.host, "config.yaml"), OWN_CONFIG)
    _writable(os.path.join(c.host, ".env"))
    c.run()
    check(c.left() == {"config.yaml": OWN_CONFIG}, "the new config survives, the .env goes (%s)" % sorted(c.left()))
    c.done()


def test_the_old_block_alone_is_reported():
    print("a copy with the old launchers' block that no longer matches anything")
    c = Case({"config.yaml": b"model: long-gone\r\n" + V041_TAIL, ".env": OLD_ENV})
    code, out = c.run("--report")
    check(c.left() == {"config.yaml": b"model: long-gone\r\n" + V041_TAIL, ".env": OLD_ENV}, "nothing deleted")
    check("config.yaml" in out and ".env" in out and "[OK]" not in out, "both reported, PC not called clean")
    c.done()


def test_a_lone_marker_is_not_called_a_key_copy():
    print("only the marker is left")
    c = Case({".u-hermes-mirror": MARK})
    code, out = c.run()
    check(not os.path.exists(c.host), "gone, folder too")
    check("标记文件" in out and "密钥副本" not in out, "and described as what it was")
    c.done()


def test_the_report_counts_only_what_it_cannot_place():
    print("[8] with a stuck copy and a stash of the machine's own")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": OWN_CONFIG, "state.db": b"machine"})
    if not _ro_blocks_delete(c):
        check(True, "skip: this OS deletes read-only files anyway")
        c.done()
        return
    _readonly(os.path.join(c.host, ".env"))
    code, out = c.run("--report")
    check("其余 1 项" in out, "only state.db counts as unplaced (%s)" % out.strip()[-160:])
    check("别删" in out and "整个文件夹" not in out, "and the whole folder is not offered for deletion")
    _writable(os.path.join(c.host, ".env"))
    c.done()


def test_no_content_even_when_restoring_or_stuck():
    print("no file content when files are restored, stuck or flagged")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": b"K=sk-secret-stuck\n",
              "config.yaml.before-u-hermes": b"api_key: sk-secret-machine\n"})
    stuck = False
    if _ro_blocks_delete(c):
        _readonly(os.path.join(c.host, ".env"))
        stuck = True
    code, out1 = c.run("--report")
    if stuck:
        _writable(os.path.join(c.host, ".env"))
    code, out2 = c.run("--report")
    check("sk-" not in out1 + out2, "no key in any output")
    c.done()


def test_a_link_to_the_stick_spelled_differently_is_still_the_stick():
    print("~/.hermes linked to the stick's data under a different case")
    if os.name != "nt":
        check(True, "skip: case-insensitive paths are tested on Windows")
        return
    c = Case({})
    other_case = os.path.join(os.path.dirname(c.data), os.path.basename(c.data).upper())
    if not junction(c.host, other_case):
        check(True, "skip: could not create a junction here")
        c.done()
        return
    c.run("--report")
    check(c.left(c.data).get(".env") == ENV and c.left(c.data).get("config.yaml") == CONFIG,
          "the stick's files are untouched")
    os.rmdir(c.host)
    c.done()


def test_a_marked_env_alone_on_a_stick_without_one_is_named():
    print("marker and a .env, no config, and this stick has never had a .env")
    c = Case({".u-hermes-mirror": MARK, ".env": OWN_ENV}, no_env=True)
    code, out = c.run("--report")
    check(c.left() == {".env": OWN_ENV}, "kept (%s)" % sorted(c.left()))
    check("[!]" in out and ".env" in out.split("[!]", 1)[1], "and named")
    c.done()


def test_the_stick_is_recognised_however_its_path_is_spelled():
    print("the data dir given in another case, ~/.hermes linked to it")
    if os.name != "nt":
        check(True, "skip: case-insensitive paths are tested on Windows")
        return
    c = Case({})
    if not junction(c.host, c.data):
        check(True, "skip: could not create a junction here")
        c.done()
        return
    out = io.StringIO()
    with redirect_stdout(out):
        rc.main(["remove-old-host-copy.py", c.data.upper(), "--home", c.home])
    check(c.left(c.data).get(".env") == ENV and c.left(c.data).get("config.yaml") == CONFIG,
          "the stick's files are untouched")
    os.rmdir(c.host)
    c.done()


def test_what_was_flagged_is_still_flagged_next_time():
    print("a flagged file is reported on every later run, not just the first")
    for label, host, kw in (
            ("marked .env on a stick without one", {".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": OWN_ENV},
             {"no_env": True}),
            (".env stash beside a proven config stash",
             {".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV,
              "config.yaml.before-u-hermes": CONFIG_BEFORE_PROTECT, ".env.before-u-hermes": OLD_ENV},
             {"backups": {"config.yaml.good.1": CONFIG_BEFORE_PROTECT}}),
            (".env beside an unmarked copy proven ours", {"config.yaml": CONFIG + V041_TAIL, ".env": OLD_ENV}, {})):
        c = Case(host, **kw)
        c.run()
        code, out = c.run("--report")
        check("[!]" in out and "[OK]" not in out and "别删" not in out, "%s: still reported on the next run" % label)
        for n in list(c.left()):
            os.remove(os.path.join(c.host, n))
        code, out = c.run("--report")
        check(not os.path.exists(c.host), "%s: once the user deletes it, the state goes too" % label)
        c.done()


def test_a_failed_env_is_tried_once_per_run():
    print("a stuck .env is reported once")
    c = Case({".u-hermes-mirror": MARK, "config.yaml": CONFIG, ".env": ENV})
    if not _ro_blocks_delete(c):
        check(True, "skip: this OS deletes read-only files anyway")
        c.done()
        return
    _readonly(os.path.join(c.host, ".env"))
    code, out = c.run()
    check(out.count("删不掉") == 1, "one line for it (%d)" % out.count("删不掉"))
    _writable(os.path.join(c.host, ".env"))
    c.done()


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
               test_the_env_stashed_with_a_proven_config_is_not_put_back,
               test_a_read_only_marker_is_still_cleared,
               test_nothing_goes_back_while_the_marker_stays,
               test_a_marker_that_stays_does_not_vouch_for_new_files,
               test_a_restore_that_fails_is_tried_again,
               test_what_was_flagged_is_still_flagged_next_time,
               test_a_failed_env_is_tried_once_per_run,
               test_nothing_goes_back_while_a_proven_stash_is_stuck,
               test_a_kept_marker_vouches_only_for_what_is_left,
               test_the_old_block_alone_is_reported,
               test_a_lone_marker_is_not_called_a_key_copy,
               test_the_report_counts_only_what_it_cannot_place,
               test_no_content_even_when_restoring_or_stuck,
               test_a_link_to_the_stick_spelled_differently_is_still_the_stick,
               test_a_marked_env_alone_on_a_stick_without_one_is_named,
               test_the_stick_is_recognised_however_its_path_is_spelled,
               test_a_failure_does_not_stop_the_launch):
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("remove old host copy: all checks passed")

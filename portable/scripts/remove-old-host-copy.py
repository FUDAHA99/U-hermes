# -*- coding: utf-8 -*-
"""Take away the copy of the user's config and API keys that U-Hermes v0.4.7
and earlier left in the host's %USERPROFILE%\\.hermes (Mac: ~/.hermes).

Those versions copied data\\config.yaml and data\\.env there on every launch,
as "a fallback for any component started without our env". No such component
exists -- on Windows the engine falls back to %LOCALAPPDATA%\\hermes, never
~/.hermes, and the Web UI hands HERMES_HOME to every Hermes process it
starts -- so v0.4.8 stopped copying. What they left behind is still there on
the machines the stick ran on, and on a machine that is not yours it is your
keys in someone else's home folder. This removes it.

    Windows v0.3.5-v0.4.1   copied config.yaml and .env with no marker,
                            overwriting whatever the machine had, appended a
                            platforms block to the config copy when it had
                            none (WINDOWS_TAIL), and never took them away;
    Windows v0.4.2-v0.4.7   wrote the marker .u-hermes-mirror, moved the
                            machine's own config.yaml aside as
                            *.before-u-hermes (and its .env, when the config
                            differed), and undid it all on a clean exit --
                            which [X], a pulled stick or Ctrl+C then Y never
                            reached;
    Mac, every version      cp -f over ~/.hermes/config.yaml and .env with no
                            marker and no stash, plus MAC_TAIL, and never
                            took them away.

Deleting someone else's file is worse than leaving ours, so every doubt
resolves to "leave it -- and say so, every time":

  * Nothing is touched when ~/.hermes is, or is inside, the stick's own data
    folder (a junction someone made on purpose): every file there would
    match "a file this stick has had", and they are the live ones. Compared
    by file identity, not by spelling.
  * With the old marker, config.yaml is ours by the marker's own promise --
    the rule the old versions' own cleanup used, whichever stick wrote it --
    and so is .env, provided this stick has or had one: those launchers
    copied .env only `if exist`. A marked .env this stick cannot account for
    is reported instead.
  * Otherwise a file is ours only if its bytes are exactly a file this stick
    has had: data\\config.yaml or data\\.env as they are now, the earlier
    ones the stick keeps (data\\config.yaml.*, data\\.env.*, anything under
    data\\backups), or one of those configs plus one of the two tails.
    Byte-equality is what makes the match certain.
  * .env is judged on its own. It holds the keys, and whether the config
    beside it still matches -- the stick's config changes on every save --
    says nothing about it.
  * A *.before-u-hermes file is checked before it is put back: v0.4.2-v0.4.7
    stashed a v0.4.1 copy as "the machine's own" whenever the stick's config
    had changed in between. A proven one is removed. The .env stashed beside
    a proven config stash, and both files of a pair whose config carries
    U-Hermes' fingerprints, stay where they are and are reported -- putting
    them back would make the stick owner's old keys live.
  * Nothing goes back while anything marked for deletion is still there, or
    while the old marker stands: a marker left over a restored file would
    have the next run delete it on the marker's word.
  * A file with nothing but whitespace is never ours by byte-equality: it
    holds nothing, and it may be the machine's.
  * The folder goes only if this run cleared something, it is left empty,
    and it is a real folder -- rd on a junction removes the link, whatever
    is behind it.

What has to outlive one run -- files still to delete (read-only, in use),
files to keep reporting (the proof that flagged them may be gone by the next
run), and whether the machine's own files are still waiting to go back -- is
written to .u-hermes-pending. A new name on purpose: the old launchers read
.u-hermes-mirror as "config.yaml and .env here are ours" and would act on it.
Every file it names carries the hash of the bytes it was about, so a later
file of the same name -- the machine's own Hermes writing a config -- is
judged afresh, never deleted or reported on its word; it is rewritten whole
on every run. An old marker that cannot be removed is recorded by mtime and
size: while it stands, the state file, not the marker, says what may be
deleted; a marker it does not know is a new one, left by an older version
that ran here since, and is taken at its word like any other.

What cannot be proven is reported, never deleted: a config.yaml carrying
U-Hermes' fingerprints or the old launchers' platforms block, any .env left
beside a config that was ours or looks it, and a marked .env this stick
cannot account for. Never prints the contents of any file. Always exits 0:
a failed cleanup must not stop the launch.

Run:  python remove-old-host-copy.py <data_dir> [--home DIR] [--report]
"""
import hashlib
import os
import stat
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARKER = ".u-hermes-mirror"
PENDING = ".u-hermes-pending"
STASH = ".before-u-hermes"
OURS = ("config.yaml", ".env")

# What Windows v0.3.5-v0.4.1 appended with `echo` under chcp 65001 when the
# copy had no top-level platforms: line, and what every Mac-Start.command
# appended with a heredoc after each cp -f. Both checked byte for byte
# against the old launchers run for real.
WINDOWS_TAIL = (b"\r\nplatforms:\r\n  api_server:\r\n    extra:\r\n      port: 8642\r\n"
                b"      host: 127.0.0.1\r\n    enabled: true\r\n    key: ''\r\n"
                b"    cors_origins: '*'\r\n")
MAC_TAIL = WINDOWS_TAIL.replace(b"\r\n", b"\n")

# Grounds for reporting a config (and for not putting a stash back), never
# for deleting one. The block itself, without the blank line before it,
# counts as well.
FINGERPRINTS = ("U-Hermes".encode("utf-8"), b"skills-cn", WINDOWS_TAIL.strip(), MAC_TAIL.strip())

MAX_BYTES = 4 * 1024 * 1024
PENDING_HEADER = "u-hermes pending"


def _read(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _meaningful(data):
    return data is not None and data.strip() != b""


def _fingerprinted(path):
    data = _read(path)
    return _meaningful(data) and any(f in data for f in FINGERPRINTS)


def _inside(path, root):
    """True if `path` is `root` or lies under it.

    By file identity (st_dev/st_ino, the file index on Windows), not by
    comparing strings: a link can name the same folder with different case
    (macOS keeps case in realpath), an 8.3 short name, or a UNC path.
    """
    try:
        if not os.path.exists(root):
            return False
        p = os.path.realpath(path)
        while True:
            if os.path.exists(p) and os.path.samefile(p, root):
                return True
            parent = os.path.dirname(p)
            if parent == p:
                return False
            p = parent
    except (OSError, ValueError):
        return False


def stick_history(data_dir):
    """(configs, envs, had_env): every config.yaml and .env this stick has,
    or had, and whether it has or had a .env file at all."""
    configs, envs = set(), set()
    had_env = [False]

    def add(path, name):
        if name.startswith(".env"):
            had_env[0] = True
        data = _read(path)
        if not _meaningful(data):
            return
        if name.startswith("config.yaml"):
            configs.update((data, data + WINDOWS_TAIL, data + MAC_TAIL))
        elif name.startswith(".env"):
            envs.add(data)

    try:
        for name in os.listdir(data_dir):
            if name.startswith(("config.yaml", ".env")):
                add(os.path.join(data_dir, name), name)
    except OSError:
        pass
    for root, _dirs, files in os.walk(os.path.join(data_dir, "backups")):
        for name in files:
            add(os.path.join(root, name), name)
    return configs, envs, had_env[0]


def _digest(path):
    data = _read(path)
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def _marker_id(path):
    try:
        st = os.stat(path)
        return "%d-%d" % (st.st_mtime_ns, st.st_size)
    except OSError:
        return ""


class State(object):
    """What .u-hermes-pending remembers. Every file it names carries the hash
    of the bytes it was about: a later file of the same name -- the
    machine's own Hermes writing a config, the user replacing a key -- is not
    the one it meant, and is judged afresh."""

    def __init__(self):
        self.delete, self.report = {}, {}
        self.restore = False
        self.marker = ""   # identity of an old marker that could not be removed

    @classmethod
    def read(cls, path):
        data = _read(path)
        if data is None:
            return None
        lines = [l.strip() for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
        if not lines or lines[0] != PENDING_HEADER:
            return None
        st = cls()
        for line in lines[1:]:
            parts = line.split(" ")
            if parts[0] == "delete" and len(parts) == 3 and parts[1] in OURS:
                st.delete[parts[1]] = parts[2]
            elif parts[0] == "report" and len(parts) == 3 and parts[1] in OURS + tuple(n + STASH for n in OURS):
                st.report[parts[1]] = parts[2]
            elif parts == ["restore"]:
                st.restore = True
            elif parts[0] == "marker" and len(parts) == 2:
                st.marker = parts[1]
        return st

    def write(self, path):
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(PENDING_HEADER + "\n")
                f.write("".join("delete %s %s\n" % kv for kv in sorted(self.delete.items())))
                f.write("".join("report %s %s\n" % kv for kv in sorted(self.report.items())))
                f.write("restore\n" if self.restore else "")
                f.write("marker %s\n" % self.marker if self.marker else "")
            return True
        except OSError:
            return False

    def empty(self):
        return not (self.delete or self.report or self.restore or self.marker)


def read_pending(path):
    """(delete, report, restore) names from a state file, or None -- for tests."""
    st = State.read(path)
    return None if st is None else (tuple(sorted(st.delete)), tuple(sorted(st.report)), st.restore)


def _is_link(path):
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def _remove(path, force=False):
    try:
        if force:
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        os.remove(path)
        return True
    except OSError:
        return False


class Outcome(object):
    def __init__(self, host):
        self.host = host
        self.removed, self.restored, self.failed, self.suspicious = [], [], [], []
        self.awaiting = []          # stashes of the machine's own, still to go back
        self.state_cleared = False  # the old marker or our state file went
        self.skipped_reason = ""
        self.left = False


def clean(data_dir, home):
    out = Outcome(os.path.join(home, ".hermes"))
    host = out.host
    if not os.path.isdir(host):
        return out
    if _inside(host, data_dir) or _inside(data_dir, host):
        out.skipped_reason = "stick"
        out.left = True
        return out
    configs, envs, had_env = stick_history(data_dir)
    pool = {"config.yaml": configs, ".env": envs}

    def at(name):
        return os.path.join(host, name)

    def ours(name, as_kind):
        path = at(name)
        if _inside(path, data_dir):
            return False
        data = _read(path)
        return _meaningful(data) and data in pool[as_kind]

    def remove(name):
        if _remove(at(name)):
            out.removed.append(name)
        else:
            out.failed.append(name)

    flagged = []

    def flag(name):
        if name not in flagged and os.path.exists(at(name)) and _meaningful(_read(at(name))):
            flagged.append(name)

    old_marker = os.path.exists(at(MARKER))
    state = State.read(at(PENDING)) if os.path.exists(at(PENDING)) else None
    fresh_marker = old_marker and (state is None or state.marker != _marker_id(at(MARKER)))
    # A marker the state file does not know about is new: an older version ran
    # here after this script last did. It is taken at its word, as always.
    vouched = dict((n, None) for n in OURS) if fresh_marker else {}
    restore_due = fresh_marker
    if state is not None:
        for name, digest in state.delete.items():
            vouched.setdefault(name, digest)
        restore_due = restore_due or state.restore
        for name, digest in state.report.items():
            if os.path.exists(at(name)) and _digest(at(name)) == digest:
                flag(name)

    # 1. Removals only.
    for name, digest in sorted(vouched.items()):
        if not os.path.exists(at(name)) or _inside(at(name), data_dir):
            continue
        if digest is not None and _digest(at(name)) != digest:
            continue  # not the file that was vouched for: judged afresh below
        if name == ".env" and not had_env and digest is None:
            # Those launchers copied .env only `if exist`, and this stick has
            # none -- but another stick may have left it. Not ours to delete;
            # not something to pass over in silence either.
            flag(name)
            continue
        remove(name)

    config_stash = "config.yaml" + STASH
    config_stash_ours = os.path.exists(at(config_stash)) and ours(config_stash, "config.yaml")
    held_pair = restore_due and not config_stash_ours and _fingerprinted(at(config_stash))
    to_restore = []
    for name in OURS:
        stash = name + STASH
        if not os.path.exists(at(stash)):
            continue
        if ours(stash, name):
            remove(stash)
        elif stash in flagged:
            pass
        elif (held_pair or (name == ".env" and config_stash_ours)) and _meaningful(_read(at(stash))):
            # Stashed together with a config that is ours, or looks it: the
            # pair was a U-Hermes copy, not the machine's own files. (An empty
            # one holds nothing either way, and goes back like any other.)
            flag(stash)
        elif restore_due and not os.path.exists(at(name)):
            to_restore.append(name)

    for name in OURS:
        if os.path.exists(at(name)) and name not in out.removed and name not in out.failed \
                and name not in flagged and ours(name, name):
            remove(name)

    # 2. What cannot be proven, but must not pass in silence.
    config_was_ours = any(n.startswith("config.yaml") for n in out.removed)
    for name in ("config.yaml", config_stash):
        if name not in out.failed and os.path.exists(at(name)) and _fingerprinted(at(name)):
            flag(name)
    if config_was_ours or any(n.startswith("config.yaml") for n in flagged):
        if ".env" not in out.failed:
            flag(".env")
        if not restore_due:
            # Under a marker an unpaired .env stash is the machine's own,
            # waiting to go back; without one nobody vouches for it.
            flag(".env" + STASH)

    # 3. The state for the next run is written before anything irreversible
    #    happens to the old marker; then the marker; then -- only with no
    #    marker standing and nothing left to delete -- the machine's own files
    #    go back; then the state is written again as it ended up. Every run
    #    rewrites it whole, so nothing it says outlives what it was about.
    def state_now(marker_id):
        st = State()
        st.delete = dict((n, _digest(at(n))) for n in out.failed if n in OURS and os.path.exists(at(n)))
        st.report = dict((n, _digest(at(n))) for n in flagged if os.path.exists(at(n)))
        waiting = [n for n in OURS if os.path.exists(at(n + STASH)) and n + STASH not in flagged]
        st.restore = restore_due and bool(waiting)
        st.marker = marker_id
        return st

    if old_marker:
        state_now(_marker_id(at(MARKER))).write(at(PENDING))
        if _remove(at(MARKER), force=True):
            out.state_cleared = True
        else:
            out.failed.append(MARKER)
    stuck_marker = _marker_id(at(MARKER)) if os.path.exists(at(MARKER)) else ""
    if not out.failed:
        for name in to_restore:
            try:
                os.replace(at(name + STASH), at(name))
                out.restored.append(name)
            except OSError:
                out.failed.append(name + STASH)
    final = state_now(stuck_marker)
    if final.restore:
        out.awaiting = [n + STASH for n in OURS if os.path.exists(at(n + STASH)) and n + STASH not in flagged]
    if not final.empty():
        final.write(at(PENDING))
    elif os.path.exists(at(PENDING)):
        if _remove(at(PENDING), force=True):
            out.state_cleared = True

    out.suspicious = [n for n in flagged if os.path.exists(at(n))]
    if (out.removed or out.state_cleared) and not _is_link(host):
        try:
            os.rmdir(host)
        except OSError:
            pass
    out.left = os.path.isdir(host)
    return out


def main(argv):
    args = argv[1:]
    report = "--report" in args
    home = None
    if "--home" in args:
        i = args.index("--home")
        home = args[i + 1]
        del args[i:i + 2]
    args = [a for a in args if a != "--report"]
    data_dir = args[0] if args else "data"
    if home is None:
        home = os.environ.get("USERPROFILE") if os.name == "nt" else os.environ.get("HOME")
        home = home or os.path.expanduser("~")

    out = clean(data_dir, home)
    host = out.host
    if out.removed:
        print("  [i] 已删掉旧版本 U-Hermes 留在这台电脑上的配置和密钥副本：")
        print("      %s（%s）" % (host, "、".join(out.removed)))
    elif out.state_cleared and not out.restored:
        print("  [i] 已清掉旧版本 U-Hermes 留在 %s 的标记文件。" % host)
    if out.restored:
        print("  [i] 旧版本挪开的、这台电脑自己的 Hermes 文件已放回原处：%s" % "、".join(out.restored))
    for name in out.failed:
        print("  [!] %s 删不掉或放不回去（可能是只读，或者正被占用），下次启动会再试。"
              % os.path.join(host, name))
    if out.suspicious:
        print("  [!] %s 里的 %s 看起来也是 U-Hermes 旧版本留下的，里面可能有密钥，"
              % (host, "、".join(out.suspicious)))
        print("      但证明不了是这个 U 盘的，所以没有自动删。")
        print("      确认这台电脑自己的 Hermes 不在用它们，可以手动删掉。")
    if report:
        if out.skipped_reason == "stick":
            print("  [i] %s 就是这个 U 盘自己的数据文件夹（链接过去的），没有动。" % host)
        elif not (out.removed or out.state_cleared or out.failed or out.suspicious or out.awaiting):
            print("  [OK] 本机上没有认得出是 U-Hermes 旧版本留下的副本。")
        if out.left and out.skipped_reason != "stick":
            try:
                names = os.listdir(host)
            except OSError:
                names = []
            known = set(out.suspicious) | set(out.failed) | set(out.awaiting) | {MARKER, PENDING}
            rest = [n for n in names if n not in known and not n.endswith(STASH)]
            unknown_stash = [n for n in names if n.endswith(STASH) and n not in known]
            if out.awaiting:
                print("  [i] %s 是旧版本挪开的这台电脑自己的文件，等上面的问题解决后会自动放回，别删。"
                      % "、".join(out.awaiting))
            if unknown_stash:
                print("  [i] %s 是旧版本挪开的文件，认不出是谁的，没有动。" % "、".join(unknown_stash))
            if rest:
                print("  [i] %s 里其余 %d 项认不出是 U-Hermes 放的，没有动；" % (host, len(rest)))
                print("      如果这台电脑自己装过 Hermes，那些多半是它的数据。")
                if not (out.failed or out.suspicious or out.awaiting or unknown_stash):
                    print("      确认不需要的话，可以手动删掉整个文件夹。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never block a launch on a cleanup
        print("  [i] 清理旧版本留下的副本时出错，已跳过（%s）" % exc.__class__.__name__)
        sys.exit(0)

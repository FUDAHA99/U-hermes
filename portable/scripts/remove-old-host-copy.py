# -*- coding: utf-8 -*-
"""Take away the copy of the user's config and API keys that U-Hermes v0.4.7
and earlier left in the host's %USERPROFILE%\\.hermes (Mac: ~/.hermes).

Those versions copied data\\config.yaml and data\\.env there on every launch,
as "a fallback for any component started without our env". No such component
exists -- on Windows the engine falls back to %LOCALAPPDATA%\\hermes, never
~/.hermes, and the Web UI hands HERMES_HOME to every Hermes process it
starts -- so v0.4.8 stopped copying. What they left behind is still there on
the machines the stick ran on, and on a machine that is not yours it is your
keys in someone else's home folder. This removes what it can prove is ours.

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

It keeps no state of its own. Two review rounds went into a state file that
remembered failures, flags and pending restores across runs; each round
found the state itself going wrong in new ways (lost entries, stale ones,
entries following the wrong file). Every run now starts from what is on disk
and does only what it can prove safe in that run:

  * Nothing is touched when ~/.hermes is, or is inside, the stick's own data
    folder (a junction someone made on purpose): every file there would
    match "a file this stick has had", and they are the live ones. Compared
    by file identity, not by spelling.
  * With the old marker, this finishes what the old version's own clean exit
    would have done -- the behaviour its users already had: config.yaml and
    .env are ours by the marker's promise, the machine's own files go back,
    the marker goes. .env only if this stick has or had one, since those
    launchers copied it only `if exist`. Three guards on top: a stash that is
    provably ours is removed, not put back; a stash pair whose config carries
    U-Hermes' fingerprints stays where it is and is reported; and the marker
    goes before anything goes back -- if it cannot, or if any copy cannot be
    removed, nothing goes back and the marker stays, so the next start runs
    the same steps again.
  * Without the marker a file is ours only if its bytes are exactly a file
    this stick has had: data\\config.yaml or data\\.env as they are now, the
    earlier ones the stick keeps (data\\config.yaml.*, data\\.env.*, anything
    under data\\backups), or one of those configs plus one of the two tails.
    .env is judged on its own.
  * A file with nothing but whitespace is never ours by byte-equality: it
    holds nothing, and it may be the machine's.
  * The folder goes only if this run removed something, it is left empty,
    and it is a real folder -- rd on a junction removes the link, whatever
    is behind it.

What cannot be proven is reported, never deleted: a config.yaml or config
stash carrying U-Hermes' fingerprints or the old launchers' platforms block,
and any .env left beside a config that was ours or looks it. That is decided
afresh each run, so a warning can be given once and not again (the config
that justified it is gone by then). The report never calls the PC clean: it
says what it removed and what it could not place.

Known limit, the old versions' own: while a marker that could not be removed
stands, it still vouches for config.yaml and .env -- including ones written
after it, as the old clean exit would have treated them.

Never prints the contents of any file. Always exits 0: a failed cleanup must
not stop the launch.

Run:  python remove-old-host-copy.py <data_dir> [--home DIR] [--report]
"""
import os
import stat
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARKER = ".u-hermes-mirror"
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
        self.not_restored = []      # the machine's own stash, left because the marker had to stay
        self.restore_failed = []    # the machine's own stash that could not be renamed back
        self.conflict = []          # the machine's own stash whose name is taken again
        self.marker_removed = False
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

    def kind(name):
        return name[:-len(STASH)] if name.endswith(STASH) else name

    def ours(name):
        path = at(name)
        if _inside(path, data_dir):
            return False
        data = _read(path)
        return _meaningful(data) and data in pool[kind(name)]

    def remove(name):
        if _remove(at(name), force=True):
            out.removed.append(name)
        else:
            out.failed.append(name)

    def flag(name):
        if name not in out.suspicious and name not in out.failed and os.path.exists(at(name)) \
                and _meaningful(_read(at(name))):
            out.suspicious.append(name)

    config_stash, env_stash = "config.yaml" + STASH, ".env" + STASH
    proven = set()   # files shown to be ours this run, removed or not

    # 1. The old marker: finish what that version's clean exit would have done.
    marked = os.path.exists(at(MARKER))
    to_restore = []
    if marked:
        for name in OURS:
            if not os.path.exists(at(name)) or _inside(at(name), data_dir):
                continue
            if name == ".env" and not had_env and not ours(name):
                # Copied only `if exist`, and this stick has none -- another
                # stick may have left it. Not ours to delete; not to be
                # passed over in silence either.
                flag(name)
                continue
            proven.add(name)
            remove(name)
        stash_is_ours = os.path.exists(at(config_stash)) and ours(config_stash)
        held_pair = not stash_is_ours and _fingerprinted(at(config_stash))
        for name in OURS:
            stash = name + STASH
            if not os.path.exists(at(stash)):
                continue
            if ours(stash):
                proven.add(stash)
                remove(stash)
            elif (held_pair or (name == ".env" and stash_is_ours)) and _meaningful(_read(at(stash))):
                # Stashed with a config that is ours, or looks it: the pair
                # was a U-Hermes copy. Putting it back would make old keys live.
                flag(stash)
            elif not os.path.exists(at(name)):
                to_restore.append(name)
            else:
                out.conflict.append(stash)
        # The marker goes first, and only if nothing it vouches for is left:
        # a marker over a restored file would have the next run delete it.
        if not out.failed and _remove(at(MARKER), force=True):
            out.marker_removed = True
            for name in to_restore:
                try:
                    os.replace(at(name + STASH), at(name))
                    out.restored.append(name)
                except OSError:
                    out.restore_failed.append(name + STASH)
        else:
            if os.path.exists(at(MARKER)) and not out.failed:
                out.failed.append(MARKER)
            # Every stash of the machine's own waits for the next run -- also
            # one whose name is still taken by a copy that could not go.
            out.not_restored = [n + STASH for n in to_restore] + out.conflict
            out.conflict = []

    # 2. Anything provably ours, marker or not.
    settled = set(out.removed) | set(out.failed) | set(out.suspicious) | set(out.not_restored) \
        | set(out.restore_failed) | set(out.conflict)
    for name in OURS + (config_stash, env_stash):
        if name in settled:
            continue
        if os.path.exists(at(name)) and ours(name):
            proven.add(name)
            remove(name)

    # 3. What cannot be proven, but must not pass in silence -- decided
    #    afresh each run.
    for name in ("config.yaml", config_stash):
        if name not in proven and os.path.exists(at(name)) and _fingerprinted(at(name)):
            flag(name)
    config_ours_or_like = any(n.startswith("config.yaml") for n in proven) \
        or any(n.startswith("config.yaml") for n in out.suspicious)
    if config_ours_or_like:
        if ".env" not in proven and ".env" not in out.restored:
            flag(".env")
        if not marked and env_stash not in proven:
            # Under the marker an unpaired stash is the machine's own; the
            # pairs that are not were settled in step 1.
            flag(env_stash)

    if (out.removed or out.marker_removed) and not _is_link(host):
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
    copies = [n for n in out.removed if n != MARKER]
    if copies:
        print("  [i] 已删掉旧版本 U-Hermes 留在这台电脑上的配置和密钥副本：")
        print("      %s（%s）" % (host, "、".join(copies)))
    elif out.marker_removed and not out.restored:
        print("  [i] 已清掉旧版本 U-Hermes 留在 %s 的标记文件。" % host)
    if out.restored:
        print("  [i] 旧版本挪开的、这台电脑自己的 Hermes 文件已放回原处：%s" % "、".join(out.restored))
    for name in out.failed:
        print("  [!] %s 删不掉（可能是只读，或者正被占用），下次启动会再试。" % os.path.join(host, name))
    for name in out.not_restored:
        print("  [i] %s 是旧版本挪开的这台电脑自己的文件，等上面的文件删掉后会自动放回，别删。"
              % os.path.join(host, name))
    for name in out.conflict:
        print("  [i] %s 是旧版本挪开的这台电脑自己的文件，但原来的位置已经有同名文件，没有放回；"
              % os.path.join(host, name))
        print("      要用哪一份，请自己决定。")
    for name in out.restore_failed:
        print("  [!] %s 是旧版本挪开的这台电脑自己的文件，没能放回原处（可能正被占用）。"
              % os.path.join(host, name))
        print("      请把它改名为 %s。" % name[:-len(STASH)])
    if out.suspicious:
        print("  [!] %s 里的 %s 看起来也是 U-Hermes 旧版本留下的，里面可能有密钥，"
              % (host, "、".join(out.suspicious)))
        print("      但证明不了，所以没有自动删。确认这台电脑自己的 Hermes 不在用它们，可以手动删掉。")
    if report:
        if out.skipped_reason == "stick":
            print("  [i] %s 就是这个 U 盘自己的数据文件夹（链接过去的），没有动。" % host)
            return 0
        if not (copies or out.marker_removed or out.failed or out.suspicious or out.restore_failed):
            print("  [i] 没有找到能确认是 U-Hermes 旧版本留下的副本。")
        if out.left:
            try:
                names = os.listdir(host)
            except OSError:
                names = []
            placed = set(out.suspicious) | set(out.failed) | set(out.not_restored) | set(out.restore_failed) \
                | set(out.conflict) | {MARKER}
            rest = [n for n in names if n not in placed]
            if rest:
                print("  [i] %s 里其余 %d 项没有动，认不出是谁的。" % (host, len(rest)))
                print("      如果这台电脑自己装过 Hermes，那些多半是它的数据；如果你用 U-Hermes 旧版本")
                print("      在这台电脑上跑过，里面的 config.yaml 和 .env 也可能是你当时的配置和密钥。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never block a launch on a cleanup
        print("  [i] 清理旧版本留下的副本时出错，已跳过（%s）" % exc.__class__.__name__)
        sys.exit(0)

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
resolves to "leave it -- and say so":

  * Nothing is touched when ~/.hermes is, or is inside, the stick's own data
    folder (a junction someone made on purpose): every file there would
    match "a file this stick has had", and they are the live ones. Compared
    by file identity, not by spelling.
  * With the marker, config.yaml is ours by the marker's own promise -- the
    rule the old versions' own cleanup used, whichever stick wrote it -- and
    so is .env, provided this stick has or had one: those launchers copied
    .env only `if exist`. A marked .env this stick cannot account for is
    reported instead.
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
    U-Hermes' fingerprints, are left where they are and reported -- putting
    them back would make the stick owner's old keys live.
  * Removals first, then the marker, then -- only once the marker is gone --
    the machine's own files go back: a marker left standing over a restored
    file would have the next run delete it on the marker's word. If anything
    cannot be removed (read-only, in use), the marker stays, rewritten to
    vouch only for what is still there, and nothing goes back until a later
    start finishes the job.
  * A file with nothing but whitespace is never ours by byte-equality: it
    holds nothing, and it may be the machine's.
  * The folder goes only if this run removed something, it is left empty,
    and it is a real folder -- rd on a junction removes the link, whatever
    is behind it.

What cannot be proven is reported, never deleted: a config.yaml carrying
U-Hermes' fingerprints or the old launchers' platforms block, and any .env
left beside a config that was ours or looks it. Never prints the contents of any file. Always exits 0: a failed
cleanup must not stop the launch.

Run:  python remove-old-host-copy.py <data_dir> [--home DIR] [--report]
"""
import os
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

# Only ever grounds for reporting (and for not putting a stash back), never
# for deleting.
FINGERPRINTS = ("U-Hermes".encode("utf-8"), b"skills-cn")

MAX_BYTES = 4 * 1024 * 1024

# First line of a marker this script had to keep; the lines after it name the
# files it still vouches for.
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


# The block itself, without the blank line before it, also marks a config as
# one of ours -- enough to report it, never enough to delete it.
TAIL_BLOCKS = (WINDOWS_TAIL.strip(), MAC_TAIL.strip())


def _fingerprinted(path):
    data = _read(path)
    return _meaningful(data) and any(f in data for f in FINGERPRINTS + TAIL_BLOCKS)


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


def _marker_names(path):
    """What a marker vouches for. The old versions wrote "u-hermes" and
    meant both files; one this script had to keep lists what is still due."""
    data = _read(path) or b""
    lines = [l.strip() for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
    if lines and lines[0] == PENDING_HEADER:
        return tuple(n for n in lines[1:] if n in OURS)
    return OURS


def _keep_marker(path, names):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(PENDING_HEADER + "\n" + "".join(n + "\n" for n in names))
    except OSError:
        pass  # the old marker stays as it was, vouching for more -- the next run retries


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


def _remove(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


class Outcome(object):
    def __init__(self, host):
        self.host = host
        self.removed, self.restored, self.failed, self.suspicious = [], [], [], []
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

    def flag(name):
        if name not in out.suspicious and os.path.exists(at(name)) and _meaningful(_read(at(name))):
            out.suspicious.append(name)

    # 1. Removals only. Nothing is put back until the marker is gone: a
    #    marker left standing over a restored file would have the next run
    #    delete the machine's own config on its word.
    marked = os.path.exists(at(MARKER))
    vouched = _marker_names(at(MARKER)) if marked else ()
    for name in vouched:
        if not os.path.exists(at(name)) or _inside(at(name), data_dir):
            continue
        if name == ".env" and not had_env:
            # Those launchers copied .env only `if exist`, and this stick has
            # none -- but another stick may have left it. Not ours to delete;
            # not something to pass over in silence either.
            flag(name)
            continue
        remove(name)

    config_stash = "config.yaml" + STASH
    config_stash_ours = os.path.exists(at(config_stash)) and ours(config_stash, "config.yaml")
    held_pair = marked and not config_stash_ours and _fingerprinted(at(config_stash))
    to_restore = []
    for name in OURS:
        stash = name + STASH
        if not os.path.exists(at(stash)):
            continue
        if ours(stash, name):
            remove(stash)
        elif (held_pair or (name == ".env" and config_stash_ours)) and _meaningful(_read(at(stash))):
            # Stashed together with a config that is ours, or looks it: the
            # pair was a U-Hermes copy, not the machine's own files. (An empty
            # one holds nothing either way, and goes back like any other.)
            flag(stash)
        elif marked and not os.path.exists(at(name)):
            to_restore.append(name)

    for name in OURS:
        if os.path.exists(at(name)) and name not in out.removed and ours(name, name):
            remove(name)

    # 2. The marker, then -- only once it is gone -- the machine's own files.
    if marked:
        if out.failed:
            # The next start tries again, vouching only for what is left.
            _keep_marker(at(MARKER), [n for n in out.failed if n in OURS])
        elif _remove(at(MARKER)):
            out.marker_removed = True
            for name in to_restore:
                try:
                    os.replace(at(name + STASH), at(name))
                    out.restored.append(name)
                except OSError:
                    out.failed.append(name + STASH)
        else:
            out.failed.append(MARKER)

    # 3. What cannot be proven, but must not pass in silence.
    config_was_ours = any(n.startswith("config.yaml") for n in out.removed)
    for name in ("config.yaml", config_stash):
        if os.path.exists(at(name)) and _fingerprinted(at(name)):
            flag(name)
    if config_was_ours or any(n.startswith("config.yaml") for n in out.suspicious):
        for name in (".env", ".env" + STASH):
            if name not in out.failed and name not in out.restored:
                flag(name)

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
    if out.removed:
        print("  [i] 已删掉旧版本 U-Hermes 留在这台电脑上的配置和密钥副本：")
        print("      %s（%s）" % (host, "、".join(out.removed)))
    elif out.marker_removed:
        print("  [i] 已清掉旧版本 U-Hermes 留在 %s 的标记文件。" % host)
    if out.restored:
        print("  [i] 旧版本挪开的、这台电脑自己的 Hermes 文件已放回原处：%s" % "、".join(out.restored))
    for name in out.failed:
        print("  [!] %s 删不掉或放不回去（可能是只读，或者正被占用），下次启动会再试。"
              % os.path.join(host, name))
    if out.suspicious:
        print("  [!] %s 里的 %s 看起来也是 U-Hermes 旧版本留下的，里面可能有密钥，"
              % (host, "、".join(out.suspicious)))
        print("      但和这个 U 盘现在或以前的配置、密钥都对不上，所以没有自动删。")
        print("      确认这台电脑自己的 Hermes 不在用它们，可以手动删掉。")
    if report:
        if out.skipped_reason == "stick":
            print("  [i] %s 就是这个 U 盘自己的数据文件夹（链接过去的），没有动。" % host)
        elif not (out.removed or out.marker_removed or out.failed or out.suspicious):
            print("  [OK] 本机上没有认得出是 U-Hermes 旧版本留下的副本。")
        if out.left and out.skipped_reason != "stick":
            try:
                names = os.listdir(host)
            except OSError:
                names = []
            ours_or_flagged = set(out.suspicious) | set(out.failed) | {MARKER}
            rest = [n for n in names if n not in ours_or_flagged and not n.endswith(STASH)]
            stashed = [n for n in names if n.endswith(STASH) and n not in ours_or_flagged]
            if rest:
                print("  [i] %s 里其余 %d 项认不出是 U-Hermes 放的，没有动；" % (host, len(rest)))
                print("      如果这台电脑自己装过 Hermes，那些多半是它的数据。")
            if stashed:
                print("  [i] %s 是旧版本挪开的这台电脑自己的文件，等上面的问题解决后会自动放回，"
                      "别删。" % "、".join(stashed))
            elif rest and not (out.failed or out.suspicious):
                print("      确认不需要的话，可以手动删掉整个文件夹。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never block a launch on a cleanup
        print("  [i] 清理旧版本留下的副本时出错，已跳过（%s）" % exc.__class__.__name__)
        sys.exit(0)

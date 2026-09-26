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
    match "a file this stick has had", and they are the live ones.
  * With the marker, config.yaml is ours by the marker's own promise -- the
    rule the old versions' own cleanup used -- and so is .env, provided the
    stick has or had one: those launchers copied .env only `if exist`.
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
    had changed in between. A proven one is removed; one whose config
    carries U-Hermes' fingerprints is left where it is, pair and all, and
    reported -- putting it back would make the stick owner's old keys live.
  * If anything the marker vouches for cannot be removed or put back
    (read-only, in use), the marker stays, so the next start tries again.
  * A file with nothing but whitespace is never ours by byte-equality: it
    holds nothing, and it may be the machine's.
  * The folder goes only if this run removed something, it is left empty,
    and it is a real folder -- rd on a junction removes the link, whatever
    is behind it.

What cannot be proven is reported, never deleted: a config.yaml carrying
U-Hermes' fingerprints, and any .env left beside a config that was ours or
looks it. Never prints the contents of any file. Always exits 0: a failed
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
    """True if `path` is `root` or lies under it, links resolved."""
    try:
        p, r = os.path.realpath(path), os.path.realpath(root)
        return os.path.normcase(os.path.commonpath([p, r])) == os.path.normcase(r)
    except ValueError:  # different drives
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

    marked = os.path.exists(at(MARKER))
    if marked:
        for name in OURS:
            if name == ".env" and not had_env:
                continue
            if os.path.exists(at(name)) and not _inside(at(name), data_dir):
                remove(name)

    # While a file the marker vouches for is still there, nothing goes back:
    # the marker must stay (to try again next time), and a marker left over a
    # restored file would have the next run delete the machine's own config.
    can_restore = marked and not out.failed
    held_pair = marked and _fingerprinted(at("config.yaml" + STASH)) \
        and not ours("config.yaml" + STASH, "config.yaml")
    for name in OURS:
        stash = name + STASH
        if not os.path.exists(at(stash)):
            continue
        if ours(stash, name):
            remove(stash)
        elif held_pair:
            out.suspicious.append(stash)
        elif can_restore and not os.path.exists(at(name)):
            try:
                os.replace(at(stash), at(name))
                out.restored.append(name)
            except OSError:
                out.failed.append(stash)

    if marked:
        if out.failed:
            pass  # keep it: the next start tries again, with the marker's promise intact
        else:
            remove(MARKER)

    for name in OURS:
        if os.path.exists(at(name)) and name not in out.removed and ours(name, name):
            remove(name)

    # What cannot be proven, but should not pass in silence.
    config_was_ours = any(n.startswith("config.yaml") for n in out.removed)
    for name in ("config.yaml", "config.yaml" + STASH):
        if name not in out.suspicious and os.path.exists(at(name)) and _fingerprinted(at(name)):
            out.suspicious.append(name)
    # A stick that never had a .env never copied one, so any .env here is the machine's.
    if had_env and (config_was_ours or any(n.startswith("config.yaml") for n in out.suspicious)):
        for name in (".env", ".env" + STASH):
            if name in out.suspicious or name in out.failed or name in out.restored:
                continue
            if os.path.exists(at(name)) and _meaningful(_read(at(name))):
                out.suspicious.append(name)

    if out.removed and not _is_link(host):
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
    shown = [n for n in out.removed if n != MARKER] or out.removed
    if out.removed:
        print("  [i] 已删掉旧版本 U-Hermes 留在这台电脑上的配置和密钥副本：")
        print("      %s（%s）" % (host, "、".join(shown)))
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
        elif not (out.removed or out.failed or out.suspicious):
            print("  [OK] 本机上没有认得出是这个 U 盘留下的副本。")
        if out.left and out.skipped_reason != "stick":
            try:
                rest = [n for n in os.listdir(host) if n not in out.suspicious and n not in out.failed]
            except OSError:
                rest = []
            if rest:
                print("  [i] %s 里其余 %d 项认不出是 U-Hermes 放的，没有动。" % (host, len(rest)))
                print("      如果这台电脑自己装过 Hermes，那些多半是它的数据；"
                      "确认不需要的话可以手动删掉整个文件夹。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never block a launch on a cleanup
        print("  [i] 清理旧版本留下的副本时出错，已跳过（%s）" % exc.__class__.__name__)
        sys.exit(0)

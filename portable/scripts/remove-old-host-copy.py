# -*- coding: utf-8 -*-
"""Take away the copy of the user's config and API keys that U-Hermes v0.4.7
and earlier left in the host's %USERPROFILE%\\.hermes (Mac: ~/.hermes).

Those versions copied data\\config.yaml and data\\.env there on every launch,
as "a fallback for any component started without our env". No such component
exists -- on Windows the engine falls back to %LOCALAPPDATA%\\hermes, never
~/.hermes, and the Web UI hands HERMES_HOME to every Hermes process it
starts -- so v0.4.8 stopped copying. What they left behind is still there on
every machine the stick ever ran on, and on a machine that is not yours it is
your keys in someone else's home folder. This removes it.

    v0.3.5-v0.4.1   copied config.yaml and .env with no marker, overwriting
                    whatever the machine had, and appended a platforms block
                    to the config copy when it had none (WINDOWS_TAIL);
    v0.4.2-v0.4.7   wrote the marker .u-hermes-mirror, moved the machine's
                    own config.yaml / .env aside as *.before-u-hermes and put
                    them back on a clean exit -- which [X] or a pulled stick
                    never reached;
    Mac, all        cp -f over ~/.hermes/config.yaml and .env with no marker
                    and no stash, plus MAC_TAIL.

Deleting someone else's file is worse than leaving ours, so every doubt
resolves to "leave it":

  * With the marker, config.yaml and .env are ours by the marker's own
    promise -- the same rule the old versions' own cleanup used.
  * Otherwise a file is ours only if its bytes are exactly a file this stick
    has had: data\\config.yaml or data\\.env as they are now, the earlier
    ones the stick keeps (data\\config.yaml.*, data\\.env.*, anything under
    data\\backups), or one of those configs plus one of the two tails. Not
    "looks like a U-Hermes config": the machine's owner may have copied one
    on purpose. Byte-equality is what makes the match certain.
  * .env is judged on its own. It holds the keys, and whether the config
    beside it still matches -- the stick's config changes on every save --
    says nothing about it.
  * A *.before-u-hermes file is checked the same way before it is put back:
    v0.4.2-v0.4.7 stashed a v0.4.1 copy as "the machine's own" whenever the
    stick's config had changed in between, and restoring it would put the
    keys back for good.
  * A file with nothing but whitespace is never ours: it holds nothing, and
    it may be the machine's.
  * The folder goes only if this run removed something, it is left empty,
    and it is a real folder -- rd on a junction removes the link, whatever
    is behind it.

A config.yaml that is not provably ours but carries U-Hermes' fingerprints is
reported, not deleted. Never prints the contents of any file.

Always exits 0: a failed cleanup must not stop the launch.

Run:  python remove-old-host-copy.py <data_dir> [--home DIR] [--report]
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARKER = ".u-hermes-mirror"
STASH = ".before-u-hermes"
OURS = ("config.yaml", ".env")

# What v0.3.5-v0.4.1's Windows-Start.bat appended with `echo` under chcp 65001
# when the copy had no top-level platforms: line, and what every
# Mac-Start.command appended with a heredoc after each cp -f.
WINDOWS_TAIL = (b"\r\nplatforms:\r\n  api_server:\r\n    extra:\r\n      port: 8642\r\n"
                b"      host: 127.0.0.1\r\n    enabled: true\r\n    key: ''\r\n"
                b"    cors_origins: '*'\r\n")
MAC_TAIL = WINDOWS_TAIL.replace(b"\r\n", b"\n")

# Only ever reported, never grounds for deleting.
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


def stick_history(data_dir):
    """(configs, envs): every config.yaml and .env this stick has, or had."""
    configs, envs = set(), set()

    def add(path, name):
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
    return configs, envs


def _is_link(path):
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def _remove(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def clean(data_dir, home):
    """Returns (removed, restored, suspicious, host_dir_left)."""
    host = os.path.join(home, ".hermes")
    removed, restored, suspicious = [], [], []
    if not os.path.isdir(host):
        return removed, restored, suspicious, False
    configs, envs = stick_history(data_dir)
    pool = {"config.yaml": configs, ".env": envs}

    def ours(path, name):
        data = _read(path)
        return _meaningful(data) and data in pool[name]

    marker = os.path.join(host, MARKER)
    if os.path.exists(marker):
        for name in OURS:
            path = os.path.join(host, name)
            if os.path.exists(path) and _remove(path):
                removed.append(name)
    for name in OURS:
        path, stash = os.path.join(host, name), os.path.join(host, name + STASH)
        if not os.path.exists(stash):
            continue
        if ours(stash, name):
            if _remove(stash):
                removed.append(name + STASH)
        elif os.path.exists(marker) and not os.path.exists(path):
            try:
                os.replace(stash, path)
                restored.append(name)
            except OSError:
                pass
    if os.path.exists(marker) and _remove(marker):
        removed.append(MARKER)

    for name in OURS:
        path = os.path.join(host, name)
        if os.path.exists(path) and ours(path, name) and _remove(path):
            removed.append(name)

    for name in ("config.yaml", "config.yaml" + STASH):
        data = _read(os.path.join(host, name))
        if _meaningful(data) and any(f in data for f in FINGERPRINTS):
            suspicious.append(name)

    if removed and not _is_link(host):
        try:
            os.rmdir(host)
        except OSError:
            pass
    return removed, restored, suspicious, os.path.isdir(host)


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
    host = os.path.join(home, ".hermes")

    removed, restored, suspicious, left = clean(data_dir, home)
    if removed:
        print("  [i] 已删掉旧版本 U-Hermes 留在这台电脑上的配置和密钥副本：")
        print("      %s（%s）" % (host, "、".join(removed)))
    if restored:
        print("  [i] 旧版本挪开的、这台电脑自己的 Hermes 文件已放回原处：%s" % "、".join(restored))
    for name in suspicious:
        print("  [!] %s 看起来也是 U-Hermes 旧版本留下的，里面可能有密钥，"
              "但和这个 U 盘现在或以前的配置都对不上，所以没有自动删。" % os.path.join(host, name))
        print("      确认这台电脑自己的 Hermes 不在用它，可以手动删掉。")
    if report:
        if not removed:
            print("  [OK] 本机上没有这个 U 盘留下的副本。")
        if left:
            try:
                count = len(os.listdir(host))
            except OSError:
                count = 0
            if count:
                print("  [i] %s 里还有 %d 项，不是 U-Hermes 放的，没有动。" % (host, count))
                print("      如果这台电脑自己装过 Hermes，那些是它的数据；"
                      "确认不需要的话可以手动删掉整个文件夹。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never block a launch on a cleanup
        print("  [i] 清理旧版本留下的副本时出错，已跳过（%s）" % exc.__class__.__name__)
        sys.exit(0)

# -*- coding: utf-8 -*-
"""Get the Web UI ready to start: its data under data\\webui, the message
gateway starting with it, and new chats on Hermes.

Run by Windows-Start.bat (and Mac-Start.command) just before node starts,
while the Web UI is not running yet. Three jobs, in this order:

1. Move the Web UI's data into HERMES_WEB_UI_HOME (data\\webui).
   Unless NODE_ENV=production, Web UI 0.7.x keeps two stores outside data\\:

     <install>\\packages\\server\\data\\hermes-web-ui.db
         the logins and the whole chat list -- Hermes chats included
         (it is cwd + packages/server/data, hence the launcher's cd /d);
     <package>\\.ekko  (node_modules\\hermes-web-ui\\.ekko)
         the Ekko agent: its memory, skills, settings and the files it wrote.

   The second lives inside the npm package, so every reinstall deletes it:
   Menu [9], setup.ps1 -Force, the launcher's own repair -- npm replaces the
   folder even when the version has not changed. Since Web UI 0.7.30 a new
   chat starts on Ekko, which made that a real loss. With NODE_ENV=production
   both live under HERMES_WEB_UI_HOME instead, which is how upstream's own
   CLI and desktop app run the server. Switching without moving them first
   would greet an upgraded stick with an empty chat list and a fresh
   admin/123456, so this moves them first; the launcher turns production
   mode on when this exits 0, or when the chat database is already in
   data\\webui (a previous run moved it, and the old mode would show it empty).

   Both move, or neither does. Every database is settled first (SQLite rolls
   back a hot journal, folds a WAL in) and the move is deferred while any of
   them still has side files. Then .ekko goes first -- it is the one an open
   Explorer window or a leftover process in its workspace can lock -- and the
   database after it; if anything fails half way, every rename done so far
   is undone. Moves are renames: one step on the same drive, so a database
   travels with whatever SQLite needs and .ekko with its skill-cleanup marker
   (without that marker, Ekko's next start quarantines and deletes the skills
   folder). Nothing is ever overwritten or merged. When both places hold
   data, the one in data\\webui is kept, unless it holds nothing of the
   user's and the old one does; the other is renamed aside next to it, never
   deleted. "Nothing of the user's" means: for the chat database, no
   conversations; for .ekko, no memory, no skill the user added, no file in
   its workspace -- not ekko.db's own sessions table, which the Web UI never
   writes (Ekko chats live in the chat database).

   Ekko records each conversation's working folder as an absolute path into
   .ekko, in both databases. Paths that still point into the npm package are
   pointed at wherever the old .ekko went, on every run, until none are left.

   Windows refuses to rename a file or folder that is open, so a database in
   use cannot move there; macOS does not refuse, and there only the port
   check below stands between a running Web UI and the move.

2. Turn on starting the gateway with the Web UI, unless the user chose.
   From Web UI 0.7.26 the Web UI starts the Hermes gateway -- messaging bots,
   Hermes cron jobs, the API on 8642 -- only when data\\webui\\config.json says
   gatewayAutoStart.enabled is true, and nothing migrates the old default,
   which was "on unless switched off". No environment variable can turn it
   on. So: missing or not a true/false means on; a true or false the settings
   page wrote is left alone. A BOM found there is removed: Node's JSON.parse
   rejects it, and the Web UI then ignores, and on its next save drops,
   every setting in the file.

   Turning it on also means the Web UI restarts the gateway after settings
   saves, per profile unless config.yaml says the gateway is unified -- and
   engine 0.21.5 parks a named profile that gets `gateway stop` while the
   default gateway serves it. That cannot happen here: the engine writes
   `gateway.multiplex_profiles: true` itself whenever it decides to serve
   every profile from one gateway, and the Web UI then sends every restart
   to the default one. Writing that key ahead of the engine would be worse:
   an explicit true skips the engine's own check for two profiles sharing a
   bot's credentials.

3. Make new chats start on Hermes.
   Web UI 0.7.30 lists Ekko first on the new-chat page, and from 0.7.32 the
   page remembers the last agent picked in the browser's localStorage under
   hermes_new_chat_agent_v1. There is no server setting, so a tiny script in
   index.html stores "hermes" there before the app loads -- only when nothing
   is stored, so a user who picks Ekko keeps Ekko. Re-applied on every start,
   because a reinstall brings back the original index.html.

Steps 2 and 3 never stop a launch; they report and carry on.

Exit codes:
  0   the Web UI's data is where production mode looks for it
      (or there was none): start it with NODE_ENV=production
  10  there was data to move and it could not be moved this time; nothing
      moved, start the old way and try again next launch
  2   bad arguments

Run:  python prepare-webui.py <install-dir> <webui-home> <package-dir>
                              [--move-only] [--legacy-db PATH]...
  --move-only   step 1 only; for Menu [9] and setup.ps1, before npm reinstalls
                the package
  --legacy-db   another place an older launcher left the chat database
                (Mac-Start.command passes ~/packages/server/data/..., where a
                Finder double-click with no cd put it)
"""
import errno
import io
import json
import os
import shutil
import socket
import sqlite3
import stat
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

READY = 0
DEFERRED = 10

DB_NAME = "hermes-web-ui.db"
EKKO = ".ekko"
SIDE_FILES = ("-journal", "-wal", "-shm")
# Where a dev-mode Ekko workspace path points: <package>\.ekko\workspace\...
OLD_EKKO_SUFFIXES = ("\\node_modules\\hermes-web-ui\\.ekko", "/node_modules/hermes-web-ui/.ekko")
BUILTIN_SKILLS = ".ekko-builtin-skills.json"

CONFIG_NAME = "config.json"
SEEDED_POLICY = {"enabled": True, "include": ["default"]}

AGENT_KEY = "hermes_new_chat_agent_v1"
SEED_MARK = 'data-u-hermes="default-agent"'
SEED_SCRIPT = (
    '<script %s>try{if(localStorage.getItem("%s")===null)'
    'localStorage.setItem("%s","hermes")}catch(e){}</script>\n' % (SEED_MARK, AGENT_KEY, AGENT_KEY)
)


def say(line):
    print("  " + line)


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


# --- step 1: move ------------------------------------------------------------

def port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _cross_drive(err):
    return getattr(err, "winerror", None) == 17 or err.errno == errno.EXDEV


def _writable_then_retry(func, path, _exc):
    """rmtree helper: git and some tools leave read-only files behind."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def _rmtree(path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_writable_then_retry)
    else:
        shutil.rmtree(path, onerror=_writable_then_retry)


def move(src, dst):
    """Rename src to dst; copy then delete when they are on different drives.
    Never overwrites: os.rename would on macOS."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "already exists", dst)
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if not _cross_drive(e):
            raise
    # data\ is a link to another drive (or HERMES_WEB_UI_HOME points there).
    # Complete copy first, under a name nothing reads, then one rename; the
    # source goes last. Copies an earlier interrupted run left are ours and
    # incomplete: they go first.
    parent, name = os.path.split(dst)
    for stale in os.listdir(parent):
        if stale.startswith(name + ".partial-"):
            stale = os.path.join(parent, stale)
            _rmtree(stale) if os.path.isdir(stale) else os.remove(stale)
    partial = dst + ".partial-" + stamp()
    if os.path.isdir(src):
        shutil.copytree(src, partial, symlinks=True)
    else:
        shutil.copy2(src, partial)
    os.rename(partial, dst)
    try:
        _rmtree(src) if os.path.isdir(src) else os.remove(src)
    except OSError as e:
        # Everything is safe at dst. What is left of src must not keep
        # looking like data still to move, or every later run would copy it
        # again and the reinstall guards would refuse npm for good.
        leftover = src + "-leftover-" + stamp()
        try:
            os.rename(src, leftover)
            say("[i] 旧位置还剩一些删不掉的文件（%s），已改名为 %s，可以手动删掉。" % (e, leftover))
        except OSError:
            say("[!] 数据已经完整复制到 %s，但旧位置 %s 删不掉（%s），请手动删掉。" % (dst, src, e))


def settle(db):
    """Let SQLite finish what a crash left half-done -- roll back a hot
    journal, fold a WAL back in -- so the database is one file before it
    moves. True when no side file is left and nothing holds a lock."""
    try:
        con = sqlite3.connect(db, timeout=2)
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            return False
    except sqlite3.Error:
        pass  # a database SQLite cannot open moves as it is; the Web UI says what is wrong with it
    return not any(os.path.exists(db + s) for s in SIDE_FILES)


def count_rows(db, *tables):
    """Rows in these tables together; a missing database or table counts 0."""
    if not os.path.isfile(db):
        return 0
    total = 0
    try:
        con = sqlite3.connect(db, timeout=2)
        try:
            for table in tables:
                try:
                    total += con.execute('SELECT count(*) FROM "%s"' % table).fetchone()[0]
                except sqlite3.OperationalError:
                    pass
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return total


def chat_db_has_data(db):
    return count_rows(db, "sessions") > 0


def ekko_has_data(root):
    """Does this .ekko hold anything of the user's? Its memory, a skill the
    user added, a file Ekko wrote in its workspace. Bundled skills, logs and
    the MCP servers the Web UI injects into config.json at every start do
    not count; neither does ekko.db's sessions table, which nothing writes."""
    if count_rows(os.path.join(root, "ekko.db"), "memory_nodes", "memory_messages") > 0:
        return True
    for dirpath, _dirs, files in os.walk(os.path.join(root, "workspace")):
        if files:
            return True
    skills = os.path.join(root, "skills")
    for profile in (os.listdir(skills) if os.path.isdir(skills) else []):
        folder = os.path.join(skills, profile)
        if not os.path.isdir(folder):
            continue
        try:
            with io.open(os.path.join(folder, BUILTIN_SKILLS), encoding="utf-8-sig") as f:
                builtin = set(json.load(f))
        except (OSError, ValueError, TypeError):
            builtin = set()
        if any(os.path.isdir(os.path.join(folder, n)) and n not in builtin and not n.startswith(".")
               for n in os.listdir(folder)):
            return True
    return False


class Journal(object):
    """Every rename done in this run, so a failure part way can undo them."""

    def __init__(self):
        self.done = []

    def move(self, src, dst):
        move(src, dst)
        self.done.append((src, dst))

    def undo(self):
        failed = []
        for src, dst in reversed(self.done):
            try:
                move(dst, src)
            except OSError as e:
                failed.append("%s -> %s（%s）" % (dst, src, e))
        self.done = []
        return failed


def place_ekko(src, dst, journal):
    """Move the old .ekko into place; return where it ended up."""
    if not os.path.exists(dst):
        journal.move(src, dst)
        say("[OK] Ekko 智能体的记忆、技能和它写的文件已搬到 %s，" % dst)
        say("     以后重装网页界面不会再清掉它们。")
        return dst
    if not ekko_has_data(dst) and ekko_has_data(src):
        aside = dst + "-empty-" + stamp()
        journal.move(dst, aside)
        journal.move(src, dst)
        say("[i] %s 里没有你的数据，已改名为 %s，换上原来的 Ekko 数据。" % (dst, os.path.basename(aside)))
        return dst
    aside = dst + "-legacy-" + stamp()
    journal.move(src, aside)
    say("[i] 网页界面安装目录里还有一份旧的 Ekko 数据，%s 里已经有在用的一份。" % dst)
    say("    旧的那份没有合并，改名放在 %s。" % aside)
    return aside


def place_database(src, dst, journal):
    if not os.path.exists(dst):
        journal.move(src, dst)
        say("[OK] 网页界面的账号和聊天列表已搬到 %s。" % os.path.dirname(dst))
        return
    if not settle(dst):
        raise OSError(errno.EBUSY, "正被占用", dst)
    if not chat_db_has_data(dst) and chat_db_has_data(src):
        aside = dst + ".empty-" + stamp()
        journal.move(dst, aside)
        journal.move(src, dst)
        say("[i] %s 里那份聊天记录库是空的，已改名为 %s，换上原来那份。" % (os.path.dirname(dst), os.path.basename(aside)))
    else:
        aside = dst + ".legacy-" + stamp()
        journal.move(src, aside)
        say("[i] 旧位置还有一份网页聊天记录库，%s 里已经有在用的一份。" % os.path.dirname(dst))
        say("    旧的那份没有合并，改名放在 %s。" % aside)


def remove_empty_dirs(path, stop):
    """rmdir path and its parents while they are empty, up to (not including) stop."""
    stop = os.path.normcase(os.path.abspath(stop))
    path = os.path.abspath(path)
    while os.path.normcase(path) != stop and os.path.normcase(path).startswith(stop):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


def move_legacy_data(install, home, package, legacy_dbs=()):
    """(ready, where the old .ekko went or None). Ready means nothing is
    left where only the old layout looks."""
    old_dbs = [os.path.join(install, "packages", "server", "data", DB_NAME)]
    old_dbs += [os.path.abspath(p) for p in legacy_dbs]
    old_dbs = [p for p in old_dbs if os.path.isfile(p)]
    old_ekko = os.path.join(package, EKKO)
    if not old_dbs and not os.path.exists(old_ekko):
        return True, None
    try:
        port = int(os.environ.get("PORT") or 8648)
    except ValueError:
        port = 8648
    if port_in_use(port):
        say("[!] 端口 %d 已经有程序在用，可能 U-Hermes 已经开着。" % port)
        say("    网页界面的数据这次先不搬，关掉它以后再启动就会自动搬。")
        return False, None
    for db in old_dbs:
        if not settle(db):
            say("[!] 网页界面的聊天记录库（%s）正被占用，或者旁边还有没收尾的临时文件，" % db)
            say("    这次先不搬，什么都没动，下次启动再试。")
            return False, None
    journal = Journal()
    ekko_went = None
    try:
        os.makedirs(home, exist_ok=True)
        if os.path.exists(old_ekko):
            ekko_went = place_ekko(old_ekko, os.path.join(home, EKKO), journal)
        for db in old_dbs:
            place_database(db, os.path.join(home, DB_NAME), journal)
    except (OSError, KeyboardInterrupt) as e:
        failed = journal.undo()
        what = "被中断" if isinstance(e, KeyboardInterrupt) else str(e)
        say("[!] 网页界面的数据这次没搬成（%s），已经放回原处，什么都没丢，下次启动再试。" % what)
        say("    多半是有文件正被占用：关掉开着的文件夹窗口，或者重启电脑后再启动。")
        for line in failed:
            say("[!] 没能放回：%s，请手动改回去。" % line)
        return False, None
    for db in old_dbs:
        remove_empty_dirs(os.path.dirname(db), install)
    return True, ekko_went


def repoint_workspaces(home, new_root=None):
    """Point Ekko conversations whose working folder is inside the npm
    package at the same folder under new_root (where the old .ekko went;
    by default the .ekko in home). Returns how many."""
    new_root = new_root or os.path.join(home, EKKO)
    if not os.path.isdir(new_root):
        return 0
    changed = 0
    for db in (os.path.join(home, DB_NAME), os.path.join(home, EKKO, "ekko.db")):
        if not os.path.isfile(db):
            continue
        con = sqlite3.connect(db, timeout=2)
        try:
            with con:
                tables = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    quoted = '"%s"' % table.replace('"', '""')
                    for col in [r[1] for r in con.execute("PRAGMA table_info(%s)" % quoted)]:
                        if "workspace" not in col.lower():
                            continue
                        c = '"%s"' % col.replace('"', '""')
                        for suffix in OLD_EKKO_SUFFIXES:
                            # lower() folds ASCII only, so positions in it are
                            # positions in the original text too.
                            cur = con.execute(
                                "UPDATE %s SET %s = ? || substr(%s, instr(lower(%s), ?) + ?) "
                                "WHERE typeof(%s) = 'text' AND instr(lower(%s), ?) > 0"
                                % (quoted, c, c, c, c, c),
                                (new_root, suffix.lower(), len(suffix), suffix.lower()))
                            changed += max(cur.rowcount, 0)
        finally:
            con.close()
    return changed


# --- step 2: gateway ---------------------------------------------------------

def seed_gateway_autostart(home):
    path = os.path.join(home, CONFIG_NAME)
    data, had_bom = {}, False
    if os.path.exists(path):
        try:
            with io.open(path, "rb") as f:
                raw = f.read()
            had_bom = raw.startswith(b"\xef\xbb\xbf")
            data = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError):
            say("[!] %s 读不出来，没有改动，网关会不会随网页界面启动以网页界面的设置为准。" % path)
            return
        if not isinstance(data, dict):
            say("[!] %s 不是预期的格式，没有改动。" % path)
            return
    policy = data.get("gatewayAutoStart")
    if isinstance(policy, dict) and isinstance(policy.get("enabled"), bool):
        if had_bom:
            write_json(path, data)
        return
    if isinstance(policy, dict):
        policy = dict(policy, enabled=True)
    else:
        policy = dict(SEEDED_POLICY)
    data["gatewayAutoStart"] = policy
    write_json(path, data)
    say("[i] 已设为随网页界面启动消息网关（机器人和定时任务要用它）。")
    say("    不需要的话，可以在网页界面的设置里关掉「Gateway 自动启动」。")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".u-hermes-tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


# --- step 3: new chats on Hermes ---------------------------------------------

def client_reads_the_key(client):
    """Does the shipped client still read the localStorage key we seed?"""
    assets = os.path.join(client, "assets", "js")
    try:
        names = [n for n in os.listdir(assets) if n.endswith(".js")]
    except OSError:
        return False
    needle = AGENT_KEY.encode("ascii")
    for name in names:
        try:
            with io.open(os.path.join(assets, name), "rb") as f:
                if needle in f.read():
                    return True
        except OSError:
            continue
    return False


def seed_default_agent(package):
    client = os.path.join(package, "dist", "client")
    index = os.path.join(client, "index.html")
    try:
        with io.open(index, encoding="utf-8", newline="") as f:
            html = f.read()
    except OSError:
        say("[!] 找不到网页界面的 index.html，新聊天默认用哪个智能体以网页界面为准。")
        return
    if SEED_MARK in html:
        return
    if not client_reads_the_key(client):
        say("[!] 这个版本的网页界面不再读 %s，新聊天可能默认用 Ekko；" % AGENT_KEY)
        say("    新建对话时请选 Hermes。")
        return
    at = html.find("</head>")
    if at < 0:
        say("[!] index.html 里找不到 </head>，新聊天可能默认用 Ekko；新建对话时请选 Hermes。")
        return
    html = html[:at] + SEED_SCRIPT + html[at:]
    tmp = index + ".u-hermes-tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    os.replace(tmp, index)


# --- entry -------------------------------------------------------------------

def parse(argv):
    args, legacy, move_only = [], [], False
    it = iter(argv[1:])
    for a in it:
        if a == "--move-only":
            move_only = True
        elif a == "--legacy-db":
            legacy.append(next(it, ""))
        else:
            args.append(a)
    if len(args) != 3 or "" in legacy:
        return None
    return args, legacy, move_only


def main(argv):
    parsed = parse(argv)
    if parsed is None:
        print("usage: prepare-webui.py <install-dir> <webui-home> <package-dir> [--move-only] [--legacy-db PATH]...")
        return 2
    (install, home, package), legacy, move_only = parsed
    install, home, package = (os.path.abspath(a) for a in (install, home, package))

    try:
        ready, ekko_went = move_legacy_data(install, home, package, legacy)
    except KeyboardInterrupt:
        return DEFERRED
    if ready:
        try:
            n = repoint_workspaces(home, ekko_went)
            if n:
                say("[i] 已把 %d 处 Ekko 对话的工作文件夹指到新位置。" % n)
        except sqlite3.Error as e:
            say("[!] Ekko 对话的工作文件夹没能指到新位置（%s），下次启动再试。" % e)
    if not move_only:
        for step, args in ((seed_gateway_autostart, (home,)), (seed_default_agent, (package,))):
            try:
                step(*args)
            except Exception as e:  # never stop the launch over a preference
                say("[!] %s 没做完（%s），不影响启动。" % (step.__name__, e))
    return READY if ready else DEFERRED


if __name__ == "__main__":
    sys.exit(main(sys.argv))

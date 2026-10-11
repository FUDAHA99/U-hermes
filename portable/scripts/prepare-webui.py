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
   admin/123456, so this moves them, and the launcher sets NODE_ENV only
   when this says the move is done.

   Moves are renames: one step on the same drive, so a database travels with
   its side files and .ekko with its skill-cleanup marker (without that
   marker, Ekko's next start quarantines and deletes the skills folder).
   Nothing is ever overwritten or merged. When both places hold data, the one
   in data\\webui is kept, unless it holds no conversations and the old one
   does; the other is renamed aside next to it, never deleted. When the move
   cannot happen now -- the Web UI is already running, a file is in use --
   nothing moves and the exit code says so.

   Ekko records each conversation's working folder as an absolute path into
   .ekko, in both databases. Paths that still point into the npm package are
   pointed at the moved folder, on every run, until none are left.

2. Turn on starting the gateway with the Web UI, unless the user chose.
   From Web UI 0.7.26 the Web UI starts the Hermes gateway -- messaging bots,
   Hermes cron jobs, the API on 8642 -- only when data\\webui\\config.json says
   gatewayAutoStart.enabled is true, and nothing migrates the old default,
   which was "on unless switched off". No environment variable can turn it
   on. So: missing or not a true/false means on; a true or false the settings
   page wrote is left alone. When the key is missing it is seeded with
   include: ["default"]: engine 0.21.5 runs one gateway per computer that
   serves every profile, and refuses a separate gateway for a named profile
   (exit 78), which the Web UI would otherwise retry for each profile before
   the page loads. The file must stay BOM-less: Node's JSON.parse rejects a
   BOM, and the Web UI then ignores, and on its next save drops, every setting
   in it. A BOM found there is removed.

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
  10  there was data to move and it could not be moved this time; nothing was
      lost, start the old way and try again next launch
  2   bad arguments

Run:  python prepare-webui.py <install-dir> <webui-home> <package-dir> [--move-only]
  --move-only   step 1 only; for Menu [9] and setup.ps1, before npm reinstalls
                the package
"""
import errno
import io
import json
import os
import shutil
import socket
import sqlite3
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
    # HERMES_WEB_UI_HOME moved to another drive in data\.env. Complete copy
    # first, under a name nothing reads, then one rename; the source goes last.
    partial = dst + ".partial-" + stamp()
    if os.path.isdir(src):
        shutil.copytree(src, partial)
        os.rename(partial, dst)
        shutil.rmtree(src)
    else:
        shutil.copy2(src, partial)
        os.rename(partial, dst)
        os.remove(src)


def settle(db):
    """Let SQLite finish what a crash left half-done -- roll back a hot
    journal, fold a WAL back in -- so the database is one file before it
    moves. True when no side file is left."""
    try:
        con = sqlite3.connect(db, timeout=2)
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        pass  # a database SQLite cannot open moves as it is; the Web UI says what is wrong with it
    return not any(os.path.exists(db + s) for s in SIDE_FILES)


def conversations(db):
    """Rows in the sessions table; 0 when the database or the table is missing."""
    if not os.path.isfile(db):
        return 0
    try:
        con = sqlite3.connect(db, timeout=2)
        try:
            return con.execute("SELECT count(*) FROM sessions").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def move_database(src, dst):
    if not settle(src):
        say("[!] 网页界面的聊天记录库（%s）旁边还有没收尾的临时文件，这次先不搬，下次启动再试。" % src)
        return False
    if not os.path.exists(dst):
        move(src, dst)
        say("[OK] 网页界面的账号和聊天列表已搬到 %s。" % os.path.dirname(dst))
        return True
    if not settle(dst):
        say("[!] %s 正被占用，网页界面的聊天记录库这次先不搬。" % dst)
        return False
    if conversations(dst) == 0 and conversations(src) > 0:
        aside = dst + ".empty-" + stamp()
        move(dst, aside)
        move(src, dst)
        say("[i] %s 里那份聊天记录库是空的，已改名为 %s，换上原来那份。" % (os.path.dirname(dst), os.path.basename(aside)))
    else:
        aside = dst + ".legacy-" + stamp()
        move(src, aside)
        say("[i] 旧位置还有一份网页聊天记录库，%s 里已经有在用的一份。" % os.path.dirname(dst))
        say("    旧的那份没有合并，改名放在 %s。" % aside)
    return True


def move_ekko(src, dst):
    if not os.path.exists(dst):
        move(src, dst)
        say("[OK] Ekko 智能体的记忆、技能和它写的文件已搬到 %s，" % dst)
        say("     以后重装网页界面不会再清掉它们。")
        return True
    old_db, new_db = os.path.join(src, "ekko.db"), os.path.join(dst, "ekko.db")
    if conversations(new_db) == 0 and conversations(old_db) > 0:
        aside = dst + "-empty-" + stamp()
        move(dst, aside)
        move(src, dst)
        say("[i] %s 是空的，已改名为 %s，换上原来的 Ekko 数据。" % (dst, os.path.basename(aside)))
    else:
        aside = dst + "-legacy-" + stamp()
        move(src, aside)
        say("[i] 网页界面安装目录里还有一份旧的 Ekko 数据，%s 里已经有在用的一份。" % dst)
        say("    旧的那份没有合并，改名放在 %s。" % aside)
    return True


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


def move_legacy_data(install, home, package):
    """True when nothing is left where only the old layout looks."""
    old_db = os.path.join(install, "packages", "server", "data", DB_NAME)
    old_ekko = os.path.join(package, EKKO)
    if not (os.path.exists(old_db) or os.path.exists(old_ekko)):
        return True
    try:
        port = int(os.environ.get("PORT") or 8648)
    except ValueError:
        port = 8648
    if port_in_use(port):
        say("[!] 端口 %d 已经有程序在用，可能 U-Hermes 已经开着。" % port)
        say("    网页界面的数据这次先不搬，关掉它以后再启动就会自动搬。")
        return False
    os.makedirs(home, exist_ok=True)
    ready = True
    for what, src, dst, step in (
            ("网页界面的聊天记录库", old_db, os.path.join(home, DB_NAME), move_database),
            ("Ekko 智能体的数据", old_ekko, os.path.join(home, EKKO), move_ekko)):
        if not os.path.exists(src):
            continue
        try:
            ready = step(src, dst) and ready
        except OSError as e:
            say("[!] %s这次没搬成（%s）。数据还在原处，没有丢，下次启动再试。" % (what, e))
            ready = False
    remove_empty_dirs(os.path.dirname(old_db), install)
    return ready


def repoint_workspaces(home):
    """Point Ekko conversations whose working folder is inside the npm
    package at the same folder under the moved .ekko. Returns how many."""
    new_root = os.path.join(home, EKKO)
    if not os.path.isdir(new_root):
        return 0
    changed = 0
    for db in (os.path.join(home, DB_NAME), os.path.join(new_root, "ekko.db")):
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

def main(argv):
    args = [a for a in argv[1:] if a != "--move-only"]
    move_only = len(args) != len(argv) - 1
    if len(args) != 3:
        print("usage: prepare-webui.py <install-dir> <webui-home> <package-dir> [--move-only]")
        return 2
    install, home, package = (os.path.abspath(a) for a in args)

    ready = move_legacy_data(install, home, package)
    if ready:
        try:
            n = repoint_workspaces(home)
            if n:
                say("[i] 已把 %d 处 Ekko 对话的工作文件夹指到新位置。" % n)
        except sqlite3.Error as e:
            say("[!] Ekko 对话的工作文件夹没能指到新位置（%s），下次启动再试。" % e)
    if not move_only:
        for step, arg in ((seed_gateway_autostart, home), (seed_default_agent, package)):
            try:
                step(arg)
            except Exception as e:  # never stop the launch over a preference
                say("[!] %s 没做完（%s），不影响启动。" % (step.__name__, e))
    return READY if ready else DEFERRED


if __name__ == "__main__":
    sys.exit(main(sys.argv))

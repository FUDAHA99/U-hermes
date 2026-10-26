# -*- coding: utf-8 -*-
"""prepare-webui.py: the Web UI's data under data\\webui, the gateway on, new
chats on Hermes -- without losing what an older version left elsewhere.

Each test builds a miniature stick in a temp dir: the install folder, the
npm package with the Web UI's client, and, for an upgraded stick, the two
places Web UI 0.7.x kept data outside production mode. The databases are
real SQLite files with the tables and columns the Web UI and Ekko use --
including ekko.db's sessions table, which exists and stays empty: the Web UI
keeps Ekko chats in the chat database, and a fixture that filled it once hid
a bug that set a user's real Ekko data aside as "empty".

A U-Hermes running on this PC answers on 8648; the move would rightly wait
for it. So the port probe is off here except in the test that is about it.

Run:  python portable/scripts/tests/test_prepare_webui.py
"""
import contextlib
import errno
import importlib.util
import io
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
PORTABLE = os.path.dirname(SCRIPTS)
SCRIPT = os.path.join(SCRIPTS, "prepare-webui.py")

_spec = importlib.util.spec_from_file_location("prepare_webui", SCRIPT)
prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prep)
_real_port_in_use = prep.port_in_use
prep.port_in_use = lambda port: False

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


INDEX = ('<!doctype html>\r\n<html lang="zh-CN">\r\n<head>\r\n  <meta charset="UTF-8" />\r\n'
         '  <script type="module" crossorigin src="/assets/js/index-x.js"></script>\r\n</head>\r\n'
         '<body><div id="app"></div></body>\r\n</html>\r\n')
CHAT_PANEL = 'const NEW_CHAT_AGENT_STORAGE_KEY = "hermes_new_chat_agent_v1";'
CONFIG_YAML = 'model:\r\n  provider: "custom:x"\r\nmemory:\r\n  enabled: true\r\n'

# A conversation's working folder, as dev-mode Ekko recorded it on this OS
# (while the stick had another drive letter, on Windows).
if os.name == "nt":
    OLD_WS = r"F:\U-Hermes\runtime\node-win-x64\node_modules\hermes-web-ui\.ekko\workspace\default\s1"
else:
    OLD_WS = "/Volumes/OLD/U-Hermes/runtime/node-mac-arm64/lib/node_modules/hermes-web-ui/.ekko/workspace/default/s1"
USER_WS = os.path.join("H:" + os.sep if os.name == "nt" else "/Volumes/U", "U-Hermes工作区", "小说")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unlock(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_chat_db(path, sessions):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    with con:
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, workspace TEXT)")
        con.execute("CREATE TABLE workspace_run_changes (id INTEGER PRIMARY KEY, workspace TEXT NOT NULL DEFAULT '')")
        for sid, ws in sessions:
            con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (sid, "chat " + sid, ws))
            if ws:
                con.execute("INSERT INTO workspace_run_changes (workspace) VALUES (?)", (ws,))
    con.close()


def make_ekko(root, memory=0, user_skill=False, files=False, sessions=()):
    """An .ekko as Web UI 0.7.33 leaves it: bundled skills named in the
    manifest, the skill-cleanup marker, an ekko.db whose sessions table the
    Web UI never fills -- plus whatever the user added."""
    db = os.path.join(root, "ekko.db")
    os.makedirs(root, exist_ok=True)
    con = sqlite3.connect(db)
    with con:
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, workspace TEXT)")
        con.execute("CREATE TABLE memory_nodes (id INTEGER PRIMARY KEY, body TEXT)")
        con.execute("CREATE TABLE memory_messages (id INTEGER PRIMARY KEY, body TEXT)")
        for i in range(memory):
            con.execute("INSERT INTO memory_nodes (body) VALUES (?)", ("remembered %d" % i,))
        for sid, ws in sessions:
            con.execute("INSERT INTO sessions VALUES (?, ?)", (sid, ws))
    con.close()
    write(os.path.join(root, ".ekko-hermes-skill-cleanup-v2.json"), "{}")
    write(os.path.join(root, "config", "config.json"), '{"mcp": {"ekko-studio-api": {}}}')
    skills = os.path.join(root, "skills", "default")
    write(os.path.join(skills, ".ekko-builtin-skills.json"), json.dumps({"bundled": {"owner": "ekko-agent"}}))
    write(os.path.join(skills, "bundled", "SKILL.md"), "# bundled")
    os.makedirs(os.path.join(root, "workspace", "default", "s0"), exist_ok=True)  # an empty session folder
    if user_skill:
        write(os.path.join(skills, "my-skill", "SKILL.md"), "# mine")
    if files:
        write(os.path.join(root, "workspace", "default", "s1", "report.md"), "written by Ekko")


def rows(db, sql="SELECT id, workspace FROM sessions ORDER BY id"):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class Stick(object):
    """A U-Hermes install in a temp dir."""

    def __init__(self, upgraded=False):
        self.root = tempfile.mkdtemp(prefix="prep-webui-")
        self.install = os.path.join(self.root, "U-Hermes")
        self.data = os.path.join(self.install, "data")
        self.home = os.path.join(self.data, "webui")
        self.package = os.path.join(self.install, "runtime", "node-win-x64", "node_modules", "hermes-web-ui")
        self.old_db = os.path.join(self.install, "packages", "server", "data", "hermes-web-ui.db")
        self.old_ekko = os.path.join(self.package, ".ekko")
        self.new_db = os.path.join(self.home, "hermes-web-ui.db")
        self.new_ekko = os.path.join(self.home, ".ekko")
        client = os.path.join(self.package, "dist", "client")
        os.makedirs(os.path.join(client, "assets", "js"))
        os.makedirs(self.data)
        with io.open(os.path.join(client, "index.html"), "w", encoding="utf-8", newline="") as f:
            f.write(INDEX)
        with io.open(os.path.join(client, "assets", "js", "ChatPanel-x.js"), "w", encoding="utf-8") as f:
            f.write(CHAT_PANEL)
        with io.open(os.path.join(self.data, "config.yaml"), "w", encoding="utf-8", newline="") as f:
            f.write(CONFIG_YAML)
        if upgraded:
            # As a 0.7.24-0.7.33 stick has it: an Ekko chat whose folder is in
            # the npm package, a Hermes chat in the user's own folder.
            make_chat_db(self.old_db, [("s1", OLD_WS), ("s2", USER_WS), ("s3", None)])
            make_ekko(self.old_ekko, memory=2, user_skill=True, files=True)

    def run(self, *extra):
        out = io.StringIO()
        saved = os.environ.pop("HERMES_HOME", None)
        os.environ["HERMES_HOME"] = self.data
        try:
            with contextlib.redirect_stdout(out):
                code = prep.main(["prepare-webui.py", self.install, self.home, self.package] + list(extra))
        finally:
            os.environ.pop("HERMES_HOME", None)
            if saved is not None:
                os.environ["HERMES_HOME"] = saved
        return code, out.getvalue()

    def index(self):
        with io.open(os.path.join(self.package, "dist", "client", "index.html"), encoding="utf-8", newline="") as f:
            return f.read()

    def config(self):
        with io.open(os.path.join(self.home, "config.json"), "rb") as f:
            return f.read()

    def config_yaml(self):
        with io.open(os.path.join(self.data, "config.yaml"), "rb") as f:
            return f.read()

    def untouched(self):
        """Both old stores where they were, nothing in data\\webui."""
        return (os.path.isfile(self.old_db) and os.path.isdir(self.old_ekko)
                and not os.path.exists(self.new_db) and not os.path.exists(self.new_ekko))

    def close(self):
        shutil.rmtree(self.root, onerror=_unlock)


# --- the contract with the launchers ----------------------------------------

def test_exit_codes_are_what_the_launchers_test_for():
    print("exit codes: Windows-Start.bat takes exactly 0 as ready, Mac-Start.command -eq 0")
    check(prep.READY == 0 and prep.DEFERRED == 10, "READY is 0 and DEFERRED is 10")
    s = Stick(upgraded=True)
    try:
        env = dict(os.environ, HERMES_HOME=s.data, PORT=str(free_port()))
        out = subprocess.run([sys.executable, SCRIPT, s.install, s.home, s.package], capture_output=True, env=env)
        check(out.returncode == 0 and os.path.isfile(s.new_db), "run as a program on an upgraded stick: exit 0, data moved")
        out = subprocess.run([sys.executable, SCRIPT, "only-one"], capture_output=True, env=env)
        check(out.returncode == 2 and b"usage" in out.stdout, "wrong arguments: exit 2 with the usage line")
    finally:
        s.close()


# --- step 1 ------------------------------------------------------------------

def test_a_fresh_stick_is_ready_at_once():
    print("fresh stick: nothing to move, production mode straight away")
    s = Stick()
    try:
        code, out = s.run()
        check(code == 0, "exit 0")
        check(not os.path.exists(os.path.join(s.install, "packages")), "no packages\\ folder appears")
        check("搬" not in out, "says nothing about moving")
    finally:
        s.close()


def test_an_upgraded_stick_moves_everything():
    print("upgraded stick: the chat list and Ekko move under data\\webui, whole")
    s = Stick(upgraded=True)
    try:
        code, out = s.run()
        check(code == 0, "exit 0")
        check(not os.path.exists(s.old_db) and os.path.isfile(s.new_db), "the account and chat database moved")
        check(os.path.isfile(s.new_db) and len(rows(s.new_db)) == 3, "with every conversation in it")
        check(not os.path.exists(s.old_ekko) and os.path.isdir(s.new_ekko), ".ekko moved out of the npm package")
        check(os.path.isfile(os.path.join(s.new_ekko, ".ekko-hermes-skill-cleanup-v2.json")),
              "...with the marker that stops Ekko deleting the skills folder")
        check(os.path.isfile(os.path.join(s.new_ekko, "skills", "default", "my-skill", "SKILL.md")),
              "...the user's own skill")
        check(os.path.isfile(os.path.join(s.new_ekko, "workspace", "default", "s1", "report.md")),
              "...and the files Ekko wrote")
        check(not os.path.exists(os.path.join(s.install, "packages")), "the emptied packages\\ folders are gone")
        if os.path.isfile(s.new_db):
            moved_ws = os.path.join(s.new_ekko, "workspace", "default", "s1")
            check(rows(s.new_db) == [("s1", moved_ws), ("s2", USER_WS), ("s3", None)],
                  "the Ekko chat's folder points at the moved one; the user's own folder is untouched")
            check(rows(s.new_db, "SELECT workspace FROM workspace_run_changes ORDER BY id") == [(moved_ws,), (USER_WS,)],
                  "...in every workspace column")
            before = rows(s.new_db)
            code, out = s.run()
            check(code == 0 and rows(s.new_db) == before, "a second run changes nothing")
            check("工作文件夹" not in out, "...and reports nothing")
    finally:
        s.close()


def test_the_other_slash_style_is_repointed_too():
    print("a workspace recorded with the other OS's separators keeps its tail")
    s = Stick(upgraded=True)
    try:
        other = ("/Volumes/X/lib/node_modules/hermes-web-ui/.ekko/workspace/default/s9" if os.name == "nt"
                 else r"F:\U\runtime\node-win-x64\node_modules\hermes-web-ui\.ekko\workspace\default\s9")
        con = sqlite3.connect(s.old_db)
        with con:
            con.execute("INSERT INTO sessions VALUES ('s9', 'x', ?)", (other,))
        con.close()
        s.run()
        got = dict(rows(s.new_db))["s9"]
        check(got.startswith(s.new_ekko) and "node_modules" not in got and got.endswith("s9"),
              "points under data\\webui\\.ekko, tail kept (%s)" % got)
    finally:
        s.close()


def test_a_hot_journal_is_rolled_back_before_the_move():
    print("a crash mid-write left a hot journal: SQLite settles it, then it moves")
    s = Stick(upgraded=True)
    try:
        work = os.path.join(s.root, "live.db")
        shutil.copy2(s.old_db, work)
        con = sqlite3.connect(work, isolation_level=None)
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA cache_size=1")
        con.execute("BEGIN")
        for i in range(400):
            con.execute("INSERT INTO sessions VALUES (?, ?, NULL)", ("x%d" % i, "x" * 400))
        check(os.path.exists(work + "-journal"), "(the transaction has a journal on disk)")
        shutil.copyfile(work, s.old_db)
        shutil.copyfile(work + "-journal", s.old_db + "-journal")
        con.execute("ROLLBACK")
        con.close()
        code, _ = s.run()
        check(code == 0, "exit 0")
        check(not os.path.exists(s.old_db + "-journal") and not os.path.exists(s.new_db + "-journal"),
              "no journal is left behind or carried along")
        check(os.path.isfile(s.new_db) and len(rows(s.new_db)) == 3, "the half-written rows are rolled back, the old ones kept")
        check(os.path.isfile(s.new_db) and rows(s.new_db, "PRAGMA integrity_check") == [("ok",)], "the moved database is intact")
    finally:
        s.close()


def test_a_database_that_cannot_settle_moves_nothing():
    print("the old database keeps a side file: neither store moves")
    s = Stick(upgraded=True)
    try:
        # A zero-length journal, as a stick pulled at the wrong moment can
        # leave: not hot, so SQLite neither rolls it back nor removes it.
        io.open(s.old_db + "-journal", "wb").close()
        check(prep.settle(s.old_db) is False, "(settle() sees the side file SQLite leaves alone)")
        code, out = s.run()
        check(code == 10, "exit 10")
        check(s.untouched(), "the database and .ekko both stay where they were")
        check("什么都没动" in out, "and the user is told nothing moved")
    finally:
        s.close()


def test_a_database_in_use_is_not_moved():
    print("the old database is locked (an old Web UI writing to it): nothing moves")
    s = Stick(upgraded=True)
    con = sqlite3.connect(s.old_db, isolation_level=None)
    real = os.rename
    renames = []

    def rename(src, dst):  # succeeds like macOS would, so only our own check can stop it
        renames.append(src)
        return real(src, dst)
    try:
        con.execute("BEGIN EXCLUSIVE")
        prep.os.rename = rename
        code, out = s.run()
        prep.os.rename = real
        check(code == 10, "exit 10, so the launcher keeps the old layout this time")
        check(not renames, "no rename was even tried (the lock is seen, not Windows' refusal)")
        check(s.untouched(), "the database and .ekko both stay where they are")
        check("没动" in out, "and the user is told nothing moved")
    finally:
        prep.os.rename = real
        con.execute("ROLLBACK")
        con.close()
        s.close()


def test_a_target_database_in_use_is_not_judged():
    print("data\\webui's database is locked: it is not judged or moved aside, and nothing moves")
    s = Stick(upgraded=True)
    make_chat_db(s.new_db, [("n1", None)])
    con = sqlite3.connect(s.new_db, isolation_level=None)
    real = os.rename
    renamed = []

    def rename(src, dst):
        renamed.append(src)
        return real(src, dst)
    try:
        con.execute("BEGIN EXCLUSIVE")
        prep.os.rename = rename
        code, _ = s.run()
        prep.os.rename = real
        check(code == 10, "exit 10")
        check(s.new_db not in renamed, "the locked database was never renamed aside")
        check(os.path.isfile(s.old_db) and os.path.isdir(s.old_ekko), "the old stores are back where they were")
    finally:
        prep.os.rename = real
        con.execute("ROLLBACK")
        con.close()
        s.close()


def test_an_ekko_folder_in_use_moves_nothing():
    print("a file in .ekko is open (an Explorer window, a leftover process): the database stays too")
    if os.name != "nt":
        print("  skip  only Windows refuses to rename a folder with an open file")
        return
    s = Stick(upgraded=True)
    held = open(os.path.join(s.old_ekko, "workspace", "default", "s1", "report.md"), "rb")
    try:
        code, _ = s.run()
        check(code == 10, "exit 10")
        check(s.untouched(), "both stores stay where they are -- no new .ekko for the Web UI to start on")
    finally:
        held.close()
        s.close()


def test_a_failure_half_way_is_undone():
    print("the database fails to move after .ekko did: .ekko goes back")
    s = Stick(upgraded=True)
    real = prep.place_database
    try:
        def fail(*_a):
            raise OSError(errno.EACCES, "denied", s.old_db)
        prep.place_database = fail
        code, out = s.run()
        check(code == 10, "exit 10")
        check(s.untouched(), ".ekko is back in the package, the database never left")
        check(os.path.isfile(os.path.join(s.old_ekko, "skills", "default", "my-skill", "SKILL.md")), "...whole")
        check("放回原处" in out, "the user is told it was put back")
    finally:
        prep.place_database = real
        s.close()


def test_an_interrupt_half_way_is_undone():
    print("Ctrl+C during the move: what moved goes back, exit 10")
    s = Stick(upgraded=True)
    real = prep.place_database
    try:
        def interrupt(*_a):
            raise KeyboardInterrupt
        prep.place_database = interrupt
        code, _ = s.run()
        check(code == 10, "exit 10, not a code the launcher could read as ready")
        check(s.untouched(), "nothing is left half moved")
    finally:
        prep.place_database = real
        s.close()


def test_a_running_web_ui_defers_the_move():
    print("something answers on the Web UI port: do not move data under it")
    s = Stick(upgraded=True)
    port = free_port()
    saved = os.environ.get("PORT")
    os.environ["PORT"] = str(port)
    srv = socket.socket()
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    prep.port_in_use = _real_port_in_use
    try:
        code, out = s.run()
        check(code == 10, "exit 10")
        check(s.untouched(), "both stores stay where they are")
        check(str(port) in out, "the message names the port")
        fresh = Stick()
        try:
            code, _ = fresh.run()
            check(code == 0, "with nothing to move, a busy port does not matter")
        finally:
            fresh.close()
    finally:
        prep.port_in_use = lambda port: False
        srv.close()
        if saved is None:
            os.environ.pop("PORT", None)
        else:
            os.environ["PORT"] = saved
        s.close()


def test_data_in_both_places_is_never_merged_or_lost():
    print("both places hold the user's data: keep data\\webui's, set the old aside")
    s = Stick(upgraded=True)
    try:
        make_chat_db(s.new_db, [("n1", None)])
        make_ekko(s.new_ekko, memory=1)
        code, out = s.run()
        names = os.listdir(s.home)
        check(code == 0, "exit 0")
        check(rows(s.new_db) == [("n1", None)], "data\\webui's database is kept as it was")
        aside = [n for n in names if n.startswith("hermes-web-ui.db.legacy-")]
        check(len(aside) == 1 and len(rows(os.path.join(s.home, aside[0]))) == 3,
              "the old one sits beside it, renamed, all rows in it")
        ekko_aside = [n for n in names if n.startswith(".ekko-legacy-")]
        check(len(ekko_aside) == 1 and os.path.isfile(os.path.join(s.home, ekko_aside[0], "skills", "default", "my-skill", "SKILL.md")),
              "the old .ekko too, whole")
        check(prep.count_rows(os.path.join(s.new_ekko, "ekko.db"), "memory_nodes") == 1, "data\\webui's .ekko is kept")
        check(not os.path.exists(s.old_db) and not os.path.exists(s.old_ekko), "nothing is left for npm to delete")
        if aside:
            check(dict(rows(os.path.join(s.home, aside[0]))).get("s1") == OLD_WS,
                  "the set-aside database keeps its own paths")
        check("没有合并" in out, "and the user is told where the old one went")
    finally:
        s.close()


def test_paths_follow_an_ekko_that_was_set_aside():
    print("the old .ekko is set aside: its chats' folders point there, never into the .ekko in use")
    s = Stick(upgraded=True)
    try:
        make_ekko(s.new_ekko, memory=1)  # in use, with the user's data: it stays
        code, _ = s.run()
        aside = [n for n in os.listdir(s.home) if n.startswith(".ekko-legacy-")]
        check(code == 0 and len(aside) == 1, "exit 0, the old .ekko renamed aside")
        if aside:
            want = os.path.join(s.home, aside[0], "workspace", "default", "s1")
            check(dict(rows(s.new_db)).get("s1") == want and os.path.isfile(os.path.join(want, "report.md")),
                  "the moved chat database points the Ekko chat at the set-aside copy, where its files are")
    finally:
        s.close()


def test_an_empty_newer_store_gives_way_to_the_real_one():
    print("data\\webui holds stores a run created but nothing of the user's: the real ones win")
    s = Stick(upgraded=True)
    try:
        make_chat_db(s.new_db, [])
        # What Ekko creates at a production start: bundled skills, the marker,
        # injected MCP servers, empty session folders. And a sessions table
        # with a row in it must not count either: it is not the user's.
        make_ekko(s.new_ekko, sessions=[("z", "x")])
        code, _ = s.run()
        names = os.listdir(s.home)
        check(code == 0, "exit 0")
        check(len(rows(s.new_db)) == 3, "the database with the chats is the one in use")
        check(any(n.startswith("hermes-web-ui.db.empty-") for n in names), "the empty one is renamed aside, not deleted")
        check(os.path.isfile(os.path.join(s.new_ekko, "skills", "default", "my-skill", "SKILL.md")),
              "the .ekko with the user's data is the one in use")
        check(any(n.startswith(".ekko-empty-") for n in names), "the empty .ekko is renamed aside")
    finally:
        s.close()


def test_what_counts_as_the_users_ekko_data():
    print("ekko_has_data: memory, an added skill, a written file -- not bundled skills or ekko.db sessions")
    root = tempfile.mkdtemp(prefix="ekko-data-")
    try:
        cases = [("fresh", {}, False), ("sessions only", {"sessions": [("a", "b")]}, False),
                 ("memory", {"memory": 1}, True), ("added skill", {"user_skill": True}, True),
                 ("written file", {"files": True}, True)]
        for i, (label, kw, want) in enumerate(cases):
            r = os.path.join(root, str(i))
            make_ekko(r, **kw)
            check(prep.ekko_has_data(r) is want, "%s -> %s" % (label, want))
    finally:
        shutil.rmtree(root, onerror=_unlock)


def test_a_database_elsewhere_is_brought_onto_the_stick():
    print("--legacy-db: the Mac launcher's ~/packages database comes onto the stick")
    s = Stick()
    try:
        elsewhere = os.path.join(s.root, "home", "packages", "server", "data", "hermes-web-ui.db")
        make_chat_db(elsewhere, [("m1", None)])
        code, _ = s.run("--legacy-db", elsewhere)
        check(code == 0 and os.path.isfile(s.new_db) and rows(s.new_db) == [("m1", None)], "moved into data\\webui")
        check(not os.path.exists(elsewhere), "and gone from where it was")
        code, _ = s.run("--legacy-db")
        check(code == 2, "--legacy-db without a path is refused")
    finally:
        s.close()


def cross_drive_once():
    real = os.rename
    calls = []

    def rename(src, dst):
        calls.append((src, dst))
        if len(calls) == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real(src, dst)
    return rename, calls, real


def test_moving_to_another_drive_copies_then_deletes():
    print("data\\ on another drive: copy, then one rename, then delete -- for a folder and a file")
    s = Stick(upgraded=True)
    try:
        os.makedirs(s.home)
        # A git checkout in Ekko's workspace: its pack files are read-only.
        pack = os.path.join(s.old_ekko, "workspace", "default", "s1", ".git", "objects", "pack", "p.pack")
        write(pack, "x")
        os.chmod(pack, stat.S_IREAD)
        os.makedirs(s.new_ekko + ".partial-19990101-000000")  # an interrupted earlier copy
        rename, calls, real = cross_drive_once()
        prep.os.rename = rename
        try:
            prep.move(s.old_ekko, s.new_ekko)
        finally:
            prep.os.rename = real
        check(os.path.isfile(os.path.join(s.new_ekko, ".ekko-hermes-skill-cleanup-v2.json"))
              and os.path.isfile(os.path.join(s.new_ekko, "workspace", "default", "s1", "report.md")),
              "the copy is complete")
        check(not os.path.exists(s.old_ekko), "the source is gone after the copy, read-only files included")
        check(".partial-" in calls[-1][0], "the copy became the target by one rename")
        check(not [n for n in os.listdir(s.home) if ".partial-" in n], "no partial copy is left, the stale one included")

        rename, calls, real = cross_drive_once()
        prep.os.rename = rename
        try:
            prep.move(s.old_db, s.new_db)
        finally:
            prep.os.rename = real
        check(len(rows(s.new_db)) == 3 and not os.path.exists(s.old_db), "the database: copied whole, source gone")
    finally:
        s.close()


def test_nothing_is_ever_overwritten():
    print("move() refuses an existing target on every OS (os.rename replaces on macOS)")
    s = Stick(upgraded=True)
    real = os.rename
    try:
        make_chat_db(s.new_db, [("n1", None)])

        def posix_rename(src, dst):  # what macOS does: replace
            if os.path.exists(dst):
                os.remove(dst)
            return real(src, dst)
        prep.os.rename = posix_rename
        try:
            prep.move(s.old_db, s.new_db)
            refused = False
        except FileExistsError:
            refused = True
        check(refused, "FileExistsError, even with a rename that would replace")
        check(len(rows(s.old_db)) == 3 and rows(s.new_db) == [("n1", None)], "both files as they were")
    finally:
        prep.os.rename = real
        s.close()


# --- step 2 ------------------------------------------------------------------

def gateway_case(before, raw=None, yaml=None):
    s = Stick()
    try:
        path = os.path.join(s.home, "config.json")
        if yaml is not None:
            with io.open(os.path.join(s.data, "config.yaml"), "wb") as f:
                f.write(yaml)
        if before is not None or raw is not None:
            os.makedirs(s.home)
            with io.open(path, "wb") as f:
                f.write(raw if raw is not None else json.dumps(before).encode("utf-8"))
        s.run()
        return (s.config() if os.path.exists(path) else None), s.config_yaml()
    finally:
        s.close()


def test_the_gateway_starts_with_the_web_ui_unless_the_user_chose():
    print("gatewayAutoStart: on unless the settings page wrote true or false")
    after, _ = gateway_case(None)
    check(after is not None and json.loads(after.decode("utf-8")) == {"gatewayAutoStart": {"enabled": True, "include": ["default"]}},
          "no config.json: one is written, gateway on, default profile only")
    after = json.loads(gateway_case({"modelAliases": {"a": "b"}})[0].decode("utf-8"))
    check(after == {"modelAliases": {"a": "b"}, "gatewayAutoStart": {"enabled": True, "include": ["default"]}},
          "a config without the key: key added, other settings kept")
    for odd in ({"include": ["work"]}, {"include": ["work"], "enabled": "true"}, {"include": ["work"], "enabled": None}):
        after = json.loads(gateway_case({"gatewayAutoStart": odd})[0].decode("utf-8"))
        check(after == {"gatewayAutoStart": {"include": ["work"], "enabled": True}},
              "enabled %r (0.7.24 meant on): becomes true, the user's list kept" % odd.get("enabled", "missing"))
    for choice in (False, True):
        raw = json.dumps({"gatewayAutoStart": {"enabled": choice}, "x": 1}).encode("utf-8")
        check(gateway_case(None, raw)[0] == raw, "enabled: %s from the settings page is left byte for byte" % str(choice).lower())
    for raw in (b"{not json", b"[1, 2]"):
        check(gateway_case(None, raw)[0] == raw, "%r is left byte for byte" % raw)
    raw = b"\xef\xbb\xbf" + json.dumps({"gatewayAutoStart": {"enabled": False}, "y": "中"}, ensure_ascii=False).encode("utf-8")
    after = gateway_case(None, raw)[0]
    check(not after.startswith(b"\xef\xbb\xbf") and json.loads(after.decode("utf-8")) == {"gatewayAutoStart": {"enabled": False}, "y": "中"},
          "a BOM (Node's JSON.parse rejects it) is removed, the choice kept")
    after = gateway_case(None, b"\xef\xbb\xbf{}")[0]
    check(not after.startswith(b"\xef\xbb\xbf") and json.loads(after.decode("utf-8"))["gatewayAutoStart"]["enabled"] is True,
          "a BOM file without the key: read, seeded, written without the BOM")


def test_the_engine_config_is_left_to_the_engine():
    print("data\\config.yaml is not touched: the engine writes multiplex_profiles itself, after its own checks")
    _, yaml = gateway_case(None)
    check(yaml == CONFIG_YAML.encode("utf-8"), "byte for byte as it was, after seeding the gateway on")


def test_a_failing_step_never_stops_the_launch():
    print("steps 2 and 3 report and carry on")
    s = Stick()
    real_seed, real_replace = prep.seed_gateway_autostart, prep.os.replace
    try:
        def boom(*_a):
            raise PermissionError("denied")
        prep.seed_gateway_autostart = boom
        code, out = s.run()
        check(code == 0 and prep.SEED_MARK in s.index(), "the gateway step fails: exit 0, step 3 still ran")
        check("不影响启动" in out, "...and says so")
    finally:
        prep.seed_gateway_autostart = real_seed
        s.close()
    s = Stick()
    try:
        os.makedirs(s.home)
        before = json.dumps({"keep": 1}).encode("utf-8")
        with io.open(os.path.join(s.home, "config.json"), "wb") as f:
            f.write(before)
        prep.os.replace = lambda *_a: (_ for _ in ()).throw(OSError("pulled"))
        code, _ = s.run()
        prep.os.replace = real_replace
        check(code == 0 and s.config() == before, "a write that fails half way leaves config.json as it was")
    finally:
        prep.os.replace = real_replace
        s.close()


# --- step 3 ------------------------------------------------------------------

def test_new_chats_start_on_hermes():
    print("index.html stores 'hermes' as the new-chat agent, once, before the app loads")
    s = Stick()
    try:
        s.run()
        html = s.index()
        check(html.count(prep.SEED_MARK) == 1, "the script is in index.html")
        check(html.index(prep.SEED_MARK) < html.index("</head>"), "...inside <head>, before the deferred app module runs")
        check(html.replace(prep.SEED_SCRIPT, "") == INDEX, "nothing else changed, line endings included")
        s.run()
        check(s.index() == html, "a second start changes nothing")
        run_seed_in_node(html)
    finally:
        s.close()


def run_seed_in_node(html):
    """Execute the injected script against a fake localStorage."""
    node = os.path.join(PORTABLE, "runtime", "node-win-x64", "node.exe")
    if not os.path.isfile(node):
        node = shutil.which("node")
    if not node:
        print("  skip  no node to run the script with")
        return
    start = html.index(prep.SEED_MARK)
    body = html[html.index(">", start) + 1:html.index("</script>", start)]
    harness = ("var store = %s; global.localStorage = {getItem: function (k) { return k in store ? store[k] : null; },"
               " setItem: function (k, v) { store[k] = String(v); }};\n%s\nprocess.stdout.write(JSON.stringify(store));")
    for before, expect, label in (
            ({}, {prep.AGENT_KEY: "hermes"}, "with nothing stored, it stores 'hermes'"),
            ({prep.AGENT_KEY: "ekko-agent"}, {prep.AGENT_KEY: "ekko-agent"}, "a user who picked Ekko keeps Ekko")):
        out = subprocess.run([node, "-e", harness % (json.dumps(before), body)], capture_output=True, text=True)
        check(out.returncode == 0 and json.loads(out.stdout or "null") == expect, "(node) " + label)
    out = subprocess.run([node, "-e", "global.localStorage = {getItem: function () { throw new Error('blocked'); }};\n" + body],
                         capture_output=True, text=True)
    check(out.returncode == 0, "(node) storage that throws (private mode) does not break the page")


def test_a_client_that_no_longer_reads_the_key_is_reported():
    print("upstream renamed the key: say so instead of seeding a value nothing reads")
    s = Stick()
    try:
        os.remove(os.path.join(s.package, "dist", "client", "assets", "js", "ChatPanel-x.js"))
        code, out = s.run()
        check(code == 0, "the launch goes on")
        check(prep.SEED_MARK not in s.index(), "index.html is left alone")
        check("Hermes" in out and prep.AGENT_KEY in out, "the console tells the user to pick Hermes")
    finally:
        s.close()


def test_an_index_without_head_is_left_alone():
    print("no </head> to anchor on: report and carry on")
    s = Stick()
    try:
        write(os.path.join(s.package, "dist", "client", "index.html"), "<html><body></body></html>")
        code, out = s.run()
        check(code == 0, "the launch goes on")
        check(prep.SEED_MARK not in s.index(), "index.html is left alone")
        check("</head>" in out, "the console says why")
    finally:
        s.close()


def test_move_only_does_only_the_move():
    print("--move-only (Menu [9], setup.ps1): move, and nothing else")
    s = Stick(upgraded=True)
    try:
        code, _ = s.run("--move-only")
        check(code == 0, "exit 0")
        check(os.path.isdir(s.new_ekko) and not os.path.exists(s.old_ekko), ".ekko moved")
        check(not os.path.exists(os.path.join(s.home, "config.json")), "no config.json written")
        check(b"multiplex_profiles" not in s.config_yaml(), "config.yaml untouched")
        check(prep.SEED_MARK not in s.index(), "index.html untouched (npm is about to replace it)")
    finally:
        s.close()


if __name__ == "__main__":
    for fn in (test_exit_codes_are_what_the_launchers_test_for,
               test_a_fresh_stick_is_ready_at_once,
               test_an_upgraded_stick_moves_everything,
               test_the_other_slash_style_is_repointed_too,
               test_a_hot_journal_is_rolled_back_before_the_move,
               test_a_database_that_cannot_settle_moves_nothing,
               test_a_database_in_use_is_not_moved,
               test_a_target_database_in_use_is_not_judged,
               test_an_ekko_folder_in_use_moves_nothing,
               test_a_failure_half_way_is_undone,
               test_an_interrupt_half_way_is_undone,
               test_a_running_web_ui_defers_the_move,
               test_data_in_both_places_is_never_merged_or_lost,
               test_paths_follow_an_ekko_that_was_set_aside,
               test_an_empty_newer_store_gives_way_to_the_real_one,
               test_what_counts_as_the_users_ekko_data,
               test_a_database_elsewhere_is_brought_onto_the_stick,
               test_moving_to_another_drive_copies_then_deletes,
               test_nothing_is_ever_overwritten,
               test_the_gateway_starts_with_the_web_ui_unless_the_user_chose,
               test_the_engine_config_is_left_to_the_engine,
               test_a_failing_step_never_stops_the_launch,
               test_new_chats_start_on_hermes,
               test_a_client_that_no_longer_reads_the_key_is_reported,
               test_an_index_without_head_is_left_alone,
               test_move_only_does_only_the_move):
        try:
            fn()
        except Exception as e:  # report and go on, so one crash does not hide the rest
            check(False, "%s crashed: %r" % (fn.__name__, e))
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")

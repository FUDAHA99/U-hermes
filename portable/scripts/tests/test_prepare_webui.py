# -*- coding: utf-8 -*-
"""prepare-webui.py: the Web UI's data under data\\webui, the gateway on, new
chats on Hermes -- without losing what an older version left elsewhere.

Each test builds a miniature stick in a temp dir: the install folder, the
npm package with the Web UI's client, and, for an upgraded stick, the two
places Web UI 0.7.x kept data outside production mode. The databases are
real SQLite files with the tables and columns the Web UI uses.

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

_spec = importlib.util.spec_from_file_location("prepare_webui", os.path.join(SCRIPTS, "prepare-webui.py"))
prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prep)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


INDEX = ('<!doctype html>\r\n<html lang="zh-CN">\r\n<head>\r\n  <meta charset="UTF-8" />\r\n'
         '  <script type="module" crossorigin src="/assets/js/index-x.js"></script>\r\n</head>\r\n'
         '<body><div id="app"></div></body>\r\n</html>\r\n')
CHAT_PANEL = 'const NEW_CHAT_AGENT_STORAGE_KEY = "hermes_new_chat_agent_v1";'


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unlock(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


class Stick(object):
    """A U-Hermes install in a temp dir."""

    def __init__(self, upgraded=False):
        self.root = tempfile.mkdtemp(prefix="prep-webui-")
        self.install = os.path.join(self.root, "U-Hermes")
        self.home = os.path.join(self.install, "data", "webui")
        self.package = os.path.join(self.install, "runtime", "node-win-x64", "node_modules", "hermes-web-ui")
        self.old_db = os.path.join(self.install, "packages", "server", "data", "hermes-web-ui.db")
        self.old_ekko = os.path.join(self.package, ".ekko")
        client = os.path.join(self.package, "dist", "client")
        os.makedirs(os.path.join(client, "assets", "js"))
        os.makedirs(os.path.join(self.install, "data"))
        with io.open(os.path.join(client, "index.html"), "w", encoding="utf-8", newline="") as f:
            f.write(INDEX)
        with io.open(os.path.join(client, "assets", "js", "ChatPanel-x.js"), "w", encoding="utf-8") as f:
            f.write(CHAT_PANEL)
        if upgraded:
            # The sessions as a 0.7.24-0.7.33 stick has them: an Ekko chat
            # whose folder is in the npm package (recorded while the stick was
            # F:), and a Hermes chat in the user's own folder.
            self.ekko_ws = r"F:\U-Hermes\runtime\node-win-x64\node_modules\hermes-web-ui\.ekko\workspace\default\s1"
            self.user_ws = "H:\\U-Hermes工作区\\小说"
            make_db(self.old_db, [("s1", self.ekko_ws), ("s2", self.user_ws), ("s3", None)])
            make_db(os.path.join(self.old_ekko, "ekko.db"), [("s1", self.ekko_ws)])
            write(os.path.join(self.old_ekko, ".ekko-hermes-skill-cleanup-v2.json"), "{}")
            write(os.path.join(self.old_ekko, "skills", "default", "my-skill", "SKILL.md"), "# mine")
            write(os.path.join(self.old_ekko, "workspace", "default", "s1", "report.md"), "written by Ekko")

    def run(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = prep.main(["prepare-webui.py", self.install, self.home, self.package] + list(extra))
        return code, out.getvalue()

    def index(self):
        with io.open(os.path.join(self.package, "dist", "client", "index.html"), encoding="utf-8", newline="") as f:
            return f.read()

    def config(self):
        with io.open(os.path.join(self.home, "config.json"), "rb") as f:
            return f.read()

    def close(self):
        shutil.rmtree(self.root, onerror=_unlock)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_db(path, sessions):
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


def rows(db, sql="SELECT id, workspace FROM sessions ORDER BY id"):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# --- step 1 ------------------------------------------------------------------

def test_a_fresh_stick_is_ready_at_once():
    print("fresh stick: nothing to move, production mode straight away")
    s = Stick()
    try:
        code, out = s.run()
        check(code == prep.READY, "exit 0")
        check(not os.path.exists(os.path.join(s.install, "packages")), "no packages\\ folder appears")
        check("搬" not in out, "says nothing about moving")
    finally:
        s.close()


def test_an_upgraded_stick_moves_everything():
    print("upgraded stick: the chat list and Ekko move under data\\webui, whole")
    s = Stick(upgraded=True)
    try:
        code, out = s.run()
        new_db = os.path.join(s.home, "hermes-web-ui.db")
        new_ekko = os.path.join(s.home, ".ekko")
        check(code == prep.READY, "exit 0")
        check(not os.path.exists(s.old_db) and os.path.isfile(new_db), "the account and chat database moved")
        check(len(rows(new_db)) == 3, "with every conversation in it")
        check(not os.path.exists(s.old_ekko) and os.path.isdir(new_ekko), ".ekko moved out of the npm package")
        check(os.path.isfile(os.path.join(new_ekko, ".ekko-hermes-skill-cleanup-v2.json")),
              "...with the marker that stops Ekko deleting the skills folder")
        check(os.path.isfile(os.path.join(new_ekko, "skills", "default", "my-skill", "SKILL.md")),
              "...the user's own skill")
        check(os.path.isfile(os.path.join(new_ekko, "workspace", "default", "s1", "report.md")),
              "...and the files Ekko wrote")
        check(not os.path.exists(os.path.join(s.install, "packages")), "the emptied packages\\ folders are gone")
        moved_ws = os.path.join(new_ekko, "workspace", "default", "s1")
        check(rows(new_db) == [("s1", moved_ws), ("s2", s.user_ws), ("s3", None)],
              "the Ekko chat's folder points at the moved one; the user's own folder is untouched")
        check(rows(new_db, "SELECT workspace FROM workspace_run_changes ORDER BY id") == [(moved_ws,), (s.user_ws,)],
              "...in every workspace column")
        check(rows(os.path.join(new_ekko, "ekko.db")) == [("s1", moved_ws)], "...and in Ekko's own database")
        check("data" in out and "webui" in out, "says where the data went")
        before = [rows(new_db), rows(os.path.join(new_ekko, "ekko.db"))]
        code, out = s.run()
        check(code == prep.READY and [rows(new_db), rows(os.path.join(new_ekko, "ekko.db"))] == before,
              "a second run changes nothing")
        check("工作文件夹" not in out, "...and reports nothing")
    finally:
        s.close()


def test_a_hot_journal_is_rolled_back_before_the_move():
    print("a crash mid-write left a hot journal: SQLite settles it, then it moves")
    s = Stick(upgraded=True)
    try:
        # Freeze the files halfway through a transaction, as a pulled stick would.
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
        new_db = os.path.join(s.home, "hermes-web-ui.db")
        check(code == prep.READY, "exit 0")
        check(not os.path.exists(s.old_db + "-journal") and not os.path.exists(new_db + "-journal"),
              "no journal is left behind or carried along")
        check(len(rows(new_db)) == 3, "the half-written rows are rolled back, the old ones kept")
        check(rows(new_db, "PRAGMA integrity_check") == [("ok",)], "the moved database is intact")
    finally:
        s.close()


def test_a_database_in_use_is_not_moved():
    print("the old database is open (an old Web UI still running): nothing moves")
    s = Stick(upgraded=True)
    con = sqlite3.connect(s.old_db, isolation_level=None)
    try:
        con.execute("BEGIN EXCLUSIVE")
        code, out = s.run()
        check(code == prep.DEFERRED, "exit 10, so the launcher keeps the old layout this time")
        check(os.path.exists(s.old_db), "the database stays where it is")
        check(not os.path.exists(os.path.join(s.home, "hermes-web-ui.db")), "no copy appears in data\\webui")
        check("没有丢" in out or "先不搬" in out, "and the user is told nothing was lost")
    finally:
        con.execute("ROLLBACK")
        con.close()
        s.close()


def test_an_ekko_folder_in_use_is_not_moved():
    print("a file in .ekko is open: the folder stays whole, in place")
    if os.name != "nt":
        print("  skip  only Windows refuses to rename a folder with an open file")
        return
    s = Stick(upgraded=True)
    held = open(os.path.join(s.old_ekko, "skills", "default", "my-skill", "SKILL.md"), "rb")
    try:
        code, _ = s.run()
        check(code == prep.DEFERRED, "exit 10")
        check(os.path.isfile(os.path.join(s.old_ekko, "ekko.db")), ".ekko is still whole in the package")
        check(not os.path.exists(os.path.join(s.home, ".ekko")), "nothing half-moved under data\\webui")
    finally:
        held.close()
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
    try:
        code, out = s.run()
        check(code == prep.DEFERRED, "exit 10")
        check(os.path.exists(s.old_db) and os.path.isdir(s.old_ekko), "both stores stay where they are")
        check(str(port) in out, "the message names the port")
        fresh = Stick()
        try:
            code, _ = fresh.run()
            check(code == prep.READY, "with nothing to move, a busy port does not matter")
        finally:
            fresh.close()
    finally:
        srv.close()
        if saved is None:
            os.environ.pop("PORT", None)
        else:
            os.environ["PORT"] = saved
        s.close()


def test_data_in_both_places_is_never_merged_or_lost():
    print("both places hold data: keep data\\webui's, set the old aside")
    s = Stick(upgraded=True)
    try:
        make_db(os.path.join(s.home, "hermes-web-ui.db"), [("n1", None)])
        make_db(os.path.join(s.home, ".ekko", "ekko.db"), [("n1", None)])
        code, out = s.run()
        names = os.listdir(s.home)
        check(code == prep.READY, "exit 0")
        check(rows(os.path.join(s.home, "hermes-web-ui.db")) == [("n1", None)], "data\\webui's database is kept as it was")
        aside = [n for n in names if n.startswith("hermes-web-ui.db.legacy-")]
        check(len(aside) == 1 and len(rows(os.path.join(s.home, aside[0]))) == 3,
              "the old one sits beside it, renamed, all rows in it")
        ekko_aside = [n for n in names if n.startswith(".ekko-legacy-")]
        check(len(ekko_aside) == 1 and os.path.isfile(os.path.join(s.home, ekko_aside[0], "skills", "default", "my-skill", "SKILL.md")),
              "the old .ekko too, whole")
        check(rows(os.path.join(s.home, ".ekko", "ekko.db")) == [("n1", None)], "data\\webui's .ekko is kept")
        check(not os.path.exists(s.old_db) and not os.path.exists(s.old_ekko), "nothing is left for npm to delete")
        check("没有合并" in out, "and the user is told where the old one went")
    finally:
        s.close()


def test_an_empty_newer_store_gives_way_to_the_real_one():
    print("data\\webui holds an empty database (a run that could not move first): the real one wins")
    s = Stick(upgraded=True)
    try:
        make_db(os.path.join(s.home, "hermes-web-ui.db"), [])
        make_db(os.path.join(s.home, ".ekko", "ekko.db"), [])
        code, _ = s.run()
        names = os.listdir(s.home)
        check(code == prep.READY, "exit 0")
        check(len(rows(os.path.join(s.home, "hermes-web-ui.db"))) == 3, "the database with the chats is the one in use")
        check(any(n.startswith("hermes-web-ui.db.empty-") for n in names), "the empty one is renamed aside, not deleted")
        check(os.path.isfile(os.path.join(s.home, ".ekko", "skills", "default", "my-skill", "SKILL.md")),
              "the .ekko with the user's data is the one in use")
        check(any(n.startswith(".ekko-empty-") for n in names), "the empty .ekko is renamed aside")
    finally:
        s.close()


def test_moving_to_another_drive_copies_then_deletes():
    print("data\\.env put HERMES_WEB_UI_HOME on another drive: copy, then one rename, then delete")
    s = Stick(upgraded=True)
    real = os.rename
    calls = []

    def cross_drive_once(src, dst):
        calls.append((src, dst))
        if len(calls) == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real(src, dst)

    try:
        os.makedirs(s.home)
        prep.os.rename = cross_drive_once
        dst = os.path.join(s.home, ".ekko")
        prep.move(s.old_ekko, dst)
        check(os.path.isfile(os.path.join(dst, ".ekko-hermes-skill-cleanup-v2.json"))
              and os.path.isfile(os.path.join(dst, "workspace", "default", "s1", "report.md")),
              "the copy is complete")
        check(not os.path.exists(s.old_ekko), "the source is gone after the copy")
        check(".partial-" in calls[-1][0], "the copy became the target by one rename")
    finally:
        prep.os.rename = real
        s.close()


def test_nothing_is_ever_overwritten():
    print("move() refuses an existing target, on every OS (os.rename replaces on macOS)")
    s = Stick(upgraded=True)
    try:
        make_db(os.path.join(s.home, "hermes-web-ui.db"), [("n1", None)])
        try:
            prep.move(s.old_db, os.path.join(s.home, "hermes-web-ui.db"))
            refused = False
        except FileExistsError:
            refused = True
        check(refused, "FileExistsError")
        check(len(rows(s.old_db)) == 3 and rows(os.path.join(s.home, "hermes-web-ui.db")) == [("n1", None)],
              "both files as they were")
    finally:
        s.close()


# --- step 2 ------------------------------------------------------------------

def gateway_case(before, raw=None):
    s = Stick()
    try:
        path = os.path.join(s.home, "config.json")
        if before is not None or raw is not None:
            os.makedirs(s.home)
            with io.open(path, "wb") as f:
                f.write(raw if raw is not None else json.dumps(before).encode("utf-8"))
        s.run()
        after = s.config() if os.path.exists(path) else None
        return after
    finally:
        s.close()


def test_the_gateway_starts_with_the_web_ui_unless_the_user_chose():
    print("gatewayAutoStart: on unless the settings page wrote true or false")
    after = gateway_case(None)
    check(after is not None and json.loads(after.decode("utf-8")) == {"gatewayAutoStart": {"enabled": True, "include": ["default"]}},
          "no config.json: one is written, gateway on, default profile only")
    after = json.loads(gateway_case({"modelAliases": {"a": "b"}}).decode("utf-8"))
    check(after == {"modelAliases": {"a": "b"}, "gatewayAutoStart": {"enabled": True, "include": ["default"]}},
          "a config without the key: key added, other settings kept")
    after = json.loads(gateway_case({"gatewayAutoStart": {"include": ["work"]}}).decode("utf-8"))
    check(after == {"gatewayAutoStart": {"include": ["work"], "enabled": True}},
          "0.7.24's shape (no 'enabled', meaning on): enabled added, the user's list kept")
    for choice in (False, True):
        before = {"gatewayAutoStart": {"enabled": choice}, "x": 1}
        raw = json.dumps(before).encode("utf-8")
        check(gateway_case(None, raw) == raw, "enabled: %s from the settings page is left byte for byte" % str(choice).lower())
    raw = b"{not json"
    check(gateway_case(None, raw) == raw, "a file that is not JSON is left byte for byte")
    raw = b"[1, 2]"
    check(gateway_case(None, raw) == raw, "a file that is not an object is left byte for byte")
    raw = b"\xef\xbb\xbf" + json.dumps({"gatewayAutoStart": {"enabled": False}, "y": "中"}, ensure_ascii=False).encode("utf-8")
    after = gateway_case(None, raw)
    check(not after.startswith(b"\xef\xbb\xbf") and json.loads(after.decode("utf-8")) == {"gatewayAutoStart": {"enabled": False}, "y": "中"},
          "a BOM (Node's JSON.parse rejects it) is removed, the choice kept")
    after = gateway_case(None, b"\xef\xbb\xbf{}")
    check(not after.startswith(b"\xef\xbb\xbf") and json.loads(after.decode("utf-8"))["gatewayAutoStart"]["enabled"] is True,
          "a BOM file without the key: read, seeded, written without the BOM")


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
        check(out.returncode == 0 and json.loads(out.stdout) == expect, "(node) " + label)
    out = subprocess.run([node, "-e", "global.localStorage = {getItem: function () { throw new Error('blocked'); }};\n" + body],
                         capture_output=True, text=True)
    check(out.returncode == 0, "(node) storage that throws (private mode) does not break the page")


def test_a_client_that_no_longer_reads_the_key_is_reported():
    print("upstream renamed the key: say so instead of seeding a value nothing reads")
    s = Stick()
    try:
        os.remove(os.path.join(s.package, "dist", "client", "assets", "js", "ChatPanel-x.js"))
        code, out = s.run()
        check(code == prep.READY, "the launch goes on")
        check(prep.SEED_MARK not in s.index(), "index.html is left alone")
        check("Hermes" in out and prep.AGENT_KEY in out, "the console tells the user to pick Hermes")
    finally:
        s.close()


def test_an_index_without_head_is_left_alone():
    print("no </head> to anchor on: report and carry on")
    s = Stick()
    try:
        path = os.path.join(s.package, "dist", "client", "index.html")
        write(path, "<html><body></body></html>")
        code, out = s.run()
        check(code == prep.READY, "the launch goes on")
        check(prep.SEED_MARK not in s.index(), "index.html is left alone")
        check("</head>" in out, "the console says why")
    finally:
        s.close()


def test_move_only_does_only_the_move():
    print("--move-only (Menu [9], setup.ps1): move, and nothing else")
    s = Stick(upgraded=True)
    try:
        code, _ = s.run("--move-only")
        check(code == prep.READY, "exit 0")
        check(os.path.isdir(os.path.join(s.home, ".ekko")) and not os.path.exists(s.old_ekko), ".ekko moved")
        check(not os.path.exists(os.path.join(s.home, "config.json")), "no config.json written")
        check(prep.SEED_MARK not in s.index(), "index.html untouched (npm is about to replace it)")
    finally:
        s.close()


def test_bad_arguments():
    print("wrong arguments: exit 2, touch nothing")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = prep.main(["prepare-webui.py", "only-one"])
    check(code == 2 and "usage" in out.getvalue(), "exit 2 with the usage line")


if __name__ == "__main__":
    test_a_fresh_stick_is_ready_at_once()
    test_an_upgraded_stick_moves_everything()
    test_a_hot_journal_is_rolled_back_before_the_move()
    test_a_database_in_use_is_not_moved()
    test_an_ekko_folder_in_use_is_not_moved()
    test_a_running_web_ui_defers_the_move()
    test_data_in_both_places_is_never_merged_or_lost()
    test_an_empty_newer_store_gives_way_to_the_real_one()
    test_moving_to_another_drive_copies_then_deletes()
    test_nothing_is_ever_overwritten()
    test_the_gateway_starts_with_the_web_ui_unless_the_user_chose()
    test_new_chats_start_on_hermes()
    test_a_client_that_no_longer_reads_the_key_is_reported()
    test_an_index_without_head_is_left_alone()
    test_move_only_does_only_the_move()
    test_bad_arguments()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")

"""The launcher must not touch the Web UI password, and must not leave an
upgraded machine unable to log in.

first-login.py used to claim the super-admin account with a generated
password. That is gone by decision: the login page prints
"默认登录名：admin，默认密码：123456" to every unauthenticated visitor, and
the product now keeps that sentence true. What holds the risk down is
BIND_HOST=127.0.0.1 in the launcher -- which data\\.env can override.

Two things still have to be right, and both are asserted here:

  * the script changes nothing. A silent POST to /api/auth/change-password
    would put a machine in a state where the login page is wrong and nobody
    knows the password.
  * a machine upgraded from a version that DID rotate still has a rotated
    password, so the login page is wrong for it. Its 登录密码.txt has to be
    surfaced, or the user types 123456, gets refused, and has no next step.

Run:  python portable/scripts/tests/test_first_login.py
"""
import http.server
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading

# CI pipes this suite's stdout, and Python then encodes it with the machine's
# ANSI codepage rather than UTF-8. On GitHub's en-US Windows runner that is
# cp1252, which cannot encode a single Chinese character, so the first label
# containing one killed the whole release job with a UnicodeEncodeError.
# Unreproducible on a Chinese Windows box, where the codepage is GBK.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "first_login", os.path.join(SCRIPTS, "first-login.py")
)
fl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fl)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


class FakeWebUI(http.server.BaseHTTPRequestHandler):
    """Mirrors packages/server/src/controllers/auth.ts closely enough to matter.

    Kept even though nothing should call the auth endpoints any more: the
    point of the first test below is that they are NOT called, and an
    assertion about a route that does not exist proves nothing.
    """

    users = {"admin": "123456"}   # username -> password
    tokens = {}         # token -> username
    calls = []

    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeWebUI.calls.append(("GET", self.path))
        if self.path == "/health":
            self._json(200, {"ok": True})
        elif self.path == "/api/auth/status":
            self._json(200, {"hasUsers": len(FakeWebUI.users) > 0})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        FakeWebUI.calls.append(("POST", self.path))
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/api/auth/login":
            username, password = body.get("username"), body.get("password")
            if FakeWebUI.users.get(username) != password:
                self._json(401, {"error": "Invalid username or password"})
                return
            token = "tok-%d" % len(FakeWebUI.tokens)
            FakeWebUI.tokens[token] = username
            self._json(200, {"token": token})
            return

        if self.path == "/api/auth/change-password":
            auth = self.headers.get("Authorization", "")
            user = FakeWebUI.tokens.get(auth[7:]) if auth.startswith("Bearer ") else None
            if not user:
                self._json(401, {"error": "unauthorized"})
                return
            current, new = body.get("currentPassword"), body.get("newPassword")
            if not current or not new:
                self._json(400, {"error": "Current password and new password are required"})
                return
            if len(new) < 6:
                self._json(400, {"error": "too short"})
                return
            if FakeWebUI.users.get(user) != current:
                self._json(400, {"error": "wrong current password"})
                return
            FakeWebUI.users[user] = new
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not found"})


def serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeWebUI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def run_script(base, home):
    """Run main() the way the launcher does, and capture what the user sees.

    webbrowser.open is stubbed rather than tolerated: on a CI runner it can
    block, and on a developer machine it opens a real window every run.
    """
    opened = []
    real_open = fl.webbrowser.open
    fl.webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    buffer = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buffer
    try:
        code = fl.main(["first-login.py", base, home, "10"])
    finally:
        sys.stdout = real_stdout
        fl.webbrowser.open = real_open
    return code, buffer.getvalue(), opened


ROTATED_FILE = """U-Hermes 网页界面登录信息

    用户名： admin
    密　码： iavz2nvaxn

这个密码是本份 U 盘专用的，换电脑登录也是这一个。
"""


def main():
    srv, base = serve()
    home = tempfile.mkdtemp(prefix="uh-firstlogin-")
    try:
        print("a normal launch")
        FakeWebUI.users = {"admin": "123456"}
        FakeWebUI.tokens, FakeWebUI.calls = {}, []
        code, out, opened = run_script(base, home)

        check(code == 0, "the launch is not blocked")
        check(FakeWebUI.users.get("admin") == "123456",
              "the factory password is left exactly as it was")
        posts = [p for method, p in FakeWebUI.calls if method == "POST"]
        check(posts == [],
              "nothing is posted at all (saw: %s)" % (posts or "nothing"))
        check("/api/auth/change-password" not in [p for _, p in FakeWebUI.calls],
              "...in particular the password is never changed")
        check(opened and opened[0].startswith(base),
              "the browser is still opened at the Web UI")
        check("123456" not in out,
              "and nothing is printed about a password on a normal launch")

        print("a machine upgraded from a version that rotated the password")
        # Its password is NOT 123456, but the login page insists that it is.
        # The only record is this file, four directories deep in data\\.
        path = os.path.join(home, fl.PASSWORD_FILE)
        with io.open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(ROTATED_FILE)
        FakeWebUI.users = {"admin": "iavz2nvaxn"}
        FakeWebUI.tokens, FakeWebUI.calls = {}, []
        code, out, _ = run_script(base, home)

        check(code == 0, "still does not block the launch")
        check("iavz2nvaxn" in out,
              "the password that actually works is printed")
        check(path in out, "...along with where it is stored")
        check("123456" in out,
              "...and it says the page's default does not apply here")
        check(FakeWebUI.users.get("admin") == "iavz2nvaxn",
              "that machine's password is not reset to the factory one")
        check([p for method, p in FakeWebUI.calls if method == "POST"] == [],
              "...and still nothing is posted")

        print("a file that cannot be read")
        # Surfacing the password is a convenience. Failing to do it must not
        # cost the user their launch.
        bad = tempfile.mkdtemp(prefix="uh-firstlogin-bad-")
        os.mkdir(os.path.join(bad, fl.PASSWORD_FILE))   # a directory, not a file
        try:
            code, out, opened = run_script(base, bad)
            check(code == 0, "a broken password file does not block the launch")
            check(opened and opened[0].startswith(base),
                  "...and the browser is still opened")
        finally:
            shutil.rmtree(bad, ignore_errors=True)

        print("the Web UI never coming up")
        check(fl.wait_for("http://127.0.0.1:9/health", 1.0) is False,
              "a dead address is reported rather than hung on")
    finally:
        srv.shutdown()
        shutil.rmtree(home, ignore_errors=True)

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("first-login: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The Web UI account must never be left unclaimed, and never be clobbered.

hermes-web-ui hands its super-admin account to whoever first posts
admin/123456, and prints those credentials on the login page. first-login.py
claims it with a generated password instead.

That script talks to three upstream endpoints, and upstream ships several
releases a week. This stands a fake Web UI in front of it that behaves the
way the real one's controllers do, so an API change shows up here instead of
as an install that is silently left open.

Run:  python portable/scripts/tests/test_first_login.py
"""
import http.server
import importlib.util
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

    In particular: login only bootstraps when there are no users AND the
    credentials are exactly the documented defaults, and change-password
    needs the current one and a new one of at least 6 characters.
    """

    # Seeded, like hermes-web-ui 0.7.22 does at startup. This mock used to
    # start empty, modelling 0.6.5 (the version that happened to be
    # installed locally), where the account was created by the first login.
    # On 0.7.22 /api/auth/status answers hasUsers=true immediately, so the
    # old gate in first-login.py returned "already claimed" on a brand-new
    # install and the factory password stayed live. The suite passed
    # throughout, because the mock agreed with the wrong version.
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


def main():
    srv, base = serve()
    home = tempfile.mkdtemp(prefix="uh-firstlogin-")
    try:
        FakeWebUI.users = {"admin": "123456"}
        FakeWebUI.tokens, FakeWebUI.calls = {}, []

        print("a fresh install, factory password still live")
        password = fl.claim(base)
        check(password is not None, "the account is claimed")
        check(len(password or "") >= 6, "the new password satisfies upstream's 6-char minimum")
        check(FakeWebUI.users.get("admin") == password, "the server now holds the generated password")
        check(FakeWebUI.users.get("admin") != "123456", "the default password no longer works")
        check(set("0O1lI").isdisjoint(password or ""),
              "the password avoids glyphs people mistype when retyping it")

        path = fl.remember(home, password)
        check(path is not None and os.path.exists(path), "the password is written to the stick")
        if path:
            raw = open(path, "rb").read()
            check(raw[:3] == b"\xef\xbb\xbf", "the file has a BOM so 记事本 shows Chinese correctly")
            check(password in raw.decode("utf-8-sig"), "the file actually contains the password")

        print("running again against the same install")
        again = fl.claim(base)
        check(again is None, "an account that already exists is left alone")
        check(FakeWebUI.users.get("admin") == password, "its password is unchanged")

        print("an install somebody already set up by hand")
        FakeWebUI.users, FakeWebUI.tokens = {"admin": "hunter2"}, {}
        check(fl.claim(base) is None, "a user's own password is never overwritten")
        check(FakeWebUI.users["admin"] == "hunter2", "...and still works")

        print("the endpoints the script depends on")
        FakeWebUI.users = {"admin": "123456"}
        FakeWebUI.tokens, FakeWebUI.calls = {}, []
        fl.claim(base)
        used = [p for _, p in FakeWebUI.calls]
        for endpoint in ("/api/auth/login", "/api/auth/change-password"):
            check(endpoint in used, "uses %s" % endpoint)
        check("/api/auth/status" not in used,
              "does NOT decide on /api/auth/status -- hasUsers is true from "
              "the first second on the version that ships")

        print("the shape that made this a no-op in the shipped build")
        # hermes-web-ui 0.7.22 seeds the default super admin at startup, so
        # hasUsers answers true on a brand-new install. Gating on it meant
        # the script decided "already claimed" every time and the factory
        # password stayed live -- while the docs told users it had been
        # replaced. Pin the behaviour, not the endpoint.
        FakeWebUI.users = {"admin": "123456"}
        FakeWebUI.tokens, FakeWebUI.calls = {}, []
        import urllib.request as _u
        with _u.urlopen(base + "/api/auth/status", timeout=5) as r:
            status = json.loads(r.read().decode())
        check(status.get("hasUsers") is True,
              "the server reports hasUsers=true before anyone has claimed it")
        fresh = fl.claim(base)
        check(fresh is not None,
              "...and the account is claimed anyway")
        check(FakeWebUI.users["admin"] == fresh,
              "...with the factory password actually replaced")

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

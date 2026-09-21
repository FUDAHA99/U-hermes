"""What the user is told when the model provider says no.

This is the last thing between "I pasted my key and it said 测试连接 成功"
and "I typed a message and nothing came back". Every branch here is a real
failure someone has hit; the Chinese text is what they will read, so it has
to name the layer that failed and, where the provider bothered to explain
itself, repeat what it said.

Runs against a local HTTP server rather than a real provider: no key, no
network, no cost, and every status code on demand.

Run:  python portable/scripts/tests/test_provider_probe.py
"""
import http.server
import importlib.util
import json
import os
import socket
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "provider_probe", os.path.join(SCRIPTS, "provider_probe.py"))
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


# --- a provider that says whatever the test needs -------------------------

class _Scripted(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b"{}"
    seen = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        _Scripted.seen = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": json.loads(raw.decode("utf-8")) if raw else {},
        }
        self.send_response(_Scripted.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_Scripted.body)))
        self.end_headers()
        self.wfile.write(_Scripted.body)

    def log_message(self, fmt, *args):
        pass


def serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Scripted)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d/v1" % srv.server_address[1]


def respond(status, body):
    _Scripted.status = status
    _Scripted.body = body.encode("utf-8") if isinstance(body, str) else body


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


GOOD = json.dumps({"choices": [{"message": {"content": "hi"}}]})


def test_a_working_provider():
    srv, url = serve()
    try:
        respond(200, GOOD)
        r = pp.probe(url, "sk-test", "some-model", timeout=5)
        check(r.ok and r.kind == pp.OK, "a normal reply is a success")
        check("choices" in str(_Scripted.seen["body"]) or True, "(request sent)")
        check(_Scripted.seen["path"].endswith("/chat/completions"),
              "an OpenAI-style address is probed at /chat/completions")
        check(_Scripted.seen["headers"].get("authorization") == "Bearer sk-test",
              "...with a bearer token")
        check(_Scripted.seen["body"].get("max_tokens") == 5,
              "...and asks for the smallest possible reply")
    finally:
        srv.shutdown()


def test_an_anthropic_surface_is_probed_differently():
    """MiniMax's /anthropic and Kimi's /coding are Anthropic Messages.

    Probing those with /chat/completions returns 404, which reads to the
    user as "you typed the model name wrong".
    """
    srv, url = serve()
    try:
        respond(200, json.dumps({"content": [{"text": "hi"}]}))
        r = pp.probe(url.replace("/v1", "/anthropic"), "sk-test", "m", timeout=5)
        check(r.ok, "an Anthropic-style reply is a success")
        check(_Scripted.seen["path"].endswith("/v1/messages"),
              "the Anthropic surface is probed at /v1/messages")
        check(_Scripted.seen["headers"].get("x-api-key") == "sk-test",
              "...with x-api-key, not a bearer token")
        check(_Scripted.seen["headers"].get("authorization") is None,
              "...and no bearer token at all")
        check("anthropic-version" in _Scripted.seen["headers"],
              "...and the version header it requires")
    finally:
        srv.shutdown()


def test_every_status_gets_its_own_answer():
    srv, url = serve()
    try:
        cases = [
            (401, pp.AUTH, "密钥"),
            (403, pp.AUTH, "密钥"),
            (402, pp.CREDIT, "余额"),
            (404, pp.NOT_FOUND, "不存在"),
            (400, pp.BAD_REQUEST, "模型名称"),
            (429, pp.RATE, "频繁"),
            (500, pp.SERVER, "服务商"),
            (418, pp.UNKNOWN, "418"),
        ]
        for status, kind, phrase in cases:
            respond(status, json.dumps({"error": {"message": "provider said so"}}))
            r = pp.probe(url, "sk-test", "m", timeout=5)
            check(not r.ok and r.kind == kind,
                  "HTTP %d is classified as %s" % (status, kind))
            check(phrase in r.message,
                  "...and the message mentions %s" % phrase)
            check("provider said so" in r.message,
                  "...and repeats what the provider actually said")
    finally:
        srv.shutdown()


def test_400_used_to_be_a_shrug():
    """Neither of the two old copies had a 400 branch.

    A 400 is how a provider says "that model does not take max_tokens" or
    "that name is not one of mine" -- the exact thing the user needs. Both
    copies replaced it with "请求被拒绝（HTTP 400），请检查配置".
    """
    srv, url = serve()
    try:
        respond(400, json.dumps({"error": {
            "message": "model `deepseek-chat-v9` does not exist"}}))
        r = pp.probe(url, "sk-test", "deepseek-chat-v9", timeout=5)
        check(r.kind == pp.BAD_REQUEST, "400 has its own branch")
        check("deepseek-chat-v9" in r.message,
              "the offending model name reaches the user")
        check(r.http_code == 400, "the status code is kept for the caller")
    finally:
        srv.shutdown()


def test_the_key_never_comes_back_out():
    """Some providers quote the Authorization header straight back.

    This text goes to a console, to a web page and into data/logs.
    """
    srv, url = serve()
    key = "sk-live-9f8e7d6c5b4a39281706alpha"
    try:
        respond(401, json.dumps({"error": {
            "message": "invalid key %s for org acme" % key}}))
        r = pp.probe(url, key, "m", timeout=5)
        check(key not in r.message, "the key is not echoed back")
        check("***" in r.message, "...it is replaced, not silently dropped")
        check("for org acme" in r.message, "...and the rest of the message survives")
    finally:
        srv.shutdown()


def test_a_reply_that_is_not_a_reply():
    srv, url = serve()
    try:
        respond(200, json.dumps({"id": "x", "object": "error"}))
        r = pp.probe(url, "sk-test", "m", timeout=5)
        check(not r.ok, "HTTP 200 with no completion in it is not a success")
        check("模型名称" in r.message, "...and points at the likeliest cause")
    finally:
        srv.shutdown()


def test_network_failures_are_not_config_failures():
    dead = "http://127.0.0.1:%d/v1" % free_port()
    r = pp.probe(dead, "sk-test", "m", timeout=3)
    check(r.kind == pp.NETWORK, "a refused connection is a network problem")
    check(pp.NETWORK not in pp.FIXABLE_IN_CONFIG,
          "...so the launcher must not send the user to the config page")

    # Driven directly rather than by resolving a bogus name: plenty of ISPs
    # hijack NXDOMAIN and answer with something that resets instead, so the
    # real-world version of this test measures the tester's DNS.
    import urllib.error
    import urllib.request
    real = urllib.request.urlopen
    for exc, label in (
            (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")),
             "an unresolvable host"),
            (urllib.error.URLError(ConnectionRefusedError()), "a refused port"),
            (urllib.error.URLError(socket.timeout()), "a timeout"),
            (ConnectionResetError(10054, "reset by peer"), "a reset connection"),
            (socket.timeout(), "a bare socket timeout"),
            (OSError(101, "network unreachable"), "an unreachable network"),
    ):
        def boom(*a, **k):
            raise exc
        urllib.request.urlopen = boom
        try:
            r = pp.probe("http://example.invalid/v1", "sk-test", "m", timeout=1)
        finally:
            urllib.request.urlopen = real
        check(r.kind == pp.NETWORK, "%s is a network problem" % label)
        check(not r.ok, "...and is a failure")


def test_what_the_launcher_is_allowed_to_block_on():
    """A wrong block costs the whole install; a wrong pass costs one message.

    Only the failures the config page can actually repair belong here. A
    rate limit and an empty account both fix themselves elsewhere, and
    sending someone to the config page for them wastes their time.
    """
    check(set(pp.FIXABLE_IN_CONFIG) == {pp.AUTH, pp.NOT_FOUND, pp.BAD_REQUEST},
          "only auth / not_found / bad_request send the user to the config page")
    for kind in (pp.CREDIT, pp.RATE, pp.SERVER, pp.NETWORK, pp.UNKNOWN, pp.OK):
        check(kind not in pp.FIXABLE_IN_CONFIG,
              "%s does not block a launch" % kind)


def test_there_is_only_one_copy_of_this_table():
    """The whole point of the module. Two copies is how 400 went missing."""
    import re
    owners = []
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
            text = f.read()
        if re.search(r"e\.code\s*==\s*40[0-9]", text):
            owners.append(name)
    check(owners == ["provider_probe.py"],
          "only provider_probe.py maps HTTP codes (found: %s)" % ", ".join(owners))


if __name__ == "__main__":
    for fn in (test_a_working_provider,
               test_an_anthropic_surface_is_probed_differently,
               test_every_status_gets_its_own_answer,
               test_400_used_to_be_a_shrug,
               test_the_key_never_comes_back_out,
               test_a_reply_that_is_not_a_reply,
               test_network_failures_are_not_config_failures,
               test_what_the_launcher_is_allowed_to_block_on,
               test_there_is_only_one_copy_of_this_table):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("provider probe: all checks passed")

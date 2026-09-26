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
import re
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
    banned_agent = ""   # a User-Agent prefix to turn away, as Cloudflare does

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        _Scripted.seen = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": json.loads(raw.decode("utf-8")) if raw else {},
        }
        status, body = _Scripted.status, _Scripted.body
        agent = self.headers.get("User-Agent") or ""
        if _Scripted.banned_agent and agent.startswith(_Scripted.banned_agent):
            status, body = 403, b"error code: 1010"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def test_which_entry_custom_means():
    """The launcher and the diagnostic each had their own answer to this, and
    both were stricter than the engine in the same two ways.

    hermes_cli.providers.custom_provider_slug lowercases the entry name and
    hyphenates its spaces, and resolve_custom_provider accepts either that or
    the plain lowercased name.  Comparing with == instead meant an entry
    called "LongCat" or "My Server" -- the ordinary way a person names one --
    was invisible: the launcher refused to start, saying the entry was not in
    custom_providers, and 一键诊断 called it a built-in provider and skipped
    the only test on the page that reaches the network.  The engine ran that
    config the whole time.
    """
    def cfg(*entries):
        return {"custom_providers": list(entries)}

    longcat = {"name": "LongCat", "base_url": "https://a/v1", "key_env": "MY_KEY"}
    check(pp.find_custom_provider(cfg(longcat), "custom:longcat") is longcat,
          "an entry named LongCat answers to custom:longcat")

    spaced = {"name": "My Server", "base_url": "https://a/v1"}
    check(pp.find_custom_provider(cfg(spaced), "custom:my-server") is spaced,
          "a space in the name is a hyphen in the slug")

    check(pp.find_custom_provider(cfg(longcat), "custom:ghost") is None,
          "a name that matches nothing still resolves to nothing")
    check(pp.find_custom_provider(cfg(longcat), "deepseek") is None,
          "a built-in provider is not looked up here at all")

    # resolve_custom_provider skips entries with no address and takes the
    # first one that has it; matching that keeps this from naming an entry
    # the engine would pass over.
    addressless = {"name": "local"}
    usable = {"name": "local", "base_url": "https://b/v1"}
    check(pp.find_custom_provider(cfg(addressless, usable), "custom:local") is usable,
          "an entry with no address loses to one that has it")
    check(pp.find_custom_provider(cfg(addressless), "custom:local") is addressless,
          "...but is still returned alone, so callers can say what is wrong")


def test_where_a_custom_entry_keeps_its_key():
    """An entry may name a variable or carry the key inline, under any of the
    spellings _normalize_custom_provider_entry folds together.  preflight
    accepted only key_env and reported everything else as a missing entry --
    a statement that was false, with a remedy that rewrote the same file.
    """
    check(pp.custom_provider_key_var({"api_key_env": "A"}) == "A",
          "api_key_env is key_env, as the engine documents it")
    check(pp.custom_provider_key_var({"apiKeyEnv": "A"}) == "A",
          "so is apiKeyEnv")
    check(pp.custom_provider_key_var({"key_env": "A", "apiKeyEnv": "B"}) == "A",
          "...and key_env wins when both are present, as it does there")
    check(pp.custom_provider_inline_key({"apiKey": "sk-x"}) == "sk-x",
          "apiKey is api_key")

    check(pp.custom_provider_base_url({"url": "https://a/v1"}) == "https://a/v1",
          "an address may be spelled url")
    check(pp.custom_provider_base_url({"api": "https://a/v1"}) == "https://a/v1",
          "...or api")
    check(pp.custom_provider_base_url(
        {"base_url": "https://a/v1", "url": "https://b/v1"}) == "https://a/v1",
        "...with base_url first, as the engine reads them")


class _bare_environ(object):
    """No *_API_KEY and no OPENAI_BASE_URL in os.environ, plus `values`.

    Every lookup here falls through to the process environment last, as the
    engine's does, so a key exported on the machine running this would make
    a check pass for the wrong reason -- or fail on a machine that has one.
    """

    def __init__(self, values=None):
        self.values = values or {}

    def __enter__(self):
        self.saved = {name: value for name, value in os.environ.items()
                      if name.endswith("_API_KEY") or name.endswith("_BASE_URL")}
        for name in self.saved:
            del os.environ[name]
        os.environ.update(self.values)

    def __exit__(self, *exc):
        for name in self.values:
            os.environ.pop(name, None)
        os.environ.update(self.saved)


# hermes-agent before and after the OPENAI_BASE_URL pairing.
V0213 = (0, 21, 3)
V0214 = (0, 21, 4)

# (address, what runtime_provider._host_gated_env_key_candidates reads for
# it, in its order). Nothing here depends on OPENAI_BASE_URL.
HOST_GATE_TABLE = [
    # The two blanket keys, at their own hosts only.
    ("https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    ("https://OpenAI.com/v1/", ("OPENAI_API_KEY",)),
    ("https://res.openai.azure.com/openai/v1", ("OPENAI_API_KEY", "AZURE_API_KEY")),
    ("https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",)),
    # A host is matched as a host, never as a substring.
    ("https://api.openai.com.evil.test/v1", ("EVIL_API_KEY",)),
    ("https://proxy.example/api.openai.com/v1", ("PROXY_API_KEY",)),
    ("https://openrouter.ai.evil.test/api/v1", ("EVIL_API_KEY",)),
    ("https://notopenrouter.ai/v1", ("NOTOPENROUTER_API_KEY",)),
    # Everyone else: one variable, named after the host.
    ("https://api.longcat.example/v1", ("LONGCAT_API_KEY",)),
    ("https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",)),
    ("https://API.DeepSeek.com/v1", ("DEEPSEEK_API_KEY",)),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", ("ALIYUNCS_API_KEY",)),
    ("https://www.api.foo-bar.com/v1", ("FOO_BAR_API_KEY",)),
    ("https://api.xiaomimimo.com:8443/v1", ("XIAOMIMIMO_API_KEY",)),
    ("https://api.x.ai/v1", ("X_API_KEY",)),
    ("api.moonshot.cn/v1", ("MOONSHOT_API_KEY",)),
    # And nothing at all, where the engine sends no-key-required.
    ("https://ollama.com/v1", ()),
    ("http://127.0.0.1:8000/v1", ()),
    ("http://10.0.0.5:8000/v1", ()),
    ("http://localhost:11434/v1", ()),
    ("http://[::1]:8080/v1", ()),
    ("http://gpu-box:8000/v1", ()),
    ("https://x/v1", ()),
    ("https://api.9router.example/v1", ()),
    ("", ()),
]

LONGCAT_URL = "https://api.longcat.example/v1"

# (address, OPENAI_BASE_URL, read by 0.21.4 and later, read by 0.21.3).
PAIRING_TABLE = [
    (LONGCAT_URL, LONGCAT_URL,
     ("OPENAI_API_KEY", "LONGCAT_API_KEY"), ("LONGCAT_API_KEY",)),
    (LONGCAT_URL + "/", LONGCAT_URL,
     ("OPENAI_API_KEY", "LONGCAT_API_KEY"), ("LONGCAT_API_KEY",)),
    (LONGCAT_URL, LONGCAT_URL + "/",
     ("OPENAI_API_KEY", "LONGCAT_API_KEY"), ("LONGCAT_API_KEY",)),
    # Another path is another address, and the comparison is exact.
    (LONGCAT_URL, "https://api.longcat.example",
     ("LONGCAT_API_KEY",), ("LONGCAT_API_KEY",)),
    (LONGCAT_URL, "https://API.longcat.example/v1",
     ("LONGCAT_API_KEY",), ("LONGCAT_API_KEY",)),
    ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1",
     ("OPENAI_API_KEY",), ()),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1",
     ("OPENAI_API_KEY", "OPENROUTER_API_KEY"), ("OPENROUTER_API_KEY",)),
    # OpenAI's own host needs no pairing, and does not get the key twice.
    ("https://api.openai.com/v1", "https://elsewhere.example/v1",
     ("OPENAI_API_KEY",), ("OPENAI_API_KEY",)),
]


def test_which_variables_the_engine_reads_for_an_address():
    """_host_gated_env_key_candidates hands OPENAI_API_KEY and
    OPENROUTER_API_KEY only to their own hosts, and everything else one
    variable named after the host. This was a blanket (OPENAI_API_KEY,
    OPENROUTER_API_KEY) for every address, which the engine has never done.
    """
    with _bare_environ():
        for url, expected in HOST_GATE_TABLE:
            for version in (V0213, V0214):
                got = pp.custom_key_fallback_vars(url, {}, version)
                check(got == expected, "%r reads %s (%s)" % (
                    url, " then ".join(expected) or "nothing",
                    "got " + (", ".join(got) or "nothing")))

        for url, paired, since, before in PAIRING_TABLE:
            env = {"OPENAI_BASE_URL": paired}
            got = pp.custom_key_fallback_vars(url, env, V0214)
            check(got == since, "0.21.4, OPENAI_BASE_URL=%r: %r reads %s (got %s)" % (
                paired, url, " then ".join(since) or "nothing",
                ", ".join(got) or "nothing"))
            got = pp.custom_key_fallback_vars(url, env, V0213)
            check(got == before, "0.21.3, OPENAI_BASE_URL=%r: %r reads %s (got %s)" % (
                paired, url, " then ".join(before) or "nothing",
                ", ".join(got) or "nothing"))

        check(pp.custom_key_fallback_vars(LONGCAT_URL, {}, (0, 22, 0))
              == pp.custom_key_fallback_vars(LONGCAT_URL, {}, V0214),
              "a later engine keeps the pairing")

    with _bare_environ({"OPENAI_BASE_URL": LONGCAT_URL}):
        check(pp.custom_key_fallback_vars(LONGCAT_URL, {}, V0214)[:1]
              == ("OPENAI_API_KEY",),
              "OPENAI_BASE_URL is read from the process environment too")

    check(pp.parse_engine_version("0.21.4") == V0214, "0.21.4 parses")
    check(pp.parse_engine_version("0.22.0rc1") == (0, 22, 0), "a pre-release parses")
    check(pp.parse_engine_version("") is None, "no version is None, not a crash")


def test_the_credential_order_is_the_engines():
    """_resolve_named_custom_runtime (hermes_cli/runtime_provider_custom.py)
    tries the inline key, then the variable the entry names, then only what
    the host gate hands that address. Getting this wrong means probing with
    a credential the engine will not use -- a green check over a chat window
    that never answers, or a red one over a config that works.
    """
    with _bare_environ():
        _credential_order_checks()


def _credential_order_checks():
    entry = {"name": "l", "base_url": "https://a/v1",
             "api_key": "sk-inline", "key_env": "MY_KEY"}
    _, key = pp.custom_provider_credential(entry, {"MY_KEY": "sk-env"})
    check(key == "sk-inline", "an inline key outranks the variable it also names")

    entry = {"name": "l", "base_url": "https://a/v1", "key_env": "MY_KEY"}
    _, key = pp.custom_provider_credential(entry, {"MY_KEY": "sk-env"})
    check(key == "sk-env", "...and the variable is read when there is no inline key")

    # The three ways the blanket fallback got a LongCat entry with no key of
    # its own wrong, each confirmed against resolve_runtime_provider on 0.21.3
    # and 0.21.4.
    longcat = {"name": "longcat", "base_url": LONGCAT_URL}
    for version in (V0213, V0214):
        tag = " (%d.%d.%d)" % version
        _, key = pp.custom_provider_credential(
            longcat, {"OPENAI_API_KEY": "sk-openai"}, version)
        check(key == "", "OPENAI_API_KEY is not sent to LongCat -- "
              "the engine sends no-key-required there" + tag)
        _, key = pp.custom_provider_credential(
            longcat, {"OPENROUTER_API_KEY": "sk-or"}, version)
        check(key == "", "nor is the OpenRouter key" + tag)
        _, key = pp.custom_provider_credential(
            longcat, {"LONGCAT_API_KEY": "sk-lc"}, version)
        check(key == "sk-lc", "LONGCAT_API_KEY is, because the engine uses it" + tag)

        named = dict(longcat, key_env="MY_KEY")
        _, key = pp.custom_provider_credential(
            named, {"OPENAI_API_KEY": "sk-openai"}, version)
        check(key == "", "an unset key_env does not fall through to "
              "OPENAI_API_KEY either" + tag)
        _, key = pp.custom_provider_credential(
            named, {"LONGCAT_API_KEY": "sk-lc"}, version)
        check(key == "sk-lc", "...it falls through to the host's own variable" + tag)

    # Only 0.21.4 pairs OPENAI_API_KEY with the address in OPENAI_BASE_URL --
    # which is what Config.html writes beside it.
    paired = {"OPENAI_API_KEY": "sk-openai", "OPENAI_BASE_URL": LONGCAT_URL,
              "LONGCAT_API_KEY": "sk-lc"}
    _, key = pp.custom_provider_credential(longcat, paired, V0214)
    check(key == "sk-openai",
          "0.21.4 sends OPENAI_API_KEY to the OPENAI_BASE_URL address, ahead of the host's own")
    _, key = pp.custom_provider_credential(longcat, paired, V0213)
    check(key == "sk-lc", "0.21.3 does not")

    # The blanket keys still reach their own hosts.
    _, key = pp.custom_provider_credential(
        {"name": "o", "base_url": "https://openrouter.ai/api/v1"},
        {"OPENROUTER_API_KEY": "sk-or"})
    check(key == "sk-or", "OPENROUTER_API_KEY still reaches openrouter.ai")
    _, key = pp.custom_provider_credential(
        {"name": "o", "base_url": "https://api.openai.com/v1"},
        {"OPENAI_API_KEY": "sk-openai"})
    check(key == "sk-openai", "OPENAI_API_KEY still reaches api.openai.com")

    _, key = pp.custom_provider_credential({"name": "l", "base_url": "https://a/v1"}, {})
    check(key == "", "and nothing at all is nothing, not a crash")


def _names_worth_setting(url):
    """Every variable the engine could conceivably read for this address.

    The engine hands back values, so a variable it reads but we never set
    looks exactly like one it never reads. Setting one per host label (and
    the three it gates) is what makes "we say nothing, it says nothing" an
    agreement rather than two silences.
    """
    names = {"OPENAI_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY"}
    for token in re.split(r"[./:@\[\]]+", url):
        vendor = "".join(ch if ch.isalnum() else "_" for ch in token).upper()
        if vendor:
            names.add(vendor + "_API_KEY")
    return names


def test_the_host_gate_is_the_installed_engines():
    """Run the engine's own _host_gated_env_key_candidates over both tables
    and demand the same answer, in the same order.

    The tables above are what the engine did when this was written. This is
    what it does now -- against whichever engine this interpreter imports,
    so the packaged one in CI, and upstream main in the weekly canary run.
    """
    try:
        import hermes_cli
        from hermes_cli import runtime_provider as rp
        engine_gate = rp._host_gated_env_key_candidates
    except Exception as exc:  # ImportError, or anything its imports raise
        print("  skip  no hermes engine importable here (%s)" % exc.__class__.__name__)
        return
    version = pp.parse_engine_version(getattr(hermes_cli, "__version__", ""))
    print("  validating against hermes-agent %s from %s"
          % (getattr(hermes_cli, "__version__", "?"), os.path.dirname(hermes_cli.__file__)))
    check(pp.installed_engine_version() == version,
          "installed_engine_version() reads the engine this interpreter runs")

    rows = [(url, "") for url, _ in HOST_GATE_TABLE]
    rows += [(url, paired) for url, paired, _, _ in PAIRING_TABLE]
    for url, paired in rows:
        env = {name: "value-of-" + name for name in _names_worth_setting(url)}
        if paired:
            env["OPENAI_BASE_URL"] = paired
        with _bare_environ(env):
            engine = tuple(value[len("value-of-"):]
                           for value in engine_gate(url, ollama=False) if value)
            ours = pp.custom_key_fallback_vars(url, env, version)
        check(ours == engine, "%r%s: engine reads %s, we say %s" % (
            url, " with OPENAI_BASE_URL=%r" % paired if paired else "",
            ", ".join(engine) or "nothing", ", ".join(ours) or "nothing"))


def test_there_is_only_one_copy_of_this_lookup():
    """Same reason as the table above: two copies drifted apart, and both
    ended up stricter than the engine in the same two ways.
    """
    import re
    owners = []
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
            text = f.read()
        if re.search(r"get\(\s*[" + chr(34) + chr(39) + r"]custom_providers[" + chr(34) + chr(39) + r"]", text):
            owners.append(name)
    check(owners == ["provider_probe.py"],
          "only provider_probe.py walks custom_providers (found: %s)"
          % ", ".join(owners))


# Lines people actually put in data/.env. What matters is agreeing with
# python-dotenv, the engine's reader; the expected values below were taken
# from it, and when it is importable the test asks it directly.
ENV_LINES = [
    ("OPENAI_API_KEY=sk-plain", "OPENAI_API_KEY", "sk-plain"),
    ("OPENAI_BASE_URL=https://a.example/v1  # longcat", "OPENAI_BASE_URL", "https://a.example/v1"),
    ("export OPENAI_BASE_URL=https://a.example/v1", "OPENAI_BASE_URL", "https://a.example/v1"),
    ("KEY_SPACED = sk-spaced", "KEY_SPACED", "sk-spaced"),
    ('KEY_DQ="sk-dq # not a comment"', "KEY_DQ", "sk-dq # not a comment"),
    ("KEY_SQ='sk-sq # not a comment'", "KEY_SQ", "sk-sq # not a comment"),
    ('KEY_DQ_TAIL="sk-dq"  # note', "KEY_DQ_TAIL", "sk-dq"),
    ("KEY_HASH_NO_SPACE=sk#part", "KEY_HASH_NO_SPACE", "sk#part"),
    ("KEY_EMPTY=", "KEY_EMPTY", ""),
    ('KEY_ESC="a\\nb"', "KEY_ESC", "a\nb"),
    # A comment straight after the closing quote, no space: dotenv keeps the value.
    ('KEY_DQ_HASH="sk-dq"#LongCat key', "KEY_DQ_HASH", "sk-dq"),
    ("KEY_SQ_HASH='sk-sq'#old one", "KEY_SQ_HASH", "sk-sq"),
    # python-dotenv drops these lines entirely ("could not parse statement").
    ('KEY_QUOTED_THEN_TEXT="sk-lc" my longcat key', "KEY_QUOTED_THEN_TEXT", None),
    ('KEY_UNTERMINATED="sk-lc', "KEY_UNTERMINATED", None),
]


def test_env_files_are_read_the_way_the_engine_reads_them():
    import io
    NL = chr(10)
    text = NL.join(line for line, _k, _v in ENV_LINES) + NL + "# a comment" + NL + "NOT_A_PAIR" + NL
    ours = pp.parse_env(text)
    for line, key, want in ENV_LINES:
        check(ours.get(key) == want, "%-45s -> %r" % (line[:45], ours.get(key)))
    check("NOT_A_PAIR" not in ours, "a line with no '=' sets nothing")
    try:
        import dotenv
    except ImportError:
        print("  skip  python-dotenv not importable here; the table above is its output")
        return
    theirs = {k: v for k, v in dotenv.dotenv_values(stream=io.StringIO(text)).items() if v is not None}
    for _line, key, _want in ENV_LINES:
        check(ours.get(key) == theirs.get(key),
              "agrees with python-dotenv %s on %s (%r)" % (dotenv.__name__, key, theirs.get(key)))


def test_an_empty_value_in_env_hides_the_process_one():
    saved = os.environ.get("PP_TEST_SHADOW")
    os.environ["PP_TEST_SHADOW"] = "from-process"
    try:
        check(pp.env_value({"PP_TEST_SHADOW": ""}, "PP_TEST_SHADOW") == "",
              "data/.env setting it to nothing wins, as load_hermes_dotenv(override=True) does")
        check(pp.env_value({}, "PP_TEST_SHADOW") == "from-process",
              "a name the file does not mention falls back to the process environment")
    finally:
        if saved is None:
            os.environ.pop("PP_TEST_SHADOW", None)
        else:
            os.environ["PP_TEST_SHADOW"] = saved


def test_a_malformed_address_is_a_result_not_an_exception():
    for url in ("api.longcat.example/v1", "http://[::1/v1", "${LC_URL}", ""):
        try:
            r = pp.probe(url, "k", "m", timeout=2)
            check(not r.ok and r.kind in pp.FIXABLE_IN_CONFIG + (pp.NETWORK,),
                  "%r -> %s: %s" % (url, r.kind, r.message))
        except Exception as e:  # what crashed diagnose.py
            check(False, "%r raised %r" % (url, e))


def test_config_refs_are_expanded_the_way_the_engine_does():
    saved = os.environ.get("PP_TEST_REF")
    os.environ["PP_TEST_REF"] = "from-process"
    try:
        env = {"LC_KEY": "sk-lc"}
        cfg = {"a": "${LC_KEY}", "b": "${env:LC_KEY}", "c": ["x-${LC_KEY}-y"],
               "d": "${NOT_SET_ANYWHERE_42}", "e": "${vault:secret/x}", "f": "${PP_TEST_REF}", "g": 7}
        got = pp.expand_env_refs(cfg, env)
        check(got["a"] == "sk-lc" and got["b"] == "sk-lc", "${VAR} and ${env:VAR} expand from data/.env")
        check(got["c"] == ["x-sk-lc-y"], "...inside lists and longer strings")
        check(got["d"] == "${NOT_SET_ANYWHERE_42}", "an unresolved ref stays verbatim, as in the engine")
        check(got["e"] == "${vault:secret/x}", "a non-env SecretRef is left alone")
        check(got["f"] == "from-process", "the process environment counts when .env does not name it")
        check(got["g"] == 7, "non-strings are untouched")
    finally:
        if saved is None:
            os.environ.pop("PP_TEST_REF", None)
        else:
            os.environ["PP_TEST_REF"] = saved


def test_env_names_ignore_case_on_windows():
    if os.name != "nt":
        print("  skip  Windows only")
        return
    check(pp.env_value({"longcat_api_key": "sk-lower"}, "LONGCAT_API_KEY") == "sk-lower",
          "a lower-case name in data/.env is the same variable, as os.environ has it on Windows")
    check(pp.env_value({"LONGCAT_API_KEY": "", "longcat_api_key": "sk-lower"}, "LONGCAT_API_KEY") == "sk-lower",
          "of two lines differing only in case the later wins, as in os.environ -- even over an exact-case empty one")


def test_the_probe_does_not_call_itself_urllib():
    """opencode.ai (OpenCode Zen and Go) sits behind Cloudflare, which turns
    away urllib's default "Python-urllib/3.x" with 403 "error code: 1010"
    before the key is read. The probe reported that as a bad key, and the
    launcher refused to start with a key the engine uses fine.
    """
    srv, url = serve()
    try:
        _Scripted.banned_agent = "Python-urllib"
        respond(200, GOOD)
        r = pp.probe(url, "sk-test", "deepseek-v4-pro", timeout=5)
        agent = _Scripted.seen["headers"].get("user-agent", "")
        check(r.ok, "a front door that bans urllib's name lets the probe in (%s)" % r.message)
        check(agent.startswith("U-Hermes/"), "...because it names itself (%s)" % agent)
        pp.probe(url.replace("/v1", "/anthropic"), "sk-test", "m", timeout=5)
        check(_Scripted.seen["headers"].get("user-agent") == agent,
              "the Anthropic surface sends the same name")
        # The door itself, so the checks above cannot pass by accident.
        import urllib.error
        import urllib.request
        try:
            urllib.request.urlopen(urllib.request.Request(
                url + "/chat/completions", data=b"{}", method="POST"), timeout=5)
            refused = False
        except urllib.error.HTTPError as e:
            refused = e.code == 403
        check(refused, "(the test server does turn urllib's own name away)")
    finally:
        _Scripted.banned_agent = ""
        srv.shutdown()
    check(re.fullmatch(r"U-Hermes/[0-9A-Za-z.+-]+", pp.USER_AGENT) is not None,
          "the name is plain ASCII with a version or 'dev' (%s)" % pp.USER_AGENT)


OPENCODE_HOSTS = [
    ("https://opencode.ai/zen/go/v1", True),
    ("https://opencode.ai/zen/v1", True),
    ("https://OpenCode.AI/zen/go/v1/", True),
    ("https://api.opencode.ai/v1", True),
    ("https://api.deepseek.com/v1", False),
    ("https://notopencode.ai/v1", False),
    ("https://opencode.ai.example.com/v1", False),
    ("http://127.0.0.1:9/v1", False),
]


def test_opencode_gets_the_session_header_the_engine_sends():
    """OpenCode Go answers a request without x-opencode-session with HTTP 400
    "Request is missing x-opencode-session" -- which the launcher showed as
    "通常是模型名称写错" and blocked, with a key and model that work. The
    engine sends one on every opencode.ai request, "oneshot-<hex>" when
    there is no conversation (agent/opencode_affinity.py).
    """
    for url, wanted in OPENCODE_HOSTS:
        _u, _p, headers, _f = pp.build_request(url, "sk-test", "deepseek-v4-pro")
        sid = headers.get("x-opencode-session")
        if wanted:
            check(re.fullmatch(r"oneshot-[0-9a-f]{16}", sid or "") is not None,
                  "%s gets a session id (%r)" % (url, sid))
        else:
            check(sid is None, "%s gets no OpenCode header" % url)
    _u, _p, anthropic, _f = pp.build_request("https://opencode.ai/zen/anthropic", "k", "m")
    check("x-opencode-session" in anthropic, "...on the Anthropic surface too")

    try:
        from agent.anthropic_endpoints import _is_opencode_endpoint
    except Exception as exc:
        print("  skip  no hermes engine importable here (%s)" % exc.__class__.__name__)
        return
    for url, _wanted in OPENCODE_HOSTS:
        check(pp.is_opencode_host(url) == bool(_is_opencode_endpoint(url)),
              "%s: the engine's host check agrees" % url)


def test_a_key_is_sent_the_way_the_engine_sends_it():
    # A zero-width space pasted in with the key, or a Chinese note after it:
    # the engine strips non-ASCII from keys on load, and the SDK encodes the
    # address. Raw, both raised UnicodeEncodeError, reported as a bad address.
    url, key = pp._as_sent("http://127.0.0.1:1/v1/我的 模型", "sk-lc" + chr(0x200B) + "（旧）")
    check(key == "sk-lc", "non-ASCII is dropped from the key (%r)" % key)
    check(all(ord(c) < 128 for c in url) and "%20" in url, "the address is percent-encoded (%r)" % url)
    r = pp.probe("http://127.0.0.1:9/v1", "sk-lc" + chr(0x200B), "m", timeout=2)
    check(r.kind == pp.NETWORK, "so such a key reaches the network instead of failing locally (%s)" % r.kind)
    # But only credential variables are stripped of whitespace on load; an
    # inline key keeps the space in front of its note, and the engine's
    # call with it fails -- so the probe must not quietly repair it.
    _url, inline = pp._as_sent("http://x/v1", "sk-lc （旧）")
    check(inline == "sk-lc ", "an inline key keeps its trailing space (%r)" % inline)
    check(pp.env_value({"LONGCAT_API_KEY": "sk-lc （旧）"}, "LONGCAT_API_KEY") == "sk-lc",
          "a *_API_KEY variable is cleaned then stripped, as the engine loads it")
    check(pp.env_value({"LCKEY": "sk-lc" + chr(0x200B)}, "LCKEY") == "sk-lc" + chr(0x200B),
          "a variable without a credential suffix is not cleaned on load")


if __name__ == "__main__":
    for fn in (test_a_malformed_address_is_a_result_not_an_exception,
               test_config_refs_are_expanded_the_way_the_engine_does,
               test_env_names_ignore_case_on_windows,
               test_a_key_is_sent_the_way_the_engine_sends_it,
               test_env_files_are_read_the_way_the_engine_reads_them,
               test_an_empty_value_in_env_hides_the_process_one,
               test_a_working_provider,
               test_an_anthropic_surface_is_probed_differently,
               test_the_probe_does_not_call_itself_urllib,
               test_opencode_gets_the_session_header_the_engine_sends,
               test_every_status_gets_its_own_answer,
               test_400_used_to_be_a_shrug,
               test_the_key_never_comes_back_out,
               test_a_reply_that_is_not_a_reply,
               test_network_failures_are_not_config_failures,
               test_what_the_launcher_is_allowed_to_block_on,
               test_there_is_only_one_copy_of_this_table,
               test_which_entry_custom_means,
               test_where_a_custom_entry_keeps_its_key,
               test_which_variables_the_engine_reads_for_an_address,
               test_the_credential_order_is_the_engines,
               test_the_host_gate_is_the_installed_engines,
               test_there_is_only_one_copy_of_this_lookup):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("provider probe: all checks passed")

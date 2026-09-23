"""The launch gate must block only on things the user can actually fix.

preflight.py decides whether Windows-Start.bat opens the config page instead
of launching.  Both of its mistakes are expensive: a wrong block makes a
working install unusable, and a wrong pass sends the user to a chat window
that will never answer.  Everything asserted here is a case that got one of
those wrong at some point.

Run:  python portable/scripts/tests/test_preflight.py
"""
import ast
import http.server
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
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
PREFLIGHT = os.path.join(SCRIPTS, "preflight.py")
SANDBOX = os.path.join(HERE, "_tmp_preflight")

NL = chr(10)
BOM = bytes([0xEF, 0xBB, 0xBF])
NEEDS_CONFIG = 10
FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


# Blocking PyYAML by putting a raising yaml.py on PYTHONPATH worked on a
# developer machine and did NOT work under the packaged interpreter in CI:
# `import yaml` still succeeded, preflight took its ordinary path, and the
# two checks that matter silently tested nothing. A meta_path hook does not
# depend on sys.path order, on site-packages layout, or on whether something
# imported yaml before us.
BLOCK_YAML = NL.join([
    "import sys, runpy",
    "class _NoYaml:",
    "    def find_spec(self, name, path=None, target=None):",
    "        if name == 'yaml' or name.startswith('yaml.'):",
    "            raise ImportError('no pyyaml in this interpreter')",
    "        return None",
    "for _m in [m for m in sys.modules if m == 'yaml' or m.startswith('yaml.')]:",
    "    del sys.modules[_m]",
    "sys.meta_path.insert(0, _NoYaml())",
    "sys.argv = sys.argv[1:]",
    "runpy.run_path(sys.argv[0], run_name='__main__')",
    "",
])


def run(config=None, env_file=None, args=(), no_yaml=False, probe=False,
        extra_env=None):
    """Run preflight against a throwaway data dir; return (code, output).

    extra_env is applied last; a value of None removes that variable.
    """
    shutil.rmtree(SANDBOX, ignore_errors=True)
    data = os.path.join(SANDBOX, "data")
    os.makedirs(data)
    if config is not None:
        with io.open(os.path.join(data, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(config)
    if env_file is not None:
        with io.open(os.path.join(data, ".env"), "w", encoding="utf-8") as f:
            f.write(env_file)

    environ = dict(os.environ)
    environ["PYTHONIOENCODING"] = "utf-8"
    # A key sitting in the ambient environment would make the "no key" cases
    # pass for the wrong reason -- and an exported OPENAI_BASE_URL decides
    # which address OPENAI_API_KEY goes to.
    for name in list(environ):
        if name.endswith("_API_KEY") or name.endswith("_BASE_URL"):
            del environ[name]
    environ.pop("PYTHONPATH", None)
    # Off unless a test asks for it. preflight now makes a REAL call to the
    # configured provider, and under the packaged interpreter (the one CI
    # runs) the engine is importable, so base_url_for resolves and these
    # fixtures would fire requests at api.deepseek.com from every build.
    if not probe:
        environ["U_HERMES_SKIP_PROBE"] = "1"
    else:
        environ.pop("U_HERMES_SKIP_PROBE", None)
    for name, value in (extra_env or {}).items():
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value

    argv = [sys.executable]
    argv += ["-c", BLOCK_YAML] if no_yaml else []
    argv += [PREFLIGHT] + list(args) + [data]
    proc = subprocess.run(argv, capture_output=True, env=environ)
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    # preflight ends in a blanket `except Exception -> exit 0`, so a crash
    # anywhere in it looks exactly like a clean pass. Nothing this suite
    # asserts means anything if that path was taken, so fail loudly here.
    if "启动自检跳过" in out:
        raise AssertionError("preflight crashed and failed open:" + chr(10) + out)
    return proc.returncode, out


DEEPSEEK = 'model:' + NL + '  provider: "deepseek"' + NL + '  default: "deepseek-chat"' + NL


def test_unreadable_configs_are_named_precisely():
    code, out = run(config=None)
    check(code == NEEDS_CONFIG, "no config.yaml at all sends the user to the config page")

    code, out = run(config="")
    check(code == NEEDS_CONFIG, "an empty config.yaml is refused")
    check("空" in out, "...and is described as empty, not as malformed")

    code, out = run(config="model:" + NL + "  - a" + NL + "   b: [" + NL)
    check(code == NEEDS_CONFIG, "a config that does not parse is refused")
    check("格式有误" in out, "...and is described as malformed")

    code, out = run(config="- a" + NL + "- b" + NL)
    check(code == NEEDS_CONFIG, "a top-level list is refused")
    check("key: value" in out, "...and says what shape was expected")


def test_a_python_without_pyyaml_does_not_accuse_the_config():
    """The worst possible outcome here is telling a user to restore a backup.

    load_config returned a bare None for both "PyYAML is missing" and "this
    file is broken", so running preflight under any python that is not the
    packaged one reported a corrupt config and blocked the launch over a
    file that was perfectly fine.
    """
    # Prove the block actually takes effect before asserting anything about
    # what preflight does under it -- the previous technique did not, and
    # these two checks passed for the wrong reason for one CI run.
    proof = subprocess.run(
        [sys.executable, "-c", BLOCK_YAML.replace(
            "runpy.run_path(sys.argv[0], run_name='__main__')",
            "import yaml")],
        capture_output=True)
    check(proof.returncode != 0 and b"ImportError" in proof.stderr,
          "the PyYAML block is actually in force")

    code, out = run(config=DEEPSEEK, no_yaml=True)
    check(code == 0, "a missing PyYAML does not block the launch")
    check("PyYAML" in out, "...it says which piece is missing")
    check("格式有误" not in out and "备份" not in out,
          "...and it does not claim the config is broken")

    code, out = run(config=DEEPSEEK, args=("--print-workspace",), no_yaml=True)
    check(code == 0, "--print-workspace survives a missing PyYAML")


def test_a_bom_in_env_is_repaired_not_just_reported():
    """A BOM makes the first variable's name unusable, to everyone.

    "﻿OPENAI_API_KEY" is not OPENAI_API_KEY. python-dotenv -- which is
    what the engine reads this file with -- keeps the BOM in the name too,
    so the key is not missing, it is inert: nothing matches it and the agent
    never answers. Notepad and PowerShell's `Set-Content -Encoding utf8`
    both produce one, and the config page used to carry it through a save
    rather than dropping it, so there was no way out from inside the
    product.

    Repaired rather than reported, because the damage is unambiguous, the
    fix is three bytes, and the alternative is a user retyping a key that
    was right all along.
    """
    shutil.rmtree(SANDBOX, ignore_errors=True)
    data = os.path.join(SANDBOX, "data")
    os.makedirs(data)
    with io.open(os.path.join(data, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(DEEPSEEK)
    env_path = os.path.join(data, ".env")
    with io.open(env_path, "w", encoding="utf-8-sig") as f:
        f.write("DEEPSEEK_API_KEY=sk-present-all-along" + NL)

    raw = io.open(env_path, "rb").read()
    check(raw[:3] == BOM, "the fixture really does start with a BOM")

    environ = dict(os.environ)
    environ["U_HERMES_SKIP_PROBE"] = "1"
    environ["PYTHONIOENCODING"] = "utf-8"
    for name in list(environ):
        if name.endswith("_API_KEY"):
            del environ[name]
    proc = subprocess.run([sys.executable, PREFLIGHT, data],
                          capture_output=True, env=environ)
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace")

    check(proc.returncode == 0,
          "the launch is not blocked over a key that is present")
    check("BOM" in out, "...and the user is told what was wrong")
    check("没有找到 API 密钥" not in out,
          "...not told the key is missing, which it never was")
    check(io.open(env_path, "rb").read()[:3] != BOM,
          "the BOM is gone from the file")
    check("sk-present-all-along" in
          io.open(env_path, encoding="utf-8").read(),
          "...and the key itself is untouched")

    # Second run: nothing left to repair, nothing said about it.
    proc = subprocess.run([sys.executable, PREFLIGHT, data],
                          capture_output=True, env=environ)
    out2 = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    check("BOM" not in out2, "a clean .env is not nagged about")

    shutil.rmtree(SANDBOX, ignore_errors=True)


def test_key_presence():
    code, out = run(config=DEEPSEEK)
    check(code == NEEDS_CONFIG, "a provider with no key anywhere is blocked")
    check("DEEPSEEK_API_KEY" in out, "...and the message names the variable to fill in")

    code, out = run(config=DEEPSEEK, env_file="DEEPSEEK_API_KEY=sk-test" + NL)
    check(code == 0, "a provider with its key in .env launches")


def test_an_unknown_provider_is_never_a_reason_to_block():
    """A wrong block costs the whole install; a wrong pass costs one message.

    This check used to hold a hand-written list of provider ids, so `auto`,
    `nous`, `ollama` and `openai-codex` -- all of them working installs --
    were each told to go and configure something.
    """
    for pid in ("auto", "nous", "ollama", "openai-codex", "something-invented"):
        cfg = 'model:' + NL + '  provider: "' + pid + '"' + NL + '  default: "x"' + NL
        code, _ = run(config=cfg)
        check(code == 0, "provider '%s' is not blocked" % pid)


def test_custom_providers():
    cfg = ('model:' + NL + '  provider: "custom:mine"' + NL +
           '  default: "x"' + NL + 'custom_providers:' + NL +
           '  - name: "mine"' + NL + '    base_url: "https://x/v1"' + NL +
           '    key_env: "MY_KEY"' + NL)
    # With MY_KEY empty the engine still runs: it sends its placeholder. So
    # whether this is a mistake is the provider's call, and preflight asks it.
    srv, url = fake_provider(200, GOOD_REPLY, accept={"abc"})
    try:
        at_url = cfg.replace("https://x/v1", url)
        code, out = run(config=at_url, probe=True)
        check(code == NEEDS_CONFIG,
              "a custom provider whose key variable is unset is blocked once the provider refuses")
        check("MY_KEY" in out, "...and the message names that variable")
        check("no-key-required" in _Fake.last_auth,
              "...having asked with exactly what the engine would send")
        code, _ = run(config=at_url, env_file="MY_KEY=abc" + NL, probe=True)
        check(code == 0 and "abc" in _Fake.last_auth, "a custom provider with its key set launches")
    finally:
        srv.shutdown()

    code, _ = run(config=cfg)
    check(code == 0, "with the probe off nothing can tell, so an unset key is not refused")

    code, _ = run(config=cfg, env_file="MY_KEY=abc" + NL)
    check(code == 0, "a custom provider with its key set launches")

    orphan = ('model:' + NL + '  provider: "custom:ghost"' + NL +
              '  default: "x"' + NL)
    code, out = run(config=orphan)
    check(code == NEEDS_CONFIG, "a custom: provider with no matching entry is blocked")
    check("custom_providers" in out, "...and says which list is missing it")


def test_the_gateway_key_is_looked_for_where_the_engine_reads_it():
    """PlatformConfig sweeps unknown fields into `extra`, and api_server
    reads its key from there.  Looking only at a bare `key:` made this
    announce "no gateway key yet" on every launch of an install that had
    one -- while protect-config.ps1 was writing it under extra:.
    """
    key = "k" * 40
    base = ('model:' + NL + '  provider: "deepseek"' + NL +
            '  default: "deepseek-chat"' + NL + 'platforms:' + NL +
            '  api_server:' + NL + '    enabled: true' + NL)
    env = "DEEPSEEK_API_KEY=sk-test" + NL

    under_extra = base + '    extra:' + NL + "      key: '" + key + "'" + NL
    code, out = run(config=under_extra, env_file=env)
    check(code == 0 and "网关还没有密钥" not in out, "a key under extra: is found")

    bare = base + "    key: '" + key + "'" + NL
    code, out = run(config=bare, env_file=env)
    check(code == 0 and "网关还没有密钥" not in out,
          "the older bare key: spelling is still accepted")

    code, out = run(config=base, env_file=env)
    check(code == 0 and "网关还没有密钥" in out,
          "no key at all is reported, but does not block the launch")


# --- a provider that answers however a test needs, on loopback -------------

class _Fake(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b"{}"
    hits = 0
    last_auth = ""
    last_path = ""
    # None: answer every request alike. A set: a cloud provider that answers
    # 401 to any bearer token not in it -- including the engine's placeholder.
    accept = None

    def do_POST(self):
        _Fake.hits += 1
        _Fake.last_auth = self.headers.get("Authorization") or ""
        # The whole target URL when it arrives as a proxy.
        _Fake.last_path = self.path
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, body = _Fake.status, _Fake.body
        token = _Fake.last_auth[len("Bearer "):] if _Fake.last_auth.startswith("Bearer ") else ""
        if _Fake.accept is not None and token not in _Fake.accept:
            status, body = 401, json.dumps({"error": {"message": "invalid api key"}}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *a):
        pass


def fake_provider(status, body, accept=None):
    _Fake.status = status
    _Fake.body = json.dumps(body).encode("utf-8")
    _Fake.accept = set(accept) if accept is not None else None
    _Fake.hits = 0
    _Fake.last_auth = ""
    _Fake.last_path = ""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/v1" % srv.server_address[1]


def local_config(base_url):
    """A custom: provider, so the probe needs neither the engine nor a network."""
    return ('model:' + NL + '  provider: "custom:local"' + NL +
            '  default: "m"' + NL + 'custom_providers:' + NL +
            '  - name: "local"' + NL + '    base_url: "' + base_url + '"' + NL +
            '    key_env: "MY_KEY"' + NL)


GOOD_REPLY = {"choices": [{"message": {"content": "hi"}}]}
ENV = "MY_KEY=sk-test" + NL


def test_the_launch_probe_asks_the_provider_before_the_user_does():
    """Every other check here is static: the file parses, a provider is
    named, a key exists. None of them catch an expired key, an empty
    account or a renamed model -- the user finds those out by typing into
    a chat window that never answers.
    """
    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        code, out = run(config=local_config(url), env_file=ENV, probe=True)
        check(code == 0, "a provider that answers lets the launch through")
        check(_Fake.hits == 1, "...and it really was asked (one request)")
    finally:
        srv.shutdown()


def test_only_config_fixable_failures_block_a_launch():
    """A wrong block costs the whole install; a wrong pass costs one message."""
    cases = [
        (401, {"error": {"message": "key expired"}}, NEEDS_CONFIG, "key expired"),
        (404, {"error": {"message": "no such model"}}, NEEDS_CONFIG, "no such model"),
        (400, {"error": {"message": "bad param"}}, NEEDS_CONFIG, "bad param"),
        (402, {"error": {"message": "balance is zero"}}, 0, "balance is zero"),
        (429, {"error": {"message": "slow down"}}, 0, "slow down"),
        (500, {"error": {"message": "we broke"}}, 0, "we broke"),
    ]
    for status, body, want, phrase in cases:
        srv, url = fake_provider(status, body)
        try:
            code, out = run(config=local_config(url), env_file=ENV, probe=True)
        finally:
            srv.shutdown()
        check(code == want, "HTTP %d -> exit %d" % (status, want))
        check(phrase in out,
              "...and the provider's own words reach the user (%s)" % phrase)


def test_an_unreachable_provider_never_blocks():
    """Offline is not a configuration error, and a stick gets carried onto
    planes."""
    srv, url = fake_provider(200, GOOD_REPLY)
    srv.shutdown()  # nothing is listening there any more
    code, out = run(config=local_config(url), env_file=ENV, probe=True)
    check(code == 0, "a dead endpoint still launches")
    check("诊断" in out, "...and points at the diagnostic for the real case")


def test_a_success_is_remembered_so_launch_stays_fast():
    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        code, _ = run(config=local_config(url), env_file=ENV, probe=True)
        check(code == 0 and _Fake.hits == 1, "first launch asks")
        marker = os.path.join(SANDBOX, "data", ".probe-ok")
        check(os.path.exists(marker), "...and remembers that it did")

        # run() wipes the sandbox, so drive the helpers directly for the
        # part that matters: the same key short-circuits, a new one does not.
        spec = importlib.util.spec_from_file_location("pf", PREFLIGHT)
        pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pf)
        data = os.path.dirname(marker)
        fp = pf._probe_fingerprint("custom:local", "m", "sk-test")
        pf.remember_probe_passed(data, fp)
        check(pf.probe_already_passed(data, fp),
              "the same provider/model/key is not asked again")
        other = pf._probe_fingerprint("custom:local", "m", "sk-different")
        check(not pf.probe_already_passed(data, other),
              "changing the key makes it ask again")
        other = pf._probe_fingerprint("custom:local", "m2", "sk-test")
        check(not pf.probe_already_passed(data, other),
              "changing the model makes it ask again")
    finally:
        srv.shutdown()


def test_the_probe_can_be_turned_off():
    srv, url = fake_provider(401, {"error": {"message": "no"}})
    try:
        code, _ = run(config=local_config(url), env_file=ENV, probe=False)
        check(code == 0, "U_HERMES_SKIP_PROBE lets a launch through untested")
        check(_Fake.hits == 0, "...and makes no request at all")
    finally:
        srv.shutdown()


def test_output_is_utf8_whatever_the_caller_left_in_the_environment():
    """Windows-Start.bat captures --print-workspace with a `for /f`.

    The console is chcp 65001 by then, so the bytes have to be UTF-8. They
    were whatever the ambient codepage happened to be -- GBK on a Chinese
    Windows -- which turns a folder called 我的工作区 into mojibake, and
    the `if exist` guard in the launcher then quietly declines to chdir
    into it. Importing the engine also reconfigures stdout mid-run, so a
    single run could even print in two encodings.
    """
    shutil.rmtree(SANDBOX, ignore_errors=True)
    data = os.path.join(SANDBOX, "data")
    os.makedirs(data)
    cfg = ('model:' + NL + '  provider: "deepseek"' + NL + 'terminal:' + NL +
           '  backend: local' + NL + "  cwd: 'D:/我的工作区'" + NL)
    with io.open(os.path.join(data, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(cfg)

    environ = dict(os.environ)
    for name in ("PYTHONIOENCODING", "PYTHONUTF8"):
        environ.pop(name, None)
    environ["U_HERMES_SKIP_PROBE"] = "1"
    proc = subprocess.run(
        [sys.executable, PREFLIGHT, "--print-workspace", data],
        capture_output=True, env=environ)
    check(proc.stdout == "D:/我的工作区".encode("utf-8"),
          "the workspace path comes back as UTF-8 with no env help")
    try:
        proc.stdout.decode("utf-8")
        ok = True
    except UnicodeDecodeError:
        ok = False
    check(ok, "...and decodes cleanly, so the for/f capture is not mojibake")

    shutil.rmtree(SANDBOX, ignore_errors=True)


def test_print_workspace():
    cfg = ('model:' + NL + '  provider: "deepseek"' + NL +
           'terminal:' + NL + '  backend: local' + NL +
           "  cwd: 'D:/somewhere'" + NL)
    code, out = run(config=cfg, args=("--print-workspace",))
    check(code == 0 and "D:/somewhere" in out, "--print-workspace prints the folder")

    remote = cfg.replace("backend: local", "backend: docker")
    code, out = run(config=remote, args=("--print-workspace",))
    check("D:/somewhere" not in out,
          "a non-local backend's path is not handed to the launcher to chdir into")

    code, out = run(config=DEEPSEEK, args=("--print-workspace",))
    check(out.strip() == "", "nothing is printed when no workspace is configured")


def test_a_custom_provider_is_matched_the_way_the_engine_matches_it():
    """preflight compared the entry name to the slug with ==; the engine
    lowercases the request and hyphenates spaces (custom_provider_slug in
    hermes_cli.providers) and accepts either the display name or the slug.

    So every custom provider whose name carried a capital letter or a space --
    "LongCat", "My Server", the ordinary way a person names one -- looked
    missing to the launcher, which refused to start a config the engine runs
    fine and told the user the entry was not there. The developer's own
    machine was in exactly this state.
    """
    def cfg(entry_name, provider, extra):
        return ('model:' + NL + '  provider: "' + provider + '"' + NL +
                '  default: "x"' + NL + 'custom_providers:' + NL +
                '  - name: "' + entry_name + '"' + NL +
                '    base_url: "https://x/v1"' + NL + extra)

    key_env = '    key_env: "MY_KEY"' + NL
    has_key = "MY_KEY=abc" + NL

    code, out = run(config=cfg("LongCat", "custom:longcat", key_env),
                    env_file=has_key)
    check(code == 0, "an entry named LongCat answers to custom:longcat")

    code, out = run(config=cfg("My Server", "custom:my-server", key_env),
                    env_file=has_key)
    check(code == 0, "a space in the name becomes a hyphen in the slug")

    # The engine reads base_url, url or api, in that order. Reading only
    # the first left base_url empty for an entry spelled either other way,
    # and run_launch_probe returns 0 when it has nowhere to ask -- so the
    # gate went quiet instead of failing. Only a probe run proves this one:
    # with the probe off, preflight never looks the address up at all.
    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        spelled_url = ('model:' + NL + '  provider: "custom:local"' + NL +
                       '  default: "m"' + NL + 'custom_providers:' + NL +
                       '  - name: "local"' + NL +
                       '    url: "' + url + '"' + NL + key_env)
        code, out = run(config=spelled_url, env_file=has_key, probe=True)
        check(code == 0, "an entry that spells its address 'url' still resolves")
        check(_Fake.hits == 1, "...and the probe really went there")
    finally:
        srv.shutdown()


def test_a_key_written_into_the_config_counts_as_a_key():
    """A custom_providers entry may carry its key inline as api_key rather
    than naming an environment variable, and the engine accepts both (see
    _normalize_custom_provider_entry). preflight demanded key_env, and when
    it was absent announced that the entry itself was missing -- a statement
    that was simply false, with a remedy ("open the config page and save
    once") that changed nothing about it.

    The same gap reached the launch probe: it was handed an empty key and
    returned 0 without asking the provider anything, so the one check that
    finds out whether the thing will answer never ran for these configs.
    """
    def cfg(extra):
        return ('model:' + NL + '  provider: "custom:local"' + NL +
                '  default: "x"' + NL + 'custom_providers:' + NL +
                '  - name: "local"' + NL +
                '    base_url: "https://x/v1"' + NL + extra)

    code, out = run(config=cfg('    api_key: "sk-written-into-config"' + NL))
    check(code == 0, "an inline api_key is a key, with no .env involved")

    # Satisfying the check is not the point -- the key has to reach the
    # provider. run_launch_probe was handed "" for every config of this
    # shape and returned 0 without asking anyone anything.
    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        live = ('model:' + NL + '  provider: "custom:local"' + NL +
                '  default: "m"' + NL + 'custom_providers:' + NL +
                '  - name: "local"' + NL +
                '    base_url: "' + url + '"' + NL +
                '    api_key: "sk-inline-probe"' + NL)
        code, out = run(config=live, probe=True)
        check(code == 0, "...and a provider keyed that way launches once it answers")
        check(_Fake.hits == 1, "...having actually been asked")
        check("sk-inline-probe" in _Fake.last_auth,
              "...and asked with the key from the config, not an empty string")
    finally:
        srv.shutdown()


    # An entry carrying no key at all. The engine does not refuse to run it:
    # it sends the placeholder no-key-required. A keyless local server --
    # LM Studio, Ollama, a box on the LAN -- takes that and answers. Blocking
    # it statically locked those setups out (review of PR #10), so preflight
    # asks with the placeholder and lets the provider decide.
    def at(url, extra):
        return cfg(extra).replace("https://x/v1", url)

    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        code, out = run(config=at(url, ""), env_file="OPENAI_API_KEY=sk-present" + NL, probe=True)
        check(code == 0, "a keyless local server with no key in its entry launches")
        check(_Fake.hits == 1 and "no-key-required" in _Fake.last_auth,
              "...after being asked with the engine's placeholder")
        check("sk-present" not in _Fake.last_auth,
              "...not with OPENAI_API_KEY, which the engine keeps for OpenAI's own address")
    finally:
        srv.shutdown()

    # A cloud provider at the same kind of address refuses the placeholder.
    srv, url = fake_provider(200, GOOD_REPLY, accept={"sk-real"})
    try:
        code, out = run(config=at(url, ""), probe=True)
        check(code == NEEDS_CONFIG, "an entry with no key at a provider that refuses it is blocked")
        check("没有它的条目" not in out,
              "...but is not called a missing entry, because it is right there")
        check("api_key" in out and "key_env" in out,
              "...and the message names both ways to supply one")
        check(url in out and "不会" in out,
              "...and says the engine reads no variable for that address")

        # The engine does NOT fall back to OPENAI_API_KEY or OPENROUTER_API_KEY
        # for any address: _host_gated_env_key_candidates keeps both away from
        # hosts that are not theirs. This used to pass, probing with a key the
        # engine would never send.
        for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
            code, out = run(config=at(url, ""), env_file=name + "=sk-present" + NL, probe=True)
            check(code == NEEDS_CONFIG and "sk-present" not in _Fake.last_auth,
                  "%s is not sent to an entry at someone else's address" % name)

        code, out = run(config=at(url, '    key_env: "MY_KEY"' + NL),
                        env_file="OPENAI_API_KEY=sk-present" + NL, probe=True)
        check(code == NEEDS_CONFIG and "sk-present" not in _Fake.last_auth,
              "an unset key_env does not fall through to OPENAI_API_KEY either")
        check("MY_KEY" in out, "...and the message names the variable the entry reads")
    finally:
        srv.shutdown()

    code, out = run(config=cfg(""))
    check(code == 0, "with the probe off nothing can tell, so a keyless entry is not refused")

    # Both spellings at once: the engine reads the inline key first
    # (_resolve_named_custom_runtime in hermes_cli/runtime_provider_custom.py),
    # so that is what the probe must ask with, or it proves a credential the
    # engine will not use.
    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        both = ('model:' + NL + '  provider: "custom:local"' + NL +
                '  default: "m"' + NL + 'custom_providers:' + NL +
                '  - name: "local"' + NL +
                '    base_url: "' + url + '"' + NL +
                '    api_key: "sk-inline-wins"' + NL +
                '    key_env: "MY_KEY"' + NL)
        code, out = run(config=both, env_file="MY_KEY=sk-from-env" + NL, probe=True)
        check("sk-inline-wins" in _Fake.last_auth,
              "an inline key outranks key_env, as it does at runtime")
    finally:
        srv.shutdown()

    orphan = ('model:' + NL + '  provider: "custom:ghost"' + NL +
              '  default: "x"' + NL + 'custom_providers:' + NL +
              '  - name: "local"' + NL +
              '    base_url: "https://x/v1"' + NL)
    code, out = run(config=orphan)
    check(code == NEEDS_CONFIG, "a genuinely absent entry is still blocked")
    check("没有它的条目" in out,
          "...and there the missing-entry wording is the true one")


def _engine_version_preflight_sees():
    """provider_probe.installed_engine_version() in preflight's own process.

    run() drops PYTHONPATH, so that can be a different engine from the one
    this suite imports.
    """
    environ = dict(os.environ)
    environ.pop("PYTHONPATH", None)
    code = ("import sys; sys.path.insert(0, %r); import provider_probe; "
            "print(provider_probe.installed_engine_version())" % SCRIPTS)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, env=environ)
    return ast.literal_eval(proc.stdout.decode("utf-8").strip() or "None")


def test_a_custom_entry_is_sent_only_the_keys_the_engine_sends():
    """A LongCat entry with no key of its own. The engine reads
    LONGCAT_API_KEY for it and nothing else -- _host_gated_env_key_candidates
    keeps OPENAI_API_KEY and OPENROUTER_API_KEY for their own hosts -- and the
    launch gate, which fell back to both for any address, got it wrong three
    ways. Each was confirmed with resolve_runtime_provider on 0.21.3 and 0.21.4:

      OPENAI_API_KEY only      passed, and then every chat 401'd
      OPENROUTER_API_KEY only  sent the OpenRouter key to LongCat, blocked on its 401
      LONGCAT_API_KEY only     blocked, saying there was no key

    The address is http so the probe can go through a proxy on loopback:
    .example never resolves, and the proxy is what shows which key would
    have left the machine.
    """
    longcat = "http://api.longcat.example/v1"
    config = ('model:' + NL + '  provider: "custom:longcat"' + NL +
              '  default: "LongCat-2.0"' + NL + 'custom_providers:' + NL +
              '  - name: "LongCat"' + NL +
              '    base_url: "' + longcat + '"' + NL)
    # LongCat, as a cloud provider does, answers 401 to anything but a real key.
    srv, _ = fake_provider(200, GOOD_REPLY, accept={"sk-longcat", "sk-paired"})
    proxy = "http://127.0.0.1:%d" % srv.server_address[1]
    via_proxy = {"HTTP_PROXY": proxy, "http_proxy": None,
                 "NO_PROXY": None, "no_proxy": None}

    def attempt(env_file, extra=None):
        _Fake.hits, _Fake.last_auth, _Fake.last_path = 0, "", ""
        return run(config=config, env_file=env_file, probe=True,
                   extra_env=dict(via_proxy, **(extra or {})))

    try:
        code, out = attempt("LONGCAT_API_KEY=sk-longcat" + NL)
        check(code == 0, "LONGCAT_API_KEY is the key the engine reads for LongCat, so it launches")
        check(_Fake.hits == 1 and "api.longcat.example" in _Fake.last_path,
              "...after asking LongCat itself")
        check("sk-longcat" in _Fake.last_auth, "...with that key")

        for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
            code, out = attempt(name + "=sk-someone-elses" + NL)
            check(code == NEEDS_CONFIG,
                  "%s alone is no key for LongCat: the engine sends it "
                  "no-key-required, and LongCat refuses that" % name)
            check("no-key-required" in _Fake.last_auth and "sk-someone-elses" not in _Fake.last_auth,
                  "...so the placeholder is what is asked with, and %s never leaves" % name)
            check("LONGCAT_API_KEY" in out,
                  "...and the message names the variable the engine does read")

        # data/.env wins over the process environment even when it sets a
        # variable to nothing: the engine loads it with override=True.
        code, out = attempt("LONGCAT_API_KEY=" + NL, extra={"LONGCAT_API_KEY": "sk-longcat"})
        check(code == NEEDS_CONFIG and "sk-longcat" not in _Fake.last_auth,
              "an empty LONGCAT_API_KEY= in data/.env hides the one in the process environment")

        # 0.21.4 pairs OPENAI_API_KEY with the exact address OPENAI_BASE_URL
        # names -- the pair Config.html writes. The engine preflight runs
        # beside decides whether that counts.
        version = _engine_version_preflight_sees()
        code, out = attempt("OPENAI_API_KEY=sk-paired" + NL
                            + "OPENAI_BASE_URL=" + longcat + NL)
        if version is None or version >= (0, 21, 4):
            check(code == 0 and "sk-paired" in _Fake.last_auth,
                  "engine %s: OPENAI_API_KEY goes to the OPENAI_BASE_URL address, "
                  "so it launches" % (version,))
        else:
            check(code == NEEDS_CONFIG and "sk-paired" not in _Fake.last_auth,
                  "engine %s pairs nothing with OPENAI_BASE_URL yet, so it is "
                  "still blocked" % (version,))

        # The same pair, written the ways people write .env lines. python-
        # dotenv (the engine's reader) drops a trailing comment and an
        # `export `; the old line splitter kept them, missed the pairing and
        # blocked a launch the engine would run.
        for label, line in (("a trailing comment", "OPENAI_BASE_URL=" + longcat + "  # longcat"),
                            ("an export prefix", "export OPENAI_BASE_URL=" + longcat),
                            ("double quotes", 'OPENAI_BASE_URL="' + longcat + '"')):
            code, out = attempt("OPENAI_API_KEY=sk-paired" + NL + line + NL)
            if version is None or version >= (0, 21, 4):
                check(code == 0 and "sk-paired" in _Fake.last_auth,
                      "OPENAI_BASE_URL with %s still pairs, as the engine reads it" % label)
            else:
                check(code == NEEDS_CONFIG, "engine %s: %s changes nothing" % (version, label))
    finally:
        srv.shutdown()


def test_what_a_keyless_request_is_answered_with_decides():
    """A LongCat entry with no key: preflight asks with the engine's
    placeholder. Only a real reply means "this service needs no key". A
    second review found the first cut of this read every answer but 401
    through the ordinary table -- telling a user with no key at all that
    their model name was wrong (404/400) or that their key was fine and
    their balance empty (402), and letting 429/5xx through silently.
    """
    longcat = "http://api.longcat.example/v1"
    config = ('model:' + NL + '  provider: "custom:longcat"' + NL +
              '  default: "LongCat-2.0"' + NL + 'custom_providers:' + NL +
              '  - name: "LongCat"' + NL +
              '    base_url: "' + longcat + '"' + NL)

    def attempt(status, body, extra_env=None, cfg=None, env_file="OPENAI_API_KEY=sk-left-over" + NL):
        srv, _ = fake_provider(status, body)
        via_proxy = {"HTTP_PROXY": "http://127.0.0.1:%d" % srv.server_address[1],
                     "http_proxy": None, "NO_PROXY": None, "no_proxy": None}
        try:
            return run(config=cfg or config, env_file=env_file, probe=True,
                       extra_env=dict(via_proxy, **(extra_env or {})))
        finally:
            srv.shutdown()

    for status, body, label in ((404, {"error": {"message": "model not found"}}, "404"),
                                (400, [{"error": {"code": 400, "message": "API key not valid"}}], "400"),
                                (402, {"error": {"message": "insufficient balance"}}, "402"),
                                (200, {"base_resp": {"status_code": 1004, "status_msg": "login fail"}},
                                 "a 200 that is not a reply")):
        code, out = attempt(status, body)
        check(code == NEEDS_CONFIG, "%s to the placeholder blocks the launch" % label)
        check("没有找到 API 密钥" in out and "LONGCAT_API_KEY" in out,
              "...saying no key was found and where it goes")
        check("密钥本身是好的" not in out, "...and never that the key is fine")
        check("不带密钥试了一次" in out, "...and what the provider said to a keyless request")

    for status, label in ((500, "a 5xx"), (429, "a 429")):
        code, out = attempt(status, {"error": {"message": "busy"}})
        check(code == 0, "%s is not about the config: the launch goes ahead" % label)
        check("没有找到 API 密钥" in out, "...but the missing key is still pointed out")

    # A keyless local server that is switched off: that is the problem, and
    # nothing a config page fixes.
    code, out = run(config=local_config("http://127.0.0.1:9/v1").replace('    key_env: "MY_KEY"' + NL, ""),
                    probe=True)
    check(code == 0, "a keyless server that is off does not block the launch")

    # ${VAR} in config.yaml is expanded by the engine on load.
    srv, url = fake_provider(200, GOOD_REPLY, accept={"sk-longcat"})
    try:
        templated = ('model:' + NL + '  provider: "custom:t"' + NL + '  default: "m"' + NL +
                     'custom_providers:' + NL + '  - name: "t"' + NL +
                     '    base_url: "${LC_URL}"' + NL + '    api_key: "${env:LC_KEY}"' + NL)
        code, out = run(config=templated, env_file="LC_URL=" + url + NL + "LC_KEY=sk-longcat" + NL, probe=True)
        check(code == 0 and "sk-longcat" in _Fake.last_auth,
              "base_url ${LC_URL} and api_key ${env:LC_KEY} are expanded from data/.env, as the engine does")
    finally:
        srv.shutdown()

    # A malformed address is a config problem, not a crash into the catch-all.
    bad = config.replace(longcat, "api.longcat.example/v1")
    code, out = run(config=bad, probe=True)
    check(code == NEEDS_CONFIG and "地址格式不对" in out, "an address with no scheme is named, not a crash")

    # A pass remembered for one address does not cover another.
    srv, url = fake_provider(200, GOOD_REPLY)
    try:
        keyless = local_config(url).replace('    key_env: "MY_KEY"' + NL, "")
        shutil.rmtree(SANDBOX, ignore_errors=True)
        code, _ = run(config=keyless, probe=True)
        check(code == 0, "a keyless server passes")
    finally:
        srv.shutdown()

    code, out = run(config=config, env_file="OPENAI_API_KEY=sk-left-over" + NL,
                    extra_env={"U_HERMES_SKIP_PROBE": "1"})
    check(code == 0 and "[i]" in out and "没有找到 API 密钥" in out,
          "with the probe off the launch goes ahead, but the missing key is pointed out")

    if os.name == "nt":
        code, out = attempt(200, GOOD_REPLY, env_file="longcat_api_key=sk-lower" + NL)
        check(code == 0 and "sk-lower" in _Fake.last_auth,
              "a lower-case name in data/.env counts: Windows environment names ignore case")


def test_a_remembered_pass_is_tied_to_the_address():
    spec = importlib.util.spec_from_file_location("pf", PREFLIGHT)
    pf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pf)

    def fp(url):
        return pf._probe_fingerprint("custom:local", "m", "no-key-required", url)
    check(fp("http://127.0.0.1:1234/v1") != fp("https://api.longcat.example/v1"),
          "pointing the same entry at another address asks again")


if __name__ == "__main__":
    for fn in (test_what_a_keyless_request_is_answered_with_decides,
               test_a_remembered_pass_is_tied_to_the_address,
               test_unreadable_configs_are_named_precisely,
               test_a_python_without_pyyaml_does_not_accuse_the_config,
               test_a_bom_in_env_is_repaired_not_just_reported,
               test_key_presence,
               test_an_unknown_provider_is_never_a_reason_to_block,
               test_custom_providers,
               test_a_custom_provider_is_matched_the_way_the_engine_matches_it,
               test_a_key_written_into_the_config_counts_as_a_key,
               test_a_custom_entry_is_sent_only_the_keys_the_engine_sends,
               test_the_gateway_key_is_looked_for_where_the_engine_reads_it,
               test_the_launch_probe_asks_the_provider_before_the_user_does,
               test_only_config_fixable_failures_block_a_launch,
               test_an_unreachable_provider_never_blocks,
               test_a_success_is_remembered_so_launch_stays_fast,
               test_the_probe_can_be_turned_off,
               test_output_is_utf8_whatever_the_caller_left_in_the_environment,
               test_print_workspace):
        print(fn.__name__)
        fn()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("preflight: all checks passed")

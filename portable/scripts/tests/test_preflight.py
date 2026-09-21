"""The launch gate must block only on things the user can actually fix.

preflight.py decides whether Windows-Start.bat opens the config page instead
of launching.  Both of its mistakes are expensive: a wrong block makes a
working install unusable, and a wrong pass sends the user to a chat window
that will never answer.  Everything asserted here is a case that got one of
those wrong at some point.

Run:  python portable/scripts/tests/test_preflight.py
"""
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


def run(config=None, env_file=None, args=(), no_yaml=False, probe=False):
    """Run preflight against a throwaway data dir; return (code, output)."""
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
    # pass for the wrong reason.
    for name in list(environ):
        if name.endswith("_API_KEY"):
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
    code, out = run(config=cfg)
    check(code == NEEDS_CONFIG, "a custom provider whose key variable is unset is blocked")
    check("MY_KEY" in out, "...and the message names that variable")

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

    def do_POST(self):
        _Fake.hits += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(_Fake.status)
        self.send_header("Content-Length", str(len(_Fake.body)))
        self.end_headers()
        self.wfile.write(_Fake.body)

    def log_message(self, fmt, *a):
        pass


def fake_provider(status, body):
    _Fake.status = status
    _Fake.body = json.dumps(body).encode("utf-8")
    _Fake.hits = 0
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


if __name__ == "__main__":
    for fn in (test_unreadable_configs_are_named_precisely,
               test_a_python_without_pyyaml_does_not_accuse_the_config,
               test_a_bom_in_env_is_repaired_not_just_reported,
               test_key_presence,
               test_an_unknown_provider_is_never_a_reason_to_block,
               test_custom_providers,
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

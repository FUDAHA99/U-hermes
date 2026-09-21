"""The launch gate must block only on things the user can actually fix.

preflight.py decides whether Windows-Start.bat opens the config page instead
of launching.  Both of its mistakes are expensive: a wrong block makes a
working install unusable, and a wrong pass sends the user to a chat window
that will never answer.  Everything asserted here is a case that got one of
those wrong at some point.

Run:  python portable/scripts/tests/test_preflight.py
"""
import io
import os
import shutil
import subprocess
import sys

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


def run(config=None, env_file=None, args=(), no_yaml=False):
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
               test_key_presence,
               test_an_unknown_provider_is_never_a_reason_to_block,
               test_custom_providers,
               test_the_gateway_key_is_looked_for_where_the_engine_reads_it,
               test_print_workspace):
        print(fn.__name__)
        fn()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("preflight: all checks passed")

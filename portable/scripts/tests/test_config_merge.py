"""Saving from the config page must not delete anything it does not own.

The config page renders a handful of settings.  Everything else in
config.yaml -- the gateway's api_server block, database.journal_mode,
terminal.cwd, providers the user added by hand, their comments -- has to
survive a save.  An earlier version truncated the file instead, which left
the gateway unable to start and was invisible until the user tried to chat.

Run:  python portable/scripts/tests/test_config_merge.py
"""
import importlib.util
import os
import shutil
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

_spec = importlib.util.spec_from_file_location(
    "config_server", os.path.join(SCRIPTS, "config-server.py")
)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

NL = chr(10)
CRLF = chr(13) + NL

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


# A config that has been through a few launches: the shipped defaults, the
# key protect-config.ps1 generated, a workspace, and the user's own edits.
LIVED_IN = """\
# U-Hermes 配置文件 —— 手写的注释必须活下来
model:
  default: "LongCat-2.0"
  provider: "custom:longcat"

# SQLite WAL is not crash-safe on the exFAT filesystems USB drives ship with.
database:
  journal_mode: "delete"

# Gateway API server. Upstream reads this under platforms.*, not at the
# top level, where it was silently ignored.
platforms:
  api_server:
    enabled: true
    extra:
      key: 'aaaaaaaabbbbbbbbccccccccddddddddeeeeeeeeffffffff00000000'
      port: 8642
      host: 127.0.0.1

terminal:
  cwd: 'H:\\U-Hermes工作区'
  backend: local

custom_providers:
  - name: "longcat"
    base_url: "https://api.longcat.chat/openai/v1"
    key_env: "LONGCAT_API_KEY"
  - name: "mycorp"
    base_url: "https://llm.internal.example/v1"
    key_env: "MYCORP_API_KEY"

fallback_providers:
  - name: "agnes"
    model: "agnes-1"

toolsets:
  enabled:
    - shell
    - files

skills:
  external_dirs:
    - "../skills-cn"

memory:
  enabled: true

cron:
  enabled: true
"""

# Exactly what Config.html posts to /save-config.
FROM_PAGE = """\
# U-Hermes Configuration (auto-generated)
# Edit manually or re-run Config.html to change

model:
  provider: "deepseek"
  default: "deepseek-chat"
  model: "deepseek-chat"

platforms:
  telegram:
    enabled: true
    token: "12345:ABC"

skills:
  external_dirs:
    - "../skills-cn"

memory:
  enabled: true

cron:
  enabled: true
"""

# What the release zip ships before the user has configured anything.
SHIPPED = """\
model:
  provider: ""
  model: ""
database:
  journal_mode: "delete"
platforms:
  api_server:
    enabled: true
    extra:
      port: 8642
      host: 127.0.0.1
skills:
  external_dirs:
    - "../skills-cn"
memory:
  enabled: true
cron:
  enabled: true
"""


def test_nothing_is_lost():
    merged = cs.merge_yaml(LIVED_IN, FROM_PAGE)
    data = cs.parse_yaml_mapping(merged)
    check(data is not None, "the merged config still parses")
    if data is None:
        return

    check(data["database"]["journal_mode"] == "delete", "database.journal_mode survives")
    check(data["platforms"]["api_server"]["enabled"] is True, "platforms.api_server survives")
    check(len(str(data["platforms"]["api_server"]["extra"]["key"])) >= 32, "the gateway api key survives")
    check(data["platforms"]["api_server"]["extra"]["port"] == 8642, "api_server.extra survives")
    check(data["terminal"]["cwd"].endswith("U-Hermes工作区"), "terminal.cwd survives, non-ASCII intact")
    check(data["terminal"]["backend"] == "local", "terminal.backend survives")
    check(data["toolsets"]["enabled"] == ["shell", "files"], "toolsets survives")
    check(any(p["name"] == "mycorp" for p in data["custom_providers"]),
          "a provider the page never heard of survives")
    check(any(p["name"] == "agnes" for p in data["fallback_providers"]), "fallback_providers survives")
    check("手写的注释必须活下来" in merged, "the file header comment survives")
    check("SQLite WAL is not crash-safe" in merged, "a comment on an untouched block survives")

    check(data["model"]["provider"] == "deepseek", "the page's provider is applied")
    check(data["model"]["default"] == "deepseek-chat", "the page's model lands in model.default")
    # The messaging section shares the top-level `platforms:` block with the
    # gateway's own api_server entry, so adding a bot must not evict it.
    check(data["platforms"]["telegram"]["token"] == "12345:ABC",
          "the page's bot token lands where the engine reads it")
    check(data["platforms"]["telegram"]["enabled"] is True, "the bot is enabled")
    check(data["platforms"]["api_server"]["extra"]["port"] == 8642,
          "adding a bot does not evict the gateway's api_server entry")


def test_shape():
    check(cs.merge_yaml(LIVED_IN, FROM_PAGE) == cs.merge_yaml(cs.merge_yaml(LIVED_IN, FROM_PAGE), FROM_PAGE),
          "saving twice in a row changes nothing")
    check(cs.merge_yaml("", FROM_PAGE) == FROM_PAGE, "an empty config file is simply written")

    fresh = cs.parse_yaml_mapping(cs.merge_yaml(SHIPPED, FROM_PAGE))
    check(fresh["database"]["journal_mode"] == "delete", "a fresh install keeps journal_mode")
    check(fresh["platforms"]["api_server"]["extra"]["port"] == 8642,
          "a fresh install keeps the gateway block, so the gateway still starts")

    crlf = cs.merge_yaml(LIVED_IN.replace("\n", "\r\n"), FROM_PAGE)
    check("\n" not in crlf.replace("\r\n", ""), "a CRLF file stays CRLF")


def test_foreign_indentation():
    """The file's own style must not be mistaken for a different shape.

    Whenever the Web UI saves, PyYAML rewrites config.yaml and puts top-level
    list items at column 0, while the config page writes them at indent 2.
    An earlier version required the two to match exactly and fell back to a
    wholesale replace when they did not -- so the very next save through the
    config page deleted every provider the page did not know about.
    """
    import yaml

    pyyaml_style = yaml.safe_dump(
        {
            "model": {"provider": "custom:longcat", "default": "LongCat-2.0", "temperature": 0.3},
            "custom_providers": [
                {"name": "longcat", "key_env": "LONGCAT_API_KEY"},
                {"name": "mycorp", "key_env": "MYCORP_API_KEY"},
            ],
            "database": {"journal_mode": "delete"},
        },
        allow_unicode=True,
        sort_keys=False,
    )
    four_space = """\
model:
    provider: "custom:longcat"
    default: "LongCat-2.0"
    temperature: 0.3
custom_providers:
    -   name: "longcat"
        key_env: "LONGCAT_API_KEY"
    -   name: "mycorp"
        key_env: "MYCORP_API_KEY"
database:
    journal_mode: "delete"
"""

    for label, existing in (("PyYAML column-0 lists", pyyaml_style), ("4-space indent", four_space)):
        data = cs.parse_yaml_mapping(cs.merge_yaml(existing, FROM_PAGE))
        check(data is not None, "%s: the merged file still parses" % label)
        if data is None:
            continue
        names = [p["name"] for p in data["custom_providers"]]
        check("mycorp" in names, "%s: a provider the page never heard of survives" % label)
        check(data["model"].get("temperature") == 0.3, "%s: an unrelated model setting survives" % label)
        check(data["database"]["journal_mode"] == "delete", "%s: database.journal_mode survives" % label)
        check(data["model"]["provider"] == "deepseek", "%s: the page's provider is still applied" % label)


def test_byte_order_mark():
    """A BOM on config.yaml must not hide the first key.

    protect-config.ps1 writes this file before the user ever opens the config
    page, and Windows PowerShell 5.1's Set-Content -Encoding utf8 writes a
    BOM. With \\ufeff in front of it, `model:` stops looking like a key, and
    the page's own model: block gets appended as a second top-level model:
    -- leaving the stale one in the file on every fresh install.
    """
    with_bom = "﻿" + SHIPPED
    merged = cs.merge_yaml(with_bom, FROM_PAGE)
    keys = [ln for ln in merged.splitlines() if ln.lstrip("﻿").startswith("model:")]
    check(len(keys) == 1, "exactly one top-level model: key survives (got %d)" % len(keys))
    check(merged.startswith("﻿"), "the file keeps the BOM it came with")

    data = cs.parse_yaml_mapping(merged)
    check(data is not None, "a config with a BOM still parses after merge")
    if data is not None:
        check(data["model"]["provider"] == "deepseek", "the page's provider is applied")
        check(data["platforms"]["api_server"]["extra"]["port"] == 8642,
              "the gateway block survives a BOM'd merge")
    check(not cs.merge_yaml(SHIPPED, FROM_PAGE).startswith("﻿"),
          "a file without a BOM does not gain one")


def test_provider_switch_does_not_inherit_the_old_endpoint():
    """Switching provider must not leave the previous one's endpoint behind.

    The engine prefers `model.base_url` over the provider's own default, so a
    leftover from an earlier Ollama or hermes-auth-login setup would send
    every chat to a dead localhost address -- after the connection test had
    just succeeded against the real provider.
    """
    was_ollama = """\
model:
  provider: "custom"
  default: "qwen3:8b"
  base_url: "http://127.0.0.1:11434/v1"
  api_key: "ollama"
  temperature: 0.2
"""
    data = cs.parse_yaml_mapping(cs.merge_yaml(was_ollama, FROM_PAGE))
    check(data is not None, "the merged config still parses")
    if data is None:
        return
    check("base_url" not in data["model"], "the old provider's base_url is gone")
    check("api_key" not in data["model"], "the old provider's inline api_key is gone")
    check(data["model"]["provider"] == "deepseek", "the new provider is in place")
    check(data["model"].get("temperature") == 0.2, "settings not tied to the provider survive")


def test_workspace():
    """The workspace box must add a path without disturbing the block it lives in.

    terminal.cwd decides where the agent reads and writes the user's files.
    The engine falls back to the Windows profile of whoever is logged in when
    it is unset, so on a borrowed PC a blanked value means the agent starts
    writing into somebody else's Documents.
    """
    import tempfile

    lived_in = """\
model:
  provider: "custom:longcat"
terminal:
  cwd: 'F:\\现有工作区'
  backend: local
  timeout: 120
platforms:
  api_server:
    enabled: true
    extra:
      key: 'aaaaaaaabbbbbbbbccccccccddddddddeeeeeeeeffffffff00000000'
"""
    from_page = """\
model:
  provider: "deepseek"
  default: "deepseek-chat"

terminal:
  cwd: 'F:\\新的工作区'

skills:
  external_dirs:
    - "../skills-cn"
"""
    data = cs.parse_yaml_mapping(cs.merge_yaml(lived_in, from_page))
    check(data is not None, "the merged config parses")
    if data is None:
        return
    check(data["terminal"]["cwd"] == "F:\\新的工作区", "the new workspace is applied")
    check(data["terminal"]["backend"] == "local", "terminal.backend is not disturbed")
    check(data["terminal"]["timeout"] == 120, "an unrelated terminal setting survives")
    check(len(str(data["platforms"]["api_server"]["extra"]["key"])) >= 32,
          "the gateway key survives a workspace change")

    # A path that does not exist is otherwise invisible: the engine walks up
    # to the nearest existing ancestor, so D:\我的工作\区 silently becomes D:\
    # and the agent writes to the root of the drive.
    root = tempfile.mkdtemp(prefix="uh-ws-")
    fresh = os.path.join(root, "工作区", "子目录")
    merged = cs.merge_yaml(lived_in, from_page.replace("F:\\新的工作区", fresh))
    check(cs.settle_workspace(merged) is None, "a workspace that does not exist is accepted")
    check(os.path.isdir(fresh), "...because it gets created rather than silently relocated")

    blocker = os.path.join(root, "这是个文件")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("x")
    merged = cs.merge_yaml(lived_in, from_page.replace("F:\\新的工作区", blocker))
    check(cs.settle_workspace(merged) is not None, "a path that is a file is refused, not ignored")

    # Nothing to create for a container or an ssh host.
    remote = lived_in.replace("backend: local", "backend: docker")
    merged = cs.merge_yaml(remote, from_page.replace("F:\\新的工作区", "/srv/work"))
    check(cs.settle_workspace(merged) is None, "a non-local backend's path is left alone")

    shutil.rmtree(root, ignore_errors=True)


def test_bad_bodies_are_refused():
    # A cross-site form POST cannot produce a YAML mapping, so requiring one
    # closes the sandboxed-iframe route around the Origin check.
    check(cs.parse_yaml_mapping("model:=evil") is None, "a form-encoded body is refused")
    check(cs.parse_yaml_mapping("just a string") is None, "a plain string body is refused")
    check(cs.parse_yaml_mapping("- a\n- b\n") is None, "a list body is refused")
    check(cs.parse_yaml_mapping("model:\n  provider: x\n") is not None, "a real config is accepted")


def test_env_merge():
    before = "DEEPSEEK_API_KEY=old\nMY_CUSTOM=keepme\nANTHROPIC_API_KEY=sk-ant-x\n"
    after = cs.merge_env(before, "DEEPSEEK_API_KEY=new\nDEEPSEEK_BASE_URL=https://x/v1\n")
    check("MY_CUSTOM=keepme" in after, ".env keeps variables the page never set")
    check("ANTHROPIC_API_KEY=sk-ant-x" in after, ".env keeps other providers' keys")
    check("DEEPSEEK_API_KEY=new" in after and "DEEPSEEK_API_KEY=old" not in after,
          ".env replaces a key in place rather than appending a second one")
    check("DEEPSEEK_BASE_URL=https://x/v1" in after, ".env gains the base url override")


UNWRITABLE_CONFIG_BODY = """model:
  provider: "deepseek"
"""

UNWRITABLE_ENV_BODY = """DEEPSEEK_API_KEY=sk-test
"""


def _fake_handler(calls):
    """A ConfigHandler with no socket behind it, so _json is just a recorder."""
    h = cs.ConfigHandler.__new__(cs.ConfigHandler)
    h._json = lambda code, obj: calls.append((code, obj))
    return h


def test_a_write_that_cannot_land_is_reported():
    """A save that never reached the disk must not answer 200.

    On a write-protected U disk both handlers used to raise straight out of
    do_POST: the socket closed with no response at all, the page's fetch()
    rejected into a swallowed .catch(), and the user got a green
    "配置已保存！" over a config.yaml that had not changed and a .env with no
    key in it.  The agent then started and answered 401 on a key the user had
    just watched pass the connection test.
    """
    root = os.path.join(HERE, "_tmp_unwritable")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    cs.DATA_DIR = root
    cs.BACKUP_DIR = os.path.join(root, "backups")

    # write_atomic writes "<path>.tmp" and renames. A directory sitting on
    # that name makes the write fail the same way read-only media does,
    # without having to mount anything.
    for name in ("config.yaml", ".env"):
        os.makedirs(os.path.join(root, name + ".tmp"))

    calls = []
    _fake_handler(calls)._save_config(UNWRITABLE_CONFIG_BODY)
    code, body = calls[-1]
    check(code == 500, "a config write that fails answers 500, not 200")
    check(body.get("ok") is False, "the failed config save reports ok=false")
    check("未保存" in str(body.get("message", "")),
          "the message says the config was not saved")

    calls = []
    _fake_handler(calls)._save_env(UNWRITABLE_ENV_BODY)
    code, body = calls[-1]
    check(code == 500, "an .env write that fails answers 500, not 200")
    check("密钥未保存" in str(body.get("message", "")),
          "the message says the key was not saved")

    # And the happy path still answers 200 once the obstruction is gone.
    for name in ("config.yaml", ".env"):
        os.rmdir(os.path.join(root, name + ".tmp"))
    calls = []
    _fake_handler(calls)._save_env(UNWRITABLE_ENV_BODY)
    check(calls[-1][0] == 200, "a writable .env still answers 200")
    check(os.path.exists(os.path.join(root, ".env")), "and the key lands on disk")

    shutil.rmtree(root, ignore_errors=True)


def test_a_failed_rename_leaves_nothing_behind():
    """The half-written .tmp has to go, and the original has to survive.

    write_atomic writes "<path>.tmp" and renames. The earlier test forces
    the failure by putting a DIRECTORY on the .tmp name, so the open fails
    before a byte is written and the cleanup this covers never runs at all
    -- deleting the whole cleanup block left that suite green. Here the
    temp file really is written and the rename is what fails, which is the
    shape a locked destination or a disk that fills mid-write takes.

    A .tmp left lying beside config.yaml is not inert: it is a truncated
    config with a name a later repair step can mistake for a real one.
    """
    root = os.path.join(HERE, "_tmp_rename")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    cs.DATA_DIR = root
    cs.BACKUP_DIR = os.path.join(root, "backups")

    original = "model:" + NL + "  provider: 'keep-me'" + NL
    with open(os.path.join(root, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(original)

    real_replace = os.replace

    def refuse(src, dst):
        raise OSError(13, "Permission denied")

    os.replace = refuse
    try:
        calls = []
        _fake_handler(calls)._save_config(UNWRITABLE_CONFIG_BODY)
    finally:
        os.replace = real_replace

    check(calls[-1][0] == 500, "a rename that fails answers 500")
    check(not os.path.exists(os.path.join(root, "config.yaml.tmp")),
          "the half-written config.yaml.tmp is removed")
    with open(os.path.join(root, "config.yaml"), encoding="utf-8") as f:
        check(f.read() == original, "and the config already on disk is untouched")

    shutil.rmtree(root, ignore_errors=True)


def test_a_blank_workspace_becomes_the_default():
    """An empty terminal.cwd must never reach config.yaml.

    The config page offers "leave it empty to go back to the default". It
    used to send the empty string, and nothing downstream turned that back
    into a folder: the engine treats "" as set-but-blank, bridges
    TERMINAL_CWD="", and resolve_agent_cwd() falls through to os.getcwd() --
    the launcher's own directory. The agent then spent the whole session
    writing its files in among the program files, which is both what the
    workspace exists to prevent and the folder the uninstall instructions
    tell people to delete. protect-config.ps1 repairs the setting on the
    next launch, so the config heals itself and the files stay orphaned.
    """
    default = cs.default_workspace()
    for label, value in (("empty", "''"), ("a dot", "'.'"), ("auto", "'auto'")):
        text = ("model:" + NL + "  provider: x" + NL + "terminal:" + NL +
                "  backend: local" + NL + "  cwd: " + value + NL +
                "memory:" + NL + "  enabled: true" + NL)
        got = cs.resolve_blank_workspace(text)
        check(default in got, "%s becomes the default workspace" % label)
        check(got.count("cwd:") == 1, "...without leaving a second cwd behind")
        check("enabled: true" in got and "provider: x" in got,
              "...and nothing else in the file is touched")

    real = ("terminal:" + NL + "  backend: local" + NL + "  cwd: 'D:/mine'" + NL)
    check(cs.resolve_blank_workspace(real) == real, "a real path is left alone")

    remote = ("terminal:" + NL + "  backend: docker" + NL + "  cwd: ''" + NL)
    check(cs.resolve_blank_workspace(remote) == remote,
          "a container or ssh path is not ours to fill in")

    none = "model:" + NL + "  provider: x" + NL
    check(cs.resolve_blank_workspace(none) == none,
          "a config with no terminal: block gains nothing")

    top = "cwd: ''" + NL + "terminal:" + NL + "  backend: local" + NL
    check(cs.resolve_blank_workspace(top) == top,
          "a top-level cwd: that is not the terminal one is not rewritten")

    # protect-config.ps1 writes this file with a BOM and CRLF; the rewrite
    # has to hand back exactly the encoding it was given.
    fancy = ("﻿terminal:" + CRLF + "  backend: local" + CRLF +
             "  cwd: ''" + CRLF)
    got = cs.resolve_blank_workspace(fancy)
    check(got.startswith("﻿"), "the BOM survives the rewrite")
    check(got.count(CRLF) == 3 and got.count(NL) == 3, "and so do the CRLFs")


def test_write_failures_are_explained_in_chinese():
    import errno
    for code, want in ((errno.EACCES, "只读"), (errno.EROFS, "只读"),
                       (errno.ENOSPC, "满")):
        err = OSError(code, "x")
        err.errno = code
        msg = cs.write_failure_message("D:/x/config.yaml", err)
        check(want in msg, "errno %d is explained as %s" % (code, want))
    other = OSError(999, "x")
    other.errno = 999
    check("config.yaml" in cs.write_failure_message("D:/x/config.yaml", other),
          "an unrecognised errno still names the file")


def test_a_custom_provider_is_replaced_not_duplicated():
    """The page slugifies the name it writes; the merge keyed on the raw one.

    A user who names their provider "My Server" ends up with two entries --
    the one already in the file and the one the page just appended -- both of
    which the engine resolves as custom:my-server. It takes the first, so the
    entry the user just edited is the one it never reads: the new address and
    the new key sit in the file underneath the old ones, the connection test
    on the page passes (it calls the URL they typed, not the one in effect),
    and every chat still goes to the dead endpoint.

    Exactly the trap the model-block merge above exists to close, one layer
    down. The developer's own config.yaml had the duplicate pair in it.
    """
    existing = """\
model:
  provider: "custom:my-server"
  default: "m"

custom_providers:
  - name: "My Server"
    base_url: "https://old.example/v1"
    key_env: "OLD_KEY"
"""
    from_page = """\
model:
  provider: "custom:my-server"
  default: "m"

custom_providers:
  - name: "my-server"
    base_url: "https://new.example/v1"
    key_env: "OPENAI_API_KEY"
"""
    data = cs.parse_yaml_mapping(cs.merge_yaml(existing, from_page))
    check(data is not None, "the merged config still parses")
    if data is None:
        return
    entries = data.get("custom_providers") or []
    check(len(entries) == 1,
          "the entry is replaced, not appended alongside itself (got %d)"
          % len(entries))
    if entries:
        check(entries[0].get("base_url") == "https://new.example/v1",
              "...and the address in effect is the one just saved")
        check(entries[0].get("key_env") == "OPENAI_API_KEY",
              "...as is the variable its key is read from")

    # A provider the page knows nothing about still has to survive; that is
    # the whole reason this merge exists.
    kept = """\
model:
  provider: "custom:my-server"
  default: "m"

custom_providers:
  - name: "My Server"
    base_url: "https://old.example/v1"
  - name: "Something Else"
    base_url: "https://other.example/v1"
"""
    data = cs.parse_yaml_mapping(cs.merge_yaml(kept, from_page))
    names = [e.get("name") for e in (data.get("custom_providers") or [])]
    check(len(names) == 2 and "Something Else" in names,
          "an unrelated entry is untouched (got %s)" % names)


if __name__ == "__main__":
    for fn in (test_nothing_is_lost, test_shape, test_foreign_indentation,
               test_byte_order_mark,
               test_provider_switch_does_not_inherit_the_old_endpoint,
               test_a_custom_provider_is_replaced_not_duplicated,
               test_workspace,
               test_bad_bodies_are_refused, test_env_merge,
               test_a_write_that_cannot_land_is_reported,
               test_a_failed_rename_leaves_nothing_behind,
               test_a_blank_workspace_becomes_the_default,
               test_write_failures_are_explained_in_chinese):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("config merge: all checks passed")

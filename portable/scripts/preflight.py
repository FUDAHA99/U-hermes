"""Answer one question before the launcher starts anything: can this install
actually reply to a message?

Without this the launcher only checked whether the first five lines of
config.yaml still said ``provider: ""``.  Everything past that -- a provider
id the engine does not know, a key that never made it into .env, a gateway
with no api_server key -- looked exactly like a working install right up to
the point where the user typed a message and nothing came back.

Usage:  preflight.py <data-dir>
Exit 0   ready to launch
Exit 10  the user has to configure something; the reason is printed in Chinese
"""
import hashlib
import io
import os
import re
import sys
import time

# Pinned up front, like diagnose.py / first-login.py / db-maintenance.py.
# This was the one script of the four that took whatever the caller gave it,
# and importing the engine below reconfigures stdout to UTF-8 as a side
# effect -- so a single run could print its first half in the console's ANSI
# codepage and its second half in UTF-8. Every shipped caller does
# `chcp 65001` first, so UTF-8 is what they are all expecting to read; the
# --print-workspace bytes in particular are captured by a `for /f` in
# Windows-Start.bat, where a Chinese folder name in the wrong codepage means
# the agent silently does not chdir into it.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import provider_probe  # noqa: E402  (needs the path set above)

# Short on purpose: this sits between a double-click and the browser opening.
PROBE_TIMEOUT = 10
# One success is good for a week, or until the provider/model/key changes.
# A real call on every launch would be a request to a third party every time
# someone opens the program, for a question that almost never changes its
# answer.
PROBE_TTL = 7 * 24 * 3600

NEEDS_CONFIG = 10


def repair_env_bom(path):
    """Strip a leading BOM from .env, and say that it did.

    A UTF-8 BOM in front of the first line makes that line's variable name
    "﻿OPENAI_API_KEY". python-dotenv -- which is what the engine reads
    this file with -- keeps the BOM in the name too, so the key is not
    missing, it is unusable: nothing ever matches it and the agent never
    answers. Notepad and PowerShell's `Set-Content -Encoding utf8` both
    produce one.

    Repairing it here rather than only reporting it, because the damage is
    unambiguous, the fix is three bytes, and the alternative is a user
    retyping a key that was correct all along. Saving from the config page
    no longer reintroduces it either.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return False
    if not raw.startswith(bytes([0xEF, 0xBB, 0xBF])):
        return False
    try:
        with open(path, "wb") as f:
            f.write(raw[3:])
    except OSError:
        return False
    return True


def read_env_file(path):
    values = {}
    if not os.path.exists(path):
        return values
    # utf-8-sig: a BOM must never become part of the first variable's name.
    with io.open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("'").strip('"')
    return values


NO_YAML = "no-yaml"


def load_config(path):
    """Return ``(config, problem)``.

    ``problem`` is ``None`` on success, ``NO_YAML`` when this interpreter has
    no PyYAML, and otherwise a short Chinese description of what is wrong
    with the file.  The two used to collapse into one ``None``, so running
    preflight under a plain python on PATH told the user their config was
    corrupt and sent them to restore a backup of a file that was fine.
    """
    try:
        import yaml
    except ImportError:
        return None, NO_YAML
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return None, "格式有误（%s）" % e.__class__.__name__
    if data is None:
        return None, "文件是空的"
    if not isinstance(data, dict):
        return None, "最外层不是 key: value 的形式"
    return data, None


# Used when this runs outside the packaged engine (a plain python on PATH).
# Only the providers the config page can produce -- enough to check a key is
# present, not enough to call a provider unknown.
FALLBACK_KEY_VARS = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "kimi-coding": ("KIMI_API_KEY", "KIMI_CODING_API_KEY"),
    "kimi-coding-cn": ("KIMI_CN_API_KEY",),
    "alibaba": ("DASHSCOPE_API_KEY",),
    "zai": ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
    "minimax-cn": ("MINIMAX_CN_API_KEY",),
    # OPENROUTER_API_KEY only: the engine seeds this pool from that one
    # variable (agent/credential_pool.py), so accepting OPENAI_API_KEY here
    # would pass a config the engine then finds no credential for.
    "openrouter": ("OPENROUTER_API_KEY",),
}


def key_vars_for(provider):
    """(env var names to look for, what we actually know).

    The verdict matters more than the names.  An id this check does not
    recognise is NOT evidence that the config is broken -- the engine accepts
    aliases (glm, kimi, ollama, zhipu ...), the literal values "auto" and
    "custom", and a dozen providers whose credential lives in auth.json
    rather than in any environment variable.  Blocking those would turn a
    working install into one that can never launch, which is far worse than
    letting a genuinely broken one through to a real error message.

    Verdicts: "api_key"    -- a key in .env is what we should find
              "elsewhere"  -- credential lives in auth.json / the pool
              "unsure"     -- cannot tell; never block on this
    """
    if provider in ("auto", "custom") or provider.startswith("custom:"):
        return (), "elsewhere"
    if provider == "openrouter":
        return FALLBACK_KEY_VARS["openrouter"], "api_key"

    try:
        from hermes_cli.auth import PROVIDER_REGISTRY
    except ImportError:
        names = FALLBACK_KEY_VARS.get(provider, ())
        return names, "api_key" if names else "unsure"

    # Let the engine normalise aliases, exactly as it will at runtime.
    resolved = provider
    try:
        from hermes_cli.auth import resolve_provider
        resolved = resolve_provider(provider) or provider
    except Exception:
        pass

    pconfig = PROVIDER_REGISTRY.get(resolved) or PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        return (), "unsure"
    if getattr(pconfig, "auth_type", "") != "api_key" or not pconfig.api_key_env_vars:
        return tuple(pconfig.api_key_env_vars), "elsewhere"
    return tuple(pconfig.api_key_env_vars), "api_key"



def base_url_for(provider, config, env):
    """The address the engine will actually call, or "" if we cannot tell.

    Same shape as key_vars_for: ask the engine, fall back, never guess in a
    way that could produce a confident wrong answer.
    """
    if provider.startswith("custom:"):
        entry = provider_probe.find_custom_provider(config, provider)
        return provider_probe.custom_provider_base_url(entry) if entry else ""
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY
    except ImportError:
        return ""
    resolved = provider
    try:
        from hermes_cli.auth import resolve_provider
        resolved = resolve_provider(provider) or provider
    except Exception:
        pass
    pconfig = PROVIDER_REGISTRY.get(resolved) or PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        return ""
    # An override in .env wins, exactly as it will at runtime.
    var = getattr(pconfig, "base_url_env_var", "") or ""
    if var:
        override = (env.get(var) or os.environ.get(var) or "").strip()
        if override:
            return override
    return str(getattr(pconfig, "inference_base_url", "") or "")


def _probe_fingerprint(provider, model, key):
    raw = "|".join([provider, model, key]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _probe_marker(data_dir):
    return os.path.join(data_dir, ".probe-ok")


def probe_already_passed(data_dir, fingerprint):
    try:
        with io.open(_probe_marker(data_dir), encoding="utf-8") as f:
            stamp, seen = f.read().strip().split(None, 1)
    except (OSError, ValueError):
        return False
    if seen != fingerprint:
        return False
    try:
        return (time.time() - float(stamp)) < PROBE_TTL
    except ValueError:
        return False


def remember_probe_passed(data_dir, fingerprint):
    try:
        with io.open(_probe_marker(data_dir), "w", encoding="utf-8") as f:
            f.write("%d %s" % (int(time.time()), fingerprint))
    except OSError:
        pass  # a read-only stick is not a reason to fail a launch


def run_launch_probe(data_dir, provider, model, base_url, key):
    """Ask the provider one question before the user does.

    Everything above this point is static: the file parses, a provider is
    named, a key exists. None of that catches the case this exists for --
    the key is expired, the balance is zero, the model was renamed -- and
    the user finds out by typing a message into a chat window that never
    answers, with no error anywhere they would look.

    Returns an exit code. Only failures the config page can repair block a
    launch; a rate limit or a flat network prints its reason and gets out
    of the way.
    """
    if os.environ.get("U_HERMES_SKIP_PROBE"):
        return 0
    if not base_url or not key:
        return 0  # nothing to ask, or nowhere to ask it

    fingerprint = _probe_fingerprint(provider, model, key)
    if probe_already_passed(data_dir, fingerprint):
        return 0

    result = provider_probe.probe(base_url, key, model, timeout=PROBE_TIMEOUT)
    if result.ok:
        remember_probe_passed(data_dir, fingerprint)
        return 0

    if result.kind in provider_probe.FIXABLE_IN_CONFIG:
        say("模型服务商拒绝了这次调用，现在聊天也不会有回复：",
            "  " + result.message,
            "配置页马上打开，改完保存就行。")
        return NEEDS_CONFIG

    say("[!] 试着调用了一次模型，没有成功：",
        "  " + result.message,
        "    程序照常启动 —— 这类问题通常和配置无关。"
        "如果聊天确实没有回复，双击「出问题点我-诊断.bat」。")
    return 0


def say(*lines):
    for line in lines:
        print("   " + line)


def print_workspace(data_dir):
    """Print the configured workspace, or nothing.

    The CLI does not honour terminal.cwd: its config loader (cli.py up to
    0.21.3, hermes_cli/cli_config_load.py from 0.21.4) overwrites it with
    os.getcwd() whenever the backend is local, before anything reads the
    config. So the launcher has to chdir there itself, or the folder the
    user picked on the config page would apply to the Web UI and not to
    `hermes chat` -- one product with two working directories, and a
    settings box that shows only one of them.
    """
    config, _problem = load_config(os.path.join(data_dir, "config.yaml"))
    terminal = (config or {}).get("terminal")
    if not isinstance(terminal, dict):
        return 0
    if str(terminal.get("backend") or "local") != "local":
        return 0
    cwd = str(terminal.get("cwd") or "").strip()
    if cwd and cwd not in (".", "./", ".\\", "auto", "cwd"):
        sys.stdout.write(cwd)
    return 0


def warn_on_engine_drift():
    """Say so when this install is not the one versions.env pins.

    Was engine-only, which turned out to be a fifth of the problem: on the
    machine this was rewritten on, all five pinned components had drifted
    and nothing said so. The name is kept because the launcher calls it.
    """
    try:
        import version_check
    except ImportError:
        return
    try:
        version_check.report(version_check.survey(), say=say)
    except Exception:
        return  # a version check is never a reason to fail a launch

def main(argv):
    if "--print-workspace" in argv:
        rest = [a for a in argv[1:] if a != "--print-workspace"]
        return print_workspace(rest[0] if rest else "data")

    warn_on_engine_drift()

    data_dir = argv[1] if len(argv) > 1 else "data"
    config_path = os.path.join(data_dir, "config.yaml")

    if not os.path.exists(config_path):
        say("还没有配置文件，需要先选择 AI 模型。")
        return NEEDS_CONFIG

    config, problem = load_config(config_path)
    if problem == NO_YAML:
        # Nothing can be checked, so claim nothing -- and above all do not
        # block a launch over it. The launcher always uses the packaged
        # interpreter; reaching here means someone ran preflight by hand.
        say("[i] 这个 Python 没有 PyYAML，跳过配置检查。",
            "    用启动器运行（Windows-Start.bat）才会用到打包好的解释器。")
        return 0
    if config is None:
        say("config.yaml 读不出来：%s。" % problem,
            "最近一次保存前的备份在 data\\backups\\ 里，可以复制回来。")
        return NEEDS_CONFIG

    model_cfg = config.get("model")
    if isinstance(model_cfg, str):
        # A bare string is a valid shape: the engine takes it as the model
        # name and resolves the provider itself. Nothing for us to check.
        model_cfg = {"default": model_cfg, "provider": "auto"}
    if not isinstance(model_cfg, dict):
        model_cfg = {}

    provider = str(model_cfg.get("provider") or "").strip()
    model = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()

    if not provider:
        say("还没有选择 AI 模型。")
        return NEEDS_CONFIG
    if not model:
        say("选了模型服务商（%s），但没有填模型名称。" % provider)
        return NEEDS_CONFIG

    # Which variable should hold the key -- or, for a custom provider, the
    # key itself, which may be written straight into config.yaml.
    inline_key = ""
    entry = None
    if provider.startswith("custom:"):
        slug = provider.split(":", 1)[1]
        entry = provider_probe.find_custom_provider(config, provider)
        if entry is None:
            say("配置里写着自定义服务商 %s，但 custom_providers 里没有它的条目。" % slug,
                "请重新打开配置页面保存一次。")
            return NEEDS_CONFIG
        inline_key = provider_probe.custom_provider_inline_key(entry)
    else:
        candidates, verdict = key_vars_for(provider)
        if verdict != "api_key":
            # Either the credential lives somewhere this check cannot read
            # (auth.json, the credential pool, an OAuth login), or we simply
            # do not recognise the id. Neither is grounds for refusing to
            # start -- a wrong block costs the user their whole install,
            # while letting it through costs them one clear error message.
            return 0

    env_path = os.path.join(data_dir, ".env")
    if repair_env_bom(env_path):
        say("[i] data\\.env 开头有一个 BOM 字符，引擎会因此读不到第一个变量。已经去掉了。")
    env = read_env_file(env_path)

    no_key_hint = ()
    if entry is not None:
        # _resolve_named_custom_runtime's order after the inline key: the
        # variable the entry names, then only what the engine's host gate
        # hands this address -- not whatever OPENAI_API_KEY is lying around,
        # which the gate keeps away from third-party hosts.
        key_var = provider_probe.custom_provider_key_var(entry)
        base_url = provider_probe.custom_provider_base_url(entry)
        fallbacks = provider_probe.custom_key_fallback_vars(base_url, env)
        candidates = tuple(n for n in (key_var,) + fallbacks if n)
        if not inline_key and not key_var:
            # Not a refusal on its own: a variable the gate hands this
            # address counts, so such an entry can run. Only a hint, and only
            # printed if nothing turns one up below -- the old code stopped
            # here and called the entry missing, which it is not.
            lead = "custom_providers 里的 %s 既没写 api_key，也没写 key_env，" % slug
            if fallbacks:
                no_key_hint = ("（" + lead + "引擎按它的地址只会去读 %s。）"
                               % "、".join(fallbacks),)
            elif base_url:
                no_key_hint = (
                    lead + "而引擎不会为 %s 这个地址去读任何环境变量。" % base_url,
                    "在条目里写上 api_key，或者写 key_env 并把密钥放进 data\\.env 的那个变量。")
            else:
                no_key_hint = (lead + "连 base_url 也没写。",
                               "请重新打开配置页面保存一次。")

    found = ""
    for name in candidates:
        value = env.get(name) or os.environ.get(name) or ""
        if value.strip():
            found = name
            break

    if not found and not inline_key:
        # candidates is () for a custom entry with no key_env at an address
        # the engine reads no variable for (an IP, localhost, a single-label
        # host), and for any provider key_vars_for does not recognise -- the
        # branch above keeps those from reaching here. Indexing it blind
        # would raise IndexError into the catch-all at the bottom of this
        # file, which turns any bug here into a silent "check skipped" -- a
        # launch gate failing open without saying so.
        if candidates:
            where = ("密钥应该写在 data\\.env 的 %s 里。" % candidates[0],) + no_key_hint
        else:
            where = no_key_hint or ("密钥应该写在 data\\.env 对应的环境变量里。",)
        auth_json = os.path.join(data_dir, "auth.json")
        has_stored_login = os.path.exists(auth_json) and os.path.getsize(auth_json) > 2
        if not has_stored_login:
            say("已经选好 %s / %s，但没有找到 API 密钥。" % (provider, model), *where)
            return NEEDS_CONFIG

    # Everything above is static. This is the only check that finds out
    # whether the thing will actually answer.
    key_value = (env.get(found) or os.environ.get(found) or "") if found else ""
    probe_code = run_launch_probe(
        data_dir, provider, model,
        base_url_for(provider, config, env),
        inline_key or key_value)
    if probe_code != 0:
        return probe_code

    # Not fatal: the launcher generates this before the gateway starts.
    #
    # The engine reads platforms.api_server.extra.key -- PlatformConfig has a
    # fixed set of typed fields and sweeps everything else into `extra`, and
    # the api_server platform looks the key up there. A bare `key:` at the top
    # of the block is the older spelling and still resolves, so accept both;
    # checking only the bare one made this line announce "no gateway key yet"
    # on every launch of an install that had one.
    gateway_key = ""
    platforms = config.get("platforms")
    if isinstance(platforms, dict):
        api_server = platforms.get("api_server")
        if isinstance(api_server, dict):
            extra = api_server.get("extra")
            if isinstance(extra, dict):
                gateway_key = str(extra.get("key") or "")
            if not gateway_key:
                gateway_key = str(api_server.get("key") or "")
    if len(gateway_key) < 32:
        say("[i] 网关还没有密钥，启动时会自动生成一个。")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never block a launch on a failing check
        print("   [i] 启动自检跳过（%s）" % exc.__class__.__name__)
        sys.exit(0)

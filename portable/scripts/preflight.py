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
import io
import os
import re
import sys

NEEDS_CONFIG = 10


def read_env_file(path):
    values = {}
    if not os.path.exists(path):
        return values
    with io.open(path, encoding="utf-8", errors="replace") as f:
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


def say(*lines):
    for line in lines:
        print("   " + line)


def print_workspace(data_dir):
    """Print the configured workspace, or nothing.

    The CLI does not honour terminal.cwd: cli.py overwrites it with
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
    """Say so when the installed engine is not the one the release pins.

    There was nothing anywhere that told a developer this. setup.ps1 cloned
    hermes-agent from main while release.yml cloned HERMES_AGENT_REF, so the
    local engine sat four months behind the shipped one and every conclusion
    drawn by reading it described a product nobody receives. Cheap to check,
    and silent whenever it cannot tell.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    portable = os.path.dirname(here)
    pin = ""
    try:
        with io.open(os.path.join(portable, "versions.env"), encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("HERMES_AGENT_REF"):
                    pin = line.split("=", 1)[1].strip()
                    break
    except OSError:
        return
    if not pin:
        return
    agent_dir = os.path.join(portable, "hermes", "hermes-agent")
    if not os.path.isdir(os.path.join(agent_dir, ".git")):
        return  # a release zip may not carry .git; nothing to compare
    try:
        import subprocess
        actual = subprocess.run(
            ["git", "-C", agent_dir, "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return
    if actual and actual != pin:
        say("[!] 本机装的 AI 引擎是 %s，而发布版本钉的是 %s。" % (actual, pin),
            "    两者行为可能不同，本地测出来的结论不代表用户拿到的版本。",
            "    重新装成钉住的版本：portable\\setup.ps1 -Force")


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

    # Which variable should hold the key.
    if provider.startswith("custom:"):
        slug = provider.split(":", 1)[1]
        key_var = ""
        for entry in config.get("custom_providers") or []:
            if isinstance(entry, dict) and str(entry.get("name", "")) == slug:
                key_var = str(entry.get("key_env") or "")
                break
        if not key_var:
            say("配置里写着自定义服务商 %s，但 custom_providers 里没有它的条目。" % slug,
                "请重新打开配置页面保存一次。")
            return NEEDS_CONFIG
        candidates = (key_var,)
    else:
        candidates, verdict = key_vars_for(provider)
        if verdict != "api_key":
            # Either the credential lives somewhere this check cannot read
            # (auth.json, the credential pool, an OAuth login), or we simply
            # do not recognise the id. Neither is grounds for refusing to
            # start -- a wrong block costs the user their whole install,
            # while letting it through costs them one clear error message.
            return 0

    env = read_env_file(os.path.join(data_dir, ".env"))
    found = ""
    for name in candidates:
        value = env.get(name) or os.environ.get(name) or ""
        if value.strip():
            found = name
            break

    if not found:
        # key_vars_for returns () for anything it does not recognise, and the
        # branch above is what keeps those from reaching here. Indexing it
        # blind would raise IndexError into the catch-all at the bottom of
        # this file, which turns any bug here into a silent "check skipped"
        # -- a launch gate failing open without saying so.
        where = candidates[0] if candidates else "对应的环境变量"
        auth_json = os.path.join(data_dir, "auth.json")
        has_stored_login = os.path.exists(auth_json) and os.path.getsize(auth_json) > 2
        if not has_stored_login:
            say("已经选好 %s / %s，但没有找到 API 密钥。" % (provider, model),
                "密钥应该写在 data\\.env 的 %s 里。" % where)
            return NEEDS_CONFIG

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

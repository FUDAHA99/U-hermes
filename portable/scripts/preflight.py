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


def load_config(path):
    try:
        import yaml
    except ImportError:
        return None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            data = yaml.safe_load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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


def main(argv):
    data_dir = argv[1] if len(argv) > 1 else "data"
    config_path = os.path.join(data_dir, "config.yaml")

    if not os.path.exists(config_path):
        say("还没有配置文件，需要先选择 AI 模型。")
        return NEEDS_CONFIG

    config = load_config(config_path)
    if config is None:
        say("config.yaml 读不出来（格式有误）。",
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
        auth_json = os.path.join(data_dir, "auth.json")
        has_stored_login = os.path.exists(auth_json) and os.path.getsize(auth_json) > 2
        if not has_stored_login:
            say("已经选好 %s / %s，但没有找到 API 密钥。" % (provider, model),
                "密钥应该写在 data\\.env 的 %s 里。" % candidates[0])
            return NEEDS_CONFIG

    # Not fatal: the launcher generates this before the gateway starts.
    gateway_key = ""
    platforms = config.get("platforms")
    if isinstance(platforms, dict):
        api_server = platforms.get("api_server")
        if isinstance(api_server, dict):
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

"""One request to the model provider, and one table of what each failure means.

The config page's 测试连接 button and 一键诊断 each carried their own copy of
this: the same request, the same HTTP mapping, the same Chinese strings --
drifting independently. Neither handled HTTP 400, and both threw away the one
thing that actually explains a failure: the provider's own error message.

"请求被拒绝（HTTP 400）" tells a user nothing. "模型不存在：deepseek-chat-v9"
tells them everything, and it is sitting in the response body both copies read
and discarded.

`kind` is what lets a caller decide. A wrong key or a wrong model name will
never fix itself, so the launcher sends the user to the config page. A rate
limit or a flaky network will, so it prints the reason and gets out of the way.
"""
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 20

# What went wrong, in terms a caller can branch on.
OK = "ok"
AUTH = "auth"                # the key is wrong, revoked, or not entitled
CREDIT = "credit"            # the key is fine, the account is empty
NOT_FOUND = "not_found"      # wrong address or wrong model name
BAD_REQUEST = "bad_request"  # the provider rejected the request shape
RATE = "rate"                # too many requests right now
SERVER = "server"            # the provider is having a bad day
NETWORK = "network"          # we never reached them
UNKNOWN = "unknown"

# Failures the config page can actually fix. Everything else is either
# temporary or needs a credit card, and blocking a launch over those costs
# more than it saves.
FIXABLE_IN_CONFIG = (AUTH, NOT_FOUND, BAD_REQUEST)


class Result(object):
    __slots__ = ("ok", "kind", "message", "http_code")

    def __init__(self, ok, kind, message, http_code=0):
        self.ok = ok
        self.kind = kind
        self.message = message
        self.http_code = http_code

    def __repr__(self):
        return "Result(ok=%r, kind=%r, message=%r)" % (
            self.ok, self.kind, self.message)


def is_anthropic_surface(base_url):
    """MiniMax's /anthropic and Kimi's /coding speak Anthropic Messages.

    Probing those with /chat/completions returns a 404, which reads to the
    user as "you typed the model name wrong".
    """
    url = (base_url or "").rstrip("/")
    return (
        "/anthropic" in url
        or url.endswith("/coding")
        or "api.anthropic.com" in url
    )


def build_request(base_url, api_key, model):
    """The smallest call that proves the whole chain works."""
    url = (base_url or "").rstrip("/")
    if is_anthropic_surface(url):
        return (
            url + "/v1/messages",
            json.dumps({
                "model": model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}],
            }).encode("utf-8"),
            {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            "content",
        )
    return (
        url if url.endswith("/chat/completions") else url + "/chat/completions",
        json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }).encode("utf-8"),
        {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (api_key or ""),
        },
        "choices",
    )


def redact(text, api_key=""):
    """Never echo the key back out of an error body.

    Some providers quote the offending Authorization header straight back.
    This text goes to a console, a web page and data/logs, so the key must
    not travel with it.
    """
    if not text:
        return ""
    out = str(text)
    if api_key and len(api_key) >= 8:
        out = out.replace(api_key, "***")
        # Some providers echo a trimmed or lower-cased form.
        out = out.replace(api_key.strip(), "***")
    parts = []
    for chunk in out.split():
        # sk-..., sk-ant-..., and the long opaque tokens most providers use.
        core = chunk.strip("\"',.;:()[]{}")
        if len(core) >= 24 and ("-" in core or core.isalnum()) and (
                core.startswith("sk-") or core.startswith("Bearer")):
            parts.append("***")
        else:
            parts.append(chunk)
    return " ".join(parts)


def provider_detail(body, api_key="", limit=200):
    """The provider's own words about what it refused, if it said anything."""
    if not body:
        return ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    text = body.strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    found = ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            found = err.get("message") or err.get("msg") or err.get("type") or ""
        elif isinstance(err, str):
            found = err
        if not found:
            found = (data.get("message") or data.get("msg")
                     or data.get("detail") or "")
        if not found and isinstance(data.get("base_resp"), dict):
            # MiniMax puts it here.
            found = data["base_resp"].get("status_msg") or ""
    if not found:
        found = text
    found = redact(" ".join(str(found).split()), api_key)
    if len(found) > limit:
        found = found[:limit] + "…"
    return found


def _with_detail(base, detail):
    return base + ("　服务商原话：" + detail if detail else "")


def probe(base_url, api_key, model, timeout=DEFAULT_TIMEOUT):
    """Make the call. Never raises; every outcome is a Result."""
    req_url, payload, headers, ok_field = build_request(base_url, api_key, model)
    req = urllib.request.Request(
        req_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get(ok_field):
            return Result(True, OK, "连接成功，模型响应正常。")
        return Result(False, BAD_REQUEST, _with_detail(
            "服务已连通，但返回的内容不是一条正常回复，多半是模型名称不对。",
            provider_detail(raw, api_key)))
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        detail = provider_detail(body, api_key)
        if e.code in (401, 403):
            return Result(False, AUTH, _with_detail(
                "API 密钥无效或没有权限（HTTP %d），请重新填一次密钥。" % e.code,
                detail), e.code)
        if e.code == 402:
            # The most common "配好了却不回话": the key is fine, the account
            # is empty. Worth its own branch so nobody re-checks the key.
            return Result(False, CREDIT, _with_detail(
                "账户余额不足（HTTP 402）。密钥本身是好的，去服务商官网充值即可。",
                detail), e.code)
        if e.code == 404:
            return Result(False, NOT_FOUND, _with_detail(
                "接口地址或模型名称不存在（HTTP 404）。", detail), e.code)
        if e.code == 400:
            # Neither copy of this table had a 400 branch, so the provider's
            # explanation -- an unsupported parameter, a model that needs a
            # different field, a malformed name -- was replaced by
            # "请求被拒绝（HTTP 400），请检查配置".
            return Result(False, BAD_REQUEST, _with_detail(
                "服务商看不懂这个请求（HTTP 400），通常是模型名称写错，"
                "或这个模型不接受这样的参数。", detail), e.code)
        if e.code == 429:
            return Result(False, RATE, _with_detail(
                "请求太频繁，或额度已经用完（HTTP 429）。", detail), e.code)
        if e.code >= 500:
            return Result(False, SERVER, _with_detail(
                "服务商自己出错了（HTTP %d），过一会儿再试。" % e.code,
                detail), e.code)
        return Result(False, UNKNOWN, _with_detail(
            "请求被拒绝（HTTP %d）。" % e.code, detail), e.code)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, socket.gaierror):
            return Result(False, NETWORK, "解析不了这个域名，请检查 API 地址。")
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return Result(False, NETWORK, "连接超时，请检查网络或换个 API 地址。")
        if isinstance(reason, ConnectionRefusedError):
            return Result(False, NETWORK, "连接被拒绝，请确认 API 地址和端口。")
        if isinstance(reason, ssl.SSLError):
            return Result(False, NETWORK, "SSL 证书有问题，请确认地址是有效的 https。")
        return Result(False, NETWORK, "网络连不上，请检查网络后重试。")
    except (socket.timeout, TimeoutError):
        return Result(False, NETWORK, "连接超时，请检查网络或换个 API 地址。")
    except OSError as e:
        # ConnectionResetError, ConnectionAbortedError and friends arrive
        # here rather than wrapped in a URLError. They were landing in the
        # catch-all below and being reported as "测试失败：ConnectionResetError"
        # -- classified UNKNOWN, which is not something the config page can
        # fix, but also not something the user was told was a network issue.
        # A hijacking DNS resolver produces exactly this.
        return Result(False, NETWORK,
                      "网络中断（%s），请检查网络、代理或防火墙后重试。"
                      % e.__class__.__name__)
    except Exception as e:
        return Result(False, UNKNOWN, "测试失败：%s" % e.__class__.__name__)


# --------------------------------------------------------------------------
# Which entry does custom:<name> mean, and where is its key?
#
# preflight and diagnose each had their own answer and both were stricter
# than the engine, in the same two ways: they compared the name with == when
# hermes_cli.providers lowercases it and hyphenates its spaces, and preflight
# accepted only key_env when an entry may carry the key inline. Between them
# a provider named "LongCat" could neither launch nor be diagnosed, while
# the engine ran it. One answer, next to the request it feeds.
# --------------------------------------------------------------------------

# The three spellings the engine reads an address under, in its order:
# _normalize_custom_provider_entry tries base_url, then url, then api.
def custom_provider_base_url(entry):
    for field in ("base_url", "url", "api"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# A custom_providers entry may name an environment variable to read the key
# from, or carry the key inline. The engine accepts both, plus the aliases
# _normalize_custom_provider_entry folds onto them; refusing either half is
# refusing a config that runs.
KEY_VAR_FIELDS = ("key_env", "api_key_env", "keyEnv", "apiKeyEnv")
INLINE_KEY_FIELDS = ("api_key", "apiKey")


def _first_field(entry, fields):
    for field in fields:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def custom_provider_slug(display_name):
    """The engine's name-to-id rule, from hermes_cli.providers.

    custom_provider_slug lowercases the display name and hyphenates its
    spaces; resolve_custom_provider then accepts either that slug or the
    plain lowercased name.
    """
    return str(display_name or "").strip().lower().replace(" ", "-")


def find_custom_provider(config, provider):
    """The custom_providers entry the engine will resolve, or None.

    This was == against the raw entry name, which is stricter than the
    engine in the one direction that costs the user their whole install:
    an entry named "LongCat" or "My Server" -- the ordinary way a
    person names one -- did not match custom:longcat or custom:my-server,
    so the launcher refused to start a config the engine runs fine and
    reported that the entry was not there.

    Entries with no address are skipped the way resolve_custom_provider
    skips them, but kept as a fallback, so a caller can still tell
    "not there" from "there but unusable" and say whichever is true.
    """
    if not isinstance(config, dict) or not provider.startswith("custom:"):
        # preflight swallows exceptions into a silent pass, so a shared
        # helper handing it an AttributeError would turn the launch gate
        # off without saying so.
        return None
    wanted = provider.split(":", 1)[1].strip().lower()
    if not wanted:
        return None
    fallback = None
    for entry in config.get("custom_providers") or []:
        if not isinstance(entry, dict):
            continue
        display = str(entry.get("name") or "").strip()
        if not display:
            continue
        if wanted not in (display.lower(), custom_provider_slug(display)):
            continue
        if custom_provider_base_url(entry):
            return entry
        if fallback is None:
            fallback = entry
    return fallback

# When an entry has no key of its own, the engine does NOT reach for whatever
# key happens to be in .env. _resolve_named_custom_runtime
# (hermes_cli/runtime_provider_custom.py) tries the inline key, then key_env,
# then runtime_provider._host_gated_env_key_candidates(base_url) -- and that
# gate hands out OPENAI_API_KEY and OPENROUTER_API_KEY only at their own hosts,
# because sending them anywhere else leaks them (GHSA-76xc-57q6-vm5m). Any
# other address gets one variable named after its host: api.longcat.example
# reads LONGCAT_API_KEY. With nothing set, the engine sends the placeholder
# "no-key-required".
#
# This used to be a blanket ("OPENAI_API_KEY", "OPENROUTER_API_KEY") for every
# address, which on a LongCat entry with no key of its own was wrong three
# ways: with only OPENAI_API_KEY set we probed with it and let the launch
# through, then every chat 401'd; with only OPENROUTER_API_KEY set we sent the
# OpenRouter key to LongCat and blocked the launch on its 401; with only
# LONGCAT_API_KEY set -- which the engine uses -- we blocked, saying no key.

# hermes-agent 0.21.4 (v2026.9.21) added one pairing: OPENAI_API_KEY also goes
# to the exact address OPENAI_BASE_URL names -- the pair Config.html writes
# for every custom provider. 0.21.3 does not. Which rule applies is
# decided by the engine installed next to these scripts, not by the pin: the
# scripts get synced onto installs whose engine is older or newer than that.
OPENAI_BASE_URL_PAIRING_SINCE = (0, 21, 4)


def parse_engine_version(raw):
    """"0.21.4" (or "0.22.0rc1") -> (0, 21, 4); None when it does not parse."""
    match = re.match(r"\s*(\d+)\.(\d+)(?:\.(\d+))?", str(raw or ""))
    return tuple(int(part or 0) for part in match.groups()) if match else None


def installed_engine_version():
    """The importable engine's version, or None when there is none.

    hermes_cli.__version__ rather than package metadata: it is the code that
    will actually run, and the package itself is a sub-millisecond import
    where a metadata lookup takes 60 ms.
    """
    try:
        import hermes_cli
    except Exception:
        return None
    return parse_engine_version(getattr(hermes_cli, "__version__", ""))


def _url_hostname(base_url):
    """utils.base_url_hostname: lowercased host, tolerating a bare host[:port]/path."""
    raw = (base_url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw if "://" in raw else "//" + raw)
        return (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""  # an unclosed [IPv6 bracket, say


def _host_matches(base_url, domain):
    """utils.base_url_host_matches: the host or a subdomain of it, never a substring.

    api.openai.com.evil.test and proxy.test/api.openai.com are not OpenAI.
    """
    hostname = _url_hostname(base_url)
    return bool(hostname) and (hostname == domain or hostname.endswith("." + domain))


def host_derived_key_var(base_url):
    """runtime_provider._host_derived_api_key, returning the name, not the value.

    The vendor is the second-to-last label once leading api./www. are dropped,
    so a lookalike (api.deepseek.com.evil.test) reads EVIL_API_KEY, never
    DEEPSEEK_API_KEY. "" for IPs, localhost and IPv6 literals, single-label
    hosts, labels starting with a digit, and OPENAI/OPENROUTER/OLLAMA, which
    have gates of their own.
    """
    hostname = _url_hostname(base_url)
    if (not hostname or any(ch.isdigit() for ch in hostname.split(".")[-1])
            or hostname == "localhost" or ":" in hostname):
        return ""
    labels = [label for label in hostname.split(".") if label]
    while labels and labels[0] in ("api", "www"):
        labels.pop(0)
    if len(labels) < 2:
        return ""
    vendor = "".join(ch if ch.isalnum() else "_" for ch in labels[-2]).upper()
    if not vendor or not vendor[0].isalpha() or vendor in ("OPENAI", "OPENROUTER", "OLLAMA"):
        return ""
    return vendor + "_API_KEY"


def _env_value(env, name):
    return ((env or {}).get(name) or os.environ.get(name) or "").strip()


def custom_key_fallback_vars(base_url, env=None, engine_version=None):
    """The variables the engine reads for an entry with no key of its own.

    _host_gated_env_key_candidates(base_url, ollama=False), in its order,
    as names. env matters only for OPENAI_BASE_URL. engine_version defaults
    to the installed engine's; with none importable, the newest rule known.
    """
    if engine_version is None:
        engine_version = installed_engine_version()
    is_openai = (_host_matches(base_url, "openai.com")
                 or _host_matches(base_url, "openai.azure.com"))
    if not is_openai and (engine_version is None
                          or engine_version >= OPENAI_BASE_URL_PAIRING_SINCE):
        paired = _env_value(env, "OPENAI_BASE_URL").rstrip("/")
        is_openai = bool(paired) and (base_url or "").strip().rstrip("/") == paired
    names = []
    if is_openai:
        names.append("OPENAI_API_KEY")
    if _host_matches(base_url, "openrouter.ai"):
        names.append("OPENROUTER_API_KEY")
    derived = host_derived_key_var(base_url)
    if derived:
        names.append(derived)
    return tuple(names)


def custom_provider_key_var(entry):
    """The environment variable this entry says holds its key, or "".

    key_env is the canonical spelling; the other three are aliases
    _normalize_custom_provider_entry folds onto it, in its order.
    """
    return _first_field(entry, KEY_VAR_FIELDS)


def custom_provider_inline_key(entry):
    """The key written straight into config.yaml, or "".

    Perfectly ordinary -- the engine takes api_key as a literal. A checker
    that demands key_env instead calls such a config broken.
    """
    return _first_field(entry, INLINE_KEY_FIELDS)


def custom_provider_credential(entry, env=None, engine_version=None):
    """(address, key) for an entry: the key the engine will send, or "".

    env is a mapping of what data/.env holds; the process environment is
    the fallback, exactly as at runtime. "" is where the engine sends its
    no-key-required placeholder.
    """
    env = env or {}
    base_url = custom_provider_base_url(entry)
    # _resolve_named_custom_runtime's order: the inline key first, then the
    # variable the entry names, then only what the host gate hands this address.
    key = custom_provider_inline_key(entry)
    if not key:
        names = ((custom_provider_key_var(entry),)
                 + custom_key_fallback_vars(base_url, env, engine_version))
        for var in names:
            if not var:
                continue
            key = _env_value(env, var)
            if key:
                break
    return base_url, key


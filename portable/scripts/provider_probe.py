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
import socket
import ssl
import urllib.error
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

# -*- coding: utf-8 -*-
"""
U-Hermes 一键诊断
检查运行环境、配置、服务端口、API 连通性和磁盘空间，
并把最近的错误日志翻译成人话和解决建议。
"""
import json
import os
import re
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.yaml")
ENV_FILE = os.path.join(DATA_DIR, ".env")
ERRORS_LOG = os.path.join(DATA_DIR, "logs", "errors.log")

OK = "[OK]"
BAD = "[X] "
WARN = "[!] "

# 错误日志分类表：正则 → (人话解释, 解决建议)
ERROR_CLASSES = [
    (r"(error_type=AuthenticationError|HTTP 401\b|Error code: 401|Invalid API Key)",
     "AI 服务不认可当前的 API 密钥，密钥可能填错、过期或被停用。",
     "去配置页检查并重新填写正确的 API 密钥，保存后再试一次。"),
    (r"(error_type=APITimeoutError|Request timed out)",
     "AI 服务太久没有回应，请求等待超时了。",
     "稍后重试；如果经常出现，检查网络是否顺畅，或换一个响应更快的模型。"),
    (r"(error_type=APIConnectionError|Connection error\.)",
     "电脑暂时连不上 AI 服务，一般是网络波动、断网或代理出问题。",
     "检查网络（包括代理/VPN）是否正常，网络恢复后程序会自动重试。"),
    (r"(Stream stale for \d+s|no chunks received|Stream drop|peer closed connection|RemoteProtocolError|Streaming failed after partial delivery)",
     "AI 回复过程中连接被中途掐断，或长时间收不到内容。",
     "程序会自动重连重试；频繁发生时请换个网络环境或切换模型。"),
    (r"(No inference provider configured|no provider available \(tried:|no_provider_configured)",
     "还没有配置任何 AI 模型和密钥，程序不知道该用哪个 AI 服务。",
     "打开配置页选择模型并填入 API 密钥后保存。"),
    (r"(error_type=RateLimitError|HTTP 429|Error code: 429|HTTP 402|Error code: 402|payment / credit error)",
     "AI 服务商提示账户余额/额度不足，或请求太频繁触发了限流。",
     "稍等几分钟再试，或到服务商官网充值，也可以切换备用模型。"),
    (r"(EADDRINUSE|\[Errno 10048\]|Port \d+ already in use|error while attempting to bind on address)",
     "程序需要的网络端口被占用了，通常是上次运行没有完全退出。",
     "关闭所有旧的 U-Hermes 窗口后重启启动器；仍不行就重启电脑。"),
    (r"(spawn EINVAL|Failed to canonicalize script path)",
     "便携版换电脑或 U 盘盘符变化后，程序里的旧路径失效了。",
     "重新运行启动器（Windows-Start.bat）让它自动修复路径。"),
    (r"(error_type=NotFoundError|HTTP 404|Error code: 404)",
     "所选的模型名称或接口地址在服务商那边找不到。",
     "去配置页核对模型名称和 API 地址是否正确。"),
]


def section(title):
    print()
    print("  ------ %s ------" % title)


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()
        except OSError:
            pass
    return env


def load_config():
    try:
        import yaml
        with open(CONFIG_FILE, encoding="utf-8", errors="replace") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None


def check_port(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def call_provider(base_url, api_key, model):
    """极简 chat/completions 调用，返回 (ok, 中文消息)。"""
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and data.get("choices"):
                return True, "连接成功，模型响应正常。"
            return False, "服务已连通，但返回内容异常，请核对模型名称。"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "API 密钥无效或无权限（HTTP %d），请更新密钥。" % e.code
        if e.code == 404:
            return False, "接口地址或模型名称不存在（HTTP 404）。"
        if e.code == 429:
            return False, "请求过于频繁或额度不足（HTTP 429）。"
        return False, "服务商返回错误（HTTP %d），请稍后重试。" % e.code
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, socket.gaierror):
            return False, "无法解析域名，请检查 API 地址。"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return False, "连接超时（20 秒无响应）。"
        if isinstance(reason, ssl.SSLError):
            return False, "SSL 证书错误，请确认地址为有效 https。"
        return False, "网络连接失败，请检查网络。"
    except (socket.timeout, TimeoutError):
        return False, "连接超时（20 秒无响应）。"
    except Exception as e:
        return False, "测试失败：%s" % e.__class__.__name__


def resolve_provider(cfg, ref, env):
    """把 custom:<name> 解析成 (base_url, api_key, 名称)，解析不了返回 None。"""
    if not ref.startswith("custom:"):
        return None
    name = ref.split(":", 1)[1]
    for entry in cfg.get("custom_providers") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            base_url = entry.get("base_url") or ""
            api_key = entry.get("api_key") or ""
            if not api_key:
                key_env = entry.get("key_env") or entry.get("api_key_env") or ""
                api_key = env.get(key_env) or os.environ.get(key_env, "")
            return base_url, api_key, name
    return None


def main():
    print()
    print("  ==============================================")
    print("    U-Hermes 一键诊断")
    print("  ==============================================")

    problems = []

    # 1. 运行环境
    section("运行环境")
    venv_python = os.path.join(ROOT, "hermes", ".venv", "Scripts", "python.exe")
    node_exe = os.path.join(ROOT, "runtime", "node-win-x64", "node.exe")
    for label, path in (("Python 运行时", venv_python), ("Node.js 运行时", node_exe)):
        if os.path.exists(path):
            print("  %s %s正常" % (OK, label))
        else:
            print("  %s %s缺失: %s" % (BAD, label, path))
            problems.append("%s缺失，请重新解压安装包或运行 setup.ps1。" % label)

    # 2. 配置检查
    section("模型配置")
    cfg = None
    if not os.path.exists(CONFIG_FILE):
        print("  %s 配置文件不存在（还没有完成首次配置）" % BAD)
        problems.append("请运行启动器完成首次配置。")
    else:
        cfg = load_config()
        if cfg is None:
            print("  %s 配置文件无法解析，可能已损坏" % BAD)
            problems.append("config.yaml 格式损坏，可从 data/config.yaml.providers.bak 恢复。")
        else:
            model_cfg = cfg.get("model") or {}
            provider = model_cfg.get("provider") or ""
            model_name = model_cfg.get("default") or model_cfg.get("model") or ""
            if provider:
                print("  %s 主模型: %s (%s)" % (OK, model_name or "未命名", provider))
            else:
                print("  %s 尚未配置 AI 模型" % BAD)
                problems.append("打开配置页选择模型并填入 API 密钥。")
            fallbacks = cfg.get("fallback_providers") or []
            if fallbacks:
                print("  %s 已配置 %d 个备用模型（主模型故障时自动切换）" % (OK, len(fallbacks)))
            else:
                print("  %s 未配置备用模型（主模型故障时无法自动切换）" % WARN)

    # 3. 服务状态
    section("服务状态")
    webui_up = check_port(8648)
    gateway_up = check_port(8642)
    print("  %s Web 界面 (端口 8648): %s" % (OK if webui_up else WARN, "运行中" if webui_up else "未运行"))
    print("  %s AI 引擎 (端口 8642): %s" % (OK if gateway_up else WARN, "运行中" if gateway_up else "未运行"))
    if not (webui_up and gateway_up):
        problems.append("服务未完全启动，请运行 Windows-Start.bat。")

    # 4. API 连通性
    section("API 连通性")
    env = load_env()
    if cfg:
        model_cfg = cfg.get("model") or {}
        provider = model_cfg.get("provider") or ""
        model_name = model_cfg.get("default") or model_cfg.get("model") or ""
        resolved = resolve_provider(cfg, provider, env)
        if resolved:
            base_url, api_key, name = resolved
            if base_url and api_key:
                ok, msg = call_provider(base_url, api_key, model_name)
                print("  %s 主模型 %s: %s" % (OK if ok else BAD, name, msg))
                if not ok:
                    problems.append("主模型连接异常：%s" % msg)
            else:
                print("  %s 主模型 %s 缺少地址或密钥" % (BAD, name))
                problems.append("主模型配置不完整，请重新配置。")
        elif provider:
            print("  %s 使用内置服务商 (%s)，跳过直连测试" % (WARN, provider))
        for fb in (cfg.get("fallback_providers") or [])[:1]:
            fb_ref = (fb or {}).get("provider") or ""
            fb_model = (fb or {}).get("model") or ""
            fb_resolved = resolve_provider(cfg, fb_ref, env)
            if fb_resolved and fb_resolved[0] and fb_resolved[1]:
                ok, msg = call_provider(fb_resolved[0], fb_resolved[1], fb_model)
                print("  %s 备用模型 %s: %s" % (OK if ok else WARN, fb_resolved[2], msg))
    else:
        print("  %s 无配置，跳过" % WARN)

    # 5. 磁盘空间
    section("磁盘空间")
    try:
        usage = shutil.disk_usage(ROOT)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1:
            print("  %s 剩余空间仅 %.1f GB，可能影响运行" % (WARN, free_gb))
            problems.append("磁盘空间不足 1GB，请清理后再使用。")
        else:
            print("  %s 剩余空间 %.1f GB" % (OK, free_gb))
    except OSError:
        print("  %s 无法读取磁盘信息" % WARN)

    # 6. 最近错误分析
    section("最近错误分析")
    if os.path.exists(ERRORS_LOG):
        try:
            size = os.path.getsize(ERRORS_LOG)
            with open(ERRORS_LOG, "rb") as f:
                f.seek(max(0, size - 64 * 1024))
                tail = f.read().decode("utf-8", "replace")
            lines = tail.splitlines()[-200:]
            found = []  # (最后出现行号, 次数, 解释, 建议)
            for pattern, meaning, advice in ERROR_CLASSES:
                rx = re.compile(pattern)
                hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
                if hits:
                    ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", lines[hits[-1]])
                    ts = ts_match.group(1) if ts_match else "时间未知"
                    found.append((hits[-1], len(hits), ts, meaning, advice))
            if found:
                found.sort(reverse=True)
                for _, count, ts, meaning, advice in found[:3]:
                    print("  %s %s（最近 %s，共 %d 次）" % (WARN, meaning, ts, count))
                    print("       建议: %s" % advice)
            else:
                print("  %s 最近日志中没有已知类型的错误" % OK)
        except OSError:
            print("  %s 无法读取错误日志" % WARN)
    else:
        print("  %s 暂无错误日志（这是好事）" % OK)

    # 总结
    print()
    print("  ==============================================")
    if problems:
        print("    发现 %d 个需要处理的问题:" % len(problems))
        for i, p in enumerate(problems, 1):
            print("    %d. %s" % (i, p))
    else:
        print("    一切正常！如仍有问题请重启启动器。")
    print("  ==============================================")
    print()


if __name__ == "__main__":
    main()

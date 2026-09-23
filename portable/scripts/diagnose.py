# -*- coding: utf-8 -*-
"""
U-Hermes 一键诊断
检查运行环境、配置、服务端口、API 连通性和磁盘空间，
并把最近的错误日志翻译成人话和解决建议。
"""
import datetime
import json
import os
import re
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.request

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import provider_probe  # noqa: E402  (needs the path set above)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.yaml")
ENV_FILE = os.path.join(DATA_DIR, ".env")
ERRORS_LOG = os.path.join(DATA_DIR, "logs", "errors.log")
STATE_DB = os.path.join(DATA_DIR, "state.db")

# 聊天记录超过这个大小就提示一句。U 盘常见 16-32 GB，几百 MB 的对话历史
# 已经值得让人知道是什么占的地方了。
STATE_DB_WARN_MB = 200

OK = "[OK]"
BAD = "[X] "
WARN = "[!] "

# 错误日志里多久以内算"最近"
RECENT_DAYS = 7


def line_time(line):
    """日志行的时间戳，认不出来返回 None。"""
    match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", line)
    if not match:
        return None
    try:
        return datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
    except ValueError:
        return None

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
    # hermes-agent 0.21.4 reworded this; older engines' logs still say the first.
    (r"(No inference provider configured|Hermes is not connected to any AI provider yet"
     r"|no provider available \(tried:|no_provider_configured)",
     "还没有配置任何 AI 模型和密钥，程序不知道该用哪个 AI 服务。",
     "打开配置页选择模型并填入 API 密钥后保存。"),
    # 0.21.4+: logged before the request goes out, naming the variable. Without
    # it the only trace was the provider's 401 a moment later.
    (r"key_env \S+ is set but the variable is empty/unset",
     "配置里写着从某个环境变量读取 API 密钥，但 data\\.env 里这个变量是空的（日志里写了变量名），"
     "请求只能带一个占位密钥发出去，服务商一定会拒绝。",
     "打开配置页，重新填写这个服务商的 API 密钥并保存。"),
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
    (r"(database\.journal_mode=delete is configured but the on-disk database is already WAL"
     r"|could not verify journal mode before applying configured journal_mode=delete)",
     "聊天记录数据库还停在 WAL 日志模式。配置里要求用 delete 模式，但引擎不会在运行中切换"
     "（有连接开着时切换会损坏数据库），所以每次启动都记一条。数据库本身读写正常。",
     "先彻底退出 U-Hermes（任务管理器里别留下 python.exe / node.exe）。如果每次启动还报，"
     "按「0-先看我-使用说明.txt」第八节最后一段做一次离线转换。转换完成之前，"
     "拔 U 盘一定要先「安全弹出」，否则 -wal 文件里没写回的对话会丢。"),
    (r"(Refusing to start: API_SERVER_KEY"
     r"|API server rejected invalid API key"
     r"|no profile-scoped API_SERVER_KEY is configured"
     r"|No API key configured \(API_SERVER_KEY)",
     "AI 引擎的本地接口密钥缺失、太短（少于 16 位）或和调用方对不上：引擎要么拒绝启动"
     "（8642 端口起不来），要么把每个请求挡回 401。",
     "重新运行 Windows-Start.bat，它会调用 scripts\\protect-config.ps1 在 data\\config.yaml 的 "
     "platforms.api_server.extra.key 下补一个强密钥。不要手工改这一项。"),
    # The same sentence prefix carries three different outcomes -- the engine
    # appends one of _WAL_RESET_BUG_ACTIONS to it -- and only the first means
    # "handled". Matching the prefix alone told the two populations that must
    # act that there was nothing to do.
    (r"vulnerable to the WAL-reset corruption bug.*"
     r"using journal_mode=DELETE instead of enabling WAL",
     "[这条不是故障] 引擎发现自带的 SQLite 版本有个已知缺陷，于是主动改用更保守的"
     "日志模式来避开它——这正是我们想要的行为，U 盘被拔掉时也更不容易丢数据。",
     "不用处理。等上游换成新版 SQLite 后这条会自己消失。"),
    # The other two: the database is STILL in WAL, on a SQLite build the
    # engine itself calls corruption-prone. This is the state 使用说明 第八节
    # exists for, and it is what an upgraded install hits -- its preserved
    # config.yaml has no database: block, so the delete-was-overridden entry
    # above never fires and this line is the only warning the user gets.
    (r"vulnerable to the WAL-reset corruption bug.*"
     r"(is already in WAL mode|journal mode could not be verified)",
     "数据库还在 WAL 日志模式，而自带的 SQLite 版本对这个模式有已知缺陷。引擎不敢在"
     "运行中切换（有连接开着时切换会损坏数据库），所以保持原样。这种状态下拔 U 盘"
     "丢数据的风险明显更高。",
     "照「0-先看我-使用说明.txt」第八节最后一段做一次离线转换（先备份整个 data 文件夹）。"
     "在转换完成之前，拔 U 盘务必先「安全弹出」。"),
]


def section(title):
    print()
    print("  ------ %s ------" % title)


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, encoding="utf-8-sig", errors="replace") as f:
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
    """极简调用，返回 (ok, 中文消息)。

    请求和 HTTP 映射表都在 provider_probe 里。这里原本是一份和
    config-server.py 几乎逐字相同的拷贝，两份各自演化：谁都没有 400 分支，
    也都把服务商自己写的报错内容丢掉了。
    """
    result = provider_probe.probe(base_url, api_key, model)
    return result.ok, result.message

def resolve_provider(cfg, ref, env):
    """把 custom:<name> 解析成 (base_url, api_key, 名称)，解析不了返回 None。

    匹配交给 provider_probe，跟引擎同一套规则。这里原先用 == 比条目名，比
    引擎严：引擎会把名字转小写、空格换连字符，于是一个叫 "LongCat" 的条目
    在这里根本找不到，诊断就报 "使用内置服务商" 然后跳过唯一有用的那项
    测试——而引擎跑这份配置毫无问题。
    """
    entry = provider_probe.find_custom_provider(cfg, ref)
    if entry is None:
        return None
    base_url, api_key = provider_probe.custom_provider_credential(entry, env)
    return base_url, api_key, str(entry.get("name") or "").strip()




def release_version():
    """The tag this package was cut from, or "" when running from a clone.

    Written into the zip by .github/workflows/release.yml. Deliberately
    absent from the repository: showing nothing beats showing a number that
    might be wrong.
    """
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""


def main():
    print()
    print("  ==============================================")
    print("    U-Hermes 一键诊断")
    print("  ==============================================")
    # This output is what people paste into a bug report, so it has to say
    # which build produced it.
    version = release_version()
    print("    版本: %s" % (version or "开发版（未打包）"))
    # Right under the version, because the first question about any bug
    # report is which build produced it -- and the second is whether that
    # build is the one we ship.
    try:
        import version_check
        for label, _k, pinned, installed, ok in version_check.survey():
            if ok is False:
                print("    [!] %s 装的是 %s，发布版钉的是 %s"
                      % (label, installed, pinned))
    except Exception:
        pass

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
        elif provider.startswith("custom:"):
            # Calling this a built-in provider was the opposite of true, and
            # it skipped the only check on this page that reaches the network.
            print("  %s 配置里写着 %s，但 custom_providers 里没有对应条目，无法测试"
                  % (BAD, provider))
            problems.append("自定义服务商 %s 在 custom_providers 里没有条目。" % provider)
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
    free_bytes = None
    try:
        usage = shutil.disk_usage(ROOT)
        free_bytes = usage.free
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1:
            print("  %s 剩余空间仅 %.1f GB，可能影响运行" % (WARN, free_gb))
            problems.append("磁盘空间不足 1GB，请清理后再使用。")
        else:
            print("  %s 剩余空间 %.1f GB" % (OK, free_gb))
    except OSError:
        print("  %s 无法读取磁盘信息" % WARN)

    # 聊天记录只增不减，产品里以前没有任何清理入口 —— U 盘被自己的历史
    # 记录塞满，而用户看不出是什么占的地方。
    #
    # 这里只看文件大小，不打开数据库：以只读方式连接一个 WAL 数据库，
    # SQLite 仍然会在 data\ 下创建 -wal / -shm，写保护的 U 盘上会直接失败。
    try:
        db_bytes = os.path.getsize(STATE_DB)
        for side in ("-wal", "-shm"):
            if os.path.exists(STATE_DB + side):
                db_bytes += os.path.getsize(STATE_DB + side)
        db_mb = db_bytes / (1024 * 1024)
        if db_mb < STATE_DB_WARN_MB:
            print("  %s 聊天记录 %.0f MB" % (OK, db_mb))
        else:
            print("  %s 聊天记录已占 %.0f MB（只增不减）" % (WARN, db_mb))
            print("       用 Windows-Menu.bat 的 [7] 清理聊天记录 可以释放。")
            # 整理时要临时占用和数据库差不多大的空间，剩余空间不够的话
            # 连清理都跑不动 —— 这种情况必须写进结论里。
            if free_bytes is not None and free_bytes < db_bytes:
                problems.append(
                    "聊天记录已 %.0f MB，而剩余空间不足以整理它。请先把 "
                    "data\\state.db 复制到别处备份，腾出至少 %.0f MB 再清理。"
                    % (db_mb, db_mb))
            elif db_mb >= 500:
                problems.append("聊天记录已 %.0f MB，建议用菜单 [7] 清理。" % db_mb)
    except OSError:
        pass

    # 6. 最近错误分析
    section("最近错误分析")
    if os.path.exists(ERRORS_LOG):
        try:
            size = os.path.getsize(ERRORS_LOG)
            with open(ERRORS_LOG, "rb") as f:
                f.seek(max(0, size - 64 * 1024))
                tail = f.read().decode("utf-8", "replace")
            lines = tail.splitlines()[-200:]

            # 只看最近这些天。以前没有这一步，几个月前的旧错误会被当成
            # "最近"报出来，读的人以为刚刚又出事了。
            cutoff = datetime.datetime.now() - datetime.timedelta(days=RECENT_DAYS)
            recent, stale = set(), 0
            for i, line in enumerate(lines):
                stamp = line_time(line)
                if stamp is None or stamp >= cutoff:
                    recent.add(i)  # 没有时间戳就无法判断新旧，保留
                else:
                    stale += 1

            found = []       # (最后出现行号, 次数, 时间, 解释, 建议)
            matched = set()
            for pattern, meaning, advice in ERROR_CLASSES:
                rx = re.compile(pattern)
                hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
                matched.update(hits)
                hits = [i for i in hits if i in recent]
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
                print("  %s 最近 %d 天没有已知类型的错误" % (OK, RECENT_DAYS))

            # 不认识的错误以前是直接丢掉的，于是日志里明明有几十行报错，
            # 诊断却说"一切正常"。认不出来也要让人看见原文。
            unknown = [i for i in sorted(recent) if i not in matched and lines[i].strip()]
            if unknown:
                print("  %s 另有 %d 行错误不属于已知类型，最近 3 条原文：" % (WARN, len(unknown)))
                for i in unknown[-3:]:
                    print("       %s" % lines[i].strip()[:160])
                print("       看不懂的话，把这几行连同 data\\logs\\errors.log")
                print("       发到 https://github.com/FUDAHA99/U-hermes/issues")
            if stale:
                print("  %s 另有 %d 行 %d 天前的旧错误，已忽略。" % (OK, stale, RECENT_DAYS))
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

"""
Lightweight config server for U-Hermes Config.html.
Starts before Config.html opens, serves save-config / save-env endpoints,
then shuts down after config is saved.

Saving MERGES into the existing config.yaml / .env.  The config page only
knows about the handful of settings it renders; everything else in the file
(database.journal_mode, platforms.api_server, terminal.cwd, fallback
providers, toolsets, the user's own comments) belongs to the user and must
survive a save.  Earlier versions truncated the file, which wiped the
gateway's api_server block and left the gateway unable to start.
"""
import datetime
import glob
import http.server
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# pythonw 下 stdout/stderr 为 None，print() 会崩溃，重定向到空设备
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

PORT = 18790
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PORTABLE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PORTABLE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
CONFIG_PAGE = os.path.join(PORTABLE_DIR, "Config.html")
KEEP_BACKUPS = 10

# This server hands out the user's API key on /test and rewrites their config
# on /save-config, so it has to know the request came from its own page.
#
# It therefore SERVES Config.html rather than letting the launcher open it
# from disk.  A file:// page sends "Origin: null" -- and so does a sandboxed
# iframe on any site the user happens to have open, which is not a
# distinction the server can make.  Serving the page makes its origin
# http://127.0.0.1:18790, which nothing else can forge, and the one-run token
# below means even a same-origin mistake is not enough on its own.
ALLOWED_ORIGINS = {
    "http://127.0.0.1:%d" % PORT,
    "http://localhost:%d" % PORT,
}
TOKEN = secrets.token_urlsafe(24)


# --------------------------------------------------------------------------
# YAML merge
#
# A real YAML round-trip would drop every comment in the file, so this walks
# the text instead: it splits a mapping body into per-key chunks, replaces
# only the keys the page actually sent, and leaves every other byte alone.
# --------------------------------------------------------------------------

_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_.-]*)\s*:(.*)$")


def _parse_blocks(lines, indent):
    """Split a mapping body at `indent` into ordered [key|None, lines] chunks.

    Comment lines directly above a key belong to that key -- replacing a
    setting should replace the comment explaining it.  The one exception is a
    comment block at the very top of the file, which is the file's header and
    outlives any single setting.
    """
    chunks = []
    pending = []
    cur = None
    at_top = indent == 0

    def close():
        nonlocal cur
        if cur is not None:
            chunks.append(cur)
            cur = None

    def drain():
        nonlocal pending
        if pending:
            chunks.append([None, pending])
            pending = []

    for line in lines:
        stripped = line.strip()
        m = _KEY_RE.match(line)
        if m and len(m.group(1)) == indent:
            close()
            if at_top:
                drain()
            cur = [m.group(2), pending + [line]]
            pending = []
            at_top = False
            continue
        if stripped.startswith("#"):
            pending.append(line)
            continue
        if stripped:
            at_top = False
        if pending:
            if cur is not None:
                cur[1].extend(pending)
                pending = []
            else:
                drain()
        if cur is not None:
            cur[1].append(line)
        else:
            chunks.append([None, [line]])
    close()
    drain()
    return chunks


def _key_line_index(chunk_lines):
    for i, line in enumerate(chunk_lines):
        if not line.strip().startswith("#") and _KEY_RE.match(line):
            return i
    return 0


def _body(chunk_lines):
    """The lines under a key line, the indent they sit at, and their kind.

    Kind is "map", "list" or "scalar".  An inline value (``key: value``) is
    always a scalar.
    """
    i = _key_line_index(chunk_lines)
    m = _KEY_RE.match(chunk_lines[i])
    if m and m.group(3).strip() and not m.group(3).strip().startswith("#"):
        return [], -1, "scalar"
    body = chunk_lines[i + 1:]
    for line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        child_indent = len(line) - len(line.lstrip())
        if stripped == "-" or stripped.startswith("- "):
            return body, child_indent, "list"
        if _KEY_RE.match(line):
            return body, child_indent, "map"
        return body, child_indent, "scalar"
    return body, -1, "scalar"


def _split_list_items(body_lines, indent):
    """Group a YAML list body into one chunk per ``-`` item."""
    items = []
    cur = None
    lead = []
    for line in body_lines:
        stripped = line.strip()
        starts_item = stripped == "-" or stripped.startswith("- ")
        if starts_item and len(line) - len(line.lstrip()) == indent:
            if cur is not None:
                items.append(cur)
            cur = lead + [line]
            lead = []
            continue
        if cur is None:
            lead.append(line)
        else:
            cur.append(line)
    if cur is not None:
        items.append(cur + lead)
    elif lead:
        items.append(lead)
    return items


def _item_name(item_lines):
    for line in item_lines:
        m = re.match(r"^\s*-?\s*name\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip("'").strip('"')
    return None


def _merge_named_list(existing_body, incoming_body, indent):
    """Merge two lists of ``- name: X`` entries, keyed on name.

    Keeps providers the user added by hand that the page knows nothing about.
    """
    out = _split_list_items(existing_body, indent)
    names = {}
    for i, item in enumerate(out):
        name = _item_name(item)
        if name is not None:
            names[name] = i
    for item in _split_list_items(incoming_body, indent):
        name = _item_name(item)
        if name is not None and name in names:
            out[names[name]] = item
        else:
            out.append(item)
    return [line for item in out for line in item]


def _reindent(lines, frm, to):
    """Shift a block so it sits at the indentation the file already uses.

    The page writes list items at indent 2; PyYAML -- which is what rewrites
    this file whenever the Web UI saves -- writes them at column 0.  Without
    this the two styles look like different shapes and the merge degrades to
    a wholesale replace, which is the data loss the merge exists to prevent.
    """
    if frm == to or frm < 0 or to < 0:
        return list(lines)
    delta = to - frm
    out = []
    for line in lines:
        if not line.strip():
            out.append(line)
        elif delta > 0:
            out.append(" " * delta + line)
        else:
            strip = min(-delta, len(line) - len(line.lstrip(" ")))
            out.append(line[strip:])
    return out


PROVIDER_OWNED_MODEL_KEYS = ("base_url", "api_key", "api_mode", "context_length")


def _has_key(lines, indent, name):
    return any(k == name for k, _ in _parse_blocks(lines, indent))


def _drop_keys(lines, indent, names):
    """Remove whole keys from a mapping body, comments and all."""
    return [
        line
        for key, chunk in _parse_blocks(lines, indent)
        if key not in names
        for line in chunk
    ]


def _keep_trailing_blanks(existing_lines, incoming_lines):
    """Replace a chunk but keep the blank-line spacing that followed it."""
    trailing = 0
    for line in reversed(existing_lines):
        if line.strip():
            break
        trailing += 1
    new = list(incoming_lines)
    while new and not new[-1].strip():
        new.pop()
    return new + ["\n"] * trailing


def _merge_chunk(existing_lines, incoming_lines, key):
    """Merge one key's chunk.  Mappings recurse; everything else is replaced."""
    ex_body, ex_indent, ex_kind = _body(existing_lines)
    in_body, in_indent, in_kind = _body(incoming_lines)

    # Same shape is enough -- the indentation is a style difference, not a
    # reason to throw the user's settings away.  The file's own indent wins.
    if ex_kind == in_kind and ex_indent >= 0 and in_indent >= 0:
        body = _reindent(in_body, in_indent, ex_indent)
        head = incoming_lines[: _key_line_index(incoming_lines) + 1]
        if ex_kind == "map":
            kept = ex_body
            if key == "model" and _has_key(body, ex_indent, "provider"):
                # These belong to whichever provider was configured last. The
                # engine prefers model.base_url over the provider's own
                # endpoint, so leaving them behind lets the previous provider
                # silently hijack the one the user just picked -- they watch
                # the connection test pass and every chat still goes nowhere.
                kept = _drop_keys(ex_body, ex_indent, PROVIDER_OWNED_MODEL_KEYS)
            return head + _merge_mapping(kept, body, ex_indent)
        if ex_kind == "list" and key in ("custom_providers", "fallback_providers"):
            return head + _merge_named_list(ex_body, body, ex_indent)

    return _keep_trailing_blanks(existing_lines, incoming_lines)


def _merge_mapping(existing_lines, incoming_lines, indent):
    existing = _parse_blocks(existing_lines, indent)
    incoming = _parse_blocks(incoming_lines, indent)
    out = [[key, list(lines)] for key, lines in existing]
    index = {key: i for i, (key, _) in enumerate(out) if key is not None}

    # A nested block ends with the blank line that separates it from the next
    # top-level key.  Hold it aside so an appended setting lands inside the
    # block rather than after the gap.
    tail = []
    if indent > 0 and out:
        last = out[-1][1]
        while last and not last[-1].strip():
            tail.insert(0, last.pop())

    for key, lines in incoming:
        if key is None:
            continue
        if key in index:
            i = index[key]
            out[i][1] = _merge_chunk(out[i][1], lines, key)
        else:
            new = list(lines)
            while new and not new[-1].strip():
                new.pop()
            # Top-level keys get a blank line between them; nested ones do not.
            if indent == 0 and out and out[-1][1] and out[-1][1][-1].strip():
                out.append([None, ["\n"]])
            out.append([key, new])
            index[key] = len(out) - 1
            if indent == 0:
                out.append([None, ["\n"]])

    if tail:
        out.append([None, tail])

    return [line for _, lines in out for line in lines]


def merge_yaml(existing_text, incoming_text):
    """Apply the page's settings on top of the file already on disk."""
    if not existing_text.strip():
        return incoming_text
    crlf = existing_text.count("\r\n") > existing_text.count("\n") / 2
    existing_lines = existing_text.replace("\r\n", "\n").splitlines(keepends=True)
    incoming_lines = incoming_text.replace("\r\n", "\n").splitlines(keepends=True)
    merged = "".join(_merge_mapping(existing_lines, incoming_lines, 0))
    if not merged.endswith("\n"):
        merged += "\n"
    return merged.replace("\n", "\r\n") if crlf else merged


def parse_yaml_mapping(text):
    """Return the parsed mapping, or None when the text is not one.

    Used both to validate a merge before it is written and to reject a body
    that did not come from the config page.
    """
    try:
        import yaml
    except ImportError:
        # No PyYAML: fall back to a shape check so a bad body is still caught.
        first = text.lstrip().split("\n", 1)[0]
        return {} if _KEY_RE.match(first) else None
    try:
        data = yaml.safe_load(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def merge_env(existing_text, incoming_text):
    """Set the variables the page sends; leave every other line alone."""
    incoming = []
    for line in incoming_text.replace("\r\n", "\n").split("\n"):
        if "=" in line and not line.lstrip().startswith("#"):
            incoming.append((line.split("=", 1)[0].strip(), line))
    wanted = dict(incoming)
    if not existing_text.strip():
        return "".join(line + "\n" for _, line in incoming)

    out = []
    seen = set()
    for line in existing_text.replace("\r\n", "\n").split("\n"):
        name = line.split("=", 1)[0].strip() if "=" in line else None
        if name and not line.lstrip().startswith("#") and name in wanted:
            if name not in seen:
                out.append(wanted[name])
                seen.add(name)
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    for name, line in incoming:
        if name not in seen:
            out.append(line)
    return "\n".join(out) + "\n"


def backup(path):
    """Keep a timestamped copy so a bad save is always recoverable."""
    if not os.path.exists(path):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = os.path.basename(path)
    dest = os.path.join(BACKUP_DIR, "%s.%s.bak" % (name, stamp))
    with open(path, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())
    old = sorted(glob.glob(os.path.join(BACKUP_DIR, "%s.*.bak" % name)))
    for stale in old[:-KEEP_BACKUPS]:
        try:
            os.remove(stale)
        except OSError:
            pass


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def test_provider(base_url, api_key, model):
    """Make a minimal call and map failures to Chinese advice.

    Talks whichever protocol the endpoint speaks: MiniMax's ``/anthropic``
    surface and Kimi's ``/coding`` surface are Anthropic Messages, not
    OpenAI, and probing them with /chat/completions returns a 404 that reads
    to the user as "you typed the model name wrong".
    """
    url = base_url.rstrip("/")
    anthropic = (
        "/anthropic" in url
        or url.endswith("/coding")
        or "api.anthropic.com" in url
    )
    if anthropic:
        req_url = url + "/v1/messages"
        payload = json.dumps({
            "model": model,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        ok_field = "content"
    else:
        req_url = url if url.endswith("/chat/completions") else url + "/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        }
        ok_field = "choices"

    req = urllib.request.Request(req_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and data.get(ok_field):
                return {"ok": True, "message": "连接成功！模型响应正常，可以保存配置了。"}
            return {"ok": False, "message": "服务已连通，但返回内容异常，请检查模型名称是否正确。"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "message": "API 密钥无效或无权限，请检查密钥是否填写正确。"}
        if e.code == 402:
            return {"ok": False, "message": "账户余额不足。密钥本身是有效的，请到服务商官网充值后再试。"}
        if e.code == 404:
            return {"ok": False, "message": "接口地址或模型名称不存在，请检查 API 地址和模型名称。"}
        if e.code == 429:
            return {"ok": False, "message": "请求过于频繁或账户额度不足，请稍后重试或检查余额。"}
        if e.code >= 500:
            return {"ok": False, "message": "服务商服务器错误（HTTP %d），请稍后重试。" % e.code}
        return {"ok": False, "message": "请求被拒绝（HTTP %d），请检查配置。" % e.code}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, socket.gaierror):
            return {"ok": False, "message": "无法解析域名，请检查 API 地址是否正确。"}
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return {"ok": False, "message": "连接超时（20 秒无响应），请检查网络或换个 API 地址。"}
        if isinstance(reason, ConnectionRefusedError):
            return {"ok": False, "message": "连接被拒绝，请确认 API 地址和端口是否正确。"}
        if isinstance(reason, ssl.SSLError):
            return {"ok": False, "message": "SSL 证书错误，请确认 API 地址是否为有效的 https 地址。"}
        return {"ok": False, "message": "网络连接失败，请检查网络后重试。"}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "message": "连接超时（20 秒无响应），请检查网络或换个 API 地址。"}
    except Exception as e:
        return {"ok": False, "message": "测试失败：%s" % e.__class__.__name__}


class ConfigHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silent

    def _cors(self):
        origin = self.headers.get("Origin", "")
        # Only ever to our own page. CORS does not stop a cross-site POST
        # from arriving -- that is what _request_ok is for -- but there is no
        # reason to hand the response body to a page we would refuse.
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def _token(self):
        query = urllib.parse.urlsplit(self.path).query
        return urllib.parse.parse_qs(query).get("t", [""])[0]

    def _route(self):
        return urllib.parse.urlsplit(self.path).path

    def _request_ok(self):
        """Is this really our own page talking to us?

        Two independent gates, because either one alone has a hole. The
        Origin must be this server -- "null", which every file:// page and
        every sandboxed iframe sends, is not good enough and is refused.
        And the request must carry the token this run was started with,
        which only a page we served can know.
        """
        if self.headers.get("Origin") not in ALLOWED_ORIGINS:
            return False
        return secrets.compare_digest(self._token(), TOKEN)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        route = self._route()
        # Readiness probe for the launcher. No state, no token.
        if route == "/ping":
            self._json(200, {"ok": True})
            return
        if route in ("/", "/Config.html"):
            if not secrets.compare_digest(self._token(), TOKEN):
                self.send_response(403)
                self.end_headers()
                self.wfile.write("配置页面的地址不对，请通过启动器重新打开。".encode("utf-8"))
                return
            try:
                with open(CONFIG_PAGE, "rb") as f:
                    page = f.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            # Nothing here should ever be framed by another page.
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(page)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self._request_ok():
            print("  [!] rejected POST from origin %r" % self.headers.get("Origin"))
            self._json(403, {"ok": False, "message": "请求来源不被允许，请通过启动器重新打开配置页面。"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        os.makedirs(DATA_DIR, exist_ok=True)

        route = self._route()
        if route == "/save-config":
            self._save_config(body)
        elif route == "/save-env":
            self._save_env(body)
        elif route == "/test":
            try:
                data = json.loads(body)
                result = test_provider(data["base_url"], data["api_key"], data["model"])
            except (ValueError, KeyError):
                result = {"ok": False, "message": "请求参数错误：需要 base_url、api_key、model。"}
            self._json(200, result)
            print("  [i] /test -> ok=%s" % result["ok"])
        else:
            self.send_response(404)
            self.end_headers()

    def _save_config(self, body):
        if parse_yaml_mapping(body) is None:
            self._json(400, {"ok": False, "message": "提交的配置不是有效的 YAML，已拒绝保存。"})
            print("  [!] /save-config rejected: body is not a YAML mapping")
            return

        path = os.path.join(DATA_DIR, "config.yaml")
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()

        try:
            merged = merge_yaml(existing, body)
        except Exception as e:
            self._json(500, {
                "ok": False,
                "message": "合并配置失败（%s），原文件未改动。" % e.__class__.__name__,
            })
            print("  [!] merge failed: %r" % e)
            return

        if existing.strip() and parse_yaml_mapping(merged) is None:
            self._json(500, {"ok": False, "message": "合并后的配置无法解析，已放弃保存，原文件未改动。"})
            print("  [!] merged config does not parse; kept the original")
            return

        backup(path)
        write_atomic(path, merged)
        self._json(200, {"ok": True})
        print("  [OK] config.yaml merged into %s" % path)
        # Auto-shutdown after saving config (give time for .env save)
        threading.Timer(2.0, lambda: os._exit(0)).start()

    def _save_env(self, body):
        path = os.path.join(DATA_DIR, ".env")
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()
        backup(path)
        write_atomic(path, merge_env(existing, body))
        self._json(200, {"ok": True})
        print("  [OK] .env merged into %s" % path)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), ConfigHandler)
    url = "http://127.0.0.1:%d/?t=%s" % (PORT, TOKEN)
    print("  [i] Config server running on http://127.0.0.1:%d" % PORT)
    # Printed so the user can paste it if their browser does not come up.
    # It is a token for a loopback service that dies after one save.
    print("  [i] 配置页面: %s" % url, flush=True)
    # The server opens the page itself: it is the only party that knows this
    # run's token, and the page has to be served rather than opened from disk
    # for the origin check to mean anything.
    if "--no-open" not in sys.argv:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

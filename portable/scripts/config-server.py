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
import errno
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

# provider_probe sits beside this file, but this file is also loaded by path
# from the test suite, where scripts/ is not on sys.path.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import provider_probe  # noqa: E402  (needs the path set above)

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


def default_workspace():
    """Where the agent works when the user has not chosen anywhere.

    Must stay identical to what protect-config.ps1 computes -- the parent of
    the install directory plus U-Hermes工作区. If the two ever disagree, every
    launch moves the user's files somewhere else.
    """
    return os.path.join(os.path.dirname(PORTABLE_DIR), "U-Hermes工作区")

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
    """Merge two lists of ``- name: X`` entries, keyed on the engine's slug.

    Keeps providers the user added by hand that the page knows nothing about.

    Keyed on the slug rather than the literal name because the two spellings
    are the same provider: the page writes the name lowercased and
    hyphenated, while an entry already in the file keeps whatever display
    name it was given. Comparing them literally made "My Server" and
    "my-server" look like different providers, so the page appended a
    second entry that config.yaml then resolved to the same
    custom:my-server -- and the engine takes the first, which is the one the
    user did not just edit. Their new address and key sat in the file
    underneath the old ones while every chat kept going to the old endpoint,
    immediately after the connection test passed against the new one.
    """
    out = _split_list_items(existing_body, indent)
    names = {}
    for i, item in enumerate(out):
        slug = provider_probe.custom_provider_slug(_item_name(item))
        if slug:
            names[slug] = i
    for item in _split_list_items(incoming_body, indent):
        slug = provider_probe.custom_provider_slug(_item_name(item))
        if slug and slug in names:
            out[names[slug]] = item
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
    # Windows PowerShell 5.1's Set-Content -Encoding utf8 writes a BOM, and
    # protect-config.ps1 writes this file before the user ever opens the
    # config page -- so on a fresh install the first key is ﻿model:,
    # which the key pattern below does not match. Left alone, the page's
    # model: block is appended as a SECOND top-level model: key and the
    # stale one stays in the file forever. Strip it to work, put it back so
    # the file's encoding is left exactly as it was found.
    bom = existing_text.startswith("﻿")
    if bom:
        existing_text = existing_text[1:]
    if not existing_text.strip():
        return ("﻿" if bom else "") + incoming_text
    crlf = existing_text.count("\r\n") > existing_text.count("\n") / 2
    existing_lines = existing_text.replace("\r\n", "\n").splitlines(keepends=True)
    incoming_lines = incoming_text.replace("\r\n", "\n").splitlines(keepends=True)
    merged = "".join(_merge_mapping(existing_lines, incoming_lines, 0))
    if not merged.endswith("\n"):
        merged += "\n"
    if crlf:
        merged = merged.replace("\n", "\r\n")
    return ("﻿" if bom else "") + merged


def parse_yaml_mapping(text):
    """Return the parsed mapping, or None when the text is not one.

    Used both to validate a merge before it is written and to reject a body
    that did not come from the config page.
    """
    text = text.lstrip("﻿")
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


CWD_PLACEHOLDERS = (".", "./", ".\\", "auto", "cwd")


def resolve_blank_workspace(merged):
    """Turn an empty terminal.cwd into the default, in the text itself.

    The config page tells the user an empty box means "back to the default",
    and it was the only thing that believed the service resolved it. It did
    not: the empty string reached disk verbatim, the engine bridged
    TERMINAL_CWD="" (an empty string is not one of the values it treats as
    unset), resolve_agent_cwd() fell through to os.getcwd(), and for that
    whole session the agent wrote its files into the install directory --
    the one place the workspace exists to keep them out of, and the folder
    the uninstall instructions tell people to delete.

    protect-config.ps1 repairs the config on the NEXT launch, which is worse
    than it sounds: the setting heals itself and the files stay orphaned.
    """
    config = parse_yaml_mapping(merged) or {}
    terminal = config.get("terminal")
    if not isinstance(terminal, dict) or "cwd" not in terminal:
        return merged
    if str(terminal.get("backend") or "local") != "local":
        return merged  # a path inside a container or over ssh; not ours to pick
    cwd = str(terminal.get("cwd") or "").strip()
    if cwd and cwd not in CWD_PLACEHOLDERS:
        return merged

    quoted = "'" + default_workspace().replace("'", "''") + "'"
    out, in_terminal, done = [], False, False
    for line in merged.splitlines(keepends=True):
        bare = line.rstrip(chr(13) + chr(10))
        m = _KEY_RE.match(bare)
        if m and not m.group(1):
            in_terminal = m.group(2) == "terminal"
        elif in_terminal and not done and m and m.group(2) == "cwd":
            out.append("%scwd: %s%s" % (m.group(1), quoted, line[len(bare):]))
            done = True
            continue
        out.append(line)
    return "".join(out)


def settle_workspace(merged):
    """Make sure the chosen workspace exists. Returns an error message or None.

    A path that does not exist is never an error the user gets to see: the
    engine walks up to the nearest existing ancestor, so a typo like
    D:\\我的工作\\区 quietly becomes D:\\ and the agent starts writing to the
    root of the drive. Create the folder here, or refuse the save.
    """
    config = parse_yaml_mapping(merged) or {}
    terminal = config.get("terminal")
    if not isinstance(terminal, dict):
        return None
    if str(terminal.get("backend") or "local") != "local":
        return None  # a path inside a container or over ssh; not ours to create
    cwd = str(terminal.get("cwd") or "").strip()
    if not cwd or cwd in CWD_PLACEHOLDERS:
        return None
    if os.path.isdir(cwd):
        return None
    if os.path.exists(cwd):
        return "工作区路径 %s 已经存在，但不是文件夹。" % cwd
    try:
        os.makedirs(cwd)
    except OSError as exc:
        return ("建不出工作区文件夹 %s（%s）。请换一个位置，"
                "或先手动建好这个文件夹。" % (cwd, exc.__class__.__name__))
    return None


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


def release_version():
    """The tag this package was cut from, or "" when running from a clone.

    Written into the zip by .github/workflows/release.yml. Deliberately
    absent from the repository: showing nothing beats showing a number that
    might be wrong.
    """
    try:
        with open(os.path.join(PORTABLE_DIR, "VERSION"), encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""


def write_failure_message(path, err):
    """Why a write failed, in terms the person holding the U disk can act on."""
    code = getattr(err, "errno", None)
    if code in (errno.EACCES, errno.EPERM, errno.EROFS):
        return ("写不进 %s：U 盘或文件夹是只读的（有些 U 盘侧面有写保护小开关），"
                "也可能是文件正被别的程序占用。" % path)
    if code == errno.ENOSPC:
        return "写不进 %s：磁盘已经满了，清理出一点空间再试。" % path
    return "写不进 %s（%s）。" % (path, err.__class__.__name__)


def write_atomic(path, text):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        # Leaving a half-written .tmp next to the real file is how a later
        # run ends up "restoring" a truncated config.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def test_provider(base_url, api_key, model):
    """Make a minimal call and map failures to Chinese advice.

    The request and the whole HTTP table used to live here, and a
    near-identical second copy lived in diagnose.py. The two drifted:
    neither grew a 400 branch, and both discarded the provider's own
    explanation of what it had refused. One copy now, in provider_probe.
    """
    result = provider_probe.probe(base_url, api_key, model)
    if result.ok:
        return {"ok": True, "message": "连接成功！模型响应正常，可以保存配置了。"}
    return {"ok": False, "message": result.message}


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

    def _request_ok(self, require_origin=True):
        """Is this really our own page talking to us?

        Two independent gates for anything that changes state, because
        either one alone has a hole. The Origin must be this server --
        "null", which every file:// page and every sandboxed iframe sends,
        is not good enough and is refused. And the request must carry the
        token this run was started with, which only a page we served knows.

        require_origin=False for reads: browsers send no Origin at all on a
        same-origin GET, so demanding one would reject our own page. The
        token still has to be right, a foreign Origin is still refused, and
        a cross-site caller cannot read the response anyway -- _cors only
        ever names this server.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            if require_origin:
                return False
        elif origin not in ALLOWED_ORIGINS:
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
            # The page reads its own version from here rather than having it
            # baked in, so a stale Config.html cannot claim a version it is
            # not part of.
            self._json(200, {"ok": True, "version": release_version()})
            return
        if route == "/workspace":
            if not self._request_ok(require_origin=False):
                self._json(403, {"ok": False})
                return
            self._json(200, self._workspace_state())
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

    def _workspace_state(self):
        """The two terminal settings the page renders -- and nothing else.

        Deliberately not "GET the config". Everything else in that file is
        the user's API keys and their gateway key; a settings page has no
        business being able to read them back, whatever guards the endpoint.
        """
        state = {"ok": True, "cwd": "", "backend": "local",
                 "default": default_workspace()}
        path = os.path.join(DATA_DIR, "config.yaml")
        if not os.path.exists(path):
            return state
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                config = parse_yaml_mapping(f.read())
        except OSError:
            return state
        terminal = (config or {}).get("terminal")
        if isinstance(terminal, dict):
            cwd = str(terminal.get("cwd") or "").strip()
            # "." and friends mean "not chosen"; showing them to the user as
            # if they were a location would be worse than showing nothing.
            state["cwd"] = "" if cwd in CWD_PLACEHOLDERS else cwd
            state["backend"] = str(terminal.get("backend") or "local")
        return state

    def _save_config(self, body):
        if parse_yaml_mapping(body) is None:
            self._json(400, {"ok": False, "message": "提交的配置不是有效的 YAML，已拒绝保存。"})
            print("  [!] /save-config rejected: body is not a YAML mapping")
            return

        path = os.path.join(DATA_DIR, "config.yaml")
        existing = ""
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()
        except OSError as e:
            # Reading is what fails when the file is locked by an editor or
            # the media has gone away mid-session. Merging against "" would
            # then write a config with the user's gateway block missing.
            self._json(500, {
                "ok": False,
                "message": "读不出 %s（%s），配置未保存。" % (path, e.__class__.__name__),
            })
            print("  [!] could not read config.yaml: %r" % e)
            return

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

        merged = resolve_blank_workspace(merged)
        problem = settle_workspace(merged)
        if problem:
            self._json(400, {"ok": False, "message": problem + " 配置未保存。"})
            print("  [!] workspace refused: %s" % problem)
            return

        # A read-only U disk used to raise straight out of the handler: the
        # socket closed with no response at all, the page's fetch() rejected,
        # and the only trace was a traceback in a window nobody reads.
        try:
            backup(path)
            write_atomic(path, merged)
        except OSError as e:
            self._json(500, {
                "ok": False,
                "message": write_failure_message(path, e) + " 配置未保存。",
            })
            print("  [!] could not write config.yaml: %r" % e)
            return
        self._json(200, {"ok": True})
        print("  [OK] config.yaml merged into %s" % path)
        # Auto-shutdown after saving config (give time for .env save)
        threading.Timer(2.0, lambda: os._exit(0)).start()

    def _save_env(self, body):
        path = os.path.join(DATA_DIR, ".env")
        existing = ""
        try:
            if os.path.exists(path):
                # utf-8-sig, so a BOM someone's editor left here is dropped
                # rather than carried through the merge. It makes the first
                # variable's name "﻿OPENAI_API_KEY", which nothing --
                # not the engine, not python-dotenv -- will ever match.
                with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                    existing = f.read()
        except OSError as e:
            self._json(500, {
                "ok": False,
                "message": "读不出 %s（%s），API 密钥未保存。" % (path, e.__class__.__name__),
            })
            print("  [!] could not read .env: %r" % e)
            return
        try:
            backup(path)
            write_atomic(path, merge_env(existing, body))
        except OSError as e:
            self._json(500, {
                "ok": False,
                "message": write_failure_message(path, e) + " API 密钥未保存。",
            })
            print("  [!] could not write .env: %r" % e)
            return
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

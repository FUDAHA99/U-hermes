"""Local Recruit Edition workbench; separate entry, no upstream source patches.

python scripts/recruit-server.py [--no-browser] [--port 8648]
Only loopback is supported here. A future server deployment must supply proper
authentication/TLS before making this HR workbench reachable by other people.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import webbrowser

# The Windows embeddable interpreter's ._pth omits the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recruit_core import ConfigStore, RecruitError, Usage, atomic_write, prompts, validate_request
from recruit_limits import EXTRACT_SECONDS

PORTABLE = Path(__file__).resolve().parent.parent
MAX_BODY = 7 * 1024 * 1024
# Fixed runtime config: never merge data/config.yaml or arbitrary client input.
RUNTIME_CONFIG = """agent:
  environment_probe: false
  api_max_retries: 1
  auto_recovery_cycles: 0
context:
  engine: compressor
security:
  allow_lazy_installs: false
tools:
  tool_search:
    enabled: 'off'
compression:
  enabled: false
model:
  streaming: false
database:
  journal_mode: DELETE
memory:
  memory_enabled: false
  user_profile_enabled: false
"""


def worker_env(home):
    # Keep platform runtime and explicit networking settings only. In particular
    # never inherit HERMES_*, model/provider keys or PYTHONPATH from general mode.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
            "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
            "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
            "https_proxy", "http_proxy", "all_proxy", "no_proxy"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(HERMES_HOME=str(home), HERMES_GATEWAY_LOCK_DIR=str(home / "gateway-locks"),
               HERMES_MANAGED_DIR=str(home / "managed-empty"),
               HERMES_SAFE_MODE="1", HERMES_ENABLE_PROJECT_PLUGINS="0", HERMES_DISABLE_LAZY_INSTALLS="1",
               HERMES_SINGLE_QUERY_SESSION="1", HERMES_API_TIMEOUT="90",
               PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONDONTWRITEBYTECODE="1", TERMINAL_CWD=str(home / "workspace"))
    # Bound native math thread pools before NumPy/OpenCV/ONNX are imported.
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1", MALLOC_ARENA_MAX="2")
    return env


class RecruitApp:
    def __init__(self, portable=PORTABLE, runner=None):
        self.store = ConfigStore(portable)
        self.store.initialize()
        self.usage = Usage(self.store.data / "usage.sqlite3")
        self.token = secrets.token_urlsafe(32)
        self.busy = threading.Lock()
        self.children_lock = threading.Lock()
        self.children = set()
        self.stopping = False
        self.runner = runner or self.worker

    def stop_workers(self):
        with self.children_lock:
            self.stopping = True
            for child in self.children:
                if child.poll() is None:
                    child.terminate()

    def worker(self, job):
        runtime = self.store.data / "runs"
        runtime.mkdir(exist_ok=True)
        # Hermes may create operational files. They remain under this stick,
        # and the whole per-request directory is removed on normal completion.
        with tempfile.TemporaryDirectory(prefix="request-", dir=runtime) as directory:
            home = Path(directory)
            (home / "workspace").mkdir()
            atomic_write(home / "config.yaml", RUNTIME_CONFIG)
            if job["mode"] == "agent":
                shutil.copytree(self.store.data / "hermes-home" / "skills", home / "skills")
            with self.children_lock:
                if self.stopping:
                    raise RecruitError("招聘版正在停止，请重新启动后再使用。")
                child = subprocess.Popen(
                    [sys.executable, "-I", str(PORTABLE / "scripts" / "recruit_worker.py")],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    encoding="utf-8", errors="replace",
                    cwd=home / "workspace", env=worker_env(home),
                )
                self.children.add(child)
            try:
                stdout, _ = child.communicate(json.dumps(job, ensure_ascii=False),
                                              timeout=150 if job["mode"] == "agent" else EXTRACT_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                if job["mode"] == "extract":
                    raise RecruitError("扫描件识别或文档解析超时（最多 180 秒），请拆分 PDF 或上传更清晰、较小的图片；本次未计用量。") from None
                raise RecruitError("处理超时，请缩小文件或稍后重试；已提交的模型请求会保留用量计数。") from None
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
                with self.children_lock:
                    self.children.discard(child)
            try:
                data = json.loads(stdout)
                if child.returncode or not isinstance(data, dict):
                    raise ValueError()
            except ValueError:
                if job["mode"] == "extract":
                    raise RecruitError("文档组件未完成处理，可能超出内存限制或缺少组件；请缩小图片、拆分 PDF，或运行安装脚本；本次未计用量。") from None
                raise RecruitError("招聘组件启动失败，请检查依赖安装；不要反复提交同一请求。") from None
            if "error" in data:
                raise RecruitError(data["error"])
            return data

    def run(self, payload):
        request = validate_request(payload)
        if not self.busy.acquire(blocking=False):
            raise RecruitError("正在处理上一项任务，请完成后再提交。")
        try:
            with self.store.lock:
                config = self.store.load()
                key = self.store.key()
                if not key:
                    raise RecruitError("请先在公司与费用配置中填写自己的 DeepSeek API Key。")
                system, user = prompts(self.store, request)
            task = request["task"]
            self.usage.reserve(task, len(request["resumes"]) if task == "screen" else 1, config["limits"][task])
            result = self.runner({"mode": "agent", "config": config, "api_key": key, "system": system, "user": user})
            return {"answer": result["answer"], "usage": self.usage.snapshot()}
        finally:
            self.busy.release()

    def extract(self, payload):
        if not self.busy.acquire(blocking=False):
            raise RecruitError("正在处理上一项任务，请完成后再上传。")
        try:
            return self.runner({"mode": "extract", "payload": payload,
                                "ocr_enabled": self.store.load()["ocr_enabled"]})
        finally:
            self.busy.release()


class RecruitServer(ThreadingHTTPServer):
    # Ctrl+C cancels workers, then waits for cleanup before the drive is removed.
    daemon_threads = False

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # No keys, resume text or request paths in access logs.

    def send(self, status, data, html=False):
        raw = data.encode("utf-8") if html else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def trusted_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.trusted_host():
            return self.send(403, {"error": "只允许从本机招聘页面访问。"})
        try:
            if self.path == "/":
                html = (self.server.app.store.assets / "index.html").read_text(encoding="utf-8")
                return self.send(200, html.replace("__RECRUIT_TOKEN__", self.server.app.token), html=True)
            if self.path == "/api/config":
                if not self.authorized():
                    return self.send(403, {"error": "请从本机招聘页面重新打开。"})
                return self.send(200, self.server.app.store.public(self.server.app.usage))
            if self.path == "/health":
                return self.send(200, {"edition": "recruit"})
            self.send(404, {"error": "页面不存在。"})
        except (RecruitError, OSError, sqlite3.Error):
            self.send(400, {"error": "招聘配置或用量记录无法读取，请检查本机文件；系统不会自动清空记录。"})

    def authorized(self):
        if not self.trusted_host():
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return False
        return hmac.compare_digest(self.headers.get("X-Recruit-Token", "").encode("utf-8"),
                                   self.server.app.token.encode("ascii"))

    def do_POST(self):
        if not self.authorized():
            return self.send(403, {"error": "请求来源校验失败，请刷新本机招聘页面。"})
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise RecruitError("请求需要 JSON 格式。")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise RecruitError("请求为空或过大。单份文件上限 5 MiB。")
            self.connection.settimeout(15)
            payload = json.loads(self.rfile.read(length))
            app = self.server.app
            if self.path == "/api/run":
                data = app.run(payload)
            elif self.path == "/api/extract":
                data = app.extract(payload)
            elif self.path == "/api/config":
                app.store.save(payload)
                data = app.store.public(app.usage)
            else:
                return self.send(404, {"error": "页面不存在。"})
            self.send(200, data)
        except RecruitError as exc:
            self.send(400, {"error": str(exc)})
        except (ValueError, TimeoutError):
            self.send(400, {"error": "请求格式不正确或上传超时，请重试。"})
        except Exception:
            self.send(500, {"error": "本机处理失败，请检查磁盘空间、配置和用量文件。原用量不会自动重置。"})


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8648)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        app = RecruitApp()
        server = RecruitServer(("127.0.0.1", args.port), app)
    except OSError:
        print("无法启动：请先关闭占用端口的通用版/招聘版，并检查目录写入权限。")
        return 1
    except (RecruitError, sqlite3.Error) as exc:
        print("招聘配置或用量记录有误，请修复后再启动。" + (str(exc) if isinstance(exc, RecruitError) else ""))
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"招聘版已启动：{url}\n数据目录：{app.store.data}\n按 Ctrl+C 停止，停止后再拔出 U 盘。")
    if app.store.load()["allow_tools"]:
        print("注意：allow_tools=true，已允许智能体执行命令和读写文件。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_workers()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

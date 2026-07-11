"""
Lightweight config server for U-Hermes Config.html.
Starts before Config.html opens, serves save-config / save-env endpoints,
then shuts down after config is saved.
"""
import http.server
import json
import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request

# pythonw 下 stdout/stderr 为 None，print() 会崩溃，重定向到空设备
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

PORT = 18790
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PORTABLE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PORTABLE_DIR, "data")


def test_provider(base_url, api_key, model):
    """Make a minimal chat/completions call and map failures to Chinese advice."""
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and data.get("choices"):
                return {"ok": True, "message": "连接成功！模型响应正常，可以保存配置了。"}
            return {"ok": False, "message": "服务已连通，但返回内容异常，请检查模型名称是否正确。"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "message": "API 密钥无效或无权限，请检查密钥是否填写正确。"}
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        os.makedirs(DATA_DIR, exist_ok=True)

        if self.path == "/save-config":
            path = os.path.join(DATA_DIR, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            print(f"  [OK] config.yaml saved to {path}")
            # Auto-shutdown after saving config (give time for .env save)
            threading.Timer(2.0, lambda: os._exit(0)).start()

        elif self.path == "/save-env":
            path = os.path.join(DATA_DIR, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            print(f"  [OK] .env saved to {path}")

        elif self.path == "/test":
            try:
                data = json.loads(body)
                result = test_provider(data["base_url"], data["api_key"], data["model"])
            except (ValueError, KeyError):
                result = {"ok": False, "message": "请求参数错误：需要 base_url、api_key、model。"}
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            print("  [i] /test -> ok=%s" % result["ok"])

        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), ConfigHandler)
    print(f"  [i] Config server running on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

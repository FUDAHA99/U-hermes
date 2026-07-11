"""
Minimal config server for U-Hermes.
Allows Config.html to save config.yaml via HTTP POST.
Auto-exits after 5 minutes of inactivity.
"""

import http.server
import json
import os
import threading
import time
from pathlib import Path

PORT = 18790
TIMEOUT = 300  # 5 minutes

# Resolve paths
SCRIPT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SCRIPT_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.yaml"


class ConfigHandler(http.server.BaseHTTPRequestHandler):
    last_activity = time.time()

    def log_message(self, format, *args):
        pass  # suppress logs

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        ConfigHandler.last_activity = time.time()

        if self.path == "/save-config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(body, encoding="utf-8")

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        elif self.path == "/save-env":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            env_file = DATA_DIR / ".env"
            env_file.write_text(body, encoding="utf-8")

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        ConfigHandler.last_activity = time.time()

        if self.path == "/read-config":
            if CONFIG_FILE.exists():
                content = CONFIG_FILE.read_text(encoding="utf-8")
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_response(404)
                self._cors_headers()
                self.end_headers()
        elif self.path == "/ping":
            self.send_response(200)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"pong")
        else:
            self.send_response(404)
            self.end_headers()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def auto_shutdown(server):
    while True:
        time.sleep(30)
        if time.time() - ConfigHandler.last_activity > TIMEOUT:
            server.shutdown()
            break


def main():
    port = PORT
    # Find free port
    for p in range(PORT, PORT + 10):
        try:
            server = http.server.HTTPServer(("127.0.0.1", p), ConfigHandler)
            port = p
            break
        except OSError:
            continue
    else:
        print(f"No free port in range {PORT}-{PORT+9}")
        sys.exit(1)

    print(f"Config server running on http://127.0.0.1:{port}")
    print(f"Will auto-exit after {TIMEOUT}s of inactivity.")

    # Auto-shutdown thread
    t = threading.Thread(target=auto_shutdown, args=(server,), daemon=True)
    t.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

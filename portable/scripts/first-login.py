# -*- coding: utf-8 -*-
"""首次启动时把网页界面的账号占下来，换成只有这份 U 盘知道的密码。

hermes-web-ui 出厂时账号是「无主」的：users 表为空时，第一个用
admin / 123456 调 /api/auth/login 的人，账号就归他（users-store.ts 的
bootstrapDefaultSuperAdmin）。而登录页会用访问者自己的语言把这组默认
凭据直接印在上面——登录之前就能看到。

在我们占下它之前，先摸到这个端口的人就是主人。而那个账号背后能在这台
电脑上开一个 PowerShell 终端。上游没有提供设置密码的环境变量，它自带的
「请修改默认密码」弹窗还有一个「稍后」按钮，所以只能在这里做。

只用公开的 HTTP 接口，不碰它的数据库结构，因此上游换存储位置也不受影响。
任何一步失败都不会挡住启动，只会把话说清楚。

用法:  first-login.py <base-url> <webui-home> [timeout-seconds]
退出码 0 界面已就绪（占下了，或本来就有主）/ 1 界面始终没有响应
"""
import io
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "123456"
# 没有 0/O/1/l/I：这串东西每换一台电脑就要重新输一次。
ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
PASSWORD_LENGTH = 10
PASSWORD_FILE = "登录密码.txt"


def call(url, payload=None, token=None, timeout=8.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def wait_for(url, limit):
    deadline = time.time() + limit
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2.0)
            return True
        except urllib.error.HTTPError:
            return True  # 401/404 也说明它在监听
        except Exception:
            time.sleep(0.4)
    return False


def claim(base):
    """占下账号并返回新密码；已经有主则返回 None。"""
    if call(base + "/api/auth/status").get("hasUsers"):
        return None
    password = "".join(secrets.choice(ALPHABET) for _ in range(PASSWORD_LENGTH))
    token = call(base + "/api/auth/login",
                 {"username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD})["token"]
    call(base + "/api/auth/change-password",
         {"currentPassword": DEFAULT_PASSWORD, "newPassword": password}, token=token)
    return password


def remember(webui_home, password):
    """把密码写在 U 盘上。那里已经有 .env 里的 API 密钥了，不算新增暴露面。"""
    path = os.path.join(webui_home, PASSWORD_FILE)
    try:
        if not os.path.isdir(webui_home):
            os.makedirs(webui_home)
        # BOM：记事本才能正确认出中文
        with io.open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write("U-Hermes 网页界面登录信息\n\n")
            f.write("    用户名： %s\n" % DEFAULT_USERNAME)
            f.write("    密　码： %s\n\n" % password)
            f.write("这个密码是本份 U 盘专用的，换电脑登录也是这一个。\n")
            f.write("想换成自己好记的：登录后进入 设置 -> 账户。\n")
    except OSError:
        return None
    return path


def main(argv):
    if len(argv) < 3:
        print("usage: first-login.py <base-url> <webui-home> [timeout-seconds]")
        return 2
    base = argv[1].rstrip("/")
    webui_home = argv[2]
    limit = float(argv[3]) if len(argv) > 3 else 90.0

    if not wait_for(base + "/health", limit):
        print("   [!] 等了 %d 秒，网页界面还没有响应。" % limit)
        print("   [!] 稍后手动打开 %s 试试。" % base)
        webbrowser.open(base + "/")
        return 1

    try:
        password = claim(base)
    except Exception as exc:
        # 绝不因为这一步挡住启动。说清楚现状和该做什么就行。
        print()
        print("   [!] 没能自动设置登录密码（%s）。" % exc.__class__.__name__)
        print("   [!] 请用默认的 admin / 123456 登录，然后立刻在")
        print("       设置 -> 账户 里把密码改掉。")
        print()
        webbrowser.open(base + "/")
        return 0

    if password:
        path = remember(webui_home, password)
        print()
        print("   ============================================")
        print("     网页界面首次启动，已为这份 U 盘设置登录密码")
        print()
        print("       用户名： %s" % DEFAULT_USERNAME)
        print("       密　码： %s" % password)
        print()
        if path:
            print("     这段信息也存在： %s" % path)
        print("     换电脑登录也是这一个，可在 设置 -> 账户 里改。")
        print("   ============================================")
        print()
    webbrowser.open(base + "/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

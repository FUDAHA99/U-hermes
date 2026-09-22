# -*- coding: utf-8 -*-
"""等网页界面起来，然后把浏览器打开。

这个脚本原来还做一件事：拿出厂的 admin / 123456 登进去，换成一串随机密码，
写进 data\\webui\\登录密码.txt。现在不换了——让登录页上印的那行
「默认登录名：admin，默认密码：123456」说的是真话。

那行字是上游 hermes-web-ui 印在登录页上的，未登录就能看见，而那个账号背后
能在这台电脑上开一个 PowerShell 终端。所以有一件事必须成立：**界面只监听
本机**。启动器默认 BIND_HOST=127.0.0.1；如果有人在 data\\.env 里把它改成
0.0.0.0（想用手机连的人会这么做），同一个网络里的任何人都能照着登录页上
那行字进来。

升级上来的机器是例外，所以下面还留着一段：早先的版本已经把那台机器的密码
换掉了，登录页上那行对它就是错的。只要 data\\webui\\登录密码.txt 还在，就
把它指出来——否则用户照着页面输 123456，被拒，然后不知道去哪找。我们不会
替他们把密码改回 123456：那是在没问过的情况下削弱一台已经装好的机器。

用法:  first-login.py <base-url> <webui-home> [timeout-seconds]
退出码 0 界面已就绪 / 1 界面始终没有响应
"""
import io
import os
import sys
import time
import urllib.error
import urllib.request
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "123456"
PASSWORD_FILE = "登录密码.txt"


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


def rotated_password_file(webui_home):
    """早先版本换过密码的话，返回那个文件的路径，否则返回 None。

    判断只看文件在不在。去问服务器「123456 还能不能登」要真发一次登录
    请求，失败的那次会计进 .login-lock.json 的失败计数里——为了显示一行
    提示而让用户离被锁更近一步，不划算。
    """
    path = os.path.join(webui_home, PASSWORD_FILE)
    return path if os.path.isfile(path) else None


def announce_rotated_password(path):
    """把早先版本生成的那组凭据打出来。

    这台机器的密码不是 123456，而登录页会坚持说它是。用户手上唯一的线索
    就是这个文件，而它在 data\\webui\\ 里，不会有人自己翻到。
    """
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as f:
            body = f.read().strip()
    except OSError:
        body = ""
    print()
    print("   ============================================")
    print("     这台机器的网页密码在早先的版本里被改过，")
    print("     所以登录页上写的 %s 对它不适用。" % DEFAULT_PASSWORD)
    print()
    for line in (body.splitlines() or ["（%s 读不出来）" % path]):
        print("     " + line)
    print()
    print("     文件位置： %s" % path)
    print("     想换回好记的：登录后进入 设置 -> 账户。")
    print("   ============================================")
    print()


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

    # 绝不因为这一步挡住启动。
    try:
        path = rotated_password_file(webui_home)
        if path:
            announce_rotated_password(path)
    except Exception as exc:
        print("   [!] 读不到登录密码文件（%s），先按登录页上写的试。"
              % exc.__class__.__name__)

    webbrowser.open(base + "/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

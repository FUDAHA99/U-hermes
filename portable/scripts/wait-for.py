"""Wait until a local service answers, then optionally open it in a browser.

The launcher used to open the browser three seconds after starting the Web
UI.  On the machine it was written on that was enough; on a USB stick, on a
cold cache, or behind a virus scanner it is not, and the user's first sight
of the product is a connection-refused page.  CI never caught it because CI
waits sixty seconds.

Usage:  wait-for.py [--open] <url> [timeout-seconds]
Exit 0  the service answered
Exit 1  it did not answer in time
"""
import sys
import time
import urllib.error
import urllib.request
import webbrowser


def responds(url, timeout=2.0):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        # 401/404 still means something is listening and serving.
        return True
    except Exception:
        return False


def main(argv):
    args = [a for a in argv[1:] if a != "--open"]
    open_browser = "--open" in argv[1:]
    if not args:
        print("usage: wait-for.py [--open] <url> [timeout-seconds]")
        return 2
    url = args[0]
    limit = float(args[1]) if len(args) > 1 else 60.0

    deadline = time.time() + limit
    while time.time() < deadline:
        if responds(url):
            if open_browser:
                webbrowser.open(url)
            return 0
        time.sleep(0.4)

    print("   [!] 等了 %d 秒，%s 还没有响应。" % (limit, url))
    print("   [!] 服务可能还在启动，稍后手动打开这个地址试试。")
    if open_browser:
        webbrowser.open(url)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

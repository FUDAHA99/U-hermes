# U-Hermes

> **U 盘里的自进化 AI Agent | Self-improving AI Agent that runs from a USB drive**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Powered by Hermes Agent](https://img.shields.io/badge/Powered%20by-Hermes%20Agent-blueviolet)](https://github.com/NousResearch/hermes-agent)

**[📖 中文官网](https://fudaha99.github.io/U-hermes/)** · **[⬇ 下载最新版](https://github.com/FUDAHA99/U-hermes/releases/latest)**

---

## What is U-Hermes

U-Hermes combines the **portable USB distribution** of [U-Claw](https://github.com/dongsheng123132/u-claw) with the **self-improving AI engine** of [Hermes Agent](https://github.com/NousResearch/hermes-agent) (by Nous Research).

**Core capabilities:**
- **Plug and play** — insert USB, double-click, AI ready. Zero external dependencies
- **Self-improving** — auto-creates skills from experience, improves them during use
- **Chat Web UI** — built-in browser chat interface (hermes-web-ui), auto-opens on startup
- **Web Dashboard** — skills, sessions and cron management in the same UI
- **Acts, doesn't just chat** — reads and writes files, runs commands and scheduled jobs on the machine it is
  plugged into, with a workspace kept outside the install directory so upgrades never touch your work
- **Self-healing** — auto-repairs stale paths when the drive letter changes, restores config after UI overwrites
- **Failover** — falls back to a backup model on timeout or quota errors, once you have configured one
- **Multi-platform messaging** — QQ / WeChat / DingTalk / Feishu / Telegram / Discord / WhatsApp / Signal / Slack
- **China-optimized** — domestic model support, China mirrors, Chinese skills pre-installed
- **Windows portable build via CI** — a macOS build exists in the workflow but cannot currently produce a working package, so it is not published (see Download)

---

## Download

Download the latest release from [**Releases**](https://github.com/FUDAHA99/U-hermes/releases):

| Platform | File | Instructions |
|----------|------|--------------|
| Windows | `u-hermes-portable-windows-v*.zip` | Unzip, double-click `Windows-Start.bat` |

**macOS is not shipping right now.** The mac zips attached to v0.4.0 and
v0.4.1 do not run: `zip` was invoked without `-y`, so the venv's interpreter
was dereferenced into a *copy* of the runner's python.org framework stub,
which loads `/Library/Frameworks/Python.framework/Versions/3.11/Python` — a
path that does not exist on a normal Mac. `Mac-Start.command` as shipped in those
builds could not recover from it either: its rebuild was gated on
`[ ! -f "$VENV_PYTHON" ]`, and that file is present, just unusable. (Both
that gate and the `-y` are fixed on this branch and the
macOS job now fails at "Verify the packaged artifact" rather than publishing
another one.) The download is delisted rather than replaced because the
build is still not self-contained. `setup.sh` never downloads an interpreter
at all — it lets `uv venv --python 3.11` pick whatever the machine has — and
`scripts/fix-portable-paths.sh` rewrites `pyvenv.cfg` to point at
`runtime/python-mac-arm64`, a directory nothing in this repository creates.
Making it work means bundling a relocatable Python the way the Windows job
already bundles the embeddable one. Tracked, not done.

### 国内加速下载

GitHub 直连较慢时，在任意下载链接前加上 `https://ghfast.top/` 即可加速：

```
https://ghfast.top/<把 Releases 页的下载链接原样粘在这里>
```

每个 [Release 页面](https://github.com/FUDAHA99/U-hermes/releases) 也附有现成的加速链接。

## Quick Start

**Before you start:** U-Hermes brings no AI of its own. You need an API key from a model
provider ([DeepSeek](https://platform.deepseek.com), [Kimi](https://platform.moonshot.cn),
[GLM](https://open.bigmodel.cn), [Qwen](https://dashscope.console.aliyun.com) ...). They bill
per use — a few yuan covers a month of ordinary use, and most give a small free allowance.
Without a key nothing will answer you; there is no way around this step.

1. Download and unzip the portable package (to USB drive or local folder)
2. Double-click `Windows-Start.bat`
3. First launch auto-opens the config page. It also downloads the web UI once, so it needs
   an internet connection and a minute or two
4. Select AI model (DeepSeek recommended for China)
5. Enter API Key, click **测试连接** to verify, save
6. Press any key in the launcher window; the browser opens the chat UI at
   `http://127.0.0.1:8648` as soon as it is actually up

If something goes wrong, run `Windows-Menu.bat` → `[6] 一键诊断`. It checks the network,
the key, the balance and the ports, and says what to do next in Chinese.

### Build from Source

```bash
# Windows
git clone https://github.com/FUDAHA99/U-hermes.git
cd u-hermes\portable
powershell -ExecutionPolicy Bypass -File setup.ps1
.\Windows-Start.bat

# macOS (does not currently produce a working package -- see Download)
git clone https://github.com/FUDAHA99/U-hermes.git
cd u-hermes/portable && bash setup.sh
bash Mac-Start.command
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Chat Web UI** | Built-in hermes-web-ui chat interface on port 8648, auto-opens in browser |
| **One-click diagnostics** | Checks config, ports and API connectivity, explains errors in plain language |
| **Connection test** | Verifies the API key from the config page before saving |
| **Model failover** | Falls back to a backup provider on timeout, 401 or quota errors. Off until you add one (`hermes fallback add`) |
| **Self-learning** | Creates skills from experience, improves them automatically |
| **Shell and file access** | The agent works in a real terminal on your machine, in a workspace beside the install |
| **12 Chinese skills** | Xiaohongshu, Douyin, WeChat articles, Weibo, Bilibili, Zhihu, Taobao listings, daily reports, China search / weather / translate |
| **Multi-model** | DeepSeek, Kimi, Qwen, GLM, MiniMax, Doubao + Claude/GPT/Gemini |
| **Messaging gateway** | QQ Bot, WeChat, WeCom, DingTalk, Feishu, Telegram, Discord, etc. |
| **Scheduled tasks** | Built-in cron with delivery to any platform |
| **Memory** | Persistent cross-session memory and user modeling |
| **MCP support** | Connect any MCP server for extended capabilities |

---

## Supported AI Models

**Chinese models (no VPN needed):**

| Model | Best for |
|-------|----------|
| DeepSeek | Coding, extremely cheap |
| Kimi K2.5 | Long documents, 256K context |
| Qwen | Large free tier |
| GLM (Zhipu) | Academic use |
| MiniMax | Voice & multimodal |
| Doubao | Volcengine ecosystem |

**International:** Claude, GPT, Gemini (via OpenRouter or direct)

---

## File Structure

```
U-Hermes/                        ← Copy to USB drive
├── Windows-Start.bat            Windows launcher
├── Windows-Menu.bat             Feature menu
├── Windows-Gateway.bat          Messaging gateway
├── Mac-Start.command            Mac launcher
├── Config.html                  Web configuration page
├── setup.ps1 / setup.sh         First-time dependency download
│
├── scripts/                     Maintenance helpers
│   ├── apply-upstream-tweaks.py Applies our tweaks to the vendored agent
│   ├── fix-portable-paths.ps1   Repairs venv paths after a drive-letter change
│   ├── protect-config.ps1       Restores config.yaml, keeps the gateway key present
│   ├── config-server.py         Backs the config page (merging save + connection test)
│   ├── preflight.py             Refuses to launch into a config that cannot answer
│   ├── wait-for.py              Opens the browser when the UI is actually up
│   ├── diagnose.py              One-click diagnostics
│   └── tests/                   Regression tests, run by CI's smoke step
│
├── runtime/                     ← Downloaded by setup (not in git)
│   ├── python-win-x64/          Embedded Python (see versions.env)
│   ├── node-win-x64/            Node.js + hermes-web-ui (Chat Web UI)
│   └── uv/                      uv package manager
│
├── hermes/                      ← Downloaded by setup (not in git)
│   ├── hermes-agent/            Hermes Agent source
│   └── .venv/                   Python virtual environment
│
├── skills-cn/                   Pre-installed Chinese skills
│   ├── xiaohongshu-writer/
│   ├── douyin-script/
│   ├── wechat-article/
│   └── ...
│
├── cloud/                       Local device identity helpers
│   └── fingerprint.py           Device fingerprint
│
├── data/                        ← User data (persists on USB)
│   ├── config.yaml              Configuration
│   ├── backups/                 Timestamped copies, written before every save
│   ├── memory/                  AI memory
│   ├── skills/                  User-created skills
│   └── sessions/                Conversation history
│
└── lib/                         Helper modules
    ├── bootstrap.py             Path management
    └── china_mirrors.py         Mirror configuration
```

---

## Ports

| Service | Port | Description |
|---------|------|-------------|
| Web UI | `8648` | hermes-web-ui chat interface (auto-opens) |
| Gateway API | `8642` | Hermes Agent gateway API server |
| Config server | `18790` | First-run configuration page backend |

## Disk Usage

Versions come from [`portable/versions.env`](portable/versions.env); sizes are
measured, not estimated. The table used to name Python 3.11 and Node 22 and
claim a 350 MB download — all three were wrong by the time anyone read them.

| Component | Size |
|-----------|------|
| Embedded Python + uv | ~72 MB |
| Node.js + hermes-web-ui | ~274 MB |
| Hermes Agent + venv | ~548 MB |
| **Download (zip)** | **457 MB** — measured on the v0.4.1 Windows asset (the 537 MB macOS asset of the same tag is the withdrawn build) |
| **Unpacked** | **~1.3 GB** |
| **Recommended USB** | **8 GB+** — the chat database grows with use; see `Windows-Menu.bat` → `[7] 清理聊天记录` |

---

## Uninstall

Deleting the folder removes the program. Three things live outside it.

**1. Your workspace.** The agent reads and writes files in a folder *beside*
the install directory, not inside it — `U-Hermes工作区` by default, or
wherever the config page's *智能体工作区* box points. Upgrades deliberately
never touch it, and neither does deleting the install folder. The launcher
prints the path on every start. Your own files are in there, so delete it
separately, once you are sure.

**2. A config copy on every machine it has run on.** While running, U-Hermes
mirrors `config.yaml` and `.env` (which holds your API key) into
`%USERPROFILE%\.hermes\`, so that any component started without our
environment still finds a working config. A clean exit removes both, and so
does the next launch on that machine — but only if you answer **N** to
cmd's `终止批处理操作吗(Y/N)?` after Ctrl+C. Answering **Y** ends the batch
file at that prompt, so the cleanup it was about to run never happens, and
closing the window with [X] skips it too. Either way the next launch on that
machine clears it, and `Windows-Menu.bat` → `[8] 清理本机残留` does it on
demand: it deletes exactly those two files and reports anything else it
finds rather than assuming it is ours.

**3. Package caches, on builds before this one.** npm and uv cache downloads
under `%LOCALAPPDATA%` by default, so earlier versions left a few hundred MB
on the host machine:

```
%LOCALAPPDATA%\npm-cache
%LOCALAPPDATA%\uv\cache
```

Both are shared with any other Node or Python work on that machine, so
nothing here deletes them for you. `npm cache clean --force` and
`uv cache clean` are the safe way. Current builds point both caches at the
stick instead, and drop the npm one as soon as the install finishes.

Nothing is written to the registry, no service or scheduled task is created,
and `PATH` is never modified — each launcher sets `PATH` for its own process
only.



---

## China Mirrors

All downloads use domestic mirrors — no VPN needed:

| Resource | Mirror |
|----------|--------|
| PyPI packages | `pypi.tuna.tsinghua.edu.cn` |
| Node.js | `npmmirror.com/mirrors/node` |
| Python | `npmmirror.com/mirrors/python` |

---

## Commercial Use

Both Hermes Agent and this project are MIT licensed — fully free for commercial use.

**Business model:**
- Pre-built USB drives
- Enterprise private deployment
- Skill marketplace
- Reseller/affiliate program

---

## CI/CD

Push a `v*` tag to trigger automatic builds:

```bash
git tag v0.x.0
git push origin v0.x.0
```

GitHub Actions builds both Windows and Mac portable packages, runs smoke tests
(package imports, CLI entry point, and a real Web UI boot answering HTTP), then
publishes them to Releases.

Run the workflow manually from the Actions tab to build and smoke-test without
publishing a release.

### Pinned versions and the weekly canary

The whole toolchain is pinned in [`portable/versions.env`](portable/versions.env),
which both `setup.sh` and the CI workflow read — including `HERMES_AGENT_REF`,
the upstream release tag the AI engine is built from. Releases are therefore
reproducible.

Because upstream moves fast (~21,500 commits landed between two of our builds),
a scheduled **canary** run every Monday builds against upstream *latest* instead
of the pins. If upstream breaks us, that scheduled run fails within days rather
than the breakage surfacing months later when someone cuts a release. Trigger one
on demand with the `canary` checkbox on a manual run.

To take a newer engine: run the canary, and if it is green, bump
`HERMES_AGENT_REF` (and any other pin) and tag a release.

Note that GitHub disables scheduled workflows after 60 days without repository
activity; re-enable it from the Actions tab if the project goes quiet.

## Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research — the self-improving AI engine
- [hermes-web-ui](https://www.npmjs.com/package/hermes-web-ui) — Chat Web UI
- [U-Claw](https://github.com/dongsheng123132/u-claw) — original USB portable AI concept
- All model providers for their APIs

---

## License

MIT — see [LICENSE](LICENSE).

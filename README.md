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
- **Self-healing** — auto-repairs stale paths when the drive letter changes, restores config after UI overwrites
- **Failover** — switches to a backup model automatically when the primary times out or runs out of credit
- **Multi-platform messaging** — QQ / WeChat / DingTalk / Feishu / Telegram / Discord / WhatsApp / Signal / Slack
- **China-optimized** — domestic model support, China mirrors, Chinese skills pre-installed
- **Dual platform** — Windows + macOS (ARM64) portable builds via CI

---

## Download

Download the latest release from [**Releases**](https://github.com/FUDAHA99/U-hermes/releases):

| Platform | File | Instructions |
|----------|------|--------------|
| Windows | `u-hermes-portable-windows-v*.zip` | Unzip, double-click `Windows-Start.bat` |
| macOS (ARM64) | `u-hermes-portable-mac-v*.zip` | Unzip, run `bash Mac-Start.command` |

### 国内加速下载

GitHub 直连较慢时，在任意下载链接前加上 `https://ghfast.top/` 即可加速：

```
https://ghfast.top/https://github.com/FUDAHA99/U-hermes/releases/download/v0.3.5/u-hermes-portable-windows-v0.3.5.zip
```

每个 [Release 页面](https://github.com/FUDAHA99/U-hermes/releases) 也附有现成的加速链接。

## Quick Start

1. Download and unzip the portable package (to USB drive or local folder)
2. Double-click the start script (`Windows-Start.bat` or `Mac-Start.command`)
3. First launch auto-opens the config page
4. Select AI model (DeepSeek recommended for China)
5. Enter API Key, click **测试连接** to verify, save, done!
6. Browser auto-opens chat UI at `http://127.0.0.1:8648`

### Build from Source

```bash
# Windows
git clone https://github.com/FUDAHA99/U-hermes.git
cd u-hermes\portable
powershell -ExecutionPolicy Bypass -File setup.ps1
.\Windows-Start.bat

# macOS
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
| **Model failover** | Falls back to a backup provider on timeout, 401 or quota errors |
| **Self-learning** | Creates skills from experience, improves them automatically |
| **10 Chinese skills** | Xiaohongshu, Douyin, WeChat articles, Weibo, Bilibili, Zhihu, etc. |
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
│   ├── protect-config.ps1       Restores config.yaml after a Web UI overwrite
│   ├── config-server.py         Backs the config page (save + connection test)
│   └── diagnose.py              One-click diagnostics
│
├── runtime/                     ← Downloaded by setup (not in git)
│   ├── python-win-x64/          Embedded Python 3.11
│   ├── node-win-x64/            Node.js 22 + hermes-web-ui (Chat Web UI)
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

| Component | Size |
|-----------|------|
| Python 3.11 + uv | ~71 MB |
| Node.js 22 LTS + hermes-web-ui | ~144 MB |
| Hermes Agent + deps | ~383 MB |
| Download (zip) | **~350 MB** Windows / **~400 MB** macOS |
| Unpacked | **~600 MB** |
| Recommended USB | **4 GB+** |

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
publishing a release — useful for checking whether upstream drift broke the build.

## Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research — the self-improving AI engine
- [hermes-web-ui](https://www.npmjs.com/package/hermes-web-ui) — Chat Web UI
- [U-Claw](https://github.com/dongsheng123132/u-claw) — original USB portable AI concept
- All model providers for their APIs

---

## License

MIT — see [LICENSE](LICENSE).

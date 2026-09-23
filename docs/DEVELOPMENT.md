# 开发说明

面向改这个项目的人。用户看的是 README 和压缩包里的 `0-先看我-使用说明.txt`。

---

## 三个位置

| 位置 | 是什么 |
|---|---|
| `F:\U-hermes` | git 仓库，改代码的源头。`portable\` 是打包模板，本身也是一个能跑的实例 |
| `H:\Hermes` | U 盘上的运行实例 = 真机测试环境。代码靠同步脚本推过去 |
| GitHub `FUDAHA99/U-hermes` | 打 `v*` 标签触发 CI，直接上传 Release |

---

## 每次开工前：确认版本

这一步不是仪式。**这个项目已经有四个 bug 出自同一个根因**——拿本机装的版本当成发布的版本去验证：

- 引擎本地是 0.14.0、发布的是 0.21.3，一整天的结论建立在错的源码上；
- 网页界面的账号占用在钉住的 0.7.22 上等于没做，因为它判断的信号只在本机的 0.6.5 上才成立（这个功能后来按产品决定整个去掉了，见下）；
- 那个功能的测试 mock 也是照 0.6.5 写的，于是套件替 bug 背书；
- 某次检查时，五个钉住的组件**全部**都是漂的。

```
portable\hermes\.venv\Scripts\python.exe portable\scripts\version_check.py
```

五个全 OK 再往下。有「不一致」就先对齐（约 2 分钟，不碰 `data\`）：

```
powershell -NoProfile -ExecutionPolicy Bypass -File portable\setup.ps1 -Force
```

---

## 改码 → 实测 → 发版

### 1. 在 F: 改，开分支，跑套件

**用打包解释器，不要用系统 python。** 两者的差别本身就制造过 bug：系统 python 有 PyYAML、代码页是 GBK；打包的没有、而 CI 的 runner 是 cp1252。同一个测试在三者上行为不同。

```
for %t in (portable\scripts\tests\test_*.py) do portable\hermes\.venv\Scripts\python.exe %t
```

### 2. 同步到 U 盘实测

先预演，看清楚要改什么；确认后再加 `-Apply`：

```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\sync-to-instance.ps1 -Target H:\Hermes
```

脚本保证两件以前靠人记的事：**只按字节复制**（文本管道会把中文重编码成乱码，这已经发生过一次，U 盘上所有文件的 LF 都被转成了 CRLF），**绝不碰目标的 `data\`**（用户的配置、`.env` 里的 API 密钥、聊天记录、网页登录密码都在那）。它还会拒绝不像 U-Hermes 实例的目标、用 `/E` 而非 `/MIR` 所以永远不删东西、最后在目标上跑一遍版本检查。

`runtime\` 和 `hermes\` 不同步——那是目标自己的工具链，由 `setup.ps1` 和 `versions.env` 管。

### 3. 开 PR，等 CI

### 4. 发版

改 `portable/versions.env` 的 pin（升引擎前先跑一次 canary），打 `v*` 标签，CI 自动上传 Release。

> **macOS job 会红，这是故意的。** 它卡在「校验压缩包」，正是为了拦住那个打不开的 Mac 包（见下）。Windows 照常发布。

---

## CI 在拦什么，以及为什么

每一条都对应一次真实事故，不是凭空加的。

| 检查 | 拦的是 |
|---|---|
| `version_check.py` | 构建出来的东西和 `versions.env` 不一致。曾经 setup 装的是 `@latest` 而不是 pin |
| `test_script_hygiene` | 被 shell 吃掉一层的转义。`%SCRIPT_DIR%\runtime` 里的 `\r` 变成过真的回车（终端里看不出来，因为回车把前半行盖掉了）；`setup.sh` 里一个续行符变成过字面的 `\n`，于是 bash 去执行了一个叫 `n` 的命令。**这类损坏不是语法错误**，`bash -n`、`py_compile`、YAML 解析全都放行 |
| `test_default_config` | 六处写「第一份 config.yaml」的地方互相漂移。曾经有三份不一致，其中一份让网关拿不到端口和密钥、中文技能永远加载不了 |
| `test_diagnose_rules` | 永远不会触发的诊断规则。这条不变量一加上就抓出三条从没被验证过的旧规则 |
| `test_provider_probe` | HTTP 映射表。曾经有两份几乎逐字相同的拷贝，都没有 400 分支，都把服务商自己的报错内容丢掉 |
| `test_sync_excludes` | 把数据库同步到实测实例。网页账号库在 `portable\packages\` 下，第一版排除名单里没有它，一次同步就会盖掉 U 盘上的账号和网页登录密码 |
| `test_upstream_tweaks` | 上游挪了代码，补丁跟丢。v0.21.4 把 `_cprint` 从 `cli.py` 挪进 `hermes_cli/cli_render.py`，补丁只认旧位置，于是周一的哨兵构建失败了，却没人看到（Actions 日志只留 1 天）。其中一条测试直接模拟「没有控制台」的场景，看打过补丁的函数会不会崩 |
| `test_launcher_env` | 引擎状态写到了别人电脑上。v0.21.4 起同一个系统用户只能跑一个网关，登记表默认放在 `%USERPROFILE%\.local\state\hermes`；不把 `HERMES_GATEWAY_LOCK_DIR` 指到 U 盘的话，网页界面带 `--replace` 启动网关时会关掉电脑主人自己的 Hermes，每次启动还会在那台电脑上留下文件。另外会调用引擎自身的函数，确认这个变量名确实是引擎读取的 |
| `test_prune_stale_files` + 校验里的清单比对 | 解压覆盖升级留下的旧程序文件。解压只替换、不删除，而引擎会自动加载 `tools/`、`providers/` 和插件目录里的所有模块：v0.4.3 覆盖升级到 v0.4.4 后留下 508 个旧文件，多加载了上游已删的工具和服务商，`importlib.metadata` 报的还是 0.21.3。现在每个发布包都带一份 `hermes/manifests/files-<版本>.txt`，启动器会删掉「旧清单里有、新清单里没有」的文件。这份清单必须和压缩包逐个对上：漏掉一个文件，而旧清单里恰好有它，这个文件就会从所有覆盖升级的安装里被删掉。v0.4.3 和 v0.4.4 发布时还没有清单，它们的清单是事后根据发布包生成的，存放在 `tools/release-manifests/` |
| 「校验压缩包」里的 `*.db` 检查 | 把构建机的状态打进发布包。冒烟测试会在 `portable\` 里起网页界面，于是账号库和 `.ekko\` 就留在了待打包的目录里 |
| 「校验压缩包」 | 解压之后跑不起来的包。Windows 会验证能 import 引擎、几个关键文件在不在；macOS 目前在这里失败 |

---

## 踩过的坑

**PowerShell 5.1 会把原生命令写到 stderr 的任何东西包成 ErrorRecord。** 配上 `$ErrorActionPreference = "Stop"` 就是终止错误。`get-pip.py` 每次都打印一句 "Scripts is not on PATH" 的警告——`setup.ps1` 因此在第 1/5 步就死了，**每一次、每一台 Windows**，而且退出码是 0。原生调用现在一律走 `Invoke-Native`。

同一个文件里还有：`[regex]::Replace` 没有接受次数的重载（字面量 `1` 会被绑成 `RegexOptions.IgnoreCase`）；`Set-Content -Encoding utf8` 会写 BOM；`Join-Path` 会解析 PSDrive，在盘符根上抛异常。

**「报告成功」比「不工作」更难发现。** `setup.ps1` 六个 bug 里有三个属于这类：包装进了旧 venv 然后说「All dependencies installed successfully」；在一个 0.6.5 的目录上说「installed hermes-web-ui@0.7.22」；第 1/5 步死掉然后退出 0。判断成功时要检查**做成了什么**，不是**文件在不在**。

**别拿本机装的版本当发布版。** 见上面的版本检查。

**Web UI 是 0.7.x 还是 0.6.x 行为不同。** `HERMES_WEB_UI_STOP_GATEWAYS_ON_SHUTDOWN` 只有 0.7 读。

**网页账号不在 `data\` 里。** hermes-web-ui 把 users 表放在
`<启动目录>\packages\server\data\hermes-web-ui.db`——按 `process.cwd()` 解析，
跟 `HERMES_WEB_UI_HOME` 无关，而 `Windows-Start.bat` 是在 `portable\` 里起的 node。
于是网页登录密码落在 `portable\packages\` 下，`data\` 之外。同步脚本的第一版排除
名单里没有它，照那份名单同步一次就会把开发机的账号库盖到 U 盘上；发布任务也会把
构建机的那一份打进压缩包。两处都补了，各自有测试盯着。

> 顺带更正一条旧结论：曾经写过「0.7.22 启动时就建好默认管理员，所以 hasUsers 一直
> 是 true」。实测不是——干净起一个实例，`hasUsers` 始终是 false，`admin/123456`
> 也确实能登进去（那是零账号时的引导通道）。hasUsers 之所以靠不住，是因为上面那个
> 数据库谁都不清，跑过一次就一直在。

**网页密码不再自动更换。** 产品决定让登录页上那行「默认登录名：admin，默认密码：
123456」说真话，所以 `first-login.py` 不再占账号了。**挡着它的就只剩
`BIND_HOST=127.0.0.1`**——而 `data\.env` 会覆盖它（[Windows-Start.bat:133]
在默认值之后加载），改成 `0.0.0.0` 的人等于把一个能开终端的账号连同密码一起
挂到局域网上。`0-先看我-使用说明.txt` 第五节把这一点写给用户了。

从旧版本升上来的机器例外：它们的密码已经被换过，登录页对它们是错的，所以
`first-login.py` 还留着一段——只要 `data\webui\登录密码.txt` 在，就在启动时
把真密码打出来。不会替用户改回 123456：那是在没问过的情况下削弱一台已经装好的机器。

**排查网关**：不监听 8642 时按端口找不到它，会留下孤儿进程占住锁，导致后续启动被静默跳过。要按命令行匹配实例路径清理。

**上游 hermes-agent**：所有安装入口必须设 `HERMES_NIX_BUILD=1`；这样构建出的 wheel 故意不含 web_dist/locales/skills，运行时从源码 checkout 解析，所以**必须保留 hermes-agent 源码目录在 venv 旁边**。改上游代码用 `portable/scripts/apply-upstream-tweaks.py`（锚点式、幂等），不要用行号补丁。

---

## macOS 现状

**不发布。** v0.4.0 / v0.4.1 附的 Mac 包打不开：`zip` 少了 `-y`，venv 的解释器被打包成了构建机上 python.org 框架的一份拷贝，到用户的 Mac 上一运行就找不到动态库。已经有 3 个人下载过。

要重新上架，需要按 Windows job 的做法往包里塞一份可搬迁的 Python：

- `setup.sh` 根本没有下载解释器这一步，它执行 `uv venv --python 3.11`（**硬编码，和 `versions.env` 无关**）；
- `scripts/fix-portable-paths.sh` 把 `pyvenv.cfg` 指向 `runtime/python-mac-arm64`，一个仓库里从不存在的目录；
- mac job 是 ARM 专用（`runs-on: macos-14`），而两个脚本在 Intel 上会去找 `node-mac-x64`。

在这之前，「校验压缩包」那一步会一直红，这正是它的作用。

---

## 还没做的

- 真实截图 / 录屏，换掉落地页上 CSS 画的假演示
- 开 Discussions + 中文 issue 模板
- Web 界面里的 Ekko Studio 品牌替换（要改第三方 `dist/client` 产物）
- npm 安装日志落到 `data\logs\npm-install.log`
- 日志轮转上限（报告里记过一个 4.96 GB 的单行重复日志）
- 版本检查在 release-zip 式安装上读不到引擎版本（没有 `.git`）。要补得让 CI 在构建时把实际装上的版本写进一个 lock 文件

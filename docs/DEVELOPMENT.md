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

> **引擎升过 v2026.9.24（0.21.5）时，必须同时把 Python 换成 3.14。** 上游打完 0.21.5 的标签后，main 就只支持 3.14 了：所有核心依赖都带上了 `; python_version >= '3.14'`，在我们的 3.13 上引擎能装上，但核心依赖一个都不装（只有 extras 带进来的那些），一 import 就报 `No module named 'ruamel'`。0.21.5 自己又要求 `<3.14`，两边没有共同的版本，只能在同一次改动里一起升。3.14 有嵌入版（3.14.8）。在这之前，**哨兵构建会一直红在冒烟测试第 1 步，原因就是这个**，不是噪音。

**版本机器人**：每周二 03:47 UTC，`.github/workflows/update-pins.yml` 会查五个组件的上游新版本，把能升的改进 `versions.env`，在 `auto/update-pins` 分支上开（或刷新）一个 PR，并自动开始构建。PR 里带着范围内的全部更新日志，涉及网关、默认设置、端口、登录、运行时、更新、中转等关键词的条目会挑到最上面。**它从不合并。** 规则写在 `tools/update_pins.py` 开头：网页界面跟 npm 最新；引擎取最新的、在我们的 Python 上能跑的正式版（只看 requires-python 不够，上游用依赖标记把 3.13 挡在外面过）；Node 只在当前大版本内升；uv 只在当前 0.x 线内升；Python 只升补丁版本，而且必须已经有嵌入版。其余新版本只写在「没有自动升级的」里。任何一项查询失败，这周就什么都不提，免得提议因为网络问题而变样。

- **构建结果不显示在 PR 页面上**（机器人触发的构建不挂在 PR 上），PR 正文里有链接。
- **U 盘实测要真的装上新版本**：同步脚本不碰 `runtime\` 和 `hermes\`，同步完还要在 U 盘上跑 `setup.ps1 -Force`。PR 正文里的清单写了完整步骤。
- **拒绝**：关掉 PR（issue 模式下关掉那个 issue）。完全相同的一组版本不会再提；但只要上游任何一个组件又出新版本，新的一组（可能还包括这次的某些版本）会重新提议。查询 GitHub 出错时，这一步会直接失败，而不是当成「没拒绝过」。
- **在那个分支上改东西**：只要 PR 还开着，机器人就不会动它（按作者、提交者和改动的文件判断）。PR 关掉或合并后，它会从 main 重新开始；如果你的提交没有任何 PR 保存着，它会停下并在运行记录里给出警告。
- **中途失败也能补上**：每次都会把 PR（或 issue）的说明更新到最新；每个提交只构建一次，没构建过的才会触发。
- **仓库设置**：目前 GitHub Actions 不能开 PR（Settings → Actions → General → Allow GitHub Actions to create and approve pull requests 没勾）。这种情况下机器人会推好分支，再开一个标题为「[自动] 上游有新版本，等你开 PR」的 issue。里面一键开 PR 的链接带着准确的标题，从它开出来的 PR 和机器人自己开的一样，关掉就算拒绝。机器人只认自己开的 issue，别人用同样标题开的不会被改。
- **测 U 盘前拉最新**：`git fetch origin` 后用 `git checkout -B auto/update-pins origin/auto/update-pins`。直接 `git checkout` 会停在上周拉下来的旧提议上，而版本检查对着的也是那份旧的，照样全是 OK。
- 手动运行：Actions → Update pinned versions → Run workflow（只能从 main 跑）。本地预演：`python tools\update_pins.py`（只打印，不改文件）。

**哨兵（canary）**：每周一 02:23 UTC 自动跑一次，也可以手动运行时勾选 `canary`。它用上游 main 的引擎和最新的网页界面构建，Node、Python、uv 仍按 pin。GitHub 的定时任务常常晚几个小时，偶尔会整个丢掉（10/5 那次就没跑），所以别默认它每周都有。日志保留 30 天，2026-10-07 之前只有 1 天，那时失败了也查不到原因。

> **macOS job 会红，这是故意的。** 它卡在「校验压缩包」，正是为了拦住那个打不开的 Mac 包（见下）。Windows 照常发布。

---

## CI 在拦什么，以及为什么

每一条都对应一次真实事故，不是凭空加的。

| 检查 | 拦的是 |
|---|---|
| `version_check.py` | 构建出来的东西和 `versions.env` 不一致。曾经 setup 装的是 `@latest` 而不是 pin。哨兵构建带 `--unpinned` 放过引擎和网页界面（这两个它本来就不钉），其余照查；否则哨兵每周必红，红了也说明不了任何问题。`test_version_check` 会盯着 `release.yml` 不许放过别的组件 |
| `test_script_hygiene` | 被 shell 吃掉一层的转义。`%SCRIPT_DIR%\runtime` 里的 `\r` 变成过真的回车（终端里看不出来，因为回车把前半行盖掉了）；`setup.sh` 里一个续行符变成过字面的 `\n`，于是 bash 去执行了一个叫 `n` 的命令。**这类损坏不是语法错误**，`bash -n`、`py_compile`、YAML 解析全都放行 |
| `test_default_config` | 六处写「第一份 config.yaml」的地方互相漂移。曾经有三份不一致，其中一份让网关拿不到端口和密钥、中文技能永远加载不了 |
| `test_diagnose_rules` | 永远不会触发的诊断规则。这条不变量一加上就抓出三条从没被验证过的旧规则 |
| `test_provider_probe` | HTTP 映射表。曾经有两份几乎逐字相同的拷贝，都没有 400 分支，都把服务商自己的报错内容丢掉 |
| `test_sync_excludes` | 把数据库同步到实测实例。网页账号库在 `portable\packages\` 下，第一版排除名单里没有它，一次同步就会盖掉 U 盘上的账号和网页登录密码 |
| `test_upstream_tweaks` | 上游挪了代码，补丁跟丢。v0.21.4 把 `_cprint` 从 `cli.py` 挪进 `hermes_cli/cli_render.py`，补丁只认旧位置，于是周一的哨兵构建失败了，却没人看到（当时 Actions 日志只留 1 天，现在是 30 天）。其中一条测试直接模拟「没有控制台」的场景，看打过补丁的函数会不会崩 |
| `test_launcher_env` | 引擎状态写到了别人电脑上。v0.21.4 起同一个系统用户只能跑一个网关，登记表默认放在 `%USERPROFILE%\.local\state\hermes`；不把 `HERMES_GATEWAY_LOCK_DIR` 指到 U 盘的话，网页界面带 `--replace` 启动网关时会关掉电脑主人自己的 Hermes，每次启动还会在那台电脑上留下文件。另外会调用引擎自身的函数，确认这个变量名确实是引擎读取的。**密钥被复制到别人电脑上**：v0.4.7 及以前每次运行都把 `config.yaml` 和 `.env` 复制到 `%USERPROFILE%\.hermes`，说是给「没带环境变量启动的组件」兜底。实际上这样的组件不存在：Windows 上引擎的默认目录是 `%LOCALAPPDATA%\hermes`，网页界面启动的每个 Hermes 进程都带着 `HERMES_HOME`。唯一会读这份副本的，是那台电脑主人自己装的 Hermes，它会拿 U 盘主人的密钥去用；直接关窗口时副本还会留下。现在检查两件事：任何启动器都不能写那个目录；清理旧副本的 `scripts/remove-old-host-copy.py` 必须放在启动器最前面，排在命令行分支、首次安装、启动前检查和 `protect-config.ps1` 之前，网关启动器、`debug.bat` 和 Mac 启动脚本也都要在引擎启动前调用它，菜单 [8] 要带 `--report` 调用。之所以要这么早，是因为第一版用批处理写的清理排在后面：菜单 [2] 根本走不到它，`protect-config.ps1` 又先改写了 `data\config.yaml`，结果比对不上，旧副本就留下了。那个脚本自己由 `test_remove_old_host_copy` 覆盖，只删能证明是我们的文件：v0.4.2 到 v0.4.7 有标记文件为证；除此之外，只有和这个 U 盘现在或以前的某个 config/.env（`data\backups` 里的也算）一字不差的才删，当年 v0.3.5 到 v0.4.1 追加过那段 platforms 的也算。`.env` 单独判断；挪到一边的 `*.before-u-hermes` 先判断再放回，看起来像 U-Hermes 的就不放回、只提示；清理脚本不记任何状态，每次都按磁盘上的现状从头判断。第四、五轮曾加过一个跨次运行的状态文件，第五、六轮又在它身上挖出新问题：记录会丢、会过时、会跟到别的文件上，所以最后整个拿掉了。有旧标记时，完全照旧版本自己「正常退出」的做法清理，另加三条保护：确认是我们的暂存文件直接删、不放回；暂存的一对里只要有一个是 U-Hermes 的副本（能证明、带痕迹，或者和这次按标记删掉的副本一字不差），另一个也不放回，只提示，并告诉用户如果其实是电脑自己的，改回原名即可；先删标记再放回，标记删不掉或者有文件删不掉，就这次什么都不放回，标记留着，下次再来。菜单 [8] 从不说「电脑是干净的」。不靠标记判断时，空白文件一律不算；`~/.hermes` 指向 U 盘自己的 data 时一律不动（按文件身份比对，不按路径字符串）；junction 永远不删。测试里旧版本追加的那段是照当年启动器原样写死的，不引用脚本里的常量；「删不掉」用被别的程序占着的文件来模拟，因为脚本会先去掉只读属性再删。脚本的 29 条关键规则逐条做过变异测试（另有 1 条经分析是等价改动）：每次只改坏一条，每一条都会被测试抓到。发布校验真启动时，会先放一份旧版本的带标记副本，**在网页界面运行期间**确认那个目录里没有任何密钥。之所以要在运行期间查，是因为「复制了、退出时再删」的写法到退出后查就查不出来了 |
| `test_batch_parse` + 校验里的真实启动 | 启动器被 cmd.exe 整个拒绝。v0.4.2 到 v0.4.5 的 `Windows-Start.bat` 双击就报 `reads was unexpected at this time.` 然后退出：`if ( )` 块里连着两行 `::` 注释，cmd 把 `::` 当标签，标签后面那一行会被当成命令解析，第二行注释里的 `()` 把块提前关掉了。cmd 读块时不管条件真假都会整块解析，所以每次启动都会触发。其他检查都直接跑引擎和网页界面，**从来没有人跑过用户实际双击的那个文件**。这个测试把每个启动器的每个顶层块包进 `if 1==0 ( )` 交给真的 cmd.exe 解析（只解析不执行），并禁止块里出现 `::`；发布校验的最后一步在全新解压的包上真的运行 `Windows-Start.bat`，等网页界面在 8648 返回 200，再按用户关窗的方式停掉 |
| `test_prune_stale_files` + 校验里的清单比对和清理演练 | 解压覆盖升级留下的旧程序文件。解压只替换、不删除，而引擎会自动加载 `tools/`、`providers/` 和插件目录里的所有模块：v0.4.3 覆盖升级到 v0.4.4 后留下 508 个旧文件，多加载了上游已删的工具和服务商，`importlib.metadata` 报的还是 0.21.3。每个发布包都带一份 `hermes/manifests/files-<版本>.txt`，启动器会删掉「比当前版本旧的清单里有、当前清单里没有」的文件。两轮对抗性审查用真实文件复现出的问题，决定了现在的规则：① 清单从**压缩包本身**生成，因为打包工具会跳过隐藏的 `.git`；② **venv 里只删旧引擎（`hermes_agent-*.dist-info` 的 RECORD 里列出的）自己的文件**。venv 是共用的，引擎运行时会往里装依赖，而这些依赖又依赖我们随包发的库。按路径删第三方包，两次都删坏了运行时装的包，引擎却以为它们还装着，不会重装。第三方残留本来就无害；上次真正有害的 28 个文件全是旧引擎自己的；③ 为 v0.4.3/v0.4.4 补做的历史清单（`tools/release-manifests/`，包里叫 `legacy-files-*`）每个安装只用一次：当前清单第一行会注明这个包带了哪几份历史清单，等它们全部完整读到并用完，才会留下 `.legacy-applied` 标记。这个标记不在任何压缩包里，所以之后重新解压，历史清单也不会再生效；而如果解压在历史清单写入前就中断了，就不会有标记，重新解压后它们照样能用上；⑤ 所有会加载或重装引擎的入口（主启动器、网关启动器、菜单里的各项、debug.bat）都要先执行清理，`test_launcher_env` 会检查这一点。否则菜单里的「更新/修复」会把源码目录里的旧模块重新装回 venv，新的 RECORD 就会认领它们。发布包也不再带 `hermes-agent/build/`（1875 个构建残留文件）；④ 只应用比当前版本旧的清单，清单末尾带文件数，遇到写了一半的清单就当作读不了。CI 在全新解压的包上用自带的 Python 真跑一次清理：一个文件都不能删，历史清单必须用掉，只要出现 `[!]` 警告就判失败；如果版本号不是正式格式（手动运行、哨兵），会先改成 v999.0.0，保证每次都走真实流程 |
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

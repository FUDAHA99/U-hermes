# ============================================================================
# 把 portable/ 里的代码同步到一个真实运行实例（U 盘或硬盘上的安装）
#
# 这一步以前是手动做的，有两个每次都要记住的约束：
#
#   1. 必须按字节复制。用 PowerShell 的文本管道（Get-Content | Set-Content）
#      会按当前代码页重新编码，把所有中文文案变成乱码——而这个产品的界面
#      几乎全是中文。这里一律用 robocopy。
#
#   2. 绝对不能碰目标的 data\。那里面是用户自己的 config.yaml、.env（含 API
#      密钥）、聊天记录和网页登录密码。同步代码不该动它们。
#
# 还有 runtime\ 和 hermes\：那是目标自己装好的工具链和引擎，体积几百 MB，
# 版本由 setup.ps1 / versions.env 管，不由这个脚本搬运。
#
# 用法：
#   tools\sync-to-instance.ps1 -Target H:\Hermes            # 只看要改什么
#   tools\sync-to-instance.ps1 -Target H:\Hermes -Apply     # 真的写
# ============================================================================

param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$source = Join-Path $repoRoot "portable"

if (-not (Test-Path $source)) { throw "找不到源目录：$source" }
if (-not (Test-Path $Target)) { throw "目标不存在：$Target" }

# 目标必须看起来像一个 U-Hermes 实例，否则一个手滑的路径就会往别处倒文件。
$marker = Join-Path $Target "Windows-Start.bat"
if (-not (Test-Path $marker)) {
    throw "目标不像 U-Hermes 实例（没有 Windows-Start.bat）：$Target"
}

# 目标自己的东西，一律不动。
$excludeDirs = @("data", "runtime", "hermes", "backups", ".uv-cache",
                 "__pycache__", ".git")
$excludeFiles = @("*.log", "*.pyc", "VERSION")

Write-Host ""
Write-Host "  源  ：$source"
Write-Host "  目标：$Target"
Write-Host "  不动：$($excludeDirs -join ', ')" -ForegroundColor DarkGray
Write-Host ""

$xd = @()
foreach ($d in $excludeDirs) { $xd += "/XD"; $xd += (Join-Path $source $d) }
$xf = @()
foreach ($f in $excludeFiles) { $xf += "/XF"; $xf += $f }

# /MIR would delete files the target has and the source does not -- including
# anything the user put there by hand. /E adds and updates, never deletes.
$args = @($source, $Target, "/E", "/NJH", "/NJS", "/NDL", "/NP", "/R:2", "/W:2")
if (-not $Apply) { $args += "/L" }    # /L = list only, change nothing
$args += $xd
$args += $xf

& robocopy @args | ForEach-Object {
    $line = $_.Trim()
    if ($line) { Write-Host "    $line" }
}
# robocopy: 0-7 是成功（0 = 无变化，1 = 复制了文件，...），8+ 才是失败
$code = $LASTEXITCODE
if ($code -ge 8) { throw "robocopy 失败（退出码 $code）" }

Write-Host ""
if (-not $Apply) {
    Write-Host "  以上只是预演，什么都没有改。加 -Apply 才会真的写。" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

if ($code -eq 0) {
    Write-Host "  目标已经和源一致，没有文件需要更新。" -ForegroundColor Green
} else {
    Write-Host "  同步完成。" -ForegroundColor Green
}

# 同步的是代码，不是引擎和工具链。目标的版本对不对，问它自己。
$checker = Join-Path $Target "scripts\version_check.py"
$targetPy = Join-Path $Target "hermes\.venv\Scripts\python.exe"
if ((Test-Path $checker) -and (Test-Path $targetPy)) {
    Write-Host ""
    Write-Host "  目标实例的版本：" -ForegroundColor White
    & $targetPy $checker
    if ($LASTEXITCODE -eq 3) {
        Write-Host "  ^ 组件版本和 versions.env 对不上。代码已经同步，" -ForegroundColor Yellow
        Write-Host "    但工具链要在目标上跑 setup.ps1 -Force 才会跟着走。" -ForegroundColor Yellow
    }
}
Write-Host ""

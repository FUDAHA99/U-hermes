# ============================================================================
# sync-to-instance.ps1 同步的是代码。这个测试盯住一件事：它不能把数据库
# 搬过去。
#
# 网页界面的账号表不在 data\ 下，而在
# <启动目录>\packages\server\data\hermes-web-ui.db —— hermes-web-ui 把这个
# 路径按 process.cwd() 解析，跟 HERMES_WEB_UI_HOME 无关，而 Windows-Start.bat
# 是在 portable\ 里起的 node。于是网页登录密码就落在 portable\packages\ 里。
#
# 第一版排除名单里没有它。照那份名单同步一次，就会把开发机的账号库盖到 U 盘
# 实例上 —— 正是这个脚本开头承诺不会发生的事。
# ============================================================================
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script   = Join-Path $repoRoot "tools\sync-to-instance.ps1"
$portable = Join-Path $repoRoot "portable"

$failures = @()
function Check($condition, $label) {
    if ($condition) { Write-Host "  ok   $label" }
    else { Write-Host "  FAIL $label"; $script:failures += $label }
}

# 造一个假的目标实例：脚本要求目标像个 U-Hermes 安装。
$target = Join-Path ([IO.Path]::GetTempPath()) ("sync-test-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $target | Out-Null
New-Item -ItemType File -Path (Join-Path $target "Windows-Start.bat") | Out-Null

# 造一个账号库的替身。真机上这个文件由网页界面自己生成，干净检出里没有，
# 所以这里自己放一个 —— 不然这条测试在 CI 上什么都测不到。
$dbDir = Join-Path $portable "packages\server\data"
$dbFile = Join-Path $dbDir "hermes-web-ui.db"
$madeDir = -not (Test-Path $dbDir)
$madeFile = -not (Test-Path $dbFile)
New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
if ($madeFile) { Set-Content -Path $dbFile -Value "not a real database" -Encoding ascii }

# 再放一个别处的 .db，确认拦的是类型不只是那一个目录名。
$strayDb = Join-Path $portable "scripts\__sync_test_stray.db"
Set-Content -Path $strayDb -Value "not a real database" -Encoding ascii

try {
    # 默认就是预演，什么都不会写。
    $plan = (& powershell -NoProfile -ExecutionPolicy Bypass -File $script -Target $target 2>&1) -join "`n"

    Check ($plan -notmatch "hermes-web-ui\.db") "网页账号库不在同步计划里"
    Check ($plan -notmatch "__sync_test_stray\.db") "别处的 .db 也不在同步计划里"
    Check ($plan -match "preflight\.py") "但代码照常同步（preflight.py 在计划里）"
    Check ((Get-ChildItem $target -Recurse -File).Count -eq 1) "预演没有往目标写任何东西"
}
finally {
    Remove-Item $strayDb -Force -ErrorAction SilentlyContinue
    if ($madeFile) { Remove-Item $dbFile -Force -ErrorAction SilentlyContinue }
    if ($madeDir) { Remove-Item (Join-Path $portable "packages") -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "$($failures.Count) check(s) failed"
    exit 1
}
Write-Host "sync excludes: all checks passed"

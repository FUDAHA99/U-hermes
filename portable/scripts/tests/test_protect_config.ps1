# The gateway refuses to start without a strong api_server key, and the user
# sees that as a chat box that swallows every message.  protect-config.ps1 is
# what puts the key there, so it has to cope with every shape of config.yaml
# a real install can be in -- including one the config page just rewrote.
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File portable/scripts/tests/test_protect_config.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path (Split-Path -Parent $here) 'protect-config.ps1'
$work = Join-Path $env:TEMP ("protect-config-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -Force | Out-Null

$failures = New-Object System.Collections.ArrayList

function Check([bool]$ok, [string]$label) {
    if ($ok) { Write-Host "  ok   $label" }
    else { Write-Host "  FAIL $label" -ForegroundColor Red; $failures.Add($label) | Out-Null }
}

function Run-Case([string]$name, [string]$yaml) {
    $file = Join-Path $work ($name + '.yaml')
    Set-Content -Path $file -Value $yaml -Encoding utf8 -NoNewline
    & $script -ConfigFile $file 2>&1 | Out-Null
    return $file
}

function Get-Text([string]$file) { return (Get-Content $file -Raw -Encoding utf8) }

# Ask the engine itself what key the api server would see, rather than
# looking for a `key:` line at a guessed indent. The first version of this
# helper matched `key:` at indent 4 -- which is exactly the position the
# engine ignores -- so every test passed while the gateway ran with no
# authentication at all. A test that cannot fail is worse than no test.
$portable = Split-Path -Parent (Split-Path -Parent $here)
$py = Join-Path $portable "hermes\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$reader = Join-Path $work "read_key.py"
@'
import io, os, sys, yaml
cfg = yaml.safe_load(io.open(sys.argv[1], encoding="utf-8")) or {}
block = ((cfg.get("platforms") or {}).get("api_server") or {})
try:
    from gateway.config import PlatformConfig
    extra = PlatformConfig.from_dict(block).extra
except Exception:
    extra = block.get("extra") or {}
sys.stdout.write(str(extra.get("key", "")))
'@ | Set-Content -Path $reader -Encoding utf8

function Get-ApiServerKey([string]$file) {
    $out = & $py $reader $file
    if ($LASTEXITCODE -ne 0) { return '<reader failed>' }
    return ($out | Out-String).Trim()
}

Write-Host 'the gateway block is missing entirely'
$cfg = Run-Case 'no-platforms' @"
model:
  provider: "deepseek"
  default: "deepseek-chat"
terminal:
  cwd: 'H:\U-Hermes工作区'
"@
Check ((Get-ApiServerKey $cfg).Length -ge 32) 'a key is created'
Check ((Get-Text $cfg) -match '(?m)^platforms:') 'the platforms block is created'
Check ((Get-Text $cfg) -match 'port: 8642') 'the gateway port is set'
Check ((Get-Text $cfg) -match 'U-Hermes工作区') 'non-ASCII elsewhere in the file survives'

Write-Host 'platforms exists but api_server does not'
$cfg = Run-Case 'no-api-server' @"
platforms:
  slack:
    enabled: false
model:
  provider: "deepseek"
"@
Check ((Get-ApiServerKey $cfg).Length -ge 32) 'a key is created'
Check ((Get-Text $cfg) -match 'slack:') 'the other platform survives'

Write-Host 'api_server exists with no key'
$cfg = Run-Case 'no-key' @"
platforms:
  api_server:
    enabled: true
    extra:
      port: 8642
      host: 127.0.0.1
"@
Check ((Get-ApiServerKey $cfg).Length -ge 32) 'a key is created'
Check ((Get-Text $cfg) -match 'port: 8642') 'the existing settings survive'

Write-Host 'a bot token elsewhere must not be overwritten'
$cfg = Run-Case 'bot-token' @"
platforms:
  api_server:
    enabled: true
gateway:
  platforms:
    - type: "telegram"
      key: "telegram-bot-token-that-must-not-move"
"@
$key = Get-ApiServerKey $cfg
Check ($key.Length -ge 32) 'the gateway key is created'
Check ((Get-Text $cfg) -match 'telegram-bot-token-that-must-not-move') "the bot's own key: line is untouched"
Check ($key -ne 'telegram-bot-token-that-must-not-move') 'the two keys did not get mixed up'

Write-Host 'a config that already has a real key is left alone'
$existing = 'ffffffffeeeeeeeeddddddddccccccccbbbbbbbbaaaaaaaa99999999'
$cfg = Run-Case 'has-key' @"
platforms:
  api_server:
    enabled: true
    extra:
      key: '$existing'
      port: 8642
"@
Check ((Get-ApiServerKey $cfg) -eq $existing) 'the existing key is kept'

# A key at the legacy position is the user's key, and on hermes-agent 0.21.3
# it is a WORKING one: from_dict promotes non-typed keys into extra. Minting a
# replacement would silently rotate a live credential, so it gets moved.
Write-Host 'a strong key at the legacy position is moved, not rotated'
$stray = 'aaaaaaaabbbbbbbbccccccccddddddddeeeeeeeeffffffff00000000'
$cfg = Run-Case 'stray-key' @"
platforms:
  api_server:
    enabled: true
    key: '$stray'
    extra:
      port: 8642
"@
$key = Get-ApiServerKey $cfg
Check ($key -eq $stray) 'the existing key is kept, not replaced'
$lineCount = ((Get-Text $cfg) -split "`r?`n" | Where-Object { $_ -match '^\s+key\s*:' }).Count
Check ($lineCount -eq 1) 'it ends up in exactly one place'
Check ((Get-Text $cfg) -match '^\s{6}key:' -or (Get-Text $cfg) -match "(?m)^      key:") 'and that place is under extra:'
Check ((Get-Text $cfg) -match 'port: 8642') 'the rest of extra survives'

Write-Host 'a weak key at the legacy position is replaced'
$cfg = Run-Case 'weak-key' @"
platforms:
  api_server:
    enabled: true
    key: 'short'
    extra:
      port: 8642
"@
$key = Get-ApiServerKey $cfg
Check ($key.Length -ge 32) 'a strong key is generated'
Check ($key -ne 'short') 'the weak one is not carried over'

Write-Host 'a cwd set on something other than terminal is not touched'
$cfg = Run-Case 'cwd-scope' @"
terminal:
  cwd: '.'
toolsets:
  shell:
    cwd: 'D:\MyProject-DO-NOT-TOUCH'
"@
$installed = Join-Path $work 'fakeinstall\Hermes'
New-Item -ItemType Directory -Path $installed -Force | Out-Null
& $script -ConfigFile $cfg -InstallDir $installed 2>&1 | Out-Null
Check ((Get-Text $cfg) -match 'D:\\MyProject-DO-NOT-TOUCH') "another block's cwd: is left alone"
Check ((Get-Text $cfg) -match 'U-Hermes工作区') 'terminal.cwd was filled in'

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host ("{0} check(s) failed" -f $failures.Count) -ForegroundColor Red
    exit 1
}
Write-Host 'protect-config: all checks passed' -ForegroundColor Green

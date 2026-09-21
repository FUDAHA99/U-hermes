# protect-config.ps1 — Keep config.yaml healthy across launches.
#   1. Restore custom_providers after the Web UI rewrites the file
#   2. Make sure the gateway API server has a key, which upstream now requires
#   3. Give the agent a workspace outside the install directory

param(
    [Parameter(Mandatory)][string]$ConfigFile,
    [string]$InstallDir
)

$backupFile = "$ConfigFile.providers.bak"

if (-not (Test-Path $ConfigFile)) { exit 0 }

$content = Get-Content $ConfigFile -Raw -Encoding utf8

# --- Agent workspace ------------------------------------------------------
# terminal.cwd is where the gateway, messaging and cron runs do their work.
# Upstream defaults it to "." -- the directory the launcher started from --
# which means everything the agent creates lands among the program files.
# Point it at a folder BESIDE the install instead, so a user's projects are
# never entangled with files an upgrade replaces.
#
# Only filled in when the user has not chosen one: a real path is left alone,
# so whatever they set here or in the config page wins.
if ($InstallDir) {
  try {
    $cwdMatch = [regex]::Match($content, "(?m)^(\s+)cwd:\s*(.*)$")
    $current = if ($cwdMatch.Success) { $cwdMatch.Groups[2].Value.Trim().Trim("'", '"') } else { "" }
    if ($current -in @("", ".", "./", ".\")) {
        # Unzipped straight to a drive root, which is exactly what someone
        # does with a USB stick: Split-Path THROWS on "H:" and returns an
        # empty string for "H:\", and Join-Path then throws on the empty
        # path. That killed this script before it reached the gateway key
        # below, so the install came up with no key and exited 78 every
        # launch -- with a red stack trace as the only clue.
        # [IO.Path]::Combine, not Join-Path: the cmdlet resolves PSDrives and
        # throws DriveNotFoundException for a drive that is not mounted,
        # which is not a reason to abandon the config file.
        $parent = ""
        try { $parent = Split-Path $InstallDir -Parent } catch { $parent = "" }
        if (-not $parent) { $parent = [System.IO.Path]::GetPathRoot($InstallDir) }
        if (-not $parent) { $parent = $InstallDir }
        $workspace = [System.IO.Path]::Combine($parent, "U-Hermes工作区")
        if (-not (Test-Path $workspace)) {
            New-Item -ItemType Directory -Path $workspace -Force | Out-Null
        }
        $escaped = $workspace -replace "'", "''"
        if ($cwdMatch.Success) {
            # Only the first one: a file-wide replace would also rewrite a cwd
            # set on an individual toolset or platform.
            #
            # This has to be the INSTANCE method. [regex]::Replace has no
            # (input, pattern, replacement, count) overload -- PowerShell
            # binds a literal 1 to RegexOptions instead, where it means
            # IgnoreCase, and every match gets replaced after all.
            $cwdRegex = [regex]"(?m)^(\s+)cwd:\s*.*$"
            $content = $cwdRegex.Replace($content, "`${1}cwd: '$escaped'", 1)
        } elseif ($content -match '(?m)^terminal:\s*$') {
            $content = $content -replace "(?m)^(terminal:\s*)$", "`${1}`n  cwd: '$escaped'"
        } else {
            $content = $content.TrimEnd() + "`nterminal:`n  cwd: '$escaped'`n"
        }
        Set-Content -Path $ConfigFile -Value $content -Encoding utf8 -NoNewline
        Write-Host "  [OK] Agent workspace set to $workspace" -ForegroundColor Green
    }
  } catch {
    # Never fatal. Whatever went wrong choosing a folder, the gateway key
    # below is what decides whether the product can answer a message.
    Write-Host "  [!] Could not set the agent workspace ($($_.Exception.GetType().Name)); continuing." -ForegroundColor Yellow
  }
}

# --- Gateway API key ------------------------------------------------------
# Since hermes-agent 0.21 the api_server platform refuses to start without a
# strong key, "including loopback-only binds on 127.0.0.1". Generate one per
# install on first run so the gateway comes up without the user doing anything.
#
# This walks the file line by line instead of running a regex over the whole
# thing, for two reasons.  `key:` also appears under gateway.platforms for bot
# tokens, and a file-wide replace would overwrite those with the gateway's
# key.  And the block itself may be missing -- an older install, or a config
# page that wrote a fresh file -- in which case the previous version of this
# script quietly did nothing and the gateway exited with code 78 on every
# launch after that.
$eol = "`n"
if ($content -match "`r`n") { $eol = "`r`n" }
$lines = [System.Collections.ArrayList]@($content -split "`r?`n")

function Get-Indent([string]$line) {
    if ($line -match '^(\s*)') { return $Matches[1].Length }
    return 0
}

# Index of a key at a given indent, searched inside [start, end).
function Find-Key {
    param([System.Collections.ArrayList]$Lines, [string]$Name, [int]$Indent, [int]$Start, [int]$End)
    for ($i = $Start; $i -lt $End; $i++) {
        $line = $Lines[$i]
        if ($line.Trim() -eq '' -or $line.Trim().StartsWith('#')) { continue }
        $ind = Get-Indent $line
        if ($ind -lt $Indent) { break }
        if ($ind -eq $Indent -and $line -match "^\s*$([regex]::Escape($Name))\s*:") { return $i }
    }
    return -1
}

# Where a block's children stop.
function Find-BlockEnd {
    param([System.Collections.ArrayList]$Lines, [int]$Start, [int]$Indent)
    for ($i = $Start + 1; $i -lt $Lines.Count; $i++) {
        $line = $Lines[$i]
        if ($line.Trim() -eq '' -or $line.Trim().StartsWith('#')) { continue }
        if ((Get-Indent $line) -le $Indent) { return $i }
    }
    return $Lines.Count
}

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$newKey = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
$changed = $false

# The key belongs under `extra:`. The engine reads it as
# `extra.get("key", os.getenv("API_SERVER_KEY", ""))`, and a keyless
# api_server refuses to start rather than running open.
#
# Where a bare `platforms.api_server.key` ends up depends on the engine
# version, so this must never assume:
#   0.14.0  PlatformConfig.from_dict keeps only the fields it knows plus
#           `extra`, so a bare key is dropped and the gateway has none.
#   0.21.3  from_dict promotes every non-typed key into extra
#           (gateway/config.py: `{**{k: v for k, v in data.items()
#           if k not in cls._TYPED_KEYS}, **extra}`), so a bare key WORKS.
# Therefore a bare key that is already strong gets MOVED, never replaced:
# regenerating it would silently rotate a working credential out from under
# whatever client is using it.
$platformsIdx = Find-Key -Lines $lines -Name 'platforms' -Indent 0 -Start 0 -End $lines.Count
if ($platformsIdx -lt 0) {
    $block = @(
        '', 'platforms:', '  api_server:', '    enabled: true',
        '    extra:', "      key: '$newKey'", '      port: 8642', '      host: 127.0.0.1'
    )
    while ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -eq '') {
        $lines.RemoveAt($lines.Count - 1) | Out-Null
    }
    $lines.AddRange($block) | Out-Null
    $changed = $true
    Write-Host "  [OK] Added the gateway api_server block with a key." -ForegroundColor Green
} else {
    $platformsEnd = Find-BlockEnd -Lines $lines -Start $platformsIdx -Indent 0
    $apiIdx = Find-Key -Lines $lines -Name 'api_server' -Indent 2 -Start ($platformsIdx + 1) -End $platformsEnd
    if ($apiIdx -lt 0) {
        $block = @(
            '  api_server:', '    enabled: true',
            '    extra:', "      key: '$newKey'", '      port: 8642', '      host: 127.0.0.1'
        )
        $lines.InsertRange($platformsIdx + 1, $block) | Out-Null
        $changed = $true
        Write-Host "  [OK] Added the gateway api_server block with a key." -ForegroundColor Green
    } else {
        $apiEnd = Find-BlockEnd -Lines $lines -Start $apiIdx -Indent 2

        # A key at the legacy position (a sibling of enabled:, indent 4).
        # 0.21.3 reads it; 0.14.0 does not. Either way it is the user's key.
        $strayIdx = Find-Key -Lines $lines -Name 'key' -Indent 4 -Start ($apiIdx + 1) -End $apiEnd
        $stray = ''
        if ($strayIdx -ge 0 -and $lines[$strayIdx] -match '^\s*key\s*:\s*(.*)$') {
            $stray = $Matches[1].Trim().Trim("'", '"')
        }

        $extraIdx = Find-Key -Lines $lines -Name 'extra' -Indent 4 -Start ($apiIdx + 1) -End $apiEnd
        $keyIdx = -1
        $current = ''
        if ($extraIdx -ge 0) {
            $extraEnd = Find-BlockEnd -Lines $lines -Start $extraIdx -Indent 4
            $keyIdx = Find-Key -Lines $lines -Name 'key' -Indent 6 -Start ($extraIdx + 1) -End $extraEnd
            if ($keyIdx -ge 0 -and $lines[$keyIdx] -match '^\s*key\s*:\s*(.*)$') {
                $current = $Matches[1].Trim().Trim("'", '"')
            }
        }

        if ($current.Length -ge 32) {
            # Already correct. extra wins over a sibling on this engine, so a
            # leftover stray is inert -- drop it rather than leave two keys.
            if ($strayIdx -ge 0) {
                $lines.RemoveAt($strayIdx) | Out-Null
                $changed = $true
                Write-Host "  [OK] Removed a duplicate gateway key outside extra." -ForegroundColor Green
            }
        } else {
            # MOVE a strong legacy key; only mint a new one when there is none.
            $keyValue = $newKey
            $moved = $false
            if ($stray.Length -ge 32) { $keyValue = $stray; $moved = $true }

            if ($extraIdx -lt 0) {
                $lines.InsertRange($apiIdx + 1, @('    extra:', "      key: '$keyValue'")) | Out-Null
            } elseif ($keyIdx -ge 0) {
                $lines[$keyIdx] = "      key: '$keyValue'"
            } else {
                $lines.Insert($extraIdx + 1, "      key: '$keyValue'") | Out-Null
            }
            # Re-locate the stray: the inserts above may have shifted it.
            $apiEnd = Find-BlockEnd -Lines $lines -Start $apiIdx -Indent 2
            $strayIdx = Find-Key -Lines $lines -Name 'key' -Indent 4 -Start ($apiIdx + 1) -End $apiEnd
            if ($strayIdx -ge 0) { $lines.RemoveAt($strayIdx) | Out-Null }

            $changed = $true
            if ($moved) {
                Write-Host "  [OK] Moved the existing gateway key to where the engine reads it." -ForegroundColor Green
            } else {
                Write-Host "  [OK] Generated a gateway API key for this install." -ForegroundColor Green
            }
        }
    }
}

if ($changed) {
    $content = ($lines -join $eol)
    Set-Content -Path $ConfigFile -Value $content -Encoding utf8 -NoNewline
}

# Check if config has custom_providers with actual entries
$hasProviders = $content -match 'custom_providers:\s*\r?\n\s+-\s+name:'

if ($hasProviders) {
    # Config looks good — save a backup of the full config
    Copy-Item $ConfigFile $backupFile -Force
    exit 0
}

# Config is missing custom_providers — check if we have a backup
if (-not (Test-Path $backupFile)) {
    # No backup either, nothing we can do
    exit 0
}

Write-Host "  [i] Detected config overwrite by Web UI, restoring providers..." -ForegroundColor Cyan

$backup = Get-Content $backupFile -Raw -Encoding utf8

# Extract custom_providers block from backup (from "custom_providers:" to end or next top-level key)
if ($backup -match '(?ms)(custom_providers:\s*\r?\n(?:\s+.+\r?\n)+)') {
    $providersBlock = $Matches[1]
} else {
    Write-Host "  [!] Backup has no custom_providers, skipping." -ForegroundColor Yellow
    exit 0
}

# Extract model section from backup
$backupModelProvider = ""
$backupModelDefault = ""
if ($backup -match '(?m)^model:\s*\r?\n\s+default:\s*(.+)\r?\n\s+provider:\s*(.+)') {
    $backupModelDefault = $Matches[1].Trim().Trim('"', "'")
    $backupModelProvider = $Matches[2].Trim().Trim('"', "'")
}

# Fix model.provider and model.default in current config
if ($backupModelProvider -and $content -match '(?m)^model:\s*\r?\n\s+default:\s*.+\r?\n\s+provider:\s*.+') {
    $content = $content -replace '(?m)(^model:\s*\r?\n\s+default:\s*).+(\r?\n\s+provider:\s*).+', "`${1}$backupModelDefault`${2}$backupModelProvider"
}

# Append custom_providers if not present
if ($content -notmatch 'custom_providers:') {
    $content = $content.TrimEnd() + "`n" + $providersBlock
}

Set-Content -Path $ConfigFile -Value $content -Encoding utf8 -NoNewline
Write-Host "  [OK] Providers and model config restored." -ForegroundColor Green

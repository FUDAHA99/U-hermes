# fix-portable-paths.ps1 — Fix stale paths in portable venv after moving to a new machine
# The CI build creates the venv on D:\a\..., but the user runs from G:\Hermes\ etc.
# This script fixes: pyvenv.cfg base path + pip exe wrappers (hermes.exe etc.)

param(
    [Parameter(Mandatory)][string]$VenvDir,
    [Parameter(Mandatory)][string]$RuntimeDir,
    [Parameter(Mandatory)][string]$AgentDir,
    [string]$UvExe
)

$scriptsDir = Join-Path $VenvDir "Scripts"
$correctPython = Join-Path $scriptsDir "python.exe"
$correctHome = Join-Path $RuntimeDir "python-win-x64"
$cfgFile = Join-Path $VenvDir "pyvenv.cfg"

if (-not (Test-Path $correctPython)) { exit 0 }
if (-not (Test-Path $cfgFile)) { exit 0 }

# --- Step 1: Check if pyvenv.cfg home path is correct ---
$cfg = Get-Content $cfgFile -Raw
$homeMatch = [regex]::Match($cfg, 'home = (.+)')
if (-not $homeMatch.Success) { exit 0 }

$currentHome = $homeMatch.Groups[1].Value.Trim()
if ($currentHome -eq $correctHome) {
    # Paths are already correct — no fix needed
    exit 0
}

Write-Host "  [i] Fixing portable paths (first run on this machine)..." -ForegroundColor Cyan

# --- Step 2: Fix pyvenv.cfg ---
$cfg = $cfg -replace 'home = .+', "home = $correctHome"
Set-Content -Path $cfgFile -Value $cfg.TrimEnd() -Encoding utf8 -NoNewline
Write-Host "  [OK] Fixed venv config." -ForegroundColor Green

# --- Step 3: Patch embedded Python paths in uv/pip-generated .exe launchers ---
# These .exe files contain: <launcher binary><zip with __main__.py><python_path_utf8><path_length_u32_le><UVSC magic>
# When the drive letter changes, the embedded path becomes stale and "Failed to canonicalize script path" occurs.
$MAGIC = [System.Text.Encoding]::ASCII.GetBytes("UVSC")
$patchCount = 0

foreach ($exePath in (Get-ChildItem $scriptsDir -Filter "*.exe")) {
    $data = [System.IO.File]::ReadAllBytes($exePath.FullName)
    $len = $data.Length
    if ($len -lt 12) { continue }

    # Check for UVSC magic at end
    $tail4 = $data[($len-4)..($len-1)]
    if ([System.Text.Encoding]::ASCII.GetString($tail4) -ne "UVSC") { continue }

    # Read path length (u32 LE) at offset -8
    $pathLen = [BitConverter]::ToUInt32($data, $len - 8)
    if ($pathLen -lt 5 -or $pathLen -gt 1024) { continue }

    # Read old embedded path
    $pathStart = $len - 8 - $pathLen
    if ($pathStart -lt 0) { continue }
    $oldPathBytes = $data[$pathStart..($pathStart + $pathLen - 1)]
    $oldPath = [System.Text.Encoding]::UTF8.GetString($oldPathBytes)

    # Determine correct target (pythonw.exe for pythonw-related, python.exe for all others)
    if ($oldPath -match 'pythonw\.exe$') {
        $newPath = Join-Path $scriptsDir "pythonw.exe"
    } else {
        $newPath = $correctPython
    }

    if ($oldPath -eq $newPath) { continue }

    # Build patched binary: prefix + new_path + new_length + MAGIC
    $newPathBytes = [System.Text.Encoding]::UTF8.GetBytes($newPath)
    $newLenBytes = [BitConverter]::GetBytes([uint32]$newPathBytes.Length)
    $prefix = New-Object byte[] $pathStart
    [Array]::Copy($data, $prefix, $pathStart)

    $newData = New-Object byte[] ($pathStart + $newPathBytes.Length + 4 + 4)
    [Array]::Copy($prefix, 0, $newData, 0, $pathStart)
    [Array]::Copy($newPathBytes, 0, $newData, $pathStart, $newPathBytes.Length)
    [Array]::Copy($newLenBytes, 0, $newData, $pathStart + $newPathBytes.Length, 4)
    [Array]::Copy($MAGIC, 0, $newData, $pathStart + $newPathBytes.Length + 4, 4)

    [System.IO.File]::WriteAllBytes($exePath.FullName, $newData)
    $patchCount++
}

if ($patchCount -gt 0) {
    Write-Host "  [OK] Patched $patchCount exe launcher(s) with correct Python path." -ForegroundColor Green
} else {
    Write-Host "  [OK] All exe launchers already have correct paths." -ForegroundColor Green
}

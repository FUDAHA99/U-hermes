# fix-portable-paths.ps1 — Make the portable venv work from wherever it was copied to.
#
# CI builds the venv under D:\a\..., users run it from H:\Hermes\ or a different
# drive letter every time. Three things have to be repaired on arrival:
#   1. pyvenv.cfg "home", so the venv finds its base interpreter
#   2. a ._pth beside the venv's python.exe, so the embedded interpreter does NOT
#      fall back to a Python installed on the host machine
#   3. the absolute python path baked into pip/uv console-script .exe launchers
#
# Every step is idempotent, and none may early-exit the others: step 2 in
# particular has to run even when the paths already look right, because whether
# it matters depends on the host, not on us.

param(
    [Parameter(Mandatory)][string]$VenvDir,
    [Parameter(Mandatory)][string]$RuntimeDir,
    [Parameter(Mandatory)][string]$AgentDir,
    [string]$UvExe
)

$scriptsDir = Join-Path $VenvDir "Scripts"
$correctPython = Join-Path $scriptsDir "python.exe"
$baseDir = Join-Path $RuntimeDir "python-win-x64"
$cfgFile = Join-Path $VenvDir "pyvenv.cfg"

if (-not (Test-Path $correctPython)) { exit 0 }
if (-not (Test-Path $cfgFile)) { exit 0 }

# --- Step 1: point pyvenv.cfg at the bundled interpreter -------------------
$cfg = Get-Content $cfgFile -Raw
$homeMatch = [regex]::Match($cfg, 'home = (.+)')
if ($homeMatch.Success -and $homeMatch.Groups[1].Value.Trim() -ne $baseDir) {
    Write-Host "  [i] Repairing portable paths for this machine..." -ForegroundColor Cyan
    $cfg = $cfg -replace 'home = .+', "home = $baseDir"
    Set-Content -Path $cfgFile -Value $cfg.TrimEnd() -Encoding utf8 -NoNewline
    Write-Host "  [OK] venv base interpreter path fixed." -ForegroundColor Green
}

# --- Step 2: isolate from any Python installed on the host -----------------
# Without a ._pth next to it, the embedded interpreter falls back to the registry
# and can load a same-version Python from the host (e.g. D:\Python\Lib + DLLs).
# Mixing that stdlib and those native DLLs with our interpreter crashes with an
# access violation, and on a host with no Python at all it fails outright. The
# ._pth shipped in runtime\python-win-x64 does not cover the venv, because the
# interpreter looks for it beside its own executable.
$pyDll = Get-ChildItem $baseDir -Filter "python3*.dll" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^python(\d)(\d+)\.dll$' } | Select-Object -First 1
if ($pyDll) {
    $tag = [System.IO.Path]::GetFileNameWithoutExtension($pyDll.Name)  # e.g. python313
    $pthFile = Join-Path $scriptsDir "$tag._pth"

    # Absolute paths: this script runs on every launch, so it simply rewrites the
    # file whenever the install moves -- cheaper to get right than relative arithmetic.
    $lines = @(
        (Join-Path $baseDir "$tag.zip"),
        $baseDir,
        (Join-Path $baseDir "DLLs"),
        (Join-Path $VenvDir "Lib\site-packages"),
        $scriptsDir,
        $VenvDir,
        "import site"
    )
    $desired = ($lines -join "`r`n") + "`r`n"
    $existing = if (Test-Path $pthFile) { Get-Content $pthFile -Raw } else { "" }
    if ($existing -ne $desired) {
        Set-Content -Path $pthFile -Value $desired -Encoding ascii -NoNewline
        Write-Host "  [OK] Isolated bundled Python from any host installation." -ForegroundColor Green
    }
}

# --- Step 3: repair console-script .exe launchers --------------------------
# uv <=0.7 trampolines carry <python_path><len u32><"UVSC"> at the end of the file.
# Newer uv releases use a different layout we do not rewrite; the launcher points
# HERMES_BIN at hermes.cmd instead, which resolves python.exe relative to itself
# and therefore never goes stale.
$MAGIC = [System.Text.Encoding]::ASCII.GetBytes("UVSC")
$patchCount = 0

foreach ($exePath in (Get-ChildItem $scriptsDir -Filter "*.exe" -ErrorAction SilentlyContinue)) {
    $data = [System.IO.File]::ReadAllBytes($exePath.FullName)
    $len = $data.Length
    if ($len -lt 12) { continue }

    if ([System.Text.Encoding]::ASCII.GetString($data[($len-4)..($len-1)]) -ne "UVSC") { continue }

    $pathLen = [BitConverter]::ToUInt32($data, $len - 8)
    if ($pathLen -lt 5 -or $pathLen -gt 1024) { continue }

    $pathStart = $len - 8 - $pathLen
    if ($pathStart -lt 0) { continue }
    $oldPath = [System.Text.Encoding]::UTF8.GetString($data[$pathStart..($pathStart + $pathLen - 1)])

    $newPath = if ($oldPath -match 'pythonw\.exe$') { Join-Path $scriptsDir "pythonw.exe" } else { $correctPython }
    if ($oldPath -eq $newPath) { continue }

    $newPathBytes = [System.Text.Encoding]::UTF8.GetBytes($newPath)
    $newLenBytes = [BitConverter]::GetBytes([uint32]$newPathBytes.Length)
    $newData = New-Object byte[] ($pathStart + $newPathBytes.Length + 8)
    [Array]::Copy($data, 0, $newData, 0, $pathStart)
    [Array]::Copy($newPathBytes, 0, $newData, $pathStart, $newPathBytes.Length)
    [Array]::Copy($newLenBytes, 0, $newData, $pathStart + $newPathBytes.Length, 4)
    [Array]::Copy($MAGIC, 0, $newData, $pathStart + $newPathBytes.Length + 4, 4)

    [System.IO.File]::WriteAllBytes($exePath.FullName, $newData)
    $patchCount++
}

if ($patchCount -gt 0) {
    Write-Host "  [OK] Repaired $patchCount console-script launcher(s)." -ForegroundColor Green
}

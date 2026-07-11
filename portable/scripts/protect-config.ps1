# protect-config.ps1 — Protect custom_providers from Web UI config overwrites
# The Web UI settings page does a full rewrite of config.yaml, wiping custom_providers.
# This script saves a backup of the providers section and restores it if overwritten.

param(
    [Parameter(Mandatory)][string]$ConfigFile
)

$backupFile = "$ConfigFile.providers.bak"

if (-not (Test-Path $ConfigFile)) { exit 0 }

$content = Get-Content $ConfigFile -Raw -Encoding utf8

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

"""
U-Hermes Device Fingerprint Module
Generates a stable hardware fingerprint for Xiapan Cloud API key binding.

Ported from U-Claw's fingerprint.mjs to Python.

Fingerprint sources (in priority order):
  Windows: USB drive (when running from USB) -> system disk + board -> seed file
  Mac:     Hardware UUID + boot volume UUID   -> seed file
  Linux:   /etc/machine-id + root disk serial -> seed file
"""

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def normalize_serial(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value.strip().rstrip(". ")).upper()


def get_seed_path(app_root: Path) -> Path:
    env_path = os.environ.get("UHERMES_SEED_PATH", "").strip()
    if env_path:
        return Path(env_path).resolve()
    home = Path.home()
    if home:
        return home / ".uhermes" / ".usb_seed"
    return app_root / ".usb_seed"


def read_or_create_seed(app_root: Path) -> dict:
    seed_path = get_seed_path(app_root)
    seed_hex = ""
    if seed_path.exists():
        seed_hex = seed_path.read_text(encoding="utf-8").strip()
    if not seed_hex or not re.match(r"^[0-9a-f]{64}$", seed_hex, re.IGNORECASE):
        seed_hex = secrets.token_hex(32)
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(seed_hex + "\n", encoding="utf-8")
    return {"source": "seed", "fingerprint": seed_hex.lower()}


def run_powershell(script: str) -> str:
    wrapped = f"$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {script}"
    candidates = [
        "powershell.exe",
        "powershell",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "pwsh.exe",
    ]
    for cmd in candidates:
        try:
            result = subprocess.run(
                [cmd, "-NoProfile", "-Command", wrapped],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return result.stdout
        except (FileNotFoundError, OSError):
            continue
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError("PowerShell not available")


def get_drive_depth(target_dir: Path) -> dict | None:
    resolved = target_dir.resolve()
    drive_root = resolved.anchor.rstrip("\\/").upper()
    if not drive_root:
        return None
    try:
        rel = resolved.relative_to(resolved.anchor)
        depth = len(rel.parts)
    except ValueError:
        depth = 0
    return {"drive_root": drive_root, "depth": depth}


# ============================================================================
# Windows fingerprint
# ============================================================================

def try_windows_usb_fingerprint(app_root: Path) -> dict | None:
    drive = get_drive_depth(app_root)
    if not drive:
        return None
    if drive["depth"] > 2:
        return None

    drive_letter = drive["drive_root"]
    if not drive_letter.endswith(":"):
        drive_letter += ":"

    # Find PNP device ID for the drive
    mapping_script = (
        f"$p = Get-WmiObject -Query \"ASSOCIATORS OF {{Win32_LogicalDisk.DeviceID='{drive_letter}'}} "
        f"WHERE AssocClass=Win32_LogicalDiskToPartition\"; "
        f"$p0 = if ($p -is [System.Array]) {{ $p[0] }} else {{ $p }}; "
        f"$d = if ($p0) {{ Get-WmiObject -Query \"ASSOCIATORS OF {{Win32_DiskPartition.DeviceID='$($p0.DeviceID)'}} "
        f"WHERE AssocClass=Win32_DiskDriveToDiskPartition\" }} else {{ $null }}; "
        f"$d0 = if ($d -is [System.Array]) {{ $d[0] }} else {{ $d }}; "
        f"if ($d0) {{ $d0.PNPDeviceID }} else {{ '' }}"
    )

    try:
        target_pnp = run_powershell(mapping_script).strip().upper()
    except Exception:
        target_pnp = ""

    # Get all disk drives
    try:
        raw = run_powershell(
            "Get-WmiObject Win32_DiskDrive | Select-Object Model, SerialNumber, PNPDeviceID | ConvertTo-Json -Compress"
        )
        parsed = json.loads(raw.strip() or "[]")
        disks = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return None

    if not disks:
        return None

    # Find USB disk
    exact_match = None
    if target_pnp:
        exact_match = next(
            (d for d in disks if (d.get("PNPDeviceID") or "").upper() == target_pnp),
            None
        )

    usb_disk = exact_match or next(
        (d for d in disks if "USB" in (d.get("PNPDeviceID") or "").upper()),
        None
    )

    if not usb_disk:
        return None

    model = (usb_disk.get("Model") or "Unknown").strip()
    serial = (usb_disk.get("SerialNumber") or "Unknown").strip()
    pnp = (usb_disk.get("PNPDeviceID") or "Unknown").strip()

    return {"source": "usb", "fingerprint": sha256_hex(f"{model}:{serial}:{pnp}")}


def try_windows_disk_fingerprint() -> dict | None:
    # Try Get-PhysicalDisk first (newer Windows)
    physical_script = (
        "$systemDrive = ($env:SystemDrive -replace ':',''); "
        "if (-not $systemDrive) { $systemDrive = 'C' }; "
        "$disk = Get-Partition -DriveLetter $systemDrive -ErrorAction SilentlyContinue | "
        "Get-Disk -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if (-not $disk) { return }; "
        "$pd = Get-PhysicalDisk -DeviceNumber $disk.Number -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if (-not $pd) { return }; "
        "[pscustomobject]@{ Serial = $pd.SerialNumber; Model = $pd.FriendlyName; BusType = $pd.BusType } | ConvertTo-Json -Compress"
    )

    disk_info = None
    try:
        raw = run_powershell(physical_script).strip()
        if raw:
            disk_info = json.loads(raw)
    except Exception:
        pass

    # Fallback to WMI
    if not disk_info:
        wmi_script = (
            "$systemDrive = $env:SystemDrive -replace ':',''; "
            "if (-not $systemDrive) { $systemDrive = 'C' }; "
            "$letter = \"$systemDrive`:\"; "
            "$lp = Get-WmiObject -Query \"ASSOCIATORS OF {Win32_LogicalDisk.DeviceID='$letter'} "
            "WHERE AssocClass=Win32_LogicalDiskToPartition\"; "
            "$lp0 = if ($lp -is [System.Array]) { $lp[0] } else { $lp }; "
            "if (-not $lp0) { return }; "
            "$dd = Get-WmiObject -Query \"ASSOCIATORS OF {Win32_DiskPartition.DeviceID='$($lp0.DeviceID)'} "
            "WHERE AssocClass=Win32_DiskDriveToDiskPartition\"; "
            "$dd0 = if ($dd -is [System.Array]) { $dd[0] } else { $dd }; "
            "if (-not $dd0) { return }; "
            "[pscustomobject]@{ Serial = $dd0.SerialNumber; Model = $dd0.Model; BusType = $dd0.InterfaceType } | ConvertTo-Json -Compress"
        )
        try:
            raw = run_powershell(wmi_script).strip()
            if raw:
                disk_info = json.loads(raw)
        except Exception:
            pass

    if not disk_info:
        return None

    # Get motherboard serial
    board_serial = "NoBoard"
    try:
        raw = run_powershell("(Get-CimInstance Win32_BaseBoard | Select-Object -First 1).SerialNumber")
        board_serial = normalize_serial(raw) or "NoBoard"
    except Exception:
        pass

    disk_serial = normalize_serial(disk_info.get("Serial", ""))
    disk_model = str(disk_info.get("Model", "Unknown")).strip()

    return {"source": "disk", "fingerprint": sha256_hex(f"DISK:{disk_serial}:{disk_model}:{board_serial}")}


# ============================================================================
# Mac fingerprint
# ============================================================================

def try_mac_fingerprint() -> dict | None:
    hardware_uuid = ""
    try:
        result = subprocess.run(
            ["/usr/sbin/system_profiler", "SPHardwareDataType"],
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"Hardware UUID:\s*([0-9A-F-]+)", result.stdout, re.IGNORECASE)
        if match:
            hardware_uuid = match.group(1).strip().upper()
    except Exception:
        pass

    boot_volume_uuid = ""
    try:
        result = subprocess.run(
            ["/usr/sbin/diskutil", "info", "/"],
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"Volume UUID:\s*([0-9A-F-]+)", result.stdout, re.IGNORECASE)
        if match:
            boot_volume_uuid = match.group(1).strip().upper()
    except Exception:
        pass

    if not hardware_uuid and not boot_volume_uuid:
        return None

    return {"source": "mac", "fingerprint": sha256_hex(f"MAC:{hardware_uuid}:{boot_volume_uuid}")}


# ============================================================================
# Linux fingerprint
# ============================================================================

def try_linux_fingerprint() -> dict | None:
    machine_id = ""
    for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
        try:
            machine_id = Path(path).read_text().strip()
            if machine_id:
                break
        except Exception:
            continue

    root_serial = ""
    try:
        result = subprocess.run(
            ["/bin/lsblk", "-no", "SERIAL,MOUNTPOINT"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "/":
                root_serial = parts[0]
                break
    except Exception:
        pass

    if not machine_id and not root_serial:
        return None

    return {"source": "linux", "fingerprint": sha256_hex(f"LINUX:{machine_id}:{root_serial}")}


# ============================================================================
# Public API
# ============================================================================

def get_fingerprint(app_root: Path | None = None) -> dict:
    """
    Get device fingerprint. Returns dict with 'source' and 'fingerprint' keys.

    Sources (in priority order):
      - 'usb': Running from a USB drive (Windows)
      - 'disk': System disk + motherboard (Windows)
      - 'mac': Hardware UUID + boot volume (macOS)
      - 'linux': machine-id + root disk serial (Linux)
      - 'seed': Random seed file (fallback for all platforms)
    """
    if app_root is None:
        app_root = Path(__file__).resolve().parent.parent

    # Check env overrides
    if os.environ.get("UHERMES_SKIP_FINGERPRINT") == "1":
        return {"source": "test", "fingerprint": sha256_hex("TEST:UHERMES_DEVELOPMENT")}

    override = os.environ.get("UHERMES_FINGERPRINT_OVERRIDE", "").strip()
    if override and re.match(r"^[0-9a-f]{64}$", override, re.IGNORECASE):
        return {"source": "test", "fingerprint": override.lower()}

    # Platform-specific fingerprint
    if sys.platform == "win32":
        # Try USB first
        result = try_windows_usb_fingerprint(app_root)
        if result:
            return result
        # Then system disk
        result = try_windows_disk_fingerprint()
        if result:
            return result

    elif sys.platform == "darwin":
        result = try_mac_fingerprint()
        if result:
            return result

    else:  # Linux
        result = try_linux_fingerprint()
        if result:
            return result

    # Fallback: seed file
    return read_or_create_seed(app_root)


def generate_api_key(fingerprint: str) -> str:
    """Generate a sk-... style API key from fingerprint hash."""
    # Use first 48 chars of fingerprint as the key body
    return f"sk-xp-{fingerprint[:48]}"


if __name__ == "__main__":
    result = get_fingerprint()
    print(f"Source: {result['source']}")
    print(f"Fingerprint: {result['fingerprint']}")
    print(f"API Key: {generate_api_key(result['fingerprint'])}")

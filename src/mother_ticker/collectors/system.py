# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Host facts: uptime, load, memory, disk, SoC temperature, throttle flags, overlay root."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

from mother_ticker.collectors.model import SystemStatus

PROC = Path("/proc")
THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")

# Bits of `vcgencmd get_throttled`, from the Raspberry Pi documentation.
THROTTLE_BITS: dict[int, str] = {
    0: "under-voltage now",
    1: "arm frequency capped now",
    2: "throttled now",
    3: "soft temperature limit now",
    16: "under-voltage occurred",
    17: "arm frequency capped occurred",
    18: "throttled occurred",
    19: "soft temperature limit occurred",
}


def parse_meminfo(text: str) -> tuple[int, int]:
    total = available = 0
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key == "MemTotal":
            total = int(rest.split()[0])
        elif key == "MemAvailable":
            available = int(rest.split()[0])
    return total, available


def parse_throttled(text: str) -> int | None:
    """`throttled=0x50000` to 0x50000."""
    _, _, value = text.strip().partition("=")
    try:
        return int(value, 16)
    except ValueError:
        return None


def throttle_reasons(flags: int | None) -> list[str]:
    if not flags:
        return []
    return [label for bit, label in THROTTLE_BITS.items() if flags & (1 << bit)]


def is_overlay_root(cmdline: str) -> bool:
    """Raspberry Pi OS's raspi-config overlay adds `boot=overlay` to the kernel command line."""
    return "boot=overlay" in cmdline.split()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _throttled() -> int | None:
    if shutil.which("vcgencmd") is None:
        return None
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_throttled(result.stdout)


def collect_system(proc: Path = PROC, thermal: Path = THERMAL, root: str = "/") -> SystemStatus:
    uptime_text = _read(proc / "uptime") or "0 0"
    load_text = _read(proc / "loadavg") or "0 0 0 0/0 0"
    mem_total, mem_avail = parse_meminfo(_read(proc / "meminfo") or "")
    usage = shutil.disk_usage(root)
    temp_text = _read(thermal)
    temperature = float(temp_text.strip()) / 1000.0 if temp_text else None
    loads = load_text.split()
    return SystemStatus(
        hostname=socket.gethostname(),
        kernel=os.uname().release,
        uptime_s=float(uptime_text.split()[0]),
        load_1=float(loads[0]),
        load_5=float(loads[1]),
        load_15=float(loads[2]),
        mem_total_kb=mem_total,
        mem_available_kb=mem_avail,
        disk_total_b=usage.total,
        disk_used_b=usage.used,
        temperature_c=temperature,
        throttled_flags=_throttled(),
        overlay_root=is_overlay_root(_read(proc / "cmdline") or ""),
    )

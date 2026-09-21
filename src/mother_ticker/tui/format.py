# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Small pure formatting helpers shared by screens, kept out of the widgets so they are testable."""

from __future__ import annotations

from mother_ticker.collectors.model import ChronyTracking, GnssStatus, PpsStatus, SystemStatus
from mother_ticker.collectors.system import throttle_reasons


def offset_text(seconds: float) -> str:
    """Human-scaled offset: ns, us, ms or s, signed."""
    magnitude = abs(seconds)
    if magnitude < 1e-6:
        return f"{seconds * 1e9:+.0f} ns"
    if magnitude < 1e-3:
        return f"{seconds * 1e6:+.1f} us"
    if magnitude < 1.0:
        return f"{seconds * 1e3:+.3f} ms"
    return f"{seconds:+.3f} s"


def duration_text(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def bytes_text(value: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def gnss_summary(gnss: GnssStatus) -> str:
    if not gnss.reachable:
        return "gpsd UNREACHABLE"
    return f"{gnss.fix_label}, {gnss.satellites_used} used of {gnss.satellites_seen} seen"


def pps_summary(pps: PpsStatus) -> str:
    if not pps.present:
        return "device MISSING"
    if pps.error:
        return f"unreadable: {pps.error}"
    age = f"{pps.age_s:.1f}s ago" if pps.age_s is not None else "age unknown"
    return f"{'pulsing' if pps.pulsing else 'NO PULSE'}, last edge {age}"


def chrony_summary(tracking: ChronyTracking) -> str:
    if not tracking.reachable:
        return "chronyd NOT ANSWERING"
    if not tracking.synchronised:
        return "NOT SYNCHRONISED"
    return (
        f"stratum {tracking.stratum} via {tracking.ref_name or tracking.ref_id}\n"
        f"offset {offset_text(tracking.system_time_offset_s)}, leap {tracking.leap_status}"
    )


def system_lines(system: SystemStatus) -> list[str]:
    temp = f"{system.temperature_c:.1f} C" if system.temperature_c is not None else "unknown"
    mem_used = (system.mem_total_kb - system.mem_available_kb) * 1024
    lines = [
        f"Host       {system.hostname}",
        f"Kernel     {system.kernel}",
        f"Uptime     {duration_text(system.uptime_s)}",
        f"Load       {system.load_1:.2f} {system.load_5:.2f} {system.load_15:.2f}",
        f"Memory     {bytes_text(mem_used)} of {bytes_text(system.mem_total_kb * 1024)}"
        f" ({system.mem_used_pct:.0f}%)",
        f"Disk /     {bytes_text(system.disk_used_b)} of {bytes_text(system.disk_total_b)}"
        f" ({system.disk_used_pct:.0f}%)",
        f"SoC temp   {temp}",
        f"Root FS    {'read-only overlay' if system.overlay_root else 'read-write'}",
    ]
    reasons = throttle_reasons(system.throttled_flags)
    if system.throttled_flags is None:
        lines.append("Throttle   vcgencmd unavailable")
    elif reasons:
        lines.append("Throttle   " + "; ".join(reasons))
    else:
        lines.append("Throttle   none")
    return lines

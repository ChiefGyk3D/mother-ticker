# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""chrony status via `chronyc -c`, the machine-readable CSV form.

chronyc reaches chronyd over UDP 323 on loopback when run unprivileged, which
is why chrony.conf keeps the command port bound to loopback rather than
setting `cmdport 0`. Monitoring commands work there; reconfiguration needs
the Unix socket and root, so nothing here can change chronyd.
"""

from __future__ import annotations

import subprocess

from mother_ticker.collectors.model import ChronySource, ChronyStatus, ChronyTracking

CHRONYC = "chronyc"


def _run(args: list[str], timeout_s: float = 5.0) -> str:
    result = subprocess.run(
        [CHRONYC, *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout).strip() or f"chronyc exit {result.returncode}"
        )
    return result.stdout


def parse_tracking_csv(text: str) -> ChronyTracking:
    """Parse `chronyc -c tracking`.

    Field order, from chrony's client.c: reference ID (hex), reference name,
    stratum, reference time, system time offset, last offset, RMS offset,
    frequency, residual frequency, skew, root delay, root dispersion, update
    interval, leap status. The system time field is positive when the system
    clock is fast of NTP time.
    """
    line = text.strip().splitlines()[0] if text.strip() else ""
    fields = line.split(",")
    if len(fields) < 14:
        raise ValueError(f"unexpected tracking line: {line!r}")
    return ChronyTracking(
        reachable=True,
        ref_id=fields[0],
        ref_name=fields[1],
        stratum=int(fields[2]),
        ref_time=float(fields[3]),
        system_time_offset_s=float(fields[4]),
        last_offset_s=float(fields[5]),
        rms_offset_s=float(fields[6]),
        frequency_ppm=float(fields[7]),
        residual_freq_ppm=float(fields[8]),
        skew_ppm=float(fields[9]),
        root_delay_s=float(fields[10]),
        root_dispersion_s=float(fields[11]),
        update_interval_s=float(fields[12]),
        leap_status=fields[13].strip(),
    )


def parse_sources_csv(text: str) -> tuple[ChronySource, ...]:
    """Parse `chronyc -c sources`.

    Fields: mode, state, name, stratum, poll, reach (decimal), last rx,
    adjusted offset, measured offset, estimated error.
    """
    sources: list[ChronySource] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        f = line.split(",")
        if len(f) < 10:
            continue
        sources.append(
            ChronySource(
                mode=f[0],
                state=f[1],
                name=f[2],
                stratum=int(f[3]),
                poll=int(f[4]),
                reach=int(f[5]),
                last_rx_s=int(f[6]),
                adjusted_offset_s=float(f[7]),
                measured_offset_s=float(f[8]),
                error_s=float(f[9]),
            )
        )
    return tuple(sources)


def collect_chrony(with_text: bool = True) -> ChronyStatus:
    """Gather tracking and sources. Human-readable text is kept for the detail view."""
    try:
        tracking = parse_tracking_csv(_run(["-c", "tracking"]))
        sources = parse_sources_csv(_run(["-c", "sources"]))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return ChronyStatus(tracking=ChronyTracking(reachable=False, error=str(exc)))
    tracking_text = ""
    sources_text = ""
    if with_text:
        try:
            tracking_text = _run(["tracking"])
            sources_text = _run(["sources", "-v"])
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
    return ChronyStatus(
        tracking=tracking,
        sources=sources,
        tracking_text=tracking_text,
        sources_text=sources_text,
    )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Kernel PPS health from sysfs.

`/sys/class/pps/pps0/assert` reads as `<seconds.nanoseconds>#<sequence>`, the
timestamp of the last rising edge in the system clock's timebase. If the last
edge is older than a few seconds the GPS has stopped pulsing (or the overlay
is not loaded), and chrony is about to start free-running.
"""

from __future__ import annotations

import time
from pathlib import Path

from mother_ticker.collectors.model import PpsStatus

SYSFS_PPS = Path("/sys/class/pps")


def parse_assert(text: str) -> tuple[float, int]:
    """Parse the sysfs assert line into (timestamp, sequence)."""
    stamp, _, seq = text.strip().partition("#")
    if not stamp or not seq:
        raise ValueError(f"unexpected assert line: {text!r}")
    return float(stamp), int(seq)


def evaluate_pps(
    device: str,
    assert_text: str | None,
    now: float,
    stale_after_s: float,
) -> PpsStatus:
    """Pure evaluation so the freshness rule is testable without sysfs."""
    if assert_text is None:
        return PpsStatus(present=False, device=device, error="no such PPS device")
    try:
        stamp, seq = parse_assert(assert_text)
    except ValueError as exc:
        return PpsStatus(present=True, device=device, error=str(exc))
    age = now - stamp
    return PpsStatus(
        present=True,
        device=device,
        assert_time=stamp,
        assert_sequence=seq,
        age_s=age,
        pulsing=0.0 <= age <= stale_after_s or (age < 0 and abs(age) < 1.0),
    )


def collect_pps(
    device: str = "pps0", stale_after_s: float = 3.0, root: Path = SYSFS_PPS
) -> PpsStatus:
    path = root / device / "assert"
    if not path.exists():
        return evaluate_pps(device, None, time.time(), stale_after_s)
    try:
        text = path.read_text(encoding="ascii")
    except OSError as exc:
        return PpsStatus(present=True, device=device, error=str(exc))
    return evaluate_pps(device, text, time.time(), stale_after_s)

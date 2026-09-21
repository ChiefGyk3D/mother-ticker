# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Turn a Snapshot into one of three states with plain-language reasons.

The same function drives the dashboard banner, the `mother_ticker_health`
metric and the health-check timer, so what the screen says, what Grafana
graphs and what triggers a restart cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from mother_ticker.collectors.model import Snapshot
from mother_ticker.collectors.system import throttle_reasons
from mother_ticker.config import Thresholds


class Level(IntEnum):
    OK = 0
    WARNING = 1
    CRITICAL = 2


Adder = Callable[["Level", str, str], None]


@dataclass(frozen=True)
class Problem:
    level: Level
    subsystem: str  # gnss, pps, chrony, system, service
    message: str


@dataclass(frozen=True)
class HealthReport:
    level: Level
    problems: tuple[Problem, ...]

    @property
    def headline(self) -> str:
        if self.level is Level.OK:
            return "ALL SYSTEMS NOMINAL"
        worst = [p for p in self.problems if p.level == self.level]
        return "; ".join(p.message for p in worst)

    def by_subsystem(self, name: str) -> Level:
        levels = [p.level for p in self.problems if p.subsystem == name]
        return max(levels) if levels else Level.OK


def _check_pps(snapshot: Snapshot, add: Adder) -> None:
    """The whole point of the box. No pulses means chrony is coasting."""
    pps = snapshot.pps
    if not pps.present:
        add(Level.CRITICAL, "pps", f"PPS device {pps.device} missing")
    elif pps.error:
        add(Level.CRITICAL, "pps", f"PPS unreadable: {pps.error}")
    elif not pps.pulsing:
        age = f"{pps.age_s:.0f}s" if pps.age_s is not None else "unknown"
        add(Level.CRITICAL, "pps", f"PPS not pulsing (last edge {age} ago)")


def _check_gnss(snapshot: Snapshot, thresholds: Thresholds, add: Adder) -> None:
    gnss = snapshot.gnss
    if not gnss.reachable:
        add(Level.CRITICAL, "gnss", "gpsd unreachable")
    elif gnss.fix_mode < 2:
        add(Level.CRITICAL, "gnss", "GNSS no fix")
    elif gnss.fix_mode == 2:
        add(Level.WARNING, "gnss", "GNSS 2D fix only")
    elif gnss.satellites_used < thresholds.min_satellites:
        add(Level.WARNING, "gnss", f"only {gnss.satellites_used} satellites in use")


def _check_chrony(snapshot: Snapshot, thresholds: Thresholds, add: Adder) -> None:
    tracking = snapshot.chrony.tracking
    if not tracking.reachable:
        add(Level.CRITICAL, "chrony", "chronyd not answering")
        return
    if not tracking.synchronised:
        add(Level.CRITICAL, "chrony", "chrony not synchronised")
        return
    if tracking.stratum > 1:
        add(Level.WARNING, "chrony", f"stratum {tracking.stratum}, not on PPS")
    offset = abs(tracking.system_time_offset_s)
    if offset >= thresholds.offset_crit_s:
        add(Level.CRITICAL, "chrony", f"offset {offset * 1000:.1f} ms")
    elif offset >= thresholds.offset_warn_s:
        add(Level.WARNING, "chrony", f"offset {offset * 1e6:.0f} us")
    if tracking.leap_status in ("Insert second", "Delete second"):
        add(Level.WARNING, "chrony", f"leap second pending: {tracking.leap_status}")


def _check_services(snapshot: Snapshot, add: Adder) -> None:
    for svc in snapshot.services:
        if svc.active_state != "active":
            add(Level.CRITICAL, "service", f"{svc.name} is {svc.active_state}")


def _check_host(snapshot: Snapshot, thresholds: Thresholds, add: Adder) -> None:
    system = snapshot.system
    if system.temperature_c is not None:
        if system.temperature_c >= thresholds.temp_crit_c:
            add(Level.CRITICAL, "system", f"SoC {system.temperature_c:.0f} C")
        elif system.temperature_c >= thresholds.temp_warn_c:
            add(Level.WARNING, "system", f"SoC {system.temperature_c:.0f} C")
    for reason in throttle_reasons(system.throttled_flags):
        if reason.endswith("now"):
            add(Level.WARNING, "system", reason)
    if system.disk_used_pct >= thresholds.disk_warn_pct:
        add(Level.WARNING, "system", f"disk {system.disk_used_pct:.0f}% full")
    if system.mem_used_pct >= thresholds.mem_warn_pct:
        add(Level.WARNING, "system", f"memory {system.mem_used_pct:.0f}% used")


def evaluate(snapshot: Snapshot, thresholds: Thresholds) -> HealthReport:
    problems: list[Problem] = []

    def add(level: Level, subsystem: str, message: str) -> None:
        problems.append(Problem(level, subsystem, message))

    _check_pps(snapshot, add)
    _check_gnss(snapshot, thresholds, add)
    _check_chrony(snapshot, thresholds, add)
    _check_services(snapshot, add)
    _check_host(snapshot, thresholds, add)

    level = max((p.level for p in problems), default=Level.OK)
    return HealthReport(level=level, problems=tuple(problems))

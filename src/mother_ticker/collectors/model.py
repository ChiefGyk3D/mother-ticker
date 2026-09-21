# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Plain data returned by the collectors and consumed by everything else."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from mother_ticker.collectors.updates import UpdatesStatus

# gpsd's gnssid values (from the u-blox UBX-NAV-SAT definition that gpsd follows).
GNSS_NAMES: dict[int, str] = {
    0: "GPS",
    1: "SBAS",
    2: "Galileo",
    3: "BeiDou",
    4: "IMES",
    5: "QZSS",
    6: "GLONASS",
    7: "NavIC",
}

FIX_LABELS: dict[int, str] = {0: "no data", 1: "no fix", 2: "2D fix", 3: "3D fix"}


@dataclass(frozen=True)
class Satellite:
    prn: int
    gnss_id: int | None
    sv_id: int | None
    elevation: float | None
    azimuth: float | None
    snr: float | None
    used: bool
    health: int | None = None

    @property
    def constellation(self) -> str:
        if self.gnss_id is None:
            return "?"
        return GNSS_NAMES.get(self.gnss_id, f"gnss{self.gnss_id}")


@dataclass(frozen=True)
class GnssStatus:
    reachable: bool
    device: str | None = None
    fix_mode: int = 0
    satellites_used: int = 0
    satellites_seen: int = 0
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    time_utc: str | None = None
    satellites: tuple[Satellite, ...] = ()
    error: str | None = None

    @property
    def fix_label(self) -> str:
        if not self.reachable:
            return "gpsd unreachable"
        return FIX_LABELS.get(self.fix_mode, f"mode {self.fix_mode}")

    @property
    def has_fix(self) -> bool:
        return self.reachable and self.fix_mode >= 2


@dataclass(frozen=True)
class ChronyTracking:
    reachable: bool
    ref_id: str = ""
    ref_name: str = ""
    stratum: int = 0
    ref_time: float = 0.0
    system_time_offset_s: float = 0.0
    last_offset_s: float = 0.0
    rms_offset_s: float = 0.0
    frequency_ppm: float = 0.0
    residual_freq_ppm: float = 0.0
    skew_ppm: float = 0.0
    root_delay_s: float = 0.0
    root_dispersion_s: float = 0.0
    update_interval_s: float = 0.0
    leap_status: str = ""
    error: str | None = None

    @property
    def synchronised(self) -> bool:
        return self.reachable and self.leap_status not in ("", "Not synchronised")


@dataclass(frozen=True)
class ChronySource:
    mode: str  # ^ server, = peer, # refclock
    state: (
        str  # * current best, + combined, - not combined, ? unreachable, x false ticker, ~ jittery
    )
    name: str
    stratum: int
    poll: int
    reach: int  # octal in the human output, decimal here
    last_rx_s: int
    adjusted_offset_s: float
    measured_offset_s: float
    error_s: float

    @property
    def mode_label(self) -> str:
        return {"^": "server", "=": "peer", "#": "refclock"}.get(self.mode, self.mode)

    @property
    def state_label(self) -> str:
        return {
            "*": "selected",
            "+": "combined",
            "-": "not combined",
            "?": "unreachable",
            "x": "false ticker",
            "~": "too variable",
        }.get(self.state, self.state)

    @property
    def reach_octal(self) -> str:
        return format(self.reach, "o").zfill(3)


@dataclass(frozen=True)
class ChronyStatus:
    tracking: ChronyTracking
    sources: tuple[ChronySource, ...] = ()
    tracking_text: str = ""
    sources_text: str = ""


@dataclass(frozen=True)
class PpsStatus:
    present: bool
    device: str
    assert_time: float | None = None
    assert_sequence: int | None = None
    age_s: float | None = None
    pulsing: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SystemStatus:
    hostname: str
    kernel: str
    uptime_s: float
    load_1: float
    load_5: float
    load_15: float
    mem_total_kb: int
    mem_available_kb: int
    disk_total_b: int
    disk_used_b: int
    temperature_c: float | None
    throttled_flags: int | None = None
    overlay_root: bool = False

    @property
    def mem_used_pct(self) -> float:
        if self.mem_total_kb == 0:
            return 0.0
        return 100.0 * (self.mem_total_kb - self.mem_available_kb) / self.mem_total_kb

    @property
    def disk_used_pct(self) -> float:
        if self.disk_total_b == 0:
            return 0.0
        return 100.0 * self.disk_used_b / self.disk_total_b


@dataclass(frozen=True)
class Address:
    family: str  # inet or inet6
    address: str
    prefix: int
    scope: str


@dataclass(frozen=True)
class Interface:
    name: str
    state: str
    mac: str | None
    mtu: int | None
    addresses: tuple[Address, ...] = ()


@dataclass(frozen=True)
class NetworkStatus:
    interfaces: tuple[Interface, ...] = ()

    def primary_addresses(self) -> list[str]:
        """Global-scope addresses on non-loopback interfaces, IPv4 first."""
        out: list[str] = []
        for iface in self.interfaces:
            if iface.name == "lo":
                continue
            for addr in iface.addresses:
                if addr.scope == "global":
                    out.append(f"{addr.address}/{addr.prefix} ({iface.name})")
        out.sort(key=lambda s: (":" in s.split("/")[0], s))
        return out


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    active_state: str
    sub_state: str
    restarts: int
    since: str


@dataclass(frozen=True)
class Snapshot:
    """Everything the TUI, exporter and health check need, gathered at one instant."""

    taken_at: datetime
    site: str
    version: str
    gnss: GnssStatus
    chrony: ChronyStatus
    pps: PpsStatus
    system: SystemStatus
    network: NetworkStatus
    services: tuple[ServiceStatus, ...] = field(default_factory=tuple)
    updates: UpdatesStatus = field(default_factory=UpdatesStatus)

    @staticmethod
    def now() -> datetime:
        return datetime.now(tz=UTC)

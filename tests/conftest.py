# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Shared fixtures. The suite never touches real hardware, gpsd or chronyd.

Sockets to anything but loopback are refused so a test cannot quietly
depend on the network.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mother_ticker.collectors.model import (
    ChronySource,
    ChronyStatus,
    ChronyTracking,
    GnssStatus,
    NetworkStatus,
    PpsStatus,
    Satellite,
    ServiceStatus,
    Snapshot,
    SystemStatus,
)

FIXTURES = Path(__file__).parent / "fixtures"

_real_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError(f"test tried to reach {host!r}; only loopback is allowed")
    _real_connect(self, address)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


def make_snapshot(
    *,
    gnss: GnssStatus | None = None,
    chrony: ChronyStatus | None = None,
    pps: PpsStatus | None = None,
    system: SystemStatus | None = None,
    services: tuple[ServiceStatus, ...] | None = None,
) -> Snapshot:
    """A healthy stratum-1 snapshot; override pieces to make it sick."""
    sats = tuple(
        Satellite(prn=p, gnss_id=0, sv_id=p, elevation=40.0, azimuth=90.0, snr=38.0, used=True)
        for p in range(1, 9)
    )
    return Snapshot(
        taken_at=datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC),
        site="main-lan",
        version="0.1.0",
        gnss=gnss
        or GnssStatus(
            reachable=True,
            device="/dev/ttyAMA0",
            fix_mode=3,
            satellites_used=8,
            satellites_seen=12,
            latitude=40.0,
            longitude=-74.0,
            altitude_m=10.0,
            time_utc="2026-09-21T12:00:00.000Z",
            satellites=sats,
        ),
        chrony=chrony
        or ChronyStatus(
            tracking=ChronyTracking(
                reachable=True,
                ref_id="50505300",
                ref_name="PPS",
                stratum=1,
                ref_time=1790000000.0,
                system_time_offset_s=-0.000000021,
                last_offset_s=0.000000010,
                rms_offset_s=0.000000050,
                frequency_ppm=-3.512,
                residual_freq_ppm=0.001,
                skew_ppm=0.010,
                root_delay_s=0.0,
                root_dispersion_s=0.0000002,
                update_interval_s=16.0,
                leap_status="Normal",
            ),
            sources=(
                ChronySource("#", "x", "NMEA", 0, 4, 377, 12, 0.05, 0.05, 0.1),
                ChronySource("#", "*", "PPS", 0, 4, 377, 12, -0.00000002, -0.00000002, 0.0000002),
            ),
        ),
        pps=pps
        or PpsStatus(
            present=True,
            device="pps0",
            assert_time=1790000000.0,
            assert_sequence=86400,
            age_s=0.4,
            pulsing=True,
        ),
        system=system
        or SystemStatus(
            hostname="ntp-main",
            kernel="6.12.0-rpi",
            uptime_s=86400.0,
            load_1=0.1,
            load_5=0.1,
            load_15=0.1,
            mem_total_kb=900000,
            mem_available_kb=600000,
            disk_total_b=30_000_000_000,
            disk_used_b=5_000_000_000,
            temperature_c=52.0,
            throttled_flags=0,
        ),
        network=NetworkStatus(),
        services=services
        or (
            ServiceStatus("chrony", "active", "running", 0, "Mon 2026-09-21 00:00:00 UTC"),
            ServiceStatus("gpsd", "active", "running", 0, "Mon 2026-09-21 00:00:00 UTC"),
        ),
    )

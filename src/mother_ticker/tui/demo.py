# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Demo mode: the TUI with fabricated data, no gpsd, no chronyd, no Pi.

`mother-ticker tui --demo` lets anyone see the interface on a laptop, and the
screenshot generator drives the same app so the README shows real renders.
Every privileged action is replaced with a notification.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from mother_ticker.collectors.model import (
    Address,
    ChronySource,
    ChronyStatus,
    ChronyTracking,
    GnssStatus,
    Interface,
    NetworkStatus,
    PpsStatus,
    Satellite,
    ServiceStatus,
    Snapshot,
    SystemStatus,
)
from mother_ticker.collectors.updates import UpdatesStatus
from mother_ticker.config import Config
from mother_ticker.tui.app import MotherTickerApp
from mother_ticker.version import __version__

DemoState = Literal["nominal", "warning", "critical"]

_SATS = [
    (18, 0, 73.0, 120.0, 44.0, True),
    (5, 0, 61.0, 210.0, 41.0, True),
    (311, 2, 66.0, 30.0, 40.0, True),
    (301, 2, 55.0, 250.0, 39.0, True),
    (13, 0, 44.0, 300.0, 36.0, True),
    (326, 2, 40.0, 270.0, 35.0, True),
    (23, 0, 35.0, 180.0, 33.0, True),
    (303, 2, 28.0, 90.0, 31.0, True),
    (15, 0, 22.0, 60.0, 28.0, True),
    (133, 1, 30.0, 200.0, 30.0, False),
    (29, 0, 12.0, 330.0, 21.0, False),
    (324, 2, 8.0, 150.0, 19.0, False),
    (70, 6, 50.0, 100.0, 33.0, False),
    (81, 6, 20.0, 320.0, 25.0, False),
]

TRACKING_TEXT = """Reference ID    : 50505300 (PPS)
Stratum         : 1
Ref time (UTC)  : Mon Sep 21 12:00:00 2026
System time     : 0.000000021 seconds slow of NTP time
Last offset     : +0.000000010 seconds
RMS offset      : 0.000000050 seconds
Frequency       : 3.512 ppm slow
Residual freq   : +0.001 ppm
Skew            : 0.010 ppm
Root delay      : 0.000000001 seconds
Root dispersion : 0.000000200 seconds
Update interval : 16.0 seconds
Leap status     : Normal
"""

SOURCES_TEXT = """  .-- Source mode  '^' = server, '=' = peer, '#' = local clock.
 / .- Source state '*' = current best, '+' = combined, '-' = not combined,
| /             'x' = may be in error, '~' = too variable, '?' = unusable.
||                                                 .- xxxx [ yyyy ] +/- zzzz
||      Reachability register (octal) -.           |  xxxx = adjusted offset,
||      Log2(Polling interval) --.      |          |  yyyy = measured offset,
||                                \\     |          |  zzzz = estimated error.
||                                 |    |           \\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#x NMEA                          0   4   377    13    +50ms[  +50ms] +/-  100ms
#* PPS                           0   4   377    13    -21ns[  -21ns] +/-  200ns
^- time.cloudflare.com           3   6   377    34   -120us[ -118us] +/- 2100us
^- time.nist.gov                 1   6   377    35   +310us[ +305us] +/- 4800us
"""

JOURNAL_TEXT = """2026-09-21T11:58:12+0000 ntp-main systemd[1]: Started gpsd.service - GPS Daemon.
2026-09-21T11:58:12+0000 ntp-main gpsd[612]: gpsd:INFO: launching (Version 3.25)
2026-09-21T11:58:12+0000 ntp-main gpsd[612]: gpsd:INFO: listening on port 2947
2026-09-21T11:58:13+0000 ntp-main gpsd[612]: gpsd:INFO: /dev/ttyAMA0 identified as type u-blox
2026-09-21T11:58:13+0000 ntp-main gpsd[612]: gpsd:INFO: KPPS:/dev/pps0 kernel PPS will be used
2026-09-21T11:58:41+0000 ntp-main gpsd[612]: gpsd:INFO: /dev/ttyAMA0: fix mode 3
"""


def demo_snapshot(state: DemoState = "nominal") -> Snapshot:
    sats = tuple(
        Satellite(
            prn=p, gnss_id=g, sv_id=p % 100, elevation=el, azimuth=az, snr=snr, used=used, health=1
        )
        for p, g, el, az, snr, used in _SATS
    )
    gnss = GnssStatus(
        reachable=True,
        device="/dev/ttyAMA0",
        fix_mode=3,
        satellites_used=9,
        satellites_seen=14,
        latitude=40.7128,
        longitude=-74.006,
        altitude_m=12.4,
        time_utc=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        satellites=sats,
    )
    tracking = ChronyTracking(
        reachable=True,
        ref_id="50505300",
        ref_name="PPS",
        stratum=1,
        ref_time=1_790_000_000.0,
        system_time_offset_s=-2.1e-8,
        last_offset_s=1.0e-8,
        rms_offset_s=5.0e-8,
        frequency_ppm=-3.512,
        residual_freq_ppm=0.001,
        skew_ppm=0.010,
        root_delay_s=1e-9,
        root_dispersion_s=2e-7,
        update_interval_s=16.0,
        leap_status="Normal",
    )
    sources = (
        ChronySource("#", "x", "NMEA", 0, 4, 255, 13, 0.05, 0.05, 0.1),
        ChronySource("#", "*", "PPS", 0, 4, 255, 13, -2.1e-8, -2.1e-8, 2e-7),
        ChronySource("^", "-", "time.cloudflare.com", 3, 6, 255, 34, -1.2e-4, -1.18e-4, 2.1e-3),
        ChronySource("^", "-", "time.nist.gov", 1, 6, 255, 35, 3.1e-4, 3.05e-4, 4.8e-3),
    )
    pps = PpsStatus(
        present=True,
        device="pps0",
        assert_time=1_790_000_000.0,
        assert_sequence=86_400,
        age_s=0.4,
        pulsing=True,
    )
    system = SystemStatus(
        hostname="ntp-main",
        kernel="6.12.34-v8+",
        uptime_s=6 * 86400 + 4 * 3600 + 17 * 60,
        load_1=0.08,
        load_5=0.06,
        load_15=0.05,
        mem_total_kb=916_256,
        mem_available_kb=702_000,
        disk_total_b=31_000_000_000,
        disk_used_b=4_600_000_000,
        temperature_c=52.6,
        throttled_flags=0,
        overlay_root=False,
    )
    network = NetworkStatus(
        interfaces=(
            Interface(
                "lo",
                "UNKNOWN",
                "00:00:00:00:00:00",
                65536,
                (Address("inet", "127.0.0.1", 8, "host"),),
            ),
            Interface(
                "eth0",
                "UP",
                "dc:a6:32:12:34:56",
                1500,
                (
                    Address("inet", "192.0.2.20", 24, "global"),
                    Address("inet6", "2001:db8::20", 64, "global"),
                    Address("inet6", "fe80::dea6:32ff:fe12:3456", 64, "link"),
                ),
            ),
            Interface("wlan0", "DOWN", "dc:a6:32:12:34:57", 1500, ()),
        )
    )
    services = (
        ServiceStatus("chrony", "active", "running", 0, "Tue 2026-09-15 07:43:02 UTC"),
        ServiceStatus("gpsd", "active", "running", 0, "Tue 2026-09-15 07:43:01 UTC"),
    )
    updates = UpdatesStatus(
        checked_at=datetime.now(tz=UTC).timestamp() - 3 * 3600,
        pending=0,
        security=0,
        lists_refreshed=True,
        lists_age_s=3 * 3600.0,
    )
    if state == "warning":
        tracking = replace(
            tracking,
            stratum=2,
            ref_name="time.cloudflare.com",
            ref_id="A29FC87B",
            system_time_offset_s=-1.2e-4,
        )
        system = replace(system, temperature_c=71.5)
        updates = replace(
            updates,
            pending=7,
            security=2,
            packages=("chrony", "gpsd", "libc6", "openssh-server", "systemd", "tzdata", "vim-tiny"),
            security_packages=("libc6", "openssh-server"),
        )
    elif state == "critical":
        pps = PpsStatus(
            present=True,
            device="pps0",
            assert_time=1_789_999_988.0,
            assert_sequence=86_388,
            age_s=12.0,
            pulsing=False,
        )
        gnss = replace(
            gnss,
            fix_mode=1,
            satellites_used=0,
            satellites=tuple(replace(s, used=False, snr=(s.snr or 0) * 0.4) for s in sats),
        )
        tracking = replace(
            tracking, leap_status="Not synchronised", stratum=0, ref_name="", ref_id="00000000"
        )
    return Snapshot(
        taken_at=datetime.now(tz=UTC),
        site="main-lan",
        version=__version__,
        gnss=gnss,
        chrony=ChronyStatus(
            tracking=tracking,
            sources=sources,
            tracking_text=TRACKING_TEXT,
            sources_text=SOURCES_TEXT,
        ),
        pps=pps,
        system=system,
        network=network,
        services=services,
        updates=updates,
    )


class DemoApp(MotherTickerApp):
    """The real app with fake collectors and harmless actions."""

    def __init__(self, config: Config, state: DemoState = "nominal") -> None:
        self.state: DemoState = state
        if not config.tui.admin_user:
            config = replace(config, tui=replace(config.tui, admin_user="admin"))
        super().__init__(
            config,
            collector=lambda _cfg, _net: demo_snapshot(self.state),
            network_collector=lambda: demo_snapshot(self.state).network,
        )

    def collect_chrony_text(self) -> ChronyStatus:
        return demo_snapshot(self.state).chrony

    def journal_tail(self, unit: str) -> str:
        return JOURNAL_TEXT.replace("gpsd", unit) if unit != "gpsd" else JOURNAL_TEXT

    def restart_service(self, unit: str) -> tuple[bool, str]:
        return True, f"demo: would restart {unit}"

    def reboot_host(self) -> tuple[bool, str]:
        return True, "demo: would reboot"

    def maintenance_mode(self, enable: bool) -> tuple[bool, str]:
        return True, f"demo: would turn maintenance mode {'on' if enable else 'off'}"

    def run_admin_shell(self, admin: str) -> int:
        self.notify(f"demo: would run su - {admin}")
        return 0

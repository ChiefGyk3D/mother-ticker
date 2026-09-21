# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The health rules: what the banner, the metric and the timer all agree on."""

from __future__ import annotations

from dataclasses import replace

from mother_ticker.collectors.model import (
    ChronyStatus,
    ChronyTracking,
    GnssStatus,
    PpsStatus,
    ServiceStatus,
)
from mother_ticker.config import Thresholds
from mother_ticker.health.evaluate import Level, evaluate
from tests.conftest import make_snapshot

T = Thresholds()


class TestHealthy:
    def test_nominal(self) -> None:
        report = evaluate(make_snapshot(), T)
        assert report.level is Level.OK
        assert report.problems == ()
        assert report.headline == "ALL SYSTEMS NOMINAL"


class TestPps:
    def test_stale_pps_is_critical(self) -> None:
        snap = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=12.0, pulsing=False))
        report = evaluate(snap, T)
        assert report.level is Level.CRITICAL
        assert report.by_subsystem("pps") is Level.CRITICAL
        assert "PPS not pulsing" in report.headline

    def test_missing_device_is_critical(self) -> None:
        snap = make_snapshot(
            pps=PpsStatus(present=False, device="pps0", error="no such PPS device")
        )
        assert evaluate(snap, T).by_subsystem("pps") is Level.CRITICAL


class TestGnss:
    def test_no_fix(self) -> None:
        snap = make_snapshot(gnss=GnssStatus(reachable=True, fix_mode=1))
        assert evaluate(snap, T).by_subsystem("gnss") is Level.CRITICAL

    def test_gpsd_down(self) -> None:
        snap = make_snapshot(gnss=GnssStatus(reachable=False, error="refused"))
        report = evaluate(snap, T)
        assert "gpsd unreachable" in [p.message for p in report.problems]

    def test_two_d_fix_warns(self) -> None:
        snap = make_snapshot(gnss=GnssStatus(reachable=True, fix_mode=2, satellites_used=5))
        assert evaluate(snap, T).by_subsystem("gnss") is Level.WARNING

    def test_few_satellites_warns(self) -> None:
        snap = make_snapshot(gnss=GnssStatus(reachable=True, fix_mode=3, satellites_used=3))
        report = evaluate(snap, T)
        assert report.level is Level.WARNING
        assert "3 satellites" in report.headline


class TestChrony:
    def test_unsynchronised_is_critical(self) -> None:
        base = make_snapshot()
        tracking = replace(base.chrony.tracking, leap_status="Not synchronised", stratum=0)
        snap = make_snapshot(chrony=ChronyStatus(tracking=tracking))
        assert evaluate(snap, T).by_subsystem("chrony") is Level.CRITICAL

    def test_fallback_to_stratum_two_warns(self) -> None:
        """PPS lost, chrony on an upstream server: still serving, but not what we built."""
        base = make_snapshot()
        tracking = replace(base.chrony.tracking, stratum=2, ref_name="time.cloudflare.com")
        snap = make_snapshot(chrony=ChronyStatus(tracking=tracking))
        report = evaluate(snap, T)
        assert report.level is Level.WARNING
        assert "stratum 2" in report.headline

    def test_offset_thresholds(self) -> None:
        base = make_snapshot()
        warn = replace(base.chrony.tracking, system_time_offset_s=0.002)
        crit = replace(base.chrony.tracking, system_time_offset_s=-0.5)
        assert evaluate(make_snapshot(chrony=ChronyStatus(tracking=warn)), T).level is Level.WARNING
        assert (
            evaluate(make_snapshot(chrony=ChronyStatus(tracking=crit)), T).level is Level.CRITICAL
        )

    def test_daemon_down(self) -> None:
        snap = make_snapshot(
            chrony=ChronyStatus(tracking=ChronyTracking(reachable=False, error="x"))
        )
        assert "chronyd not answering" in [p.message for p in evaluate(snap, T).problems]


class TestHost:
    def test_temperature(self) -> None:
        base = make_snapshot()
        warm = replace(base.system, temperature_c=72.0)
        hot = replace(base.system, temperature_c=81.0)
        assert evaluate(make_snapshot(system=warm), T).level is Level.WARNING
        assert evaluate(make_snapshot(system=hot), T).level is Level.CRITICAL

    def test_throttled_now_warns_but_history_does_not(self) -> None:
        base = make_snapshot()
        history_only = replace(base.system, throttled_flags=0x50000)
        now = replace(base.system, throttled_flags=0x5)
        assert evaluate(make_snapshot(system=history_only), T).level is Level.OK
        assert evaluate(make_snapshot(system=now), T).level is Level.WARNING

    def test_disk_and_memory(self) -> None:
        base = make_snapshot()
        full = replace(base.system, disk_used_b=29_000_000_000)
        tight = replace(base.system, mem_available_kb=50_000)
        assert "disk" in evaluate(make_snapshot(system=full), T).headline
        assert "memory" in evaluate(make_snapshot(system=tight), T).headline

    def test_inactive_service(self) -> None:
        snap = make_snapshot(services=(ServiceStatus("gpsd", "failed", "failed", 3, ""),))
        report = evaluate(snap, T)
        assert report.level is Level.CRITICAL
        assert report.by_subsystem("service") is Level.CRITICAL


class TestOrdering:
    def test_headline_names_only_the_worst(self) -> None:
        base = make_snapshot()
        warm = replace(base.system, temperature_c=72.0)
        snap = make_snapshot(
            system=warm, pps=PpsStatus(present=False, device="pps0", error="no such PPS device")
        )
        report = evaluate(snap, T)
        assert report.level is Level.CRITICAL
        assert "PPS" in report.headline
        assert "SoC" not in report.headline

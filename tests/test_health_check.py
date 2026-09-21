# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The escalation ladder is a pure decision; these tests walk it tick by tick."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mother_ticker.collectors.model import (
    ChronyStatus,
    ChronyTracking,
    GnssStatus,
    PpsStatus,
    ServiceStatus,
)
from mother_ticker.config import HealthConfig, Thresholds
from mother_ticker.health import check
from mother_ticker.health.evaluate import evaluate
from tests.conftest import make_snapshot

CFG = HealthConfig(
    thresholds=Thresholds(),
    restart_after_failures=3,
    reboot_after_failures=10,
    min_uptime_before_reboot_s=600,
)


def _walk(snapshot: object, ticks: int, uptime: float = 10_000.0) -> list[list[check.Action]]:
    state = check.LadderState()
    out: list[list[check.Action]] = []
    for _ in range(ticks):
        report = evaluate(snapshot, CFG.thresholds)  # type: ignore[arg-type]
        state, actions = check.decide(report, state, CFG, uptime)
        out.append(actions)
    return out


class TestState:
    def test_round_trip(self, tmp_path: Path) -> None:
        s = check.LadderState(
            consecutive_critical=3, critical_by_subsystem={"pps": 3}, restarts={"gpsd": 1}
        )
        s.save(tmp_path / "h.json")
        loaded = check.LadderState.load(tmp_path / "h.json")
        assert loaded == s

    def test_corrupt_file_resets(self, tmp_path: Path) -> None:
        (tmp_path / "h.json").write_text("{not json")
        assert check.LadderState.load(tmp_path / "h.json") == check.LadderState()

    def test_missing_file_resets(self, tmp_path: Path) -> None:
        assert check.LadderState.load(tmp_path / "nope.json") == check.LadderState()


class TestHealthyTicks:
    def test_no_actions_when_ok(self) -> None:
        assert _walk(make_snapshot(), 5) == [[], [], [], [], []]

    def test_recovery_logs_once(self) -> None:
        sick = make_snapshot(gnss=GnssStatus(reachable=False, error="refused"))
        state = check.LadderState()
        state, _ = check.decide(evaluate(sick, CFG.thresholds), state, CFG, 10_000)
        assert state.consecutive_critical == 1
        state, actions = check.decide(evaluate(make_snapshot(), CFG.thresholds), state, CFG, 10_000)
        assert state.consecutive_critical == 0
        assert [a.kind for a in actions] == ["log"]
        assert actions[0].reason.startswith("recovered")


class TestGpsd:
    def test_unreachable_gpsd_restarts_on_threshold(self) -> None:
        sick = make_snapshot(gnss=GnssStatus(reachable=False, error="refused"))
        ticks = _walk(sick, 3)
        assert ticks[0] == [] and ticks[1] == []
        assert [(a.kind, a.target) for a in ticks[2]] == [("restart", "gpsd")]

    def test_restart_backs_off(self) -> None:
        """Restart at 3, then every 6 ticks, not every tick."""
        sick = make_snapshot(gnss=GnssStatus(reachable=False, error="refused"))
        ticks = _walk(sick, 9)
        restarts = [i for i, t in enumerate(ticks) if any(a.kind == "restart" for a in t)]
        assert restarts == [2, 8]

    def test_no_fix_does_not_restart(self) -> None:
        """An antenna problem is not a daemon problem; the ladder says so instead."""
        sick = make_snapshot(gnss=GnssStatus(reachable=True, fix_mode=1))
        ticks = _walk(sick, 3)
        assert [a.kind for a in ticks[2]] == ["log"]
        assert "antenna" in ticks[2][0].reason


class TestChrony:
    def test_daemon_not_answering_restarts(self) -> None:
        sick = make_snapshot(
            chrony=ChronyStatus(tracking=ChronyTracking(reachable=False, error="x"))
        )
        ticks = _walk(sick, 3)
        assert [(a.kind, a.target) for a in ticks[2]] == [("restart", "chrony")]

    def test_unsynchronised_with_good_pps_restarts(self) -> None:
        base = make_snapshot()
        tracking = replace(base.chrony.tracking, leap_status="Not synchronised", stratum=0)
        sick = make_snapshot(chrony=ChronyStatus(tracking=tracking))
        ticks = _walk(sick, 3)
        assert [(a.kind, a.target) for a in ticks[2]] == [("restart", "chrony")]

    def test_unsynchronised_with_dead_pps_waits(self) -> None:
        base = make_snapshot()
        tracking = replace(base.chrony.tracking, leap_status="Not synchronised", stratum=0)
        sick = make_snapshot(
            chrony=ChronyStatus(tracking=tracking),
            pps=PpsStatus(present=True, device="pps0", age_s=30.0, pulsing=False),
        )
        ticks = _walk(sick, 3)
        kinds = {(a.kind, a.target) for a in ticks[2]}
        assert ("restart", "chrony") not in kinds
        assert any(a.kind == "log" and a.target == "chrony" for a in ticks[2])


class TestServicesAndPps:
    def test_stopped_unit_is_restarted(self) -> None:
        sick = make_snapshot(services=(ServiceStatus("gpsd", "inactive", "dead", 0, ""),))
        ticks = _walk(sick, 3)
        assert ("restart", "gpsd") in {(a.kind, a.target) for a in ticks[2]}

    def test_missing_pps_device_only_logs(self) -> None:
        sick = make_snapshot(
            pps=PpsStatus(present=False, device="pps0", error="no such PPS device")
        )
        ticks = _walk(sick, 3)
        assert [a.kind for a in ticks[2]] == ["log"]
        assert "config.txt" in ticks[2][0].reason


class TestReboot:
    def test_reboot_after_sustained_critical(self) -> None:
        sick = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=99.0, pulsing=False))
        ticks = _walk(sick, 10)
        assert any(a.kind == "reboot" for a in ticks[9])
        assert not any(a.kind == "reboot" for t in ticks[:9] for a in t)

    def test_no_reboot_during_early_uptime(self) -> None:
        """The boot-loop guard: a box that just came up never reboots itself again."""
        sick = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=99.0, pulsing=False))
        ticks = _walk(sick, 12, uptime=120.0)
        assert not any(a.kind == "reboot" for t in ticks for a in t)

    def test_reboot_disabled(self) -> None:
        sick = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=99.0, pulsing=False))
        cfg = replace(CFG, reboot_enabled=False)
        state = check.LadderState()
        seen: list[check.Action] = []
        for _ in range(12):
            state, actions = check.decide(evaluate(sick, cfg.thresholds), state, cfg, 10_000)
            seen.extend(actions)
        assert not any(a.kind == "reboot" for a in seen)

    def test_reboot_counter_resets_so_it_is_not_immediate_again(self) -> None:
        sick = make_snapshot(pps=PpsStatus(present=True, device="pps0", age_s=99.0, pulsing=False))
        ticks = _walk(sick, 20)
        reboots = [i for i, t in enumerate(ticks) if any(a.kind == "reboot" for a in t)]
        assert reboots == [9, 19]


class TestApply:
    def test_dry_run_runs_nothing(self) -> None:
        actions = [check.Action("restart", "gpsd", "x"), check.Action("reboot", "host", "y")]
        assert check.apply(actions, dry_run=True) == 0

    def test_run_check_end_to_end(self, tmp_path: Path) -> None:
        cfg = replace(CFG, state_path=tmp_path / "state.json")
        assert check.run_check(make_snapshot(), cfg, dry_run=True) == 0
        sick = make_snapshot(gnss=GnssStatus(reachable=False, error="refused"))
        assert check.run_check(sick, cfg, dry_run=True) == 2

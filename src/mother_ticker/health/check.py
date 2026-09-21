# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The escalation ladder behind `mother-ticker healthcheck`.

Runs from a systemd timer as root every 30 seconds. State between runs lives
in a small JSON file under /run (tmpfs, cleared at boot). The ladder:

1. Consecutive critical results are counted per subsystem.
2. After `restart_after_failures`, the responsible unit is restarted:
   gpsd when gpsd is unreachable or the fix has been lost with the receiver
   silent; chronyd when chronyc cannot reach it or it stays unsynchronised
   while PPS and GNSS are fine.
3. After `reboot_after_failures` with no recovery, and only if uptime exceeds
   `min_uptime_before_reboot_s`, the host reboots. The uptime floor bounds a
   reboot loop to one attempt per floor interval.

Anything the ladder cannot fix (an antenna with no sky, a missing overlay) is
logged plainly so the journal says what to look at. Restarting a service that
is not the cause is avoided on purpose: a gpsd restart with no antenna does
nothing useful and hides the real fault.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mother_ticker.collectors.model import Snapshot
from mother_ticker.config import HealthConfig
from mother_ticker.health.evaluate import HealthReport, Level, evaluate

log = logging.getLogger(__name__)


@dataclass
class LadderState:
    consecutive_critical: int = 0
    critical_by_subsystem: dict[str, int] = field(default_factory=dict)
    restarts: dict[str, int] = field(default_factory=dict)
    last_level: int = 0

    @classmethod
    def load(cls, path: Path) -> LadderState:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        try:
            return cls(
                consecutive_critical=int(data.get("consecutive_critical", 0)),
                critical_by_subsystem={
                    str(k): int(v) for k, v in data.get("critical_by_subsystem", {}).items()
                },
                restarts={str(k): int(v) for k, v in data.get("restarts", {}).items()},
                last_level=int(data.get("last_level", 0)),
            )
        except (TypeError, ValueError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self)), encoding="utf-8")
        tmp.replace(path)


@dataclass(frozen=True)
class Action:
    kind: str  # restart, reboot, log
    target: str
    reason: str


def _advance_counters(report: HealthReport, state: LadderState) -> LadderState:
    new = LadderState(
        consecutive_critical=state.consecutive_critical + 1,
        critical_by_subsystem=dict(state.critical_by_subsystem),
        restarts=dict(state.restarts),
        last_level=int(report.level),
    )
    critical = {p.subsystem for p in report.problems if p.level is Level.CRITICAL}
    for name in critical:
        new.critical_by_subsystem[name] = new.critical_by_subsystem.get(name, 0) + 1
    for name in list(new.critical_by_subsystem):
        if name not in critical:
            del new.critical_by_subsystem[name]
    return new


def _due(state: LadderState, subsystem: str, threshold: int) -> bool:
    """Fire at the threshold, then every 2 x threshold ticks, so restarts back off."""
    count = state.critical_by_subsystem.get(subsystem, 0)
    return count >= threshold and (count - threshold) % (threshold * 2) == 0


def _restart(state: LadderState, unit: str, reason: str) -> Action:
    state.restarts[unit] = state.restarts.get(unit, 0) + 1
    return Action("restart", unit, reason)


def _subsystem_actions(report: HealthReport, state: LadderState, threshold: int) -> list[Action]:
    actions: list[Action] = []
    messages = {p.subsystem: p.message for p in report.problems if p.level is Level.CRITICAL}
    critical = set(messages)

    # gpsd: an unreachable daemon is the only GNSS fault a restart can fix.
    if "gnss" in critical and _due(state, "gnss", threshold):
        if "unreachable" in messages["gnss"]:
            actions.append(_restart(state, "gpsd", messages["gnss"]))
        else:
            actions.append(
                Action("log", "gnss", "no fix; check antenna and sky view, restart will not help")
            )

    # chrony: restart when it does not answer, or stays unsynchronised with a good PPS.
    if "chrony" in critical and _due(state, "chrony", threshold):
        if "not answering" in messages["chrony"] or "pps" not in critical:
            actions.append(_restart(state, "chrony", messages["chrony"]))
        else:
            actions.append(
                Action("log", "chrony", "unsynchronised because PPS is down; fixing PPS first")
            )

    # service: systemd's Restart= handles crashes; we nudge only a stopped unit.
    if "service" in critical and _due(state, "service", threshold):
        for problem in report.problems:
            if problem.subsystem == "service":
                unit = problem.message.split(" ", 1)[0]
                actions.append(_restart(state, unit, problem.message))

    if "pps" in critical and _due(state, "pps", threshold) and "missing" in messages["pps"]:
        actions.append(
            Action(
                "log",
                "pps",
                "PPS device missing: overlay not loaded or wiring; "
                "needs a reboot after fixing config.txt",
            )
        )
    return actions


def decide(
    report: HealthReport, state: LadderState, config: HealthConfig, uptime_s: float
) -> tuple[LadderState, list[Action]]:
    """Pure decision: new state and the actions to take. No side effects."""
    if report.level is not Level.CRITICAL:
        actions = (
            [Action("log", "health", "recovered: " + report.headline)]
            if state.consecutive_critical
            else []
        )
        return LadderState(restarts=dict(state.restarts), last_level=int(report.level)), actions

    new = _advance_counters(report, state)
    actions = _subsystem_actions(report, new, config.restart_after_failures)

    if (
        config.reboot_enabled
        and new.consecutive_critical >= config.reboot_after_failures
        and uptime_s >= config.min_uptime_before_reboot_s
    ):
        actions.append(
            Action(
                "reboot",
                "host",
                f"critical for {new.consecutive_critical} consecutive checks: {report.headline}",
            )
        )
        new.consecutive_critical = 0

    return new, actions


def apply(actions: list[Action], dry_run: bool = False) -> int:
    """Execute actions. Returns the count that failed."""
    failures = 0
    for action in actions:
        if action.kind == "log":
            log.warning("%s: %s", action.target, action.reason)
            continue
        cmd = (
            ["systemctl", "reboot"]
            if action.kind == "reboot"
            else ["systemctl", "restart", action.target]
        )
        log.warning(
            "%s %s (%s)%s",
            action.kind,
            action.target,
            action.reason,
            " [dry run]" if dry_run else "",
        )
        if dry_run:
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.error("%s failed: %s", " ".join(cmd), exc)
            failures += 1
            continue
        if result.returncode != 0:
            log.error("%s failed: %s", " ".join(cmd), (result.stderr or result.stdout).strip())
            failures += 1
    return failures


def run_check(snapshot: Snapshot, config: HealthConfig, dry_run: bool = False) -> int:
    """One timer tick. Exit status 0 ok, 1 warning, 2 critical, so the journal shows it."""
    report = evaluate(snapshot, config.thresholds)
    state = LadderState.load(config.state_path)
    new_state, actions = decide(report, state, config, snapshot.system.uptime_s)
    if report.level is Level.OK:
        log.info("ok")
    else:
        log.warning("%s: %s", report.level.name.lower(), report.headline)
    apply(actions, dry_run=dry_run)
    if not dry_run:
        try:
            new_state.save(config.state_path)
        except OSError as exc:
            log.error("cannot save state to %s: %s", config.state_path, exc)
    return int(report.level)

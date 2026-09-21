# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Pending package updates, checked daily by a timer and read by everything else.

`mother-ticker check-updates` runs as root from `mother-ticker-updates.timer`:
it refreshes the apt lists when it can (the isolated unit usually cannot and
that is not an error), simulates a dist-upgrade, counts what would install
and how many of those come from a security suite, and writes a JSON state
file. The collectors read that file; the metrics, the syslog message, the
health banner and the optional webhook all follow from it. Nothing is ever
installed by this path.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

STATE_PATH = Path("/var/lib/mother-ticker/updates.json")
REBOOT_REQUIRED = Path("/run/reboot-required")
APT_LISTS = Path("/var/lib/apt/lists")

# `apt-get -s dist-upgrade` prints one line per package it would install:
#   Inst chrony [4.5-1] (4.6-1 Debian:13.1/stable, Debian-Security:13/stable-security [arm64])
_INST = re.compile(
    r"^Inst (?P<name>\S+) (?:\[(?P<old>[^\]]+)\] )?\((?P<new>\S+) (?P<origin>[^)]*)\)"
)


@dataclass(frozen=True)
class UpdatesStatus:
    checked_at: float | None = None
    pending: int = 0
    security: int = 0
    packages: tuple[str, ...] = ()
    security_packages: tuple[str, ...] = ()
    reboot_required: bool = False
    lists_refreshed: bool = False
    lists_age_s: float | None = None
    error: str | None = None

    @property
    def checked(self) -> bool:
        return self.checked_at is not None

    @property
    def age_s(self) -> float | None:
        return None if self.checked_at is None else max(0.0, time.time() - self.checked_at)


def parse_simulated_upgrade(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(all packages that would install, the subset from a security suite)."""
    all_pkgs: list[str] = []
    security: list[str] = []
    for line in text.splitlines():
        m = _INST.match(line)
        if not m:
            continue
        all_pkgs.append(m.group("name"))
        origin = m.group("origin").lower()
        if "security" in origin:
            security.append(m.group("name"))
    return tuple(all_pkgs), tuple(security)


def _last_line(text: str, fallback: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else fallback


def lists_age(lists_dir: Path = APT_LISTS) -> float | None:
    """Seconds since the newest apt list file was written, or None when there are none."""
    try:
        stamps = [
            p.stat().st_mtime for p in lists_dir.iterdir() if p.is_file() and p.name != "lock"
        ]
    except OSError:
        return None
    return None if not stamps else max(0.0, time.time() - max(stamps))


def run_check(
    *,
    refresh: bool = True,
    state_path: Path = STATE_PATH,
    reboot_flag: Path = REBOOT_REQUIRED,
    lists_dir: Path = APT_LISTS,
) -> UpdatesStatus:
    """Perform the check and write the state file. Root, from the timer."""
    refreshed = False
    error: str | None = None
    if refresh:
        try:
            result = subprocess.run(
                ["apt-get", "-q", "update"],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            refreshed = result.returncode == 0
            if not refreshed:
                error = _last_line(result.stderr or result.stdout, "apt-get update failed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"apt-get update: {exc}"
    try:
        sim = subprocess.run(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        packages, security = parse_simulated_upgrade(sim.stdout)
        if sim.returncode != 0:
            error = (
                error
                or (sim.stderr.strip().splitlines()[-1:] or ["apt-get -s dist-upgrade failed"])[0]
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        packages, security = (), ()
        error = error or f"apt-get -s dist-upgrade: {exc}"
    status = UpdatesStatus(
        checked_at=time.time(),
        pending=len(packages),
        security=len(security),
        packages=packages,
        security_packages=security,
        reboot_required=reboot_flag.exists(),
        lists_refreshed=refreshed,
        lists_age_s=lists_age(lists_dir),
        error=error,
    )
    save_state(status, state_path)
    return status


def save_state(status: UpdatesStatus, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(status)), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path = STATE_PATH) -> UpdatesStatus:
    """Read the last check. A missing or corrupt file is 'never checked', not an error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return UpdatesStatus(
            checked_at=data.get("checked_at"),
            pending=int(data.get("pending", 0)),
            security=int(data.get("security", 0)),
            packages=tuple(str(p) for p in data.get("packages", [])),
            security_packages=tuple(str(p) for p in data.get("security_packages", [])),
            reboot_required=bool(data.get("reboot_required", False)),
            lists_refreshed=bool(data.get("lists_refreshed", False)),
            lists_age_s=data.get("lists_age_s"),
            error=data.get("error"),
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return UpdatesStatus()


def collect_updates(path: Path = STATE_PATH) -> UpdatesStatus:
    return load_state(path)

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""systemd service state, journal excerpts and the few privileged actions the TUI may take.

Privileged actions go through `sudo -n` and a sudoers file installed by the
Ansible role that lists exactly these commands for the TUI user. Nothing here
builds a command line from user input.
"""

from __future__ import annotations

import subprocess

from mother_ticker.collectors.model import ServiceStatus

ALLOWED_ACTIONS: dict[str, list[str]] = {
    "restart": ["systemctl", "restart"],
}


def parse_show(name: str, text: str) -> ServiceStatus:
    props: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        props[key] = value
    return ServiceStatus(
        name=name,
        active_state=props.get("ActiveState", "unknown"),
        sub_state=props.get("SubState", "unknown"),
        restarts=int(props.get("NRestarts") or 0),
        since=props.get("ActiveEnterTimestamp", ""),
    )


def collect_service(name: str) -> ServiceStatus:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                name,
                "--property=ActiveState,SubState,NRestarts,ActiveEnterTimestamp",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ServiceStatus(
            name=name, active_state="unknown", sub_state=str(exc), restarts=0, since=""
        )
    return parse_show(name, result.stdout)


def collect_services(names: tuple[str, ...]) -> tuple[ServiceStatus, ...]:
    return tuple(collect_service(n) for n in names)


def journal_tail(unit: str, lines: int = 60) -> str:
    """Recent journal for a unit. The TUI user is in systemd-journal, so no sudo."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"journalctl failed: {exc}"
    return result.stdout or result.stderr


def restart_service(unit: str, allowed: tuple[str, ...]) -> tuple[bool, str]:
    """Restart a unit named in the config's allow-list via sudo. Returns (ok, message)."""
    if unit not in allowed:
        return False, f"{unit} is not in the allowed service list"
    try:
        result = subprocess.run(
            ["sudo", "-n", *ALLOWED_ACTIONS["restart"], unit],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
    return True, f"{unit} restarted"


def reboot_host() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "reboot"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
    return True, "reboot requested"


def maintenance_mode(enable: bool) -> tuple[bool, str]:
    """Toggle the read-only overlay through the helper the role installs."""
    action = "on" if enable else "off"
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/mother-ticker-maint", action],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output or f"exit {result.returncode}"

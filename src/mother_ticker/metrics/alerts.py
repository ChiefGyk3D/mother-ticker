# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Optional one-way webhook for health transitions and pending updates.

The exporter calls `AlertSender.consider()` after every collection. A message
goes out when the health level crosses `min_level` in either direction, or
when the set of pending security updates changes. Nothing is read back and
a failed send is logged and counted, never retried in a loop.

Two payload shapes: "json", a plain document for n8n, Home Assistant, a
Grafana webhook contact point or anything that takes JSON; and "ntfy", the
JSON publish format ntfy.sh and self-hosted ntfy accept at their base URL.
A bearer token, if any, is read from a file at send time so it never lives
in config.toml.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mother_ticker.collectors.model import Snapshot
from mother_ticker.config import AlertsConfig
from mother_ticker.health.evaluate import HealthReport, Level

log = logging.getLogger(__name__)

LEVEL_FLOOR = {"warning": Level.WARNING, "critical": Level.CRITICAL}


@dataclass(frozen=True)
class Alert:
    kind: str  # health or updates
    level: Level
    title: str
    message: str
    snapshot: Snapshot
    report: HealthReport

    def as_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "level": self.level.name.lower(),
            "title": self.title,
            "message": self.message,
            "host": self.snapshot.system.hostname,
            "site": self.snapshot.site,
            "version": self.snapshot.version,
            "time": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "problems": [
                {"level": p.level.name.lower(), "subsystem": p.subsystem, "message": p.message}
                for p in self.report.problems
            ],
            "updates": {
                "pending": self.snapshot.updates.pending,
                "security": self.snapshot.updates.security,
                "security_packages": list(self.snapshot.updates.security_packages),
                "reboot_required": self.snapshot.updates.reboot_required,
            },
        }

    def as_ntfy(self, topic: str) -> dict[str, object]:
        priority = {Level.OK: 3, Level.WARNING: 4, Level.CRITICAL: 5}[self.level]
        tags = {
            Level.OK: ["white_check_mark"],
            Level.WARNING: ["warning"],
            Level.CRITICAL: ["rotating_light"],
        }
        return {
            "topic": topic,
            "title": f"{self.snapshot.system.hostname}: {self.title}",
            "message": self.message,
            "priority": priority,
            "tags": tags[self.level],
        }


def build_payload(alert: Alert, config: AlertsConfig) -> bytes:
    body = alert.as_ntfy(config.ntfy_topic) if config.format == "ntfy" else alert.as_json()
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def decide(
    snapshot: Snapshot,
    report: HealthReport,
    *,
    last_level: Level | None,
    last_security: tuple[str, ...] | None,
    min_level: Level,
) -> list[Alert]:
    """Pure: which alerts a new observation produces, given what was last sent."""
    alerts: list[Alert] = []
    if last_level is not None and report.level != last_level:
        crossed_up = report.level >= min_level and last_level < min_level
        moved_while_high = report.level >= min_level and last_level >= min_level
        recovered = report.level < min_level and last_level >= min_level
        if crossed_up or moved_while_high:
            alerts.append(
                Alert(
                    "health",
                    report.level,
                    f"{report.level.name.lower()}: time service",
                    report.headline,
                    snapshot,
                    report,
                )
            )
        elif recovered:
            alerts.append(
                Alert(
                    "health",
                    report.level,
                    "recovered: time service",
                    report.headline,
                    snapshot,
                    report,
                )
            )
    security = snapshot.updates.security_packages
    if last_security is not None and set(security) - set(last_security):
        names = ", ".join(sorted(set(security) - set(last_security)))
        title = f"{snapshot.updates.security} security updates pending"
        if snapshot.updates.reboot_required:
            title += ", reboot required"
        alerts.append(Alert("updates", Level.WARNING, title, f"new: {names}", snapshot, report))
    return alerts


class AlertSender:
    def __init__(self, config: AlertsConfig) -> None:
        self.config = config
        self.last_level: Level | None = None
        self.last_security: tuple[str, ...] | None = None
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.webhook_url)

    def _token(self) -> str | None:
        if not self.config.token_file:
            return None
        try:
            return Path(self.config.token_file).read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            log.warning("cannot read webhook token %s: %s", self.config.token_file, exc)
            return None

    def send(self, alert: Alert) -> bool:
        req = urllib.request.Request(  # noqa: S310  scheme is fixed by config, validated below
            self.config.webhook_url,
            data=build_payload(alert, self.config),
            headers={"Content-Type": "application/json", "User-Agent": "mother-ticker"},
            method="POST",
        )
        if not req.full_url.startswith(("http://", "https://")):
            self.last_error = "webhook_url must be http or https"
            self.failed += 1
            return False
        token = self._token()
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(  # noqa: S310  # nosec B310  scheme checked above
                req, timeout=self.config.timeout_s
            ) as resp:
                resp.read(64)
        except (urllib.error.URLError, OSError) as exc:
            self.failed += 1
            self.last_error = str(exc)
            log.warning("webhook %s failed: %s", self.config.webhook_url, exc)
            return False
        self.sent += 1
        self.last_error = None
        return True

    def consider(self, snapshot: Snapshot, report: HealthReport) -> list[Alert]:
        """Decide, send, remember. Returns what was sent (or would be, when disabled)."""
        alerts = decide(
            snapshot,
            report,
            last_level=self.last_level,
            last_security=self.last_security,
            min_level=LEVEL_FLOOR[self.config.min_level],
        )
        self.last_level = report.level
        self.last_security = snapshot.updates.security_packages
        if self.enabled:
            for alert in alerts:
                self.send(alert)
        return alerts

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""RFC 5424 syslog sender for the malware-net relay path.

Message shape:

    <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [mt@57006 site="..." version="..."] {json}

PRI is facility * 8 + severity; severity follows the health level so a SIEM
can alert on the syslog severity alone. Transport is TCP or UDP from config.
TCP framing defaults to newline (RFC 6587 non-transparent), which rsyslog and
syslog-ng both accept by default; octet-counted is available for relays that
require it. Nothing is read back from the relay, and the sender never
retries in a tight loop: one attempt per interval, failure logged and
surfaced as a metric on the next send.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from mother_ticker.health.evaluate import Level
from mother_ticker.metrics.model import Metric, metrics_to_flat_dict

log = logging.getLogger(__name__)

# Private enterprise number placeholder for the structured-data element ID.
# RFC 5424 requires SD-ID of the form name@PEN for private elements; 32473 is
# the IANA example range reserved for documentation, so it never collides.
SD_ID = "mt@32473"

SEVERITY_BY_LEVEL: dict[Level, int] = {
    Level.OK: 6,  # informational
    Level.WARNING: 4,  # warning
    Level.CRITICAL: 2,  # critical
}


def _sd_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def format_rfc5424(
    *,
    facility: int,
    severity: int,
    timestamp: datetime,
    hostname: str,
    app_name: str,
    procid: str,
    msgid: str,
    sd_params: dict[str, str],
    message: str,
) -> str:
    pri = facility * 8 + severity
    ts = timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    sd = "-"
    if sd_params:
        params = " ".join(f'{k}="{_sd_escape(v)}"' for k, v in sd_params.items())
        sd = f"[{SD_ID} {params}]"
    host = hostname.replace(" ", "_")[:255] or "-"
    return f"<{pri}>1 {ts} {host} {app_name[:48]} {procid[:128]} {msgid[:32]} {sd} {message}"


def build_message(
    metrics: list[Metric],
    level: Level,
    *,
    site: str,
    version: str,
    hostname: str,
    facility: int,
    app_name: str,
    timestamp: datetime | None = None,
) -> str:
    body = metrics_to_flat_dict(metrics)
    body["site"] = site
    body["health_level"] = float(level)
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True)
    return format_rfc5424(
        facility=facility,
        severity=SEVERITY_BY_LEVEL[level],
        timestamp=timestamp or datetime.now(tz=UTC),
        hostname=hostname,
        app_name=app_name,
        procid=str(os.getpid()),
        msgid="metrics",
        sd_params={"site": site, "version": version},
        message=payload,
    )


def frame(
    message: str, transport: Literal["tcp", "udp"], framing: Literal["newline", "octet-counted"]
) -> bytes:
    data = message.encode("utf-8")
    if transport == "udp":
        return data
    if framing == "octet-counted":
        return f"{len(data)} ".encode("ascii") + data
    return data + b"\n"


class SyslogSender:
    """Send one message per call. Reconnects lazily; never blocks the caller for long."""

    def __init__(
        self,
        host: str,
        port: int,
        transport: Literal["tcp", "udp"],
        framing: Literal["newline", "octet-counted"],
        timeout_s: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.transport = transport
        self.framing = framing
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self.last_error: str | None = None
        self.sent = 0
        self.failed = 0

    def _connect(self) -> socket.socket:
        if self.transport == "udp":
            family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_DGRAM)
            sock.settimeout(self.timeout_s)
            return sock
        return socket.create_connection((self.host, self.port), timeout=self.timeout_s)

    def send(self, message: str) -> bool:
        data = frame(message, self.transport, self.framing)
        try:
            if self._sock is None:
                self._sock = self._connect()
            if self.transport == "udp":
                self._sock.sendto(data, (self.host, self.port))
            else:
                self._sock.sendall(data)
        except OSError as exc:
            self.failed += 1
            self.last_error = str(exc)
            self.close()
            log.warning("syslog send to %s:%d failed: %s", self.host, self.port, exc)
            return False
        self.sent += 1
        self.last_error = None
        return True

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


def run_forever(
    sender: SyslogSender,
    build: Callable[[], str],
    interval_s: float,
    stop: threading.Event,
) -> None:
    log.info("syslog exporter sending to %s:%d over %s", sender.host, sender.port, sender.transport)
    try:
        while not stop.is_set():
            try:
                sender.send(build())
            except Exception:
                log.exception("metrics build failed")
            stop.wait(interval_s)
    finally:
        sender.close()

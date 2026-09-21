# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""A pretend gpsd for the bench before the receiver is fitted.

`mother-ticker fake-gpsd` listens where gpsd would (127.0.0.1:2947 by
default) and speaks enough of gpsd's JSON protocol for this project's
collector, `gpspipe -w` and `gpsmon`: VERSION on connect, `?WATCH` to start
a stream of SKY and TPV reports once a second, `?DEVICES`, `?VERSION` and
`?POLL` on request. The fix is a public landmark (the Royal Observatory at
Greenwich), never anywhere real to the operator.

It writes nothing to chrony's shared memory and produces no PPS, so chrony
stays exactly as unhappy as it should be without a receiver; what it lets
you test is the gpsd collector, the GNSS screen, the health ladder's
reaction to a fix appearing and disappearing, and the metrics that describe
the sky. Stop the real gpsd first (`sudo systemctl stop gpsd.socket
gpsd.service`), or run this on another port and point `[gpsd] port` at it.
"""

from __future__ import annotations

import json
import logging
import socket
import socketserver
import threading
from datetime import UTC, datetime
from typing import Any, Literal

log = logging.getLogger(__name__)

Scenario = Literal["3dfix", "2dfix", "nofix", "silent"]
SCENARIOS: tuple[Scenario, ...] = ("3dfix", "2dfix", "nofix", "silent")

DEFAULT_DEVICE = "/dev/ttyAMA0"
# Royal Observatory, Greenwich: a landmark, so the fake fix is obviously not you.
GREENWICH = (51.4779, -0.0015, 46.0)

# (PRN, gnssid, svid, elevation, azimuth, signal). gnssid 0 GPS, 2 Galileo,
# 6 GLONASS, 1 SBAS; the same mix the recorded fixture shows the M8 tracking.
_SKY: tuple[tuple[int, int, int, float, float, float], ...] = (
    (18, 0, 18, 73.0, 120.0, 44.0),
    (5, 0, 5, 61.0, 210.0, 41.0),
    (311, 2, 11, 66.0, 30.0, 40.0),
    (301, 2, 1, 55.0, 250.0, 39.0),
    (13, 0, 13, 44.0, 300.0, 36.0),
    (326, 2, 26, 40.0, 270.0, 35.0),
    (23, 0, 23, 35.0, 180.0, 33.0),
    (303, 2, 3, 28.0, 90.0, 31.0),
    (15, 0, 15, 22.0, 60.0, 28.0),
    (133, 1, 133, 30.0, 200.0, 30.0),
    (29, 0, 29, 12.0, 330.0, 21.0),
    (324, 2, 24, 8.0, 150.0, 19.0),
    (70, 6, 6, 50.0, 100.0, 33.0),
    (81, 6, 17, 20.0, 320.0, 25.0),
)


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def version_report() -> dict[str, Any]:
    return {
        "class": "VERSION",
        "release": "mother-ticker fake",
        "rev": "mother-ticker fake",
        "proto_major": 3,
        "proto_minor": 15,
    }


def devices_report(device: str) -> dict[str, Any]:
    return {
        "class": "DEVICES",
        "devices": [
            {
                "class": "DEVICE",
                "path": device,
                "driver": "u-blox",
                "subtype": "fake receiver",
                "activated": _now_iso(),
                "flags": 1,
                "native": 1,
                "bps": 9600,
                "parity": "N",
                "stopbits": 1,
                "cycle": 1.0,
            }
        ],
    }


def sky_report(device: str, scenario: Scenario, satellites_used: int) -> dict[str, Any]:
    used_count = 0 if scenario == "nofix" else max(0, min(satellites_used, len(_SKY)))
    sats = []
    for index, (prn, gnssid, svid, el, az, ss) in enumerate(_SKY):
        sats.append(
            {
                "PRN": prn,
                "gnssid": gnssid,
                "svid": svid,
                "el": el,
                "az": az,
                "ss": ss,
                "used": index < used_count,
                "health": 1,
            }
        )
    return {
        "class": "SKY",
        "device": device,
        "time": _now_iso(),
        "hdop": 1.01,
        "vdop": 1.27,
        "pdop": 1.62,
        "nSat": len(sats),
        "uSat": used_count,
        "satellites": sats,
    }


def tpv_report(device: str, scenario: Scenario) -> dict[str, Any]:
    mode = {"3dfix": 3, "2dfix": 2, "nofix": 1, "silent": 0}[scenario]
    report: dict[str, Any] = {"class": "TPV", "device": device, "mode": mode, "time": _now_iso()}
    if mode >= 2:
        lat, lon, alt = GREENWICH
        report.update({"lat": lat, "lon": lon, "leapseconds": 18, "ept": 0.005})
    if mode == 3:
        report.update({"altHAE": alt, "altMSL": alt, "alt": alt})
    return report


def _encode(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, separators=(",", ":")) + "\r\n").encode()


class FakeGpsd:
    """The server. `start()` runs it in a thread; `serve_forever()` blocks."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 2947,
        scenario: Scenario = "3dfix",
        satellites_used: int = 9,
        device: str = DEFAULT_DEVICE,
        interval_s: float = 1.0,
    ) -> None:
        self.scenario: Scenario = scenario
        self.satellites_used = satellites_used
        self.device = device
        self.interval_s = interval_s
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                fake._serve_client(self.request)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server((host, port), Handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        port: int = self._server.server_address[1]
        return port

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _reports(self) -> list[dict[str, Any]]:
        if self.scenario == "silent":
            return []
        return [
            sky_report(self.device, self.scenario, self.satellites_used),
            tpv_report(self.device, self.scenario),
        ]

    def _serve_client(self, sock: socket.socket) -> None:
        """One client: VERSION first, commands as they arrive, reports once watching."""
        watching = False
        buf = b""
        try:
            sock.sendall(_encode(version_report()))
            while True:
                sock.settimeout(self.interval_s if watching else None)
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    for report in self._reports():
                        sock.sendall(_encode(report))
                    continue
                if not chunk:
                    return
                buf += chunk
                while b";" in buf:
                    command, _, buf = buf.partition(b";")
                    watching = self._handle(
                        sock, command.strip().decode(errors="replace"), watching
                    )
        except OSError as exc:
            log.debug("client gone: %s", exc)

    def _handle(self, sock: socket.socket, command: str, watching: bool) -> bool:
        if command.startswith("?WATCH"):
            enable = True
            _, _, body = command.partition("=")
            if body:
                try:
                    enable = bool(json.loads(body).get("enable", True))
                except (json.JSONDecodeError, AttributeError):
                    enable = True
            if enable:
                sock.sendall(_encode(devices_report(self.device)))
            sock.sendall(
                _encode({"class": "WATCH", "enable": enable, "json": enable, "nmea": False})
            )
            if enable:
                for report in self._reports():
                    sock.sendall(_encode(report))
            return enable
        if command.startswith("?DEVICES"):
            sock.sendall(_encode(devices_report(self.device)))
        elif command.startswith("?VERSION"):
            sock.sendall(_encode(version_report()))
        elif command.startswith("?POLL"):
            reports = self._reports()
            sock.sendall(
                _encode(
                    {
                        "class": "POLL",
                        "time": _now_iso(),
                        "active": 1,
                        "tpv": [r for r in reports if r["class"] == "TPV"],
                        "sky": [r for r in reports if r["class"] == "SKY"],
                    }
                )
            )
        elif command:
            sock.sendall(_encode({"class": "ERROR", "message": f"unrecognized request {command}"}))
        return watching

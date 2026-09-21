# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""gpsd client over its JSON socket (port 2947), stdlib only.

We do not depend on python3-gps so the venv installs from PyPI alone and the
parser can be tested from recorded JSON. Protocol: connect, send
`?WATCH={"enable":true,"json":true};`, read newline-delimited JSON objects.
TPV carries the fix, SKY carries the satellite list.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any

from mother_ticker.collectors.model import GnssStatus, Satellite

WATCH_COMMAND = b'?WATCH={"enable":true,"json":true};\n'


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def parse_sky(report: dict[str, Any]) -> tuple[Satellite, ...]:
    """Turn a SKY report's satellite array into Satellite records."""
    sats: list[Satellite] = []
    for raw in report.get("satellites", []):
        if not isinstance(raw, dict):
            continue
        prn = _int(raw.get("PRN"))
        if prn is None:
            continue
        sats.append(
            Satellite(
                prn=prn,
                gnss_id=_int(raw.get("gnssid")),
                sv_id=_int(raw.get("svid")),
                elevation=_float(raw.get("el")),
                azimuth=_float(raw.get("az")),
                snr=_float(raw.get("ss")),
                used=bool(raw.get("used", False)),
                health=_int(raw.get("health")),
            )
        )
    sats.sort(key=lambda s: (not s.used, -(s.snr or 0.0)))
    return tuple(sats)


def merge_reports(reports: list[dict[str, Any]]) -> GnssStatus:
    """Fold a sequence of gpsd JSON reports into one GnssStatus.

    The most recent TPV and SKY win. A VERSION or DEVICES report proves the
    daemon answered even when the receiver has said nothing yet.
    """
    tpv: dict[str, Any] | None = None
    sky: dict[str, Any] | None = None
    device: str | None = None
    for report in reports:
        cls = report.get("class")
        if cls == "TPV":
            tpv = report
        elif cls == "SKY":
            sky = report
        elif cls == "DEVICES":
            devices = report.get("devices") or []
            if devices and isinstance(devices[0], dict):
                device = devices[0].get("path")
        if "device" in report and device is None:
            device = str(report["device"])

    satellites = parse_sky(sky) if sky else ()
    used = sum(1 for s in satellites if s.used)
    if sky and "uSat" in sky:
        used = int(sky["uSat"])
    seen = len(satellites)
    if sky and "nSat" in sky:
        seen = int(sky["nSat"])

    mode = int(tpv.get("mode", 0)) if tpv else 0
    return GnssStatus(
        reachable=True,
        device=device,
        fix_mode=mode,
        satellites_used=used,
        satellites_seen=seen,
        latitude=_float(tpv.get("lat")) if tpv else None,
        longitude=_float(tpv.get("lon")) if tpv else None,
        altitude_m=_float(tpv.get("altHAE", tpv.get("alt"))) if tpv else None,
        time_utc=str(tpv["time"]) if tpv and "time" in tpv else None,
        satellites=satellites,
    )


def parse_stream(text: str) -> list[dict[str, Any]]:
    """Split raw socket text into JSON objects, ignoring anything malformed."""
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _read_until(sock: socket.socket, want_classes: set[str], timeout_s: float) -> str:
    """Read from gpsd until we have seen every wanted class or the timeout passes."""
    sock.settimeout(timeout_s)
    buf = b""
    seen: set[str] = set()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not want_classes <= seen:
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        for obj in parse_stream(buf.decode("utf-8", errors="replace")):
            cls = obj.get("class")
            if isinstance(cls, str):
                seen.add(cls)
    return buf.decode("utf-8", errors="replace")


def collect_gnss(host: str = "127.0.0.1", port: int = 2947, timeout_s: float = 5.0) -> GnssStatus:
    """Connect to gpsd, watch briefly, and return the current fix and sky view.

    A receiver with a fix emits TPV every second and SKY once per cycle, so a
    few seconds is enough. If gpsd is not answering we say so rather than
    pretending the sky is empty.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall(WATCH_COMMAND)
            text = _read_until(sock, {"TPV", "SKY"}, timeout_s)
    except OSError as exc:
        return GnssStatus(reachable=False, error=f"gpsd {host}:{port}: {exc}")
    reports = parse_stream(text)
    if not reports:
        return GnssStatus(reachable=False, error="gpsd sent nothing")
    return merge_reports(reports)

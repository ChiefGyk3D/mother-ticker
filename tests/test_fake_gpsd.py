# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The bench receiver, driven through the real gpsd client on loopback.

Nothing else in the suite opens a socket to the collector; this is the one
place the `?WATCH` handshake and the read loop run for real.
"""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator

import pytest

from mother_ticker.bench import fake_gpsd
from mother_ticker.bench.fake_gpsd import FakeGpsd
from mother_ticker.cli import build_parser
from mother_ticker.collectors import gpsd


def _server(scenario: fake_gpsd.Scenario, **kw: object) -> Iterator[FakeGpsd]:
    server = FakeGpsd(port=0, scenario=scenario, interval_s=0.1, **kw)  # type: ignore[arg-type]
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def fix_server() -> Iterator[FakeGpsd]:
    yield from _server("3dfix", satellites_used=7)


@pytest.fixture
def nofix_server() -> Iterator[FakeGpsd]:
    yield from _server("nofix")


@pytest.fixture
def silent_server() -> Iterator[FakeGpsd]:
    yield from _server("silent")


class TestThroughTheCollector:
    def test_three_d_fix(self, fix_server: FakeGpsd) -> None:
        status = gpsd.collect_gnss("127.0.0.1", fix_server.port, timeout_s=3.0)
        assert status.reachable and status.error is None
        assert status.fix_mode == 3
        assert status.satellites_used == 7
        assert status.satellites_seen == 14
        assert status.device == fake_gpsd.DEFAULT_DEVICE
        assert status.latitude == pytest.approx(fake_gpsd.GREENWICH[0])
        assert status.time_utc is not None and status.time_utc.endswith("Z")
        assert sum(1 for s in status.satellites if s.used) == 7

    def test_no_fix_tracks_but_uses_nothing(self, nofix_server: FakeGpsd) -> None:
        status = gpsd.collect_gnss("127.0.0.1", nofix_server.port, timeout_s=3.0)
        assert status.reachable
        assert status.fix_mode == 1
        assert not status.has_fix
        assert status.satellites_used == 0
        assert status.satellites_seen == 14
        assert status.latitude is None

    def test_silent_receiver_is_reachable_without_a_sky(self, silent_server: FakeGpsd) -> None:
        status = gpsd.collect_gnss("127.0.0.1", silent_server.port, timeout_s=0.5)
        assert status.reachable
        assert status.fix_mode == 0
        assert status.satellites_seen == 0
        assert status.device == fake_gpsd.DEFAULT_DEVICE


def _talk(port: int, payload: bytes, listen_s: float = 0.5) -> list[dict[str, object]]:
    """Send a payload and collect whatever arrives within listen_s (the stream never ends)."""
    with socket.create_connection(("127.0.0.1", port), timeout=1.0) as sock:
        if payload:
            sock.sendall(payload)
        sock.settimeout(0.2)
        buf = b""
        deadline = time.monotonic() + listen_s
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                break
            buf += chunk
    out: list[dict[str, object]] = []
    for line in buf.splitlines():
        if line.strip():
            obj = json.loads(line)
            assert isinstance(obj, dict)
            out.append(obj)
    return out


class TestProtocol:
    def test_version_greets_before_any_command(self, fix_server: FakeGpsd) -> None:
        reports = _talk(fix_server.port, b"")
        assert [r["class"] for r in reports] == ["VERSION"]

    def test_watch_replies_devices_then_watch_then_reports(self, fix_server: FakeGpsd) -> None:
        reports = _talk(fix_server.port, gpsd.WATCH_COMMAND)
        classes = [r["class"] for r in reports]
        assert classes[:3] == ["VERSION", "DEVICES", "WATCH"]
        assert "SKY" in classes and "TPV" in classes
        assert classes.count("TPV") >= 2, "reports must keep coming while watching"

    def test_poll_and_devices_and_an_unknown_request(self, fix_server: FakeGpsd) -> None:
        reports = _talk(fix_server.port, b"?DEVICES;?POLL;?BOGUS;")
        classes = [r["class"] for r in reports]
        assert classes == ["VERSION", "DEVICES", "POLL", "ERROR"]
        poll = reports[2]
        assert isinstance(poll["tpv"], list) and poll["tpv"][0]["mode"] == 3

    def test_every_line_parses_with_the_collector(self, fix_server: FakeGpsd) -> None:
        raw = _talk(fix_server.port, gpsd.WATCH_COMMAND)
        text = "\n".join(json.dumps(r) for r in raw)
        assert len(gpsd.parse_stream(text)) == len(raw)


class TestCli:
    def test_subcommand_and_defaults(self) -> None:
        args = build_parser().parse_args(["fake-gpsd"])
        assert args.port == 2947
        assert args.bind == "127.0.0.1"
        assert args.scenario == "3dfix"
        assert args.satellites == 9

    def test_scenarios_are_the_module_list(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["fake-gpsd", "--scenario", "warp"])
        for scenario in fake_gpsd.SCENARIOS:
            build_parser().parse_args(["fake-gpsd", "--scenario", scenario])

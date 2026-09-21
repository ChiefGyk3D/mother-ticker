# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The gpsd parser against recorded JSON streams."""

from __future__ import annotations

import socket
import threading

from mother_ticker.collectors import gpsd
from tests.conftest import FIXTURES


class TestParseStream:
    def test_ignores_non_json_lines(self) -> None:
        reports = gpsd.parse_stream((FIXTURES / "gpsd_stream_nofix.jsonl").read_text())
        assert [r["class"] for r in reports] == ["VERSION", "DEVICES", "WATCH", "SKY", "TPV"]

    def test_empty_input(self) -> None:
        assert gpsd.parse_stream("") == []


class TestMergeReports:
    def test_three_d_fix(self) -> None:
        status = gpsd.merge_reports(
            gpsd.parse_stream((FIXTURES / "gpsd_stream_3dfix.jsonl").read_text())
        )
        assert status.reachable
        assert status.has_fix
        assert status.fix_mode == 3
        assert status.fix_label == "3D fix"
        assert status.device == "/dev/ttyAMA0"
        assert status.satellites_used == 9
        assert status.satellites_seen == 14
        assert status.latitude == 40.7128
        assert status.time_utc == "2026-09-21T12:00:00.000Z"
        assert len(status.satellites) == 14

    def test_satellites_sorted_used_first_then_snr(self) -> None:
        status = gpsd.merge_reports(
            gpsd.parse_stream((FIXTURES / "gpsd_stream_3dfix.jsonl").read_text())
        )
        used = [s.used for s in status.satellites]
        assert used == sorted(used, reverse=True)
        first = status.satellites[0]
        assert first.prn == 18
        assert first.snr == 44.0
        assert first.constellation == "GPS"

    def test_constellation_names(self) -> None:
        status = gpsd.merge_reports(
            gpsd.parse_stream((FIXTURES / "gpsd_stream_3dfix.jsonl").read_text())
        )
        names = {s.constellation for s in status.satellites}
        assert names == {"GPS", "Galileo", "SBAS", "GLONASS"}

    def test_no_fix_is_reachable_but_unfixed(self) -> None:
        """A receiver searching for satellites is not the same as gpsd being down."""
        status = gpsd.merge_reports(
            gpsd.parse_stream((FIXTURES / "gpsd_stream_nofix.jsonl").read_text())
        )
        assert status.reachable
        assert not status.has_fix
        assert status.fix_label == "no fix"
        assert status.satellites_used == 0
        assert status.satellites_seen == 3

    def test_no_reports_at_all(self) -> None:
        status = gpsd.merge_reports([])
        assert status.reachable
        assert status.fix_mode == 0
        assert status.fix_label == "no data"


class TestCollectAgainstFakeDaemon:
    def test_talks_the_watch_protocol(self) -> None:
        """A loopback fake gpsd: we must send ?WATCH and read until TPV and SKY arrive."""
        payload = (FIXTURES / "gpsd_stream_3dfix.jsonl").read_bytes()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        received: list[bytes] = []

        def serve() -> None:
            conn, _ = server.accept()
            with conn:
                received.append(conn.recv(1024))
                conn.sendall(payload)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        status = gpsd.collect_gnss("127.0.0.1", port, timeout_s=3.0)
        thread.join(timeout=3)
        server.close()
        assert received and received[0].startswith(b"?WATCH=")
        assert status.fix_mode == 3
        assert status.satellites_used == 9

    def test_connection_refused_is_unreachable(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        status = gpsd.collect_gnss("127.0.0.1", port, timeout_s=1.0)
        assert not status.reachable
        assert status.error is not None
        assert status.fix_label == "gpsd unreachable"

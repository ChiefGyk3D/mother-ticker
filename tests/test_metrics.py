# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Metrics model and the two writers."""

from __future__ import annotations

import json
import socket
import threading
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer

from mother_ticker.config import Thresholds
from mother_ticker.health.evaluate import Level, evaluate
from mother_ticker.metrics import prometheus, syslog
from mother_ticker.metrics.model import Metric, metrics_to_flat_dict, snapshot_to_metrics
from tests.conftest import make_snapshot


def _metrics() -> list[Metric]:
    snap = make_snapshot()
    return snapshot_to_metrics(snap, evaluate(snap, Thresholds()))


class TestModel:
    def test_every_metric_is_prefixed_and_site_labelled(self) -> None:
        for m in _metrics():
            assert m.name.startswith("mother_ticker_")
            assert dict(m.labels)["site"] == "main-lan"

    def test_core_values(self) -> None:
        by_name = {m.name: m for m in _metrics() if len(m.labels) == 1}
        assert by_name["mother_ticker_chrony_stratum"].value == 1
        assert by_name["mother_ticker_pps_pulsing"].value == 1
        assert by_name["mother_ticker_gnss_fix_mode"].value == 3
        assert by_name["mother_ticker_health_level"].value == 0
        assert by_name["mother_ticker_soc_temperature_celsius"].value == 52.0

    def test_per_source_and_per_service_labels(self) -> None:
        sources = [m for m in _metrics() if m.name == "mother_ticker_chrony_source_offset_seconds"]
        assert {dict(m.labels)["source"] for m in sources} == {"NMEA", "PPS"}
        services = [m for m in _metrics() if m.name == "mother_ticker_service_active"]
        assert {dict(m.labels)["unit"] for m in services} == {"chrony", "gpsd"}

    def test_flat_dict_for_json_body(self) -> None:
        flat = metrics_to_flat_dict(_metrics())
        assert flat["version"] == "0.1.0"
        assert flat["chrony_stratum"] == 1
        assert flat["chrony_source_offset_seconds.PPS.refclock.selected"] == -2e-08
        assert flat["service_active.gpsd"] == 1
        assert "info" not in flat


class TestPrometheusRender:
    def test_format(self) -> None:
        text = prometheus.render(_metrics())
        assert "# HELP mother_ticker_chrony_stratum Stratum chrony is serving" in text
        assert "# TYPE mother_ticker_chrony_stratum gauge" in text
        assert 'mother_ticker_chrony_stratum{site="main-lan"} 1\n' in text
        assert 'mother_ticker_info{site="main-lan",version="0.1.0"} 1' in text
        assert text.endswith("\n")

    def test_help_and_type_once_per_family(self) -> None:
        text = prometheus.render(_metrics())
        assert text.count("# TYPE mother_ticker_chrony_source_reach") == 1

    def test_label_escaping_and_specials(self) -> None:
        text = prometheus.render(
            [
                Metric("x", "gauge", "h", float("nan"), (("a", 'q"b\\c\n'),)),
                Metric("y", "gauge", "h", float("inf")),
                Metric("z", "gauge", "h", 1.5),
            ]
        )
        assert 'x{a="q\\"b\\\\c\\n"} NaN' in text
        assert "y +Inf" in text
        assert "z 1.5" in text

    def test_http_endpoint(self) -> None:
        cache = prometheus.MetricsCache()
        cache.update(_metrics())
        server = ThreadingHTTPServer(("127.0.0.1", 0), prometheus.make_handler(cache))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
                s.sendall(b"GET /metrics HTTP/1.0\r\nHost: x\r\n\r\n")
                data = b""
                while chunk := s.recv(65536):
                    data += chunk
            assert data.startswith(b"HTTP/1.0 200")
            assert b"text/plain; version=0.0.4" in data
            assert b"mother_ticker_chrony_stratum" in data
            with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
                s.sendall(b"GET /nope HTTP/1.0\r\n\r\n")
                assert s.recv(64).startswith(b"HTTP/1.0 404")
        finally:
            server.shutdown()
            server.server_close()


class TestSyslog:
    def test_rfc5424_header(self) -> None:
        msg = syslog.format_rfc5424(
            facility=16,
            severity=6,
            timestamp=datetime(2026, 9, 21, 12, 0, 0, 123456, tzinfo=UTC),
            hostname="ntp-malware",
            app_name="mother-ticker",
            procid="1234",
            msgid="metrics",
            sd_params={"site": "malware-net", "version": "0.1.0"},
            message='{"a":1}',
        )
        assert msg == (
            "<134>1 2026-09-21T12:00:00.123456Z ntp-malware mother-ticker 1234 metrics "
            '[mt@32473 site="malware-net" version="0.1.0"] {"a":1}'
        )

    def test_sd_param_escaping(self) -> None:
        msg = syslog.format_rfc5424(
            facility=16,
            severity=6,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            hostname="h",
            app_name="a",
            procid="-",
            msgid="m",
            sd_params={"v": 'x"y]z\\'},
            message="m",
        )
        assert '[mt@32473 v="x\\"y\\]z\\\\"]' in msg

    def test_severity_follows_health(self) -> None:
        snap = make_snapshot()
        metrics = snapshot_to_metrics(snap, evaluate(snap, Thresholds()))
        ok = syslog.build_message(
            metrics,
            Level.OK,
            site="malware-net",
            version="0.1.0",
            hostname="h",
            facility=16,
            app_name="mt",
        )
        crit = syslog.build_message(
            metrics,
            Level.CRITICAL,
            site="malware-net",
            version="0.1.0",
            hostname="h",
            facility=16,
            app_name="mt",
        )
        assert ok.startswith("<134>1 ")
        assert crit.startswith("<130>1 ")

    def test_body_is_parseable_json(self) -> None:
        snap = make_snapshot()
        metrics = snapshot_to_metrics(snap, evaluate(snap, Thresholds()))
        msg = syslog.build_message(
            metrics,
            Level.OK,
            site="malware-net",
            version="0.1.0",
            hostname="h",
            facility=16,
            app_name="mt",
        )
        body = json.loads(msg.split("] ", 1)[1])
        assert body["site"] == "malware-net"
        assert body["chrony_stratum"] == 1
        assert body["pps_pulsing"] == 1

    def test_framing(self) -> None:
        assert syslog.frame("abc", "udp", "newline") == b"abc"
        assert syslog.frame("abc", "tcp", "newline") == b"abc\n"
        assert syslog.frame("abc", "tcp", "octet-counted") == b"3 abc"

    def test_tcp_send_to_loopback_relay(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        got: list[bytes] = []

        def accept() -> None:
            conn, _ = server.accept()
            with conn:
                got.append(conn.recv(4096))

        t = threading.Thread(target=accept, daemon=True)
        t.start()
        sender = syslog.SyslogSender("127.0.0.1", port, "tcp", "newline", timeout_s=3)
        assert sender.send("<134>1 hello")
        t.join(3)
        sender.close()
        server.close()
        assert got == [b"<134>1 hello\n"]
        assert sender.sent == 1 and sender.failed == 0

    def test_udp_send_to_loopback_relay(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(3)
        port = server.getsockname()[1]
        sender = syslog.SyslogSender("127.0.0.1", port, "udp", "newline")
        assert sender.send("<134>1 hello")
        data, _ = server.recvfrom(4096)
        server.close()
        sender.close()
        assert data == b"<134>1 hello"

    def test_tcp_failure_is_reported_not_raised(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        sender = syslog.SyslogSender("127.0.0.1", port, "tcp", "newline", timeout_s=1)
        assert not sender.send("x")
        assert sender.failed == 1
        assert sender.last_error

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""The update check parser and state file, and the one-way alert decisions."""

from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from mother_ticker.collectors import updates
from mother_ticker.collectors.updates import UpdatesStatus
from mother_ticker.config import AlertsConfig, Thresholds
from mother_ticker.health.evaluate import Level, evaluate
from mother_ticker.metrics import alerts
from mother_ticker.metrics.model import snapshot_to_metrics
from tests.conftest import make_snapshot

SIM = """NOTE: This is only a simulation!
      apt-get needs root privileges for real execution.
Reading package lists...
Building dependency tree...
Inst libc6 [2.41-9] (2.41-10 Debian-Security:13/stable-security [arm64])
Inst openssh-server [1:10.0p1-5] (1:10.0p1-6 Debian-Security:13/stable-security [arm64])
Inst chrony [4.6.1-1] (4.6.1-2 Debian:13.1/stable [arm64])
Inst tzdata (2026a-1 Debian:13.1/stable [all])
Conf libc6 (2.41-10 Debian-Security:13/stable-security [arm64])
Conf chrony (4.6.1-2 Debian:13.1/stable [arm64])
"""


class TestParser:
    def test_counts_and_security(self) -> None:
        pkgs, sec = updates.parse_simulated_upgrade(SIM)
        assert pkgs == ("libc6", "openssh-server", "chrony", "tzdata")
        assert sec == ("libc6", "openssh-server")

    def test_nothing_to_do(self) -> None:
        assert updates.parse_simulated_upgrade("Reading package lists...\n0 upgraded.\n") == (
            (),
            (),
        )


class TestState:
    def test_round_trip(self, tmp_path: Path) -> None:
        status = UpdatesStatus(
            checked_at=1_790_000_000.0,
            pending=4,
            security=2,
            packages=("a", "b", "c", "d"),
            security_packages=("a", "b"),
            reboot_required=True,
            lists_refreshed=False,
            lists_age_s=99.0,
            error="apt-get update: no route",
        )
        updates.save_state(status, tmp_path / "u.json")
        assert updates.load_state(tmp_path / "u.json") == status

    def test_missing_or_corrupt_is_unchecked(self, tmp_path: Path) -> None:
        assert not updates.load_state(tmp_path / "nope.json").checked
        (tmp_path / "bad.json").write_text("{nope")
        assert not updates.load_state(tmp_path / "bad.json").checked

    def test_run_check_with_fake_apt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The offline unit: apt-get update fails, the simulation still counts from old lists."""
        lists = tmp_path / "lists"
        lists.mkdir()
        (lists / "deb.debian.org_dists_trixie_InRelease").write_text("x")

        def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if args[:3] == ["apt-get", "-q", "update"]:
                return subprocess.CompletedProcess(
                    args, 100, stdout="", stderr="E: no route to host\n"
                )
            return subprocess.CompletedProcess(args, 0, stdout=SIM, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        status = updates.run_check(
            state_path=tmp_path / "u.json", reboot_flag=tmp_path / "no-reboot", lists_dir=lists
        )
        assert status.pending == 4 and status.security == 2
        assert not status.lists_refreshed
        assert status.error == "E: no route to host"
        assert status.lists_age_s is not None
        assert updates.load_state(tmp_path / "u.json").pending == 4


class TestHealthAndMetrics:
    def test_security_updates_warn(self) -> None:
        snap = make_snapshot()
        snap = replace(
            snap,
            updates=UpdatesStatus(
                checked_at=1.0, pending=3, security=1, security_packages=("libc6",)
            ),
        )
        report = evaluate(snap, Thresholds())
        assert report.level is Level.WARNING
        assert "1 security update pending" in report.headline

    def test_plain_updates_do_not_warn(self) -> None:
        snap = replace(make_snapshot(), updates=UpdatesStatus(checked_at=1.0, pending=3))
        assert evaluate(snap, Thresholds()).level is Level.OK

    def test_reboot_required_warns(self) -> None:
        snap = replace(make_snapshot(), updates=UpdatesStatus(checked_at=1.0, reboot_required=True))
        assert "reboot required" in evaluate(snap, Thresholds()).headline

    def test_metrics_present(self) -> None:
        snap = replace(
            make_snapshot(), updates=UpdatesStatus(checked_at=5.0, pending=3, security=1)
        )
        names = {m.name: m.value for m in snapshot_to_metrics(snap, evaluate(snap, Thresholds()))}
        assert names["mother_ticker_updates_pending"] == 3
        assert names["mother_ticker_security_updates_pending"] == 1
        assert names["mother_ticker_updates_checked"] == 1
        assert names["mother_ticker_updates_checked_timestamp_seconds"] == 5.0


class TestAlertDecisions:
    def _report(self, snap: object) -> object:
        return evaluate(snap, Thresholds())  # type: ignore[arg-type]

    def test_first_observation_sends_nothing(self) -> None:
        snap = make_snapshot()
        out = alerts.decide(
            snap,
            evaluate(snap, Thresholds()),
            last_level=None,
            last_security=None,
            min_level=Level.WARNING,
        )
        assert out == []

    def test_crossing_into_warning_and_recovering(self) -> None:
        ok = make_snapshot()
        sick = replace(ok, updates=UpdatesStatus(checked_at=1.0, reboot_required=True))
        up = alerts.decide(
            sick,
            evaluate(sick, Thresholds()),
            last_level=Level.OK,
            last_security=(),
            min_level=Level.WARNING,
        )
        assert [a.kind for a in up] == ["health"] and up[0].level is Level.WARNING
        down = alerts.decide(
            ok,
            evaluate(ok, Thresholds()),
            last_level=Level.WARNING,
            last_security=(),
            min_level=Level.WARNING,
        )
        assert [a.title for a in down] == ["recovered: time service"]

    def test_below_floor_is_silent(self) -> None:
        ok = make_snapshot()
        sick = replace(ok, updates=UpdatesStatus(checked_at=1.0, reboot_required=True))
        out = alerts.decide(
            sick,
            evaluate(sick, Thresholds()),
            last_level=Level.OK,
            last_security=(),
            min_level=Level.CRITICAL,
        )
        assert out == []

    def test_new_security_package_alerts_once(self) -> None:
        snap = replace(
            make_snapshot(),
            updates=UpdatesStatus(
                checked_at=1.0, pending=2, security=2, security_packages=("libc6", "sshd")
            ),
        )
        report = evaluate(snap, Thresholds())
        first = alerts.decide(
            snap,
            report,
            last_level=Level.WARNING,
            last_security=("libc6",),
            min_level=Level.WARNING,
        )
        assert [a.kind for a in first] == ["updates"] and "sshd" in first[0].message
        again = alerts.decide(
            snap,
            report,
            last_level=Level.WARNING,
            last_security=("libc6", "sshd"),
            min_level=Level.WARNING,
        )
        assert again == []

    def test_payload_shapes(self) -> None:
        snap = make_snapshot()
        report = evaluate(snap, Thresholds())
        alert = alerts.Alert("health", Level.CRITICAL, "t", "m", snap, report)
        j = json.loads(alerts.build_payload(alert, AlertsConfig(webhook_url="http://x")))
        assert j["level"] == "critical" and j["host"] == "ntp-main" and "updates" in j
        n = json.loads(
            alerts.build_payload(
                alert, AlertsConfig(webhook_url="http://x", format="ntfy", ntfy_topic="tt")
            )
        )
        assert n["topic"] == "tt" and n["priority"] == 5 and n["title"].startswith("ntp-main: ")


class TestSender:
    def test_posts_to_loopback_with_bearer(self, tmp_path: Path) -> None:
        got: list[tuple[str, bytes]] = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                got.append((self.headers.get("Authorization", ""), body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), H)
        Thread(target=server.serve_forever, daemon=True).start()
        token = tmp_path / "tok"
        token.write_text("s3cret\n")
        cfg = AlertsConfig(
            webhook_url=f"http://127.0.0.1:{server.server_address[1]}/hook", token_file=str(token)
        )
        sender = alerts.AlertSender(cfg)
        snap = make_snapshot()
        sender.consider(
            snap, evaluate(snap, Thresholds())
        )  # first observation: remembers, sends nothing
        sick = replace(snap, updates=UpdatesStatus(checked_at=1.0, reboot_required=True))
        sent = sender.consider(sick, evaluate(sick, Thresholds()))
        server.shutdown()
        assert len(sent) == 1 and sender.sent == 1 and sender.failed == 0
        assert got[0][0] == "Bearer s3cret"
        assert json.loads(got[0][1])["kind"] == "health"

    def test_failure_is_counted_not_raised(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        sender = alerts.AlertSender(
            AlertsConfig(webhook_url=f"http://127.0.0.1:{port}/x", timeout_s=1)
        )
        snap = make_snapshot()
        sender.consider(snap, evaluate(snap, Thresholds()))
        sick = replace(snap, updates=UpdatesStatus(checked_at=1.0, reboot_required=True))
        sender.consider(sick, evaluate(sick, Thresholds()))
        assert sender.failed == 1 and sender.last_error

    def test_disabled_sender_decides_but_sends_nothing(self) -> None:
        sender = alerts.AlertSender(AlertsConfig())
        snap = make_snapshot()
        sender.consider(snap, evaluate(snap, Thresholds()))
        sick = replace(snap, updates=UpdatesStatus(checked_at=1.0, reboot_required=True))
        assert len(sender.consider(sick, evaluate(sick, Thresholds()))) == 1
        assert sender.sent == 0

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Host and network parsers."""

from __future__ import annotations

import json
from pathlib import Path

from mother_ticker.collectors import network, services, system
from tests.conftest import FIXTURES


class TestSystemParsers:
    def test_meminfo(self) -> None:
        total, avail = system.parse_meminfo((FIXTURES / "meminfo.txt").read_text())
        assert (total, avail) == (916256, 700000)

    def test_meminfo_missing_fields(self) -> None:
        assert system.parse_meminfo("") == (0, 0)

    def test_throttled_flags(self) -> None:
        assert system.parse_throttled("throttled=0x50005\n") == 0x50005
        assert system.parse_throttled("garbage") is None
        reasons = system.throttle_reasons(0x50005)
        assert "under-voltage now" in reasons
        assert "throttled now" in reasons
        assert "under-voltage occurred" in reasons
        assert system.throttle_reasons(0) == []
        assert system.throttle_reasons(None) == []

    def test_overlay_detection(self) -> None:
        assert system.is_overlay_root("console=tty1 root=PARTUUID=x boot=overlay rootwait")
        assert not system.is_overlay_root("console=tty1 root=PARTUUID=x rootwait")

    def test_collect_from_fake_proc(self, tmp_path: Path) -> None:
        proc = tmp_path / "proc"
        proc.mkdir()
        (proc / "uptime").write_text("12345.67 40000.0\n")
        (proc / "loadavg").write_text("0.15 0.10 0.05 1/120 4321\n")
        (proc / "meminfo").write_text((FIXTURES / "meminfo.txt").read_text())
        (proc / "cmdline").write_text("boot=overlay\n")
        thermal = tmp_path / "temp"
        thermal.write_text("51234\n")
        status = system.collect_system(proc=proc, thermal=thermal, root=str(tmp_path))
        assert status.uptime_s == 12345.67
        assert status.load_5 == 0.10
        assert status.temperature_c == 51.234
        assert status.overlay_root
        assert 0 < status.mem_used_pct < 100
        assert status.disk_total_b > 0

    def test_collect_without_thermal(self, tmp_path: Path) -> None:
        proc = tmp_path / "proc"
        proc.mkdir()
        (proc / "uptime").write_text("1 1\n")
        (proc / "loadavg").write_text("0 0 0 1/1 1\n")
        (proc / "meminfo").write_text("")
        status = system.collect_system(proc=proc, thermal=tmp_path / "nope", root=str(tmp_path))
        assert status.temperature_c is None
        assert status.mem_used_pct == 0.0


class TestNetwork:
    def test_parse_ip_addr(self) -> None:
        status = network.parse_ip_addr(json.loads((FIXTURES / "ip_addr.json").read_text()))
        names = [i.name for i in status.interfaces]
        assert names == ["lo", "eth0", "wlan0"]
        eth0 = status.interfaces[1]
        assert eth0.state == "UP"
        assert eth0.mac == "dc:a6:32:12:34:56"
        assert [a.address for a in eth0.addresses] == [
            "192.0.2.20",
            "2001:db8::20",
            "fe80::dea6:32ff:fe12:3456",
        ]

    def test_primary_addresses_skip_loopback_and_link_local(self) -> None:
        status = network.parse_ip_addr(json.loads((FIXTURES / "ip_addr.json").read_text()))
        assert status.primary_addresses() == ["192.0.2.20/24 (eth0)", "2001:db8::20/64 (eth0)"]

    def test_empty(self) -> None:
        assert network.parse_ip_addr([]).primary_addresses() == []


class TestServices:
    def test_parse_show(self) -> None:
        text = (
            "ActiveState=active\nSubState=running\nNRestarts=2\n"
            "ActiveEnterTimestamp=Mon 2026-09-21\n"
        )
        s = services.parse_show("gpsd", text)
        assert s.active_state == "active" and s.restarts == 2 and s.since == "Mon 2026-09-21"

    def test_parse_show_missing(self) -> None:
        s = services.parse_show("gpsd", "")
        assert s.active_state == "unknown" and s.restarts == 0

    def test_restart_refuses_unlisted_unit(self) -> None:
        ok, msg = services.restart_service("sshd", allowed=("chrony", "gpsd"))
        assert not ok and "not in the allowed" in msg

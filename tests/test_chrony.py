# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""chronyc CSV parsers against recorded output."""

from __future__ import annotations

import subprocess

import pytest

from mother_ticker.collectors import chrony
from tests.conftest import FIXTURES


class TestTracking:
    def test_pps_locked(self) -> None:
        t = chrony.parse_tracking_csv((FIXTURES / "chronyc_tracking_pps.csv").read_text())
        assert t.reachable
        assert t.synchronised
        assert t.ref_name == "PPS"
        assert t.stratum == 1
        assert t.system_time_offset_s == pytest.approx(-2.1e-8)
        assert t.frequency_ppm == pytest.approx(-3.512)
        assert t.leap_status == "Normal"

    def test_not_synchronised(self) -> None:
        t = chrony.parse_tracking_csv((FIXTURES / "chronyc_tracking_unsync.csv").read_text())
        assert t.reachable
        assert not t.synchronised
        assert t.stratum == 0

    def test_short_line_rejected(self) -> None:
        with pytest.raises(ValueError, match="unexpected tracking line"):
            chrony.parse_tracking_csv("a,b,c\n")


class TestSources:
    def test_parses_all_rows(self) -> None:
        sources = chrony.parse_sources_csv((FIXTURES / "chronyc_sources_pps.csv").read_text())
        assert [s.name for s in sources] == ["NMEA", "PPS", "time.cloudflare.com", "time.nist.gov"]

    def test_labels_and_reach(self) -> None:
        sources = chrony.parse_sources_csv((FIXTURES / "chronyc_sources_pps.csv").read_text())
        nmea, pps, cf, nist = sources
        assert nmea.mode_label == "refclock"
        assert nmea.state_label == "false ticker"  # noselect sources show as x, which is expected
        assert pps.state_label == "selected"
        assert pps.reach_octal == "377"
        assert cf.mode_label == "server"
        assert nist.state_label == "unreachable"
        assert nist.reach_octal == "000"

    def test_blank_and_short_lines_skipped(self) -> None:
        assert chrony.parse_sources_csv("\n\n#,*,PPS\n") == ()


class TestCollect:
    def test_chronyc_missing_reports_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_: object, **__: object) -> None:
            raise FileNotFoundError("chronyc")

        monkeypatch.setattr(subprocess, "run", boom)
        status = chrony.collect_chrony()
        assert not status.tracking.reachable
        assert "chronyc" in (status.tracking.error or "")

    def test_nonzero_exit_reports_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                ["chronyc"], 1, stdout="", stderr="506 Cannot talk to daemon"
            )

        monkeypatch.setattr(subprocess, "run", fake)
        status = chrony.collect_chrony()
        assert not status.tracking.reachable
        assert "506" in (status.tracking.error or "")

    def test_success_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        outputs = {
            ("-c", "tracking"): (FIXTURES / "chronyc_tracking_pps.csv").read_text(),
            ("-c", "sources"): (FIXTURES / "chronyc_sources_pps.csv").read_text(),
            ("tracking",): "Reference ID    : 50505300 (PPS)\n",
            ("sources", "-v"): "MS Name/IP address ...\n",
        }

        def fake(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, stdout=outputs[tuple(args[1:])], stderr="")

        monkeypatch.setattr(subprocess, "run", fake)
        status = chrony.collect_chrony()
        assert status.tracking.ref_name == "PPS"
        assert len(status.sources) == 4
        assert "PPS" in status.tracking_text

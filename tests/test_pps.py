# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""PPS freshness rule against a fake sysfs tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from mother_ticker.collectors import pps


class TestParseAssert:
    def test_parses(self) -> None:
        assert pps.parse_assert("1790000000.000000123#86400\n") == (1790000000.000000123, 86400)

    def test_garbage(self) -> None:
        with pytest.raises(ValueError, match="unexpected assert line"):
            pps.parse_assert("nonsense")


class TestEvaluate:
    def test_fresh_edge_is_pulsing(self) -> None:
        s = pps.evaluate_pps("pps0", "1000.5#10", now=1001.0, stale_after_s=3.0)
        assert s.present and s.pulsing
        assert s.age_s == pytest.approx(0.5)
        assert s.assert_sequence == 10

    def test_stale_edge_is_not_pulsing(self) -> None:
        """The failure this guards: a device that exists but whose GPS stopped pulsing."""
        s = pps.evaluate_pps("pps0", "1000.0#10", now=1010.0, stale_after_s=3.0)
        assert s.present and not s.pulsing
        assert s.age_s == pytest.approx(10.0)

    def test_missing_device(self) -> None:
        s = pps.evaluate_pps("pps0", None, now=0.0, stale_after_s=3.0)
        assert not s.present and not s.pulsing
        assert s.error == "no such PPS device"

    def test_unreadable_line(self) -> None:
        s = pps.evaluate_pps("pps0", "junk", now=0.0, stale_after_s=3.0)
        assert s.present and not s.pulsing and s.error


class TestCollectFromSysfs:
    def test_reads_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "pps0").mkdir()
        (tmp_path / "pps0" / "assert").write_text("500.25#7\n")
        monkeypatch.setattr("mother_ticker.collectors.pps.time.time", lambda: 501.0)
        s = pps.collect_pps("pps0", 3.0, root=tmp_path)
        assert s.pulsing and s.assert_sequence == 7

    def test_absent_tree(self, tmp_path: Path) -> None:
        s = pps.collect_pps("pps0", 3.0, root=tmp_path)
        assert not s.present
